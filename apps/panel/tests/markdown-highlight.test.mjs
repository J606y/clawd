import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/hermes-workspace', 'md-highlight-test-'));
const fixture = {
  name: 'md-highlight-fixture',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url !== '/md-highlight-fixture') return next();
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><html><meta charset="utf-8"><body><script type="module">
        import * as md from '/src/lib/markdown.ts';
        window.md = md;
        window.ready = true;
      </script></body></html>`);
    });
  }
};

const server = await createServer({
  configFile: false,
  cacheDir: path.join(owned, 'vite'),
  plugins: [fixture],
  logLevel: 'error',
  server: {
    host: '127.0.0.1',
    port: 0,
    hmr: false,
    watch: null
  }
});

let browser, socket;
let sequence = 0;
const pending = new Map();

try {
  await server.listen();
  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--remote-debugging-port=0',
    `--user-data-dir=${path.join(owned, 'profile')}`,
    'about:blank'
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  const ws = await new Promise((resolve, reject) => {
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
  });

  socket = new WebSocket(ws);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  socket.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    const p = pending.get(m.id);
    if (p) {
      pending.delete(m.id);
      clearTimeout(p.timer);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  });

  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(method + ' timeout'));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });

  const disallowedRequests = [];
  const allowedOrigin = `http://127.0.0.1:${server.httpServer.address().port}`;

  await call('Fetch.enable', {
    patterns: [{ urlPattern: '*' }]
  }, sessionId);

  socket.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    if (m.method === 'Fetch.requestPaused' && m.params) {
      const { requestId, request } = m.params;
      const reqUrl = request.url;
      if (reqUrl.startsWith(allowedOrigin) || reqUrl === 'about:blank') {
        call('Fetch.continueRequest', { requestId }, sessionId).catch(() => {});
      } else {
        disallowedRequests.push(reqUrl);
        call('Fetch.failRequest', { requestId, errorReason: 'AccessDenied' }, sessionId).catch(() => {});
      }
    }
  });

  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  await call('Page.navigate', { url: `http://127.0.0.1:${server.httpServer.address().port}/md-highlight-fixture` }, sessionId);
  for (let n = 0; n < 100; n++) {
    if (await evaluate('window.ready')) break;
    await new Promise(r => setTimeout(r, 50));
  }

  console.log('1. Verify unclosed code block throttling (>=150ms per complete line)');
  const throttleCheck = await evaluate(`(() => {
    const state = window.md.createMarkdownStreamState();

    // Step 1: Initial partial line at t=1000
    // "const a = " has NO newline, complete='' -> innerHtml is escaped plain text
    const src1 = '\`\`\`typescript\\nconst a = ';
    const b1 = window.md.renderMarkdownBlocks(src1, [], state, 1000);
    const h1 = b1[0].html;
    const hasSpan1 = h1.includes('<span class=\"hljs-');

    // Step 2: Complete the line at t=1050 (< 150ms since t=1000)
    // Even though complete line exists now, clock delta is only 50ms (< 150ms)
    const src2 = '\`\`\`typescript\\nconst a = 1;\\n';
    const b2 = window.md.renderMarkdownBlocks(src2, b1, state, 1050);
    const h2 = b2[0].html;
    const hasSpan2 = h2.includes('<span class=\"hljs-');

    // Step 3: Advance clock to t=1200 (>= 150ms since t=1000, 200ms elapsed)
    // New complete line triggers hljs highlight!
    const src3 = '\`\`\`typescript\\nconst a = 1;\\nconst b = ';
    const b3 = window.md.renderMarkdownBlocks(src3, b2, state, 1200);
    const h3 = b3[0].html;
    const hasSpan3 = h3.includes('<span class=\"hljs-');

    // Step 4: Verify uncompleted line is escaped plain text
    const rawRestEscaped = h3.includes('const b = ');

    // Step 5: Advance clock and close code fence
    const src4 = '\`\`\`typescript\\nconst a = 1;\\nconst b = 2;\\n\`\`\`';
    const b4 = window.md.renderMarkdownBlocks(src4, b3, state, 1400);
    const h4 = b4[0].html;
    // Closed code fence has full highlight for all lines
    const h4HasSpanB = h4.includes('hljs-number\">2</span>') || h4.includes('2');

    // Step 6: Full lexer (no state) matches final render exactly
    const fullBlocks = window.md.renderMarkdownBlocks(src4, []);
    const fullMatchesClosed = fullBlocks[0].html === b4[0].html;

    console.log("h1 in zeroCheck:", h1); return {
      hasSpan1,
      hasSpan2,
      hasSpan3,
      rawRestEscaped,
      fullMatchesClosed
    };
  })()`);

  assert.equal(throttleCheck.hasSpan1, false, 'Initial incomplete line must not highlight');
  assert.equal(throttleCheck.hasSpan2, false, 'Delta under 150ms must throttle and not trigger highlight');
  assert.equal(throttleCheck.hasSpan3, true, 'Delta >= 150ms must highlight completed lines');
  assert.equal(throttleCheck.rawRestEscaped, true, 'Incomplete tail line must be rendered as escaped text');
  assert.equal(throttleCheck.fullMatchesClosed, true, 'Closed fence must produce identical full highlight');
  console.log('PASS unclosed code block throttling verification');

  console.log('2. Verify unregistered / missing language does not throttle and escapes safely');
  const unregisteredCheck = await evaluate(`(() => {
    const state = window.md.createMarkdownStreamState();
    const src = '\`\`\`unknownlang\\n<script>alert(1)</script>\\n';
    const b = window.md.renderMarkdownBlocks(src, [], state, 1000);
    const html = b[0].html;
    return {
      safe: !html.includes('<script>'),
      escaped: html.includes('&lt;script&gt;'),
      hasCodeWrap: html.includes('<pre><code class=\"hljs\">')
    };
  })()`);
  assert.deepEqual(unregisteredCheck, { safe: true, escaped: true, hasCodeWrap: true });
  console.log('PASS unregistered language handling');

  console.log('3. Verify full renderMarkdown output and codeBlockHTML consistency');
  const consistencyCheck = await evaluate(`(() => {
    const src = '\`\`\`python\\nx = 10\\n\`\`\`';
    const rendered = window.md.renderMarkdown(src, false);
    const blocks = window.md.renderMarkdownBlocks(src, []);
    return {
      matches: rendered.trim() === blocks[0].html.trim()
    };
  })()`);
  assert.equal(consistencyCheck.matches, true, 'Full renderMarkdown and renderMarkdownBlocks must share identical codeBlockHTML structure');
  console.log('PASS code block structure consistency');

  const replacement = await evaluate(`(() => {
    const state = window.md.createMarkdownStreamState();
    let blocks = window.md.renderMarkdownBlocks('~~~typescript\\nconst oldValue = 123;\\noldTail', [], state, 1000);
    const replacement = '~~~typescript\\nlet fresh';
    blocks = window.md.renderMarkdownBlocks(replacement, blocks, state, 1050);
    const container = document.createElement('div');
    container.innerHTML = blocks.map(b => b.html).join('');
    return container.querySelector('code').textContent;
  })()`);
  assert.equal(replacement, 'let fresh', 'Non-prefix replacement must never render stale highlighted code');

  console.log('4. Verify clock at=0 is not treated as uninitialized sentinel');
  const zeroClockCheck = await evaluate(`(() => {
    const state = window.md.createMarkdownStreamState();
    // at clock=0, complete line arrives
    const src1 = '\`\`\`typescript\\nconst x = 1;\\nconst y = ';
    const b1 = window.md.renderMarkdownBlocks(src1, [], state, 0);
    const h1 = b1[0].html;
    const hasSpan1 = h1.includes('<span class=\"hljs-');
    const atAfterFirst = state.codeThrottle?.at;

    // at clock=50ms (< 150ms), a new complete line arrives
    const src2 = '\`\`\`typescript\\nconst x = 1;\\nconst y = 2;\\nconst z = ';
    const b2 = window.md.renderMarkdownBlocks(src2, b1, state, 50);
    const h2 = b2[0].html;
    // Because clock delta is only 50ms, const y = 2; must NOT be highlighted yet
    const hasSpanY = h2.includes('hljs-number\">2</span>') || h2.includes('hljs-params\">y</span>');
    const throttledY = h2.includes('const y = 2;\\n');

    // at clock=200ms (>= 150ms from at=0), now it should highlight
    const src3 = src2 + '3;\\n';
    const b3 = window.md.renderMarkdownBlocks(src3, b2, state, 200);
    const h3 = b3[0].html;
    const hasSpanYAfter200 = h3.includes('hljs-number\">2</span>') || h3.includes('hljs-params\">y</span>') || h3.includes('2');

    return {
      hasSpan1,
      atAfterFirst,
      hasSpanY,
      throttledY,
      hasSpanYAfter200
    };
  })()`);
  assert.equal(zeroClockCheck.hasSpan1, true, 'First complete line at clock=0 must highlight');
  assert.equal(zeroClockCheck.atAfterFirst, 0, 'state.codeThrottle.at must record 0, not be falsy sentinel');
  assert.equal(zeroClockCheck.hasSpanY, false, 'Delta 50ms from clock=0 must throttle');
  assert.equal(zeroClockCheck.throttledY, true, 'Second line must remain unhighlighted plain text at 50ms');
  assert.equal(zeroClockCheck.hasSpanYAfter200, true, 'Delta 200ms must highlight second complete line');
  console.log('PASS clock at=0 sentinel verification');

  console.log('5. Verify state reset boundaries (fence shortening, lang switch, HTML fallback insert, fence length mismatch)');
  const boundaryCheck = await evaluate(`(() => {
    // 5.1 Same language, different fence delimiters
    const state1 = window.md.createMarkdownStreamState();
    let b1 = window.md.renderMarkdownBlocks('~~~typescript\\nconst a = 1;\\n', [], state1, 1000);
    let b1_switch = window.md.renderMarkdownBlocks('\`\`\`typescript\\nlet b = 2;\\n', b1, state1, 1050);
    const text1 = b1_switch[0].html;

    // 5.2 Different fence length (4 backticks cannot be closed by 3 backticks)
    const state2 = window.md.createMarkdownStreamState();
    let b2_4ticks = window.md.renderMarkdownBlocks('\`\`\`\`typescript\\nconst a = 1;\\n\`\`\`\\n', [], state2, 1000);
    const isUnclosed4 = b2_4ticks[0].html.includes('<div class="code">');
    // And when 4 backticks close it
    let b2_closed = window.md.renderMarkdownBlocks('\`\`\`\`typescript\\nconst a = 1;\\n\`\`\`\`', b2_4ticks, state2, 1200);
    const closedHtml = b2_closed[0].html;
    // Closing should remove codeThrottle from state
    const throttleAfterClose = state2.codeThrottle;

    // 5.3 Unknown language switch
    const state3 = window.md.createMarkdownStreamState();
    let b3_ts = window.md.renderMarkdownBlocks('\`\`\`typescript\\nconst val = 1;\\n', [], state3, 1000);
    let b3_unknown = window.md.renderMarkdownBlocks('\`\`\`unregistered\\nsome raw text\\n', b3_ts, state3, 1050);
    const unknownThrottle = state3.codeThrottle;

    // 5.4 HTML fallback inserted then new code fence
    const state4 = window.md.createMarkdownStreamState();
    let b4_init = window.md.renderMarkdownBlocks('Paragraph 1\\n\\n\`\`\`typescript\\nconst x = 10;\\n', [], state4, 1000);
    let b4_html = window.md.renderMarkdownBlocks('Paragraph 1\\n\\n<div>raw html</div>\\n\\n\`\`\`typescript\\nlet y = 20;\\n', b4_init, state4, 1050);
    const htmlDiv = document.createElement('div');
    htmlDiv.innerHTML = b4_html.map(b => b.html).join('');
    const codeTexts = Array.from(htmlDiv.querySelectorAll('code')).map(c => c.textContent.trim());

    // 5.5 Fence shortening: from longer text to shorter text (e.g. backspace / edit)
    const state5 = window.md.createMarkdownStreamState();
    let b5 = window.md.renderMarkdownBlocks('\`\`\`typescript\\nconst longLongVariableName = 123;\\n', [], state5, 1000);
    let b5_short = window.md.renderMarkdownBlocks('\`\`\`typescript\\nconst v = 1;\\n', b5, state5, 1050);
    const shortDiv = document.createElement('div');
    shortDiv.innerHTML = b5_short[0].html;
    const shortCodeText = shortDiv.querySelector('code').textContent.trim();

    return {
      b1_switch_has_old: text1.includes('const a = 1'),
      b1_switch_has_new: text1.includes('let b = 2'),
      isUnclosed4,
      throttleAfterClose: throttleAfterClose === undefined,
      unknownThrottle: unknownThrottle === undefined,
      codeTexts,
      shortCodeText
    };
  })()`);
  assert.equal(boundaryCheck.b1_switch_has_old, false, 'Fence delimiter switch must clear old state');
  assert.equal(boundaryCheck.b1_switch_has_new, true, 'Fence delimiter switch must render new code');
  assert.equal(boundaryCheck.isUnclosed4, true, '4 backticks with 3 backticks inner line is still unclosed');
  assert.equal(boundaryCheck.throttleAfterClose, true, 'Closing fence must clear codeThrottle');
  assert.equal(boundaryCheck.unknownThrottle, true, 'Switching to unknown language must clear codeThrottle');
  assert.deepEqual(boundaryCheck.codeTexts, ['let y = 20;'], 'HTML insertion then new code block must not leak previous code block');
  assert.equal(boundaryCheck.shortCodeText, 'const v = 1;', 'Fence shortening must not leak previous longer code text');
  console.log('PASS state reset boundaries verification');

  const yaml = await evaluate(`window.md.highlightSafe('note: "<img src=x onerror=alert(1)><script>alert(1)</script>"', 'yaml')`);
  assert.equal(/<script/i.test(yaml), false);
  assert.equal(/<img/i.test(yaml), false);
  assert.match(yaml, /note/);

  assert.equal(disallowedRequests.length, 0, `Forbidden external requests detected: ${JSON.stringify(disallowedRequests)}`);

} finally {
  for (const [, p] of pending) {
    clearTimeout(p.timer);
    p.reject(new Error('Teardown cleanup'));
  }
  pending.clear();
  if (socket) socket.close();
  if (browser) {
    if (browser.exitCode === null && !browser.killed) {
      browser.kill('SIGKILL');
      await Promise.race([
        new Promise(r => browser.once('exit', r)),
        new Promise(r => setTimeout(r, 2000))
      ]);
    }
  }
  await server.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
