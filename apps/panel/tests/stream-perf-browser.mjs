import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, mkdir, writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

// Stream Performance Baseline Runner (Stage 0 - Corrected)
// Tests real hp-chat rendering performance with synthetic data under CDP CPU throttling (rate: 4).
// Scenarios:
//   Scenario A: Short history (<=5 turns), 2,000 chars response.
//   Scenario B: 200 turns history with tool groups, 20,000 chars response containing a 300-line ts code block.
// Metrics recorded:
//   - Frame time distribution via rAF (p50, p95, max)
//   - Long tasks (>50ms) count & details via PerformanceObserver
//   - CDP Profiler CPU execution time: marked lexer, parseAssistantContent, stableHTML/isEqualNode, hljs.highlight, Lit render/update
//   - Layout & Forced reflow metrics via CDP Tracing and DOM layout property intercepts
//   - Receive vs visible display lag via DOM text watermarks (p50, p95, max, unmatched)
//   - Settle duration upon run completion
// Output: JSON written to STREAM_PERF_REPORT and stdout.

const scratchRoot = process.env.PANEL_TEST_OUTPUT || process.env.TMPDIR || '/tmp';
const candidateRoot = process.env.STREAM_PERF_CANDIDATE_ROOT || path.resolve(import.meta.dirname, '..');
const owned = await mkdtemp(path.join(scratchRoot, 'stream-perf-'));

let server, browser, socket;
let sequence = 0;
const pending = new Map();

try {
  server = await createServer({
    configFile: false,
    root: candidateRoot,
    cacheDir: path.join(owned, 'vite'),
    plugins: [{
      name: 'current-performance-fixture-for-candidate-source',
      configureServer(vite) {
        vite.middlewares.use(async (request, response, next) => {
          const requestUrl = new URL(request.url, 'http://fixture.invalid');
          const route = requestUrl.pathname;
          // Vite serves transformed inline modules through its html-proxy cache, not HTML.
          if (requestUrl.searchParams.has('html-proxy')) return next();
          if (!['/tests/stream-perf-fixture.html', '/tests/stream-perf-watermark.mjs'].includes(route)) return next();
          try {
            const file = path.join(import.meta.dirname, path.basename(route));
            const source = await readFile(file, 'utf8');
            const html = route.endsWith('.html');
            response.setHeader('Content-Type', html ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8');
            response.end(html ? await vite.transformIndexHtml(route, source) : source);
          } catch (error) { next(error); }
        });
      },
    }],
    server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
    logLevel: 'error'
  });
  await server.listen();
  const port = server.httpServer.address().port;
  const allowedOrigin = `http://127.0.0.1:${port}`;

  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--remote-debugging-port=0',
    `--user-data-dir=${path.join(owned, 'profile')}`,
    'about:blank'
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  const wsUrl = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Chromium startup timeout')), 15000);
    browser.stderr.on('data', chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.once('error', e => { clearTimeout(timer); reject(e); });
    browser.once('exit', code => { clearTimeout(timer); reject(new Error(`Chromium exited ${code}`)); });
  });

  socket = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  const traceEvents = [];
  const blockedNetworkRequests = [];
  const networkGuardErrors = [];

  socket.addEventListener('message', ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.method === 'Tracing.dataCollected') {
      traceEvents.push(...msg.params.value);
    }
    // CDP Network guard intercept: fail any unauthorized requests to non-fixture origin
    if (msg.method === 'Fetch.requestPaused') {
      const { requestId, request } = msg.params;
      const reqUrl = request.url;
      const isAllowed = reqUrl.startsWith(allowedOrigin + '/') || reqUrl === allowedOrigin || reqUrl.startsWith('about:blank') || reqUrl.startsWith('data:');
      if (!isAllowed) {
        blockedNetworkRequests.push(reqUrl);
        call('Fetch.failRequest', { requestId, errorReason: 'AccessDenied' }, msg.sessionId).catch(error => networkGuardErrors.push(String(error)));
      } else {
        call('Fetch.continueRequest', { requestId }, msg.sessionId).catch(error => networkGuardErrors.push(String(error)));
      }
    }
    const p = pending.get(msg.id);
    if (p) {
      pending.delete(msg.id);
      clearTimeout(p.timer);
      msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
    }
  });

  const call = (method, params = {}, sessionId, timeoutMs = 60000) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  const targets = await call('Target.getTargets');
  const pageTarget = targets.targetInfos.find(t => t.type === 'page');
  const { sessionId } = await call('Target.attachToTarget', { targetId: pageTarget.targetId, flatten: true });

  const evaluate = async (expr, targetSession = sessionId, timeoutMs = 60000) => {
    const r = await call('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, targetSession, timeoutMs);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  // Configure CDP Network guard before any navigation occurs
  await call('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, sessionId);

  // Configure viewport and CPU throttling (4x)
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  await call('Emulation.setCPUThrottlingRate', { rate: 4 }, sessionId);

  await call('Page.navigate', { url: `${allowedOrigin}/tests/stream-perf-fixture.html` }, sessionId);
  for (let i = 0; i < 100; i++) {
    if (await evaluate('window.ready')) break;
    await new Promise(r => setTimeout(r, 50));
  }

  assert.equal(await evaluate('window.ready === true'), true, 'Candidate fixture must be ready');

  async function prepareReferences(fullText, chunkBoundaries) {
    const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
    try {
      const { sessionId: oracleSession } = await call('Target.attachToTarget', { targetId, flatten: true });
      await call('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, oracleSession);
      await call('Page.navigate', { url: `${allowedOrigin}/tests/stream-perf-fixture.html` }, oracleSession);
      let ready = false;
      for (let i = 0; i < 200; i++) {
        if (await evaluate('window.ready === true', oracleSession)) { ready = true; break; }
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      assert.equal(ready, true, 'Isolated reference fixture must be ready');

      // Execute preparePrefixDomReferences in isolated realm with approved 120s budget
      const prepResult = await evaluate(
        `window.preparePrefixDomReferences(${JSON.stringify(fullText)}, ${JSON.stringify(chunkBoundaries)}, { budgetMs: 120000 })`,
        oracleSession, 130000
      );

      const stats = prepResult.stats;
      console.error(`Reference preparation stats: elapsed=${stats.elapsedMs.toFixed(1)}ms, codepoints=${stats.codepointCount}, records=${stats.recordsCount}, complete=${stats.complete}, aborted=${stats.aborted}, heap=${stats.memoryMB}MB`);

      if (stats.aborted) {
        throw new Error(`Oracle preparation aborted: ${stats.abortReason}`);
      }

      // Serialize inside the oracle realm: CDP cannot preserve Map/function values.
      assert.ok(Array.isArray(prepResult.serializedIndex?.entries), 'Oracle must return a serialized reference index');
      assert.ok(stats.complete && stats.recordsCount === stats.codepointCount + 1, 'Oracle must cover every codepoint, including zero');
      assert.ok(stats.elapsedMs <= 120000, 'Reference preparation exceeded the approved time budget');
      assert.ok(stats.memoryMB !== null && stats.memoryMB <= 128, 'Reference memory measurement must be available and within budget');
      return { serializedIndex: prepResult.serializedIndex, stats };
    } finally { await call('Target.closeTarget', { targetId }); }
  }

  // Pseudo-random sequence with fixed seed for deterministic reproducible chunks
  function createPrng(seed = 123456789) {
    let s = seed;
    return function next() {
      s = (s * 1664525 + 1013904223) % 4294967296;
      return s / 4294967296;
    };
  }

  // Helper to run a test scenario
  async function runScenario(scenarioName, config) {
    console.error(`Starting ${scenarioName}...`);
    // 1. Generate synthetic history messages
    const history = [];
    let userTurnCount = 0;
    let toolCount = 0;

    if (config.historyTurns > 0) {
      for (let t = 0; t < config.historyTurns; t++) {
        const uId = t * 2 + 1;
        const aId = t * 2 + 2;
        // User query turn
        history.push({
          id: uId,
          role: 'user',
          content: `User query ${t}: ${'Please analyze this task component. '.repeat(2)}`,
          timestamp: 1000 + t * 10
        });
        userTurnCount++;

        // Assistant turn
        if (config.includeToolsInHistory && t % 5 === 0) {
          history.push({
            id: aId,
            role: 'assistant',
            content: `Step commentary before tool call in turn ${t}.\n`,
            tool_calls: [{
              id: `tool-${t}`,
              function: { name: 'bash', arguments: '{"command":"git status"}' }
            }],
            timestamp: 1000 + t * 10 + 2
          });
          history.push({
            id: aId + 10000,
            role: 'tool',
            tool_call_id: `tool-${t}`,
            tool_name: 'bash',
            content: `Output for step ${t}: status clean, branch hk ahead of origin/hk by 1 commit.`,
            timestamp: 1000 + t * 10 + 4
          });
          toolCount++;
        } else {
          history.push({
            id: aId,
            role: 'assistant',
            content: `Assistant response ${t}: ${'Here is the intermediate result for discussion. '.repeat(5)}`,
            timestamp: 1000 + t * 10 + 2
          });
        }
      }
    }

    // 2. Mount chat with history
    await evaluate(`mountChat(${JSON.stringify(history)})`);

    // Assert exact component history turns and composition loaded
    const loadedHistoryInfo = await evaluate(`(() => {
      const turns = window.chat && window.chat.turns ? window.chat.turns : [];
      let uCount = 0;
      let aCount = 0;
      let tCount = 0;
      for (const t of turns) {
        if (t.role === 'user') uCount++;
        else if (t.role === 'assistant') {
          aCount++;
          if (Array.isArray(t.steps)) {
            for (const st of t.steps) {
              if (st.kind === 'tool' || st.tool_call) tCount++;
            }
          }
        }
      }
      return { totalTurns: turns.length, userCount: uCount, assistantCount: aCount, toolCount: tCount };
    })()`);

    if (config.historyTurns >= 200) {
      assert.equal(loadedHistoryInfo.userCount, userTurnCount, `Expected exact ${userTurnCount} user turns, got ${loadedHistoryInfo.userCount}`);
      if (config.includeToolsInHistory) {
        assert.equal(loadedHistoryInfo.toolCount, toolCount, `Expected exact ${toolCount} tool steps, got ${loadedHistoryInfo.toolCount}`);
      }
      assert.ok(loadedHistoryInfo.totalTurns >= 200, `Expected total turns >= 200, got ${loadedHistoryInfo.totalTurns}`);
    }

    // Generate all synthetic data and isolated DOM references before starting timed instrumentation.

    // 3. Generate stream chunks to dispatch
    // Rule: push 3-8 chars every 16ms, interspersed with 200ms bursts of 400 chars.
    // If hasCodeBlock: midway into stream, insert ```ts with 300 lines of code, then close ```.
    const fullTextParts = [];
    const targetLength = config.streamTargetChars;

    if (config.include300LineCode) {
      const codeLines = [];
      for (let l = 1; l <= 300; l++) {
        codeLines.push(`  const item${l}: number = ${l};`);
      }
      assert.equal(codeLines.length, 300, 'Must have 300 lines of code in TS block');
      const codeBlock = `\n\n\`\`\`ts\nexport function calculateMetrics(): void {\n${codeLines.join('\n')}\n}\n\`\`\`\n\n`;

      const targetChars = config.streamTargetChars;
      const codeBlockLen = codeBlock.length;
      const remainChars = Math.max(1000, targetChars - codeBlockLen);
      const halfChars = Math.ceil(remainChars / 2);
      const leadBase = 'In this analysis, we examine the streaming pipeline performance and architectural boundaries.\n\n';
      const tailBase = 'Following the code inspection, we conclude that incremental parsing and stable DOM retention are essential.\n\n';
      const leadProse = leadBase.repeat(Math.ceil(halfChars / leadBase.length)).slice(0, halfChars);
      const neededTailChars = Math.max(0, targetChars - codeBlockLen - leadProse.length);
      const tailProse = tailBase.repeat(Math.ceil(neededTailChars / tailBase.length)).slice(0, neededTailChars);
      fullTextParts.push(leadProse, codeBlock, tailProse);
    } else {
      const base = 'In this benchmark, we test the assistant text streaming capabilities under throttling.\n\n';
      const repeated = base.repeat(Math.ceil(targetLength / base.length));
      fullTextParts.push(repeated.slice(0, targetLength));
    }
    const fullStreamText = fullTextParts.join('');

    if (config.streamTargetChars >= 20000) {
      assert.ok(fullStreamText.length >= 20000, `Expected streamChars >= 20000, got ${fullStreamText.length}`);
    } else if (config.streamTargetChars >= 2000) {
      assert.ok(fullStreamText.length >= 2000, `Expected streamChars >= 2000, got ${fullStreamText.length}`);
    }

    // Slice fullStreamText into realistic chunks deterministically using seeded PRNG
    const prng = createPrng(config.historyTurns + 42);
    const chunks = [];
    let idx = 0;
    let burstCounter = 0;
    while (idx < fullStreamText.length) {
      burstCounter++;
      let chunkSize;
      if (burstCounter % 12 === 0) {
        // ~200ms burst (12 frames * 16ms ~= 192ms)
        chunkSize = Math.min(400, fullStreamText.length - idx);
      } else {
        chunkSize = Math.min(Math.floor(prng() * 6) + 3, fullStreamText.length - idx);
      }
      chunks.push(fullStreamText.slice(idx, idx + chunkSize));
      idx += chunkSize;
    }

    console.error('Preparing reference DOM in an isolated page (outside measurement)...');
    const chunkBoundaries = [];
    let cumulativeLen = 0;
    for (const c of chunks) {
      cumulativeLen += c.length;
      chunkBoundaries.push(cumulativeLen);
    }
    const { serializedIndex, stats: prepStats } = await prepareReferences(fullStreamText, chunkBoundaries);
    await evaluate(`setDomReferences(${JSON.stringify(serializedIndex)})`);
    traceEvents.length = 0;
    await call('Tracing.start', { traceConfig: {
      includedCategories: ['devtools.timeline', 'disabled-by-default-devtools.timeline'], recordMode: 'recordAsMuchAsPossible'
    } });
    await call('Profiler.enable', {}, sessionId);
    await call('Profiler.setSamplingInterval', { interval: 100 }, sessionId);
    await call('Profiler.start', {}, sessionId);
    await evaluate(`(() => {
      perfMetrics.forcedReflowCount = 0;
      perfMetrics.interceptReflow = true;
      perfMetrics.longTasks = [];
      startRafTracking();
    })()`);

    // 4. Dispatch stream chunks to hp-chat via synthetic runs
    console.error(`Streaming ${chunks.length} chunks (${fullStreamText.length} chars) to hp-chat...`);

    const streamStartWall = Date.now();
    await evaluate(`runStreamWorkload(${JSON.stringify(chunks)})`);
    const streamEndWall = Date.now();

    // 5. Measure completion wall time separately from the worst settle frame.
    await evaluate('window.perfMetrics.streamFrameCount = window.perfMetrics.rafIntervals.length');
    console.error(`Triggering run completion and measuring settle cost...`);
    const settleDurationMs = await evaluate(`runSettleWorkload()`);

    // 6. Stop Profiler and Tracing
    const profileRes = await call('Profiler.stop', {}, sessionId);
    await call('Profiler.disable', {}, sessionId);

    await new Promise(res => {
      const onComplete = ({ data }) => {
        const m = JSON.parse(data);
        if (m.method === 'Tracing.tracingComplete') {
          socket.removeEventListener('message', onComplete);
          res();
        }
      };
      socket.addEventListener('message', onComplete);
      call('Tracing.end');
    });

    // Stop rAF and reflow tracking
    await evaluate(`(async () => {
      await window.stopRafTracking();
      window.perfMetrics.interceptReflow = false;
    })()`);

    // Check disallowed requests (both in-page fetch guard and CDP network guard)
    const disallowedRequests = await evaluate('window.disallowedRequests');
    assert.deepEqual(disallowedRequests, [], 'Fixture in-page fetch guard must not execute disallowed network requests');
    assert.deepEqual(blockedNetworkRequests, [], 'CDP Fetch guard must not intercept external network requests');
    assert.deepEqual(networkGuardErrors, [], 'CDP network guard failures must remain visible');

    // 7. Extract metrics from browser
    const clientMetrics = await evaluate(`(() => {
      const intervals = window.perfMetrics.rafIntervals;
      const settleRaf = intervals.slice(window.perfMetrics.streamFrameCount);
      const streamingRaf = intervals.slice(0, window.perfMetrics.streamFrameCount).sort((a, b) => a - b);
      const raf = [...intervals].sort((a, b) => a - b);
      const longTasks = window.perfMetrics.longTasks;
      const lag = window.calculateLagMetrics();
      return {
        rafCount: raf.length,
        streamingFrameCount: streamingRaf.length,
        streamingP95Ms: streamingRaf[Math.floor(streamingRaf.length * 0.95)] ?? null,
        settleFrameCount: settleRaf.length,
        settleMaxFrameMs: settleRaf.length ? Math.max(...settleRaf) : null,
        rafP50Ms: raf[Math.floor(raf.length * 0.5)] || 0,
        rafP95Ms: raf[Math.floor(raf.length * 0.95)] || 0,
        rafMaxMs: raf[raf.length - 1] || 0,
        longTaskCount: longTasks.length,
        longTasksTotalMs: longTasks.reduce((acc, t) => acc + t.duration, 0),
        longTasksMaxMs: longTasks.reduce((max, t) => Math.max(max, t.duration), 0),
        forcedReflowCount: window.perfMetrics.forcedReflowCount,
        lagP50Ms: lag.p50LagMs,
        lagP95Ms: lag.p95LagMs,
        lagMaxMs: lag.maxLagMs,
        lagMatchedCount: lag.matchedCount,
        lagUnmatchedCount: lag.unmatchedCount,
        lagAmbiguousCount: lag.ambiguousCount,
        lagSampledCount: lag.sampledCount
      };
    })()`);

    // 8. Analyze CPU Profile (distinguish active script execution from (idle) / (program) / root)
    const { nodes, samples = [], timeDeltas = [] } = profileRes.profile;
    const nodeMap = new Map();
    for (const n of nodes) nodeMap.set(n.id, n);

    const parentMap = new Map();
    for (const n of nodes) {
      if (n.children) {
        for (const childId of n.children) {
          parentMap.set(childId, n.id);
        }
      }
    }

    function getStack(nodeId) {
      const chain = [];
      let curr = nodeId;
      while (curr !== undefined) {
        chain.push(curr);
        curr = parentMap.get(curr);
      }
      return chain;
    }

    const selfTimesUs = new Map();
    const inclusiveTimesUs = new Map();
    let actualScriptTotalUs = 0;

    for (let i = 0; i < samples.length; i++) {
      const leafId = samples[i];
      const dt = timeDeltas[i] || 0;
      const leafNode = nodeMap.get(leafId);
      if (leafNode) {
        const fn = leafNode.callFrame.functionName || '(anonymous)';
        // Exclude system idle and root from script calculation
        if (fn !== '(idle)' && fn !== '(root)' && fn !== '(garbage collector)') {
          actualScriptTotalUs += dt;
        }
        selfTimesUs.set(fn, (selfTimesUs.get(fn) || 0) + dt);
      }
      const stack = getStack(leafId);
      const seen = new Set();
      for (const nid of stack) {
        const n = nodeMap.get(nid);
        if (!n) continue;
        const fn = n.callFrame.functionName || '(anonymous)';
        if (!seen.has(fn)) {
          seen.add(fn);
          inclusiveTimesUs.set(fn, (inclusiveTimesUs.get(fn) || 0) + dt);
        }
      }
    }

    // Function time helpers
    const getFnTime = (fnNameRegex) => {
      let selfMs = 0;
      let incMs = 0;
      for (const [fn, us] of selfTimesUs.entries()) {
        if (fnNameRegex.test(fn)) selfMs += us / 1000;
      }
      for (const [fn, us] of inclusiveTimesUs.entries()) {
        if (fnNameRegex.test(fn)) incMs += us / 1000;
      }
      return { selfMs, incMs };
    };

    // Stage breakdown:
    const markedLexer = getFnTime(/^lexer$|^lex$/i);
    const parseAssistant = getFnTime(/^parseAssistantContent$/i);
    const stableHTMLTime = getFnTime(/^StableHTML$|^patchChildren$|^patchNode$/i);
    const isEqualNodeTime = getFnTime(/^isEqualNode$/i);
    const hljsTime = getFnTime(/^highlight$|^_highlight$/i);
    const litRenderTime = getFnTime(/^performUpdate$|^_render$|^renderBody$|^renderLive$/i);

    // Total script duration from actual script execution samples (excluding idle)
    const totalScriptMs = actualScriptTotalUs / 1000;
    const avgScriptPerFrameMs = clientMetrics.rafCount > 0 ? (totalScriptMs / clientMetrics.rafCount) : 0;

    // 9. Analyze Tracing Layout Events
    const layoutEvents = traceEvents.filter(e => e.name === 'Layout');
    const updateLayoutTreeEvents = traceEvents.filter(e => e.name === 'UpdateLayoutTree');
    const totalLayoutDurUs = layoutEvents.reduce((acc, e) => acc + (e.dur || 0), 0);
    const totalUpdateLayoutTreeDurUs = updateLayoutTreeEvents.reduce((acc, e) => acc + (e.dur || 0), 0);

    return {
      scenario: scenarioName,
      historyTurns: config.historyTurns,
      loadedHistory: loadedHistoryInfo,
      streamChars: fullStreamText.length,
      chunksDispatched: chunks.length,
      streamDurationMs: streamEndWall - streamStartWall,
      settleDurationMs: Math.round(settleDurationMs * 100) / 100,
      settleFrames: { count: clientMetrics.settleFrameCount, maxMs: clientMetrics.settleMaxFrameMs },
      streamingFrames: { count: clientMetrics.streamingFrameCount, p95Ms: clientMetrics.streamingP95Ms },
      frames: {
        totalRafFrames: clientMetrics.rafCount,
        p50Ms: Math.round(clientMetrics.rafP50Ms * 100) / 100,
        p95Ms: Math.round(clientMetrics.rafP95Ms * 100) / 100,
        maxMs: Math.round(clientMetrics.rafMaxMs * 100) / 100,
        longTasksCount: clientMetrics.longTaskCount,
        longTasksTotalMs: Math.round(clientMetrics.longTasksTotalMs * 100) / 100,
        longTasksMaxMs: Math.round(clientMetrics.longTasksMaxMs * 100) / 100
      },
      scriptTimes: {
        totalScriptMs: Math.round(totalScriptMs * 100) / 100,
        avgScriptPerFrameMs: Math.round(avgScriptPerFrameMs * 100) / 100,
        markedLexerIncMs: Math.round(markedLexer.incMs * 100) / 100,
        parseAssistantContentIncMs: Math.round(parseAssistant.incMs * 100) / 100,
        stableHTMLIncMs: Math.round(stableHTMLTime.incMs * 100) / 100,
        isEqualNodeIncMs: Math.round(isEqualNodeTime.incMs * 100) / 100,
        hljsHighlightIncMs: Math.round(hljsTime.incMs * 100) / 100,
        litRenderIncMs: Math.round(litRenderTime.incMs * 100) / 100
      },
      layout: {
        traceLayoutCount: layoutEvents.length,
        traceLayoutTotalMs: Math.round(totalLayoutDurUs / 10) / 100,
        traceUpdateLayoutTreeCount: updateLayoutTreeEvents.length,
        traceUpdateLayoutTreeTotalMs: Math.round(totalUpdateLayoutTreeDurUs / 10) / 100,
        forcedReflowPropertyReadCount: clientMetrics.forcedReflowCount
      },
      lag: {
        sampledCount: clientMetrics.lagSampledCount,
        matchedCount: clientMetrics.lagMatchedCount,
        unmatchedCount: clientMetrics.lagUnmatchedCount,
        ambiguousCount: clientMetrics.lagAmbiguousCount,
        measurement: 'receive-to-observed-DOM-source-version (not painted pixels)',
        p50LagMs: clientMetrics.lagP50Ms,
        p95LagMs: clientMetrics.lagP95Ms,
        maxLagMs: clientMetrics.lagMaxMs
      }
    };
  }

  // Run Scenario A: Short history (<=5 turns), 2,000 chars response
  const resultA = await runScenario('Scenario_A_ShortHistory_2K', {
    historyTurns: 4,
    streamTargetChars: 2000,
    includeToolsInHistory: false,
    include300LineCode: false
  });

  // Run Scenario B: 200 turns history (with tool groups), 20,000 chars response with 300-line code block
  const resultB = await runScenario('Scenario_B_200Turns_20K_300LineCode', {
    historyTurns: 200,
    streamTargetChars: 20000,
    includeToolsInHistory: true,
    include300LineCode: true
  });

  const fullReport = {
    timestamp: new Date().toISOString(),
    baselineCommit: 'f7acd2bd',
    cpuThrottlingRate: 4,
    deviceMetrics: { width: 1280, height: 800 },
    scenarios: {
      scenarioA: resultA,
      scenarioB: resultB
    },
    targetsComparison: {
      frameP95TargetMs: 16.7,
      scenarioA_frameP95: resultA.frames.p95Ms,
      scenarioB_frameP95: resultB.frames.p95Ms,
      frameP95TargetMet: resultA.frames.p95Ms <= 16.7 && resultB.frames.p95Ms <= 16.7,

      longTaskTarget: 0,
      scenarioA_longTasks: resultA.frames.longTasksCount,
      scenarioB_longTasks: resultB.frames.longTasksCount,
      longTaskTargetMet: resultA.frames.longTasksCount === 0 && resultB.frames.longTasksCount === 0,

      scriptPerFrameTargetMs: 4.0,
      scenarioA_avgScriptPerFrame: resultA.scriptTimes.avgScriptPerFrameMs,
      scenarioB_avgScriptPerFrame: resultB.scriptTimes.avgScriptPerFrameMs,
      scriptPerFrameTargetMet: resultA.scriptTimes.avgScriptPerFrameMs <= 4.0 && resultB.scriptTimes.avgScriptPerFrameMs <= 4.0,

      outputLagP95TargetMs: 300.0,
      scenarioA_lagP95Ms: resultA.lag.p95LagMs,
      scenarioB_lagP95Ms: resultB.lag.p95LagMs,
      outputLagTargetMet: [resultA, resultB].every(result => result.lag.sampledCount > 0
        && result.lag.unmatchedCount === 0 && result.lag.ambiguousCount === 0
        && result.lag.p95LagMs !== null && result.lag.p95LagMs <= 300.0),

      endSettleFrameTargetMs: 50.0,
      scenarioA_settleDurationMs: resultA.settleDurationMs,
      scenarioB_settleDurationMs: resultB.settleDurationMs,
      endSettleTargetMet: [resultA, resultB].every(result => result.settleFrames.count > 0
        && result.settleFrames.maxMs !== null && result.settleFrames.maxMs <= 50.0)
    }
  };

  const outputJson = JSON.stringify(fullReport, null, 2);
  console.log('\n===== STREAM PERFORMANCE BASELINE REPORT =====\n');
  console.log(outputJson);
  console.log('\n==============================================\n');

  const reportPath = process.env.STREAM_PERF_REPORT || path.resolve(import.meta.dirname, '../../panel-improvement-evidence/parent-review/perf-corrected/baseline-perf.json');
  await mkdir(path.dirname(reportPath), { recursive: true });
  await writeFile(reportPath, outputJson);
  console.error(`Wrote report to ${reportPath}`);

} catch (err) {
  console.error('Test execution failed:', err);
  process.exitCode = 1;
} finally {
  socket?.close();
  for (const p of pending.values()) clearTimeout(p.timer);
  if (browser) {
    browser.kill('SIGTERM');
    await new Promise(r => browser.exitCode !== null ? r() : browser.once('exit', r));
  }
  await server?.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
