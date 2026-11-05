import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { createServer as createHttpServer } from 'node:http';
import { existsSync, writeSync } from 'node:fs';
import { cp, mkdtemp, mkdir, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { build as viteBuild, createLogger, createServer } from 'vite';
import { calculateLagMetrics } from './stream-perf-watermark.mjs';

const probeDir = import.meta.dirname;
const scratch = process.env.TMPDIR || '/root/.hermes/cache/scratch';
const browserLockPath = '/root/hermes-workspace/fullgate-evidence/browser-validation.lock';
const productionEvidenceFallback = '/root/hermes-workspace/fullgate-evidence/perf-production-a';
const DEV_RUNTIME_MARKERS = ['jsxDEV', 'jsx-dev-runtime', 'validatePropertiesInDevelopment'];
const THRESHOLDS = {
  frameP95Ms: 16.7,
  longTasks: 0,
  scriptPerFrameMs: 4.0,
  lagP95Ms: 300.0,
  settleMaxMs: 50.0,
  budgetMs: 120000,
};
const SCENARIOS = {
  A: {
    name: 'Scenario_A_ShortHistory_2K',
    config: { sessionId: 'perf-a', historyTurns: 4, streamTargetChars: 2000, includeTools: false, includeCode: false },
  },
  B: {
    name: 'Scenario_B_200Turns_20K_300LineCode',
    config: { sessionId: 'perf-b', historyTurns: 200, streamTargetChars: 20000, includeTools: true, includeCode: true },
  },
};

function parseArgs(argv) {
  return new Map(argv.map((arg) => {
    const eq = arg.indexOf('=');
    return eq === -1 ? [arg, ''] : [arg.slice(0, eq), arg.slice(eq + 1)];
  }));
}

function selectedScenarios(args) {
  if (!args.has('--scenario')) return ['A', 'B'];
  const value = args.get('--scenario');
  if (value === 'A' || value === 'B') return [value];
  const shown = value === '' ? '(empty)' : String(value);
  throw new Error(`invalid --scenario=${shown}; expected A or B`);
}

function selectedBuild(args) {
  if (!args.has('--build')) return 'dev';
  const value = args.get('--build');
  if (value === 'dev' || value === 'production') return value;
  const shown = value === '' ? '(empty)' : String(value);
  throw new Error(`invalid --build=${shown}; expected dev or production`);
}

function productionRefused(scenarios, buildMode) {
  return buildMode === 'production' && !(scenarios.length === 1 && scenarios[0] === 'A');
}

function percentile(values, ratio) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * ratio))];
}

function round(value) {
  return value == null ? null : Math.round(value * 100) / 100;
}

function summarizeProfile(profileResult) {
  const wrapped = profileResult?.profile;
  const profile = wrapped && (Array.isArray(wrapped.nodes) || Array.isArray(wrapped.samples)) ? wrapped : profileResult;
  const nodes = profile?.nodes ?? [];
  const samples = profile?.samples ?? [];
  const timeDeltas = profile?.timeDeltas ?? [];
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const selfUs = { program: 0, idle: 0, gc: 0, root: 0, named: 0, anonymous: 0 };
  const frames = new Map();
  let scriptUs = 0;
  for (let index = 0; index < samples.length; index++) {
    const node = nodeMap.get(samples[index]);
    const name = node?.callFrame?.functionName || '(anonymous)';
    const dt = timeDeltas[index] || 0;
    // Oracle: exclude only (idle), (root), (garbage collector). (program) stays in totalScriptMs.
    if (name !== '(idle)' && name !== '(root)' && name !== '(garbage collector)') scriptUs += dt;
    let bucket = 'named';
    if (name === '(program)') bucket = 'program';
    else if (name === '(idle)') bucket = 'idle';
    else if (name === '(garbage collector)') bucket = 'gc';
    else if (name === '(root)') bucket = 'root';
    else if (name === '(anonymous)') bucket = 'anonymous';
    selfUs[bucket] += dt;
    if (bucket !== 'named') continue;
    const url = node?.callFrame?.url || '';
    const lineNumber = Number.isInteger(node?.callFrame?.lineNumber) ? node.callFrame.lineNumber : null;
    const key = `${name}\0${url}\0${lineNumber}`;
    let row = frames.get(key);
    if (!row) {
      row = { functionName: name, url, lineNumber, selfUs: 0 };
      frames.set(key, row);
    }
    row.selfUs += dt;
  }
  const sampled = selfUs.program + selfUs.idle + selfUs.gc + selfUs.root + selfUs.named + selfUs.anonymous;
  const top15 = [...frames.values()]
    .sort((a, b) => b.selfUs - a.selfUs || a.functionName.localeCompare(b.functionName) || a.url.localeCompare(b.url) || ((a.lineNumber ?? -1) - (b.lineNumber ?? -1)))
    .slice(0, 15)
    .map((row) => ({
      functionName: row.functionName,
      url: row.url,
      lineNumber: row.lineNumber,
      selfMs: round(row.selfUs / 1000),
      selfUs: row.selfUs,
    }));
  return {
    scriptUs,
    selfUs: { ...selfUs, sampled, script: scriptUs },
    top15,
    counts: { nodes: nodes.length, samples: samples.length },
    reconciled: sampled === selfUs.program + selfUs.idle + selfUs.gc + selfUs.root + selfUs.named + selfUs.anonymous
      && scriptUs === selfUs.program + selfUs.named + selfUs.anonymous,
  };
}

function avgScriptPerFrameMs(totalScriptMs, frameCount) {
  return round(frameCount ? totalScriptMs / frameCount : 0);
}

function profileSiblingPath(reportPath, scenarioKey) {
  const dir = path.dirname(reportPath);
  const base = path.basename(reportPath, path.extname(reportPath));
  return path.join(dir, `${base}.${scenarioKey}.cpuprofile.json`);
}

async function persistRawProfile(reportPath, scenarioKey, profile) {
  if (!reportPath) return null;
  if (scenarioKey !== 'A' && scenarioKey !== 'B') throw new Error(`invalid profile scenario ${scenarioKey}`);
  const dest = profileSiblingPath(reportPath, scenarioKey);
  await mkdir(path.dirname(dest), { recursive: true });
  await writeFile(dest, JSON.stringify(profile));
  return dest;
}

function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex');
}

function devRuntimeHits(text) {
  return DEV_RUNTIME_MARKERS.filter((marker) => text.includes(marker));
}

function assertWritableTarget(target, banned) {
  const resolved = path.resolve(target);
  for (const prefix of banned) {
    const root = path.resolve(prefix);
    if (resolved === root || resolved.startsWith(root + path.sep)) {
      throw new Error(`refusing to write ${resolved}`);
    }
  }
}

function acquireBrowserLock() {
  return new Promise((resolve, reject) => {
    const holder = spawn('flock', [browserLockPath, 'python3', '-c', 'print("READY", flush=True); import sys; sys.stdin.read()'], {
      stdio: ['pipe', 'pipe', 'inherit'],
    });
    let settled = false;
    let buf = '';
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      holder.kill('SIGTERM');
      reject(new Error('browser-validation.lock acquire timeout'));
    }, 480000);
    holder.stdout.on('data', (chunk) => {
      buf += chunk.toString();
      if (settled || !buf.includes('READY')) return;
      settled = true;
      clearTimeout(timer);
      resolve(() => new Promise((done) => {
        holder.once('exit', () => done());
        holder.stdin.end();
        setTimeout(() => { if (holder.exitCode === null) holder.kill('SIGTERM'); }, 1000);
      }));
    });
    holder.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    holder.once('exit', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`flock exited ${code} before acquire`));
    });
  });
}

async function hashKeyFiles(candidateRoot) {
  const relative = [
    'package.json',
    'vite.config.ts',
    'src/react/pages/chat/chat-page.tsx',
    'src/react/pages/chat/message-list.tsx',
    'src/react/pages/chat/reveal.ts',
  ];
  const rows = [];
  for (const rel of relative) {
    const file = path.join(candidateRoot, rel);
    if (!existsSync(file)) {
      rows.push({ path: rel, missing: true });
      continue;
    }
    const body = await readFile(file);
    rows.push({ path: rel, bytes: body.length, sha256: sha256(body) });
  }
  return rows;
}

async function scanBuiltAssets(dir) {
  const rows = [];
  async function walk(current) {
    const entries = await readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        await walk(full);
        continue;
      }
      if (!/\.(js|mjs|css|html)$/.test(entry.name)) continue;
      const body = await readFile(full);
      const text = body.toString('utf8');
      rows.push({
        path: path.relative(dir, full),
        bytes: body.length,
        sha256: sha256(body),
        devRuntimeMarkers: devRuntimeHits(text),
      });
    }
  }
  await walk(dir);
  return rows;
}

function startStaticServer(dir) {
  const rootDir = path.resolve(dir);
  const types = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.map': 'application/json',
    '.ico': 'image/x-icon',
  };
  const httpServer = createHttpServer((request, response) => {
    const url = new URL(request.url || '/', 'http://127.0.0.1');
    let pathname = decodeURIComponent(url.pathname);
    if (pathname.endsWith('/')) pathname += 'index.html';
    const file = path.normalize(path.join(rootDir, pathname));
    if (file !== rootDir && !file.startsWith(rootDir + path.sep)) {
      response.writeHead(403);
      response.end('forbidden');
      return;
    }
    readFile(file).then((body) => {
      const ext = path.extname(file).toLowerCase();
      response.writeHead(200, {
        'Content-Type': types[ext] || 'application/octet-stream',
        'Cache-Control': 'no-store',
      });
      response.end(body);
    }).catch(() => {
      response.writeHead(404);
      response.end('not found');
    });
  });
  return new Promise((resolve, reject) => {
    httpServer.once('error', reject);
    httpServer.listen(0, '127.0.0.1', () => {
      resolve({
        httpServer,
        close: () => new Promise((done) => httpServer.close(() => done())),
      });
    });
  });
}

async function buildProductionFixture({ candidateRoot, owned, fixtureHtml, evidenceDir, reportPath }) {
  const banned = [
    '/usr/local/lib/hermes-agent',
    '/root/hermes-workspace/hermes-panel',
    '/var/www/hermes-panel',
    candidateRoot,
  ];
  assertWritableTarget(owned, banned);
  assertWritableTarget(evidenceDir, banned);
  await mkdir(evidenceDir, { recursive: true });
  const entryDir = path.join(owned, 'prod-entry');
  const outDir = path.join(owned, 'prod-dist');
  const nodeModulesLink = path.join(entryDir, 'node_modules');
  await mkdir(entryDir, { recursive: true });
  await mkdir(outDir, { recursive: true });
  const html = await readFile(fixtureHtml, 'utf8');
  const entryHtml = html.replace('src="/tests/stream-perf-react-fixture.tsx"', 'src="./stream-perf-react-fixture.tsx"');
  if (entryHtml === html || !entryHtml.includes('src="./stream-perf-react-fixture.tsx"')) {
    throw new Error('fixture html entry was not rewritten for vite build');
  }
  await writeFile(path.join(entryDir, 'index.html'), entryHtml);
  await symlink(fixtureTsxPath(), path.join(entryDir, 'stream-perf-react-fixture.tsx'));
  await symlink(path.join(candidateRoot, 'node_modules'), nodeModulesLink);
  const configPath = path.join(candidateRoot, 'vite.config.ts');
  const configReal = await realpath(configPath);
  const rootReal = await realpath(candidateRoot);
  if (configReal !== path.join(rootReal, 'vite.config.ts')) {
    throw new Error(`vite config resolved outside candidate root: ${configReal}`);
  }
  const aliasRoot = path.join(rootReal, 'src', 'react');
  if (!existsSync(path.join(aliasRoot, 'pages', 'chat', 'chat-page.tsx'))) {
    throw new Error(`candidate alias target missing: ${aliasRoot}`);
  }
  const lines = [];
  const record = (level, msg) => {
    const text = typeof msg === 'string' ? msg : (msg && msg.message) || String(msg);
    lines.push(`[${level}] ${text}`);
    process.stderr.write(`[vite:${level}] ${text}\n`);
  };
  const logger = createLogger('info', { allowClearScreen: false });
  for (const level of ['info', 'warn', 'warnOnce', 'error']) {
    logger[level] = (msg) => record(level, msg);
  }
  const proof = { resolved: null, aliasSamples: [] };
  const previousNodeEnv = process.env.NODE_ENV;
  process.env.NODE_ENV = 'production';
  const logPath = path.join(evidenceDir, 'production-build.log');
  try {
    await viteBuild({
      configFile: configReal,
      root: entryDir,
      mode: 'production',
      cacheDir: path.join(owned, 'vite-prod'),
      publicDir: existsSync(path.join(rootReal, 'public')) ? path.join(rootReal, 'public') : false,
      logLevel: 'info',
      customLogger: logger,
      define: {
        'process.env.NODE_ENV': JSON.stringify('production'),
      },
      build: {
        outDir,
        emptyOutDir: true,
        sourcemap: false,
      },
      server: {
        fs: {
          allow: [rootReal, probeDir, owned, entryDir],
        },
      },
      plugins: [{
        name: 'candidate-root-production-proof',
        enforce: 'pre',
        configResolved(config) {
          proof.resolved = {
            command: config.command,
            mode: config.mode,
            isProduction: config.isProduction,
            root: config.root,
            outDir: config.build.outDir,
          };
          record('probe', `command=${config.command} mode=${config.mode} isProduction=${config.isProduction} outDir=${config.build.outDir}`);
          if (config.command !== 'build' || config.mode !== 'production' || config.isProduction !== true) {
            throw new Error(`refusing mode-string-only bundle command=${config.command} mode=${config.mode} isProduction=${config.isProduction}`);
          }
          if (path.resolve(config.build.outDir) !== path.resolve(outDir)) {
            throw new Error(`build outDir escaped task temp: ${config.build.outDir}`);
          }
        },
        async resolveId(id, importer, options) {
          if (id !== '@' && !id.startsWith('@/')) return null;
          const rest = id === '@' ? '' : id.slice(2);
          const target = path.join(aliasRoot, rest);
          if (proof.aliasSamples.length < 8) proof.aliasSamples.push({ id, target });
          const resolved = await this.resolve(target, importer, { skipSelf: true, ...options });
          if (resolved) return resolved;
          if (existsSync(target)) return target;
          for (const ext of ['.tsx', '.ts', '.js', '.mjs', '.css', '.json']) {
            if (existsSync(target + ext)) return target + ext;
          }
          return null;
        },
      }],
    });
  } finally {
    if (previousNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousNodeEnv;
    await writeFile(logPath, `${lines.join('\n')}\n`).catch(() => undefined);
  }
  const assets = await scanBuiltAssets(outDir);
  const evidenceDist = path.join(evidenceDir, 'dist');
  await rm(evidenceDist, { recursive: true, force: true });
  await cp(outDir, evidenceDist, { recursive: true, dereference: true });
  const scanPath = path.join(evidenceDir, 'asset-scan.json');
  const hitRows = assets.filter((row) => row.devRuntimeMarkers.length > 0);
  await writeFile(scanPath, JSON.stringify({
    outDir: evidenceDist,
    jsxDevRuntimePresent: hitRows.length > 0,
    markers: DEV_RUNTIME_MARKERS,
    assets,
  }, null, 2));
  if (!assets.some((row) => row.path.endsWith('.js'))) {
    throw new Error(`production vite build produced no js assets; log ${logPath}`);
  }
  if (hitRows.length > 0) {
    const failure = {
      root: candidateRoot,
      legacyGate: false,
      passed: false,
      failures: ['production artifact contains jsxDEV development runtime'],
      build: {
        requested: 'production',
        method: 'vite-build',
        modeStringOnly: false,
        realProductionBundle: false,
        jsxDevRuntimePresent: true,
        assetScanPath: scanPath,
        buildLogPath: logPath,
        evidenceDist,
      },
    };
    if (reportPath) await writeFile(reportPath, JSON.stringify(failure, null, 2));
    throw new Error(`production artifact contains development runtime markers; see ${scanPath}`);
  }
  const htmlFiles = assets.filter((row) => row.path.endsWith('.html')).map((row) => row.path);
  const htmlRel = htmlFiles.includes('index.html') ? 'index.html' : htmlFiles[0];
  if (!htmlRel) throw new Error('production vite build produced no html');
  return {
    outDir,
    nodeModulesLink,
    urlPath: `/${htmlRel.split(path.sep).join('/')}`,
    evidenceDist,
    scanPath,
    logPath,
    assets,
    proof,
    configReal,
    aliasRoot,
    keyHashes: await hashKeyFiles(rootReal),
  };
}

function fixtureTsxPath() {
  return path.join(probeDir, 'stream-perf-react-fixture.tsx');
}

function buildReport({ candidateRoot, scenarios, results, buildInfo = null }) {
  const report = {
    root: candidateRoot,
    cpuThrottlingRate: 4,
    thresholds: THRESHOLDS,
    scenariosRequested: [...scenarios],
    scenarioA: results.A ? results.A.metrics : null,
    scenarioB: results.B ? results.B.metrics : null,
    profileFiles: scenarios.map((key) => ({
      scenario: key,
      path: results[key]?.metrics?.scriptTimes?.profilePath ?? null,
    })),
    failures: scenarios.flatMap((key) => results[key].failures),
    passed: scenarios.every((key) => results[key].failures.length === 0),
  };
  if (buildInfo) {
    report.build = buildInfo;
    report.legacyGate = false;
  }
  return report;
}

async function runSelfCheck() {
  const errors = [];
  const expect = (name, cond, detail = '') => {
    if (!cond) errors.push(detail ? `${name}: ${detail}` : name);
  };
  try {
    const omitted = selectedScenarios(parseArgs([]));
    expect('default is A+B', omitted.length === 2 && omitted[0] === 'A' && omitted[1] === 'B', JSON.stringify(omitted));
    expect('scenario A', JSON.stringify(selectedScenarios(parseArgs(['--scenario=A']))) === '["A"]');
    expect('scenario B', JSON.stringify(selectedScenarios(parseArgs(['--scenario=B']))) === '["B"]');
    expect('other flags do not narrow', JSON.stringify(selectedScenarios(parseArgs(['--root=/tmp/x', '--report=/tmp/r.json']))) === '["A","B"]');
    expect('build flag does not narrow scenarios', JSON.stringify(selectedScenarios(parseArgs(['--build=production']))) === '["A","B"]');
    expect('default build is dev', selectedBuild(parseArgs([])) === 'dev');
    expect('explicit build dev', selectedBuild(parseArgs(['--build=dev'])) === 'dev');
    expect('explicit build production', selectedBuild(parseArgs(['--build=production'])) === 'production');
    expect('production refuses default AB', productionRefused(['A', 'B'], 'production') === true);
    expect('production allows explicit A', productionRefused(['A'], 'production') === false);
    expect('dev allows AB', productionRefused(['A', 'B'], 'dev') === false);
    expect('dev runtime detector', devRuntimeHits('exports.jsxDEV from jsx-dev-runtime').includes('jsxDEV') && devRuntimeHits('function jsx(){return 1}').length === 0);
    for (const bad of ['', 'a', 'A+B', 'AB', 'both', 'A,B', 'nope']) {
      let threw = false;
      try { selectedScenarios(parseArgs([`--scenario=${bad}`])); } catch (error) {
        threw = error instanceof Error && error.message.startsWith('invalid --scenario=');
      }
      expect(`reject ${bad || '(empty)'}`, threw);
    }
    for (const bad of ['', 'prod', 'Production', 'development']) {
      let threw = false;
      try { selectedBuild(parseArgs([`--build=${bad}`])); } catch (error) {
        threw = error instanceof Error && error.message.startsWith('invalid --build=');
      }
      expect(`reject build ${bad || '(empty)'}`, threw);
    }
    let bare = false;
    try { selectedScenarios(parseArgs(['--scenario'])); } catch (error) {
      bare = error instanceof Error && error.message.includes('invalid --scenario=(empty)');
    }
    expect('reject bare --scenario', bare);

    expect('thresholds unchanged', THRESHOLDS.frameP95Ms === 16.7 && THRESHOLDS.longTasks === 0 && THRESHOLDS.scriptPerFrameMs === 4 && THRESHOLDS.lagP95Ms === 300 && THRESHOLDS.settleMaxMs === 50 && THRESHOLDS.budgetMs === 120000);

    const nodes = [
      { id: 1, callFrame: { functionName: '(root)', url: '', lineNumber: 0 } },
      { id: 2, callFrame: { functionName: '(idle)', url: '', lineNumber: 0 } },
      { id: 3, callFrame: { functionName: '(program)', url: '', lineNumber: 0 } },
      { id: 4, callFrame: { functionName: '(garbage collector)', url: '', lineNumber: 0 } },
      { id: 5, callFrame: { functionName: 'noteVisible', url: 'stream-perf-react-fixture.tsx', lineNumber: 226 } },
      { id: 6, callFrame: { functionName: 'bodyText', url: 'stream-perf-react-fixture.tsx', lineNumber: 185 } },
      { id: 7, callFrame: { functionName: 'bodyText', url: 'stream-perf-react-fixture.tsx', lineNumber: 186 } },
      { id: 8, callFrame: { functionName: '', url: 'anon.js', lineNumber: 4 } },
    ];
    const samples = [1, 2, 3, 3, 4, 5, 5, 6, 7, 8, 99];
    const timeDeltas = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 9000, 10000];
    for (let i = 0; i < 16; i++) {
      nodes.push({ id: 10 + i, callFrame: { functionName: `fn${String(i).padStart(2, '0')}`, url: 'hot.js', lineNumber: i } });
      samples.push(10 + i);
      timeDeltas.push((i + 1) * 100);
    }
    const summary = summarizeProfile({ profile: { nodes, samples, timeDeltas } });
    expect('reconciled', summary.reconciled === true, JSON.stringify(summary.selfUs));
    expect('program included', summary.selfUs.program === 7000 && summary.scriptUs === 69600, JSON.stringify(summary.selfUs));
    expect('buckets', summary.selfUs.idle === 2000 && summary.selfUs.gc === 5000 && summary.selfUs.root === 1000 && summary.selfUs.named === 43600 && summary.selfUs.anonymous === 19000 && summary.selfUs.sampled === 77600 && summary.selfUs.script === 69600, JSON.stringify(summary.selfUs));
    expect('script parts', summary.scriptUs === summary.selfUs.program + summary.selfUs.named + summary.selfUs.anonymous);
    expect('top15 length', summary.top15.length === 15, String(summary.top15.length));
    expect('top15 fields', summary.top15.every((row) => typeof row.functionName === 'string' && typeof row.url === 'string' && (row.lineNumber === null || Number.isInteger(row.lineNumber)) && typeof row.selfMs === 'number'));
    expect('top1 noteVisible', summary.top15[0].functionName === 'noteVisible' && summary.top15[0].url === 'stream-perf-react-fixture.tsx' && summary.top15[0].lineNumber === 226 && summary.top15[0].selfMs === 13 && summary.top15[0].selfUs === 13000);
    expect('bodyText lines split', summary.top15[1].functionName === 'bodyText' && summary.top15[1].lineNumber === 186 && summary.top15[1].selfMs === 9 && summary.top15[2].functionName === 'bodyText' && summary.top15[2].lineNumber === 185 && summary.top15[2].selfMs === 8);
    const names = summary.top15.map((row) => row.functionName);
    expect('specials excluded from top15', !names.includes('(program)') && !names.includes('(idle)') && !names.includes('(garbage collector)') && !names.includes('(root)') && !names.includes('(anonymous)') && !names.includes('fn00') && !names.includes('fn03'));
    expect('same denominator', avgScriptPerFrameMs(summary.scriptUs / 1000, 10) === 6.96 && avgScriptPerFrameMs(summary.scriptUs / 1000, 0) === 0, String(avgScriptPerFrameMs(summary.scriptUs / 1000, 10)));
    const missingDelta = summarizeProfile({ profile: { nodes: [{ id: 1, callFrame: { functionName: 'only', url: 'u', lineNumber: 2 } }], samples: [1, 1], timeDeltas: [5000] } });
    expect('missing delta is zero', missingDelta.scriptUs === 5000 && missingDelta.selfUs.named === 5000 && missingDelta.reconciled === true);

    const both = buildReport({
      candidateRoot: '/synthetic',
      scenarios: ['A', 'B'],
      results: {
        A: { metrics: { name: 'A', scriptTimes: { profilePath: '/tmp/a.json' } }, failures: [] },
        B: { metrics: { name: 'B', scriptTimes: { profilePath: '/tmp/b.json' } }, failures: ['lag'] },
      },
    });
    expect('default report keeps B', both.scenariosRequested.join(',') === 'A,B' && both.scenarioA.name === 'A' && both.scenarioB.name === 'B' && both.passed === false && both.failures.length === 1 && both.thresholds === THRESHOLDS && both.build === undefined && both.legacyGate === undefined);
    const onlyA = buildReport({
      candidateRoot: '/synthetic',
      scenarios: ['A'],
      results: { A: { metrics: { name: 'A', scriptTimes: { profilePath: null } }, failures: [] } },
    });
    expect('A alone does not invent B', onlyA.scenarioB === null && onlyA.passed === true && onlyA.profileFiles.length === 1);

    expect('empty report path skips file', await persistRawProfile('', 'A', { profile: { samples: [] } }) === null);
    const ownedCheck = await mkdtemp(path.join(scratch, 'stream-perf-self-check-'));
    try {
      const report = path.join(ownedCheck, 'scenario-a-split.json');
      const raw = { profile: { nodes, samples, timeDeltas } };
      const dest = await persistRawProfile(report, 'A', raw);
      expect('profile beside report', dest === path.join(ownedCheck, 'scenario-a-split.A.cpuprofile.json'));
      const saved = JSON.parse(await readFile(dest, 'utf8'));
      expect('raw profile roundtrip', saved.profile.samples.length === samples.length && saved.profile.nodes.length === nodes.length);
    } finally {
      await rm(ownedCheck, { recursive: true, force: true });
    }

    const source = await readFile(new URL(import.meta.url), 'utf8');
    const mainStart = source.indexOf('let scenarios;');
    const main = mainStart === -1 ? '' : source.slice(mainStart);
    const oracle = source.slice(source.indexOf('function summarizeProfile'), source.indexOf('function avgScriptPerFrameMs'));
    expect('main uses selector', main.includes('selectedScenarios(args)') && main.includes('for (const scenarioKey of scenarios)'));
    const probePath = path.resolve(process.argv[1]);
    const runEntry = (extra) => spawnSync(process.execPath, [probePath, ...extra], {
      encoding: 'utf8',
      timeout: 60000,
    });
    const noBrowser = (child) => !/DevTools listening|Chromium/.test(`${child.stdout || ''}\n${child.stderr || ''}`);
    const readPlan = (child) => {
      const lines = String(child.stdout || '').split('\n').map((row) => row.trim()).filter((row) => row.startsWith('{'));
      for (let i = lines.length - 1; i >= 0; i -= 1) {
        try {
          const parsed = JSON.parse(lines[i]);
          if (parsed && parsed.planOnly === true) return parsed;
        } catch { /* keep looking */ }
      }
      return null;
    };
    const planDefault = runEntry(['--plan-only']);
    const planDefaultJson = readPlan(planDefault);
    expect('main not hardcoded to A only',
      planDefault.status === 0
      && planDefaultJson?.planOnly === true
      && planDefaultJson.browserStarted === false
      && planDefaultJson.scenarios?.length === 2
      && planDefaultJson.scenarios[0] === 'A'
      && planDefaultJson.scenarios[1] === 'B'
      && planDefaultJson.names?.[0] === SCENARIOS.A.name
      && planDefaultJson.names?.[1] === SCENARIOS.B.name
      && noBrowser(planDefault),
      `status=${planDefault.status} stdout=${(planDefault.stdout || '').slice(0, 400)} stderr=${(planDefault.stderr || '').slice(0, 400)}`);
    expect('default plan stays dev', planDefaultJson?.build === 'dev' && planDefaultJson?.viteBuildStarted === false);
    const planA = runEntry(['--scenario=A', '--plan-only']);
    const planAJson = readPlan(planA);
    expect('explicit entry A',
      planA.status === 0
      && planAJson?.browserStarted === false
      && planAJson.scenarios?.length === 1
      && planAJson.scenarios[0] === 'A'
      && planAJson.names?.[0] === SCENARIOS.A.name
      && noBrowser(planA),
      `status=${planA.status} stdout=${(planA.stdout || '').slice(0, 300)} stderr=${(planA.stderr || '').slice(0, 300)}`);
    const planB = runEntry(['--scenario=B', '--plan-only']);
    const planBJson = readPlan(planB);
    expect('explicit entry B',
      planB.status === 0
      && planBJson?.browserStarted === false
      && planBJson.scenarios?.length === 1
      && planBJson.scenarios[0] === 'B'
      && planBJson.names?.[0] === SCENARIOS.B.name
      && noBrowser(planB),
      `status=${planB.status} stdout=${(planB.stdout || '').slice(0, 300)} stderr=${(planB.stderr || '').slice(0, 300)}`);
    const planBad = runEntry(['--scenario=nope', '--plan-only']);
    expect('invalid entry fails before browser',
      planBad.status === 1
      && (planBad.stderr || '').includes('invalid --scenario=nope')
      && !String(planBad.stdout || '').includes('"planOnly"')
      && !(planBad.stderr || '').includes('plan-only reached browser spawn')
      && noBrowser(planBad),
      `status=${planBad.status} stdout=${(planBad.stdout || '').slice(0, 300)} stderr=${(planBad.stderr || '').slice(0, 300)}`);
    const planProd = runEntry(['--scenario=A', '--build=production', '--plan-only']);
    const planProdJson = readPlan(planProd);
    expect('production plan does not build',
      planProd.status === 0
      && planProdJson?.planOnly === true
      && planProdJson.build === 'production'
      && planProdJson.viteBuildStarted === false
      && planProdJson.browserStarted === false
      && planProdJson.realProductionBundle === false
      && planProdJson.scenarios?.length === 1
      && planProdJson.scenarios[0] === 'A'
      && noBrowser(planProd)
      && !/vite:info|built in/i.test(planProd.stderr || ''),
      `status=${planProd.status} stdout=${(planProd.stdout || '').slice(0, 400)} stderr=${(planProd.stderr || '').slice(0, 400)}`);
    const planBadBuild = runEntry(['--build=nope', '--plan-only']);
    expect('invalid build fails before browser',
      planBadBuild.status === 1
      && (planBadBuild.stderr || '').includes('invalid --build=nope')
      && !String(planBadBuild.stdout || '').includes('"planOnly"')
      && noBrowser(planBadBuild),
      `status=${planBadBuild.status} stdout=${(planBadBuild.stdout || '').slice(0, 300)} stderr=${(planBadBuild.stderr || '').slice(0, 300)}`);
    expect('program stays in script total', oracle.includes("name !== '(idle)' && name !== '(root)' && name !== '(garbage collector)'") && !oracle.includes("!== '(program)'") && oracle.includes("name === '(program)'"));
    expect('scenario uses summary and denominator', main.includes('summarizeProfile(profile)') && main.includes('const totalScriptMs = scriptUs / 1000') && main.includes('avgScriptPerFrameMs(totalScriptMs, intervals.length)'));
    const joined = (parts) => parts.join('');
    const viteCall = joined(['await vite', 'Build(']);
    const lockCall = joined(['releaseLock = await acquire', 'BrowserLock();']);
    const prodCall = joined(['await buildProduction', 'Fixture({']);
    const serverCall = joined(['server = await create', 'Server({']);
    expect('production calls vite build', source.includes(viteCall) && source.includes(lockCall) && source.includes(prodCall) && source.includes(serverCall) && source.indexOf(lockCall) < source.indexOf(prodCall) && source.indexOf(lockCall) < source.indexOf(serverCall));
    expect('sampling and cpu unchanged', main.includes('Profiler.setSamplingInterval') && main.includes('interval: 100') && main.includes('rate: 4'));

    const child = spawnSync(process.execPath, [path.resolve(process.argv[1]), '--scenario=nope'], {
      encoding: 'utf8',
      timeout: 90000,
    });
    expect('invalid scenario exits 1', child.status === 1, `status=${child.status} error=${child.error || ''} stderr=${(child.stderr || '').slice(0, 500)}`);
    expect('invalid scenario message', (child.stderr || '').includes('invalid --scenario=nope'));
    expect('invalid scenario does not start browser', !/DevTools listening|Chromium/.test(`${child.stdout || ''}\n${child.stderr || ''}`));
    expect('invalid scenario does not print a measurement', !/"scenarioA"\s*:/.test(child.stdout || ''));
  } catch (error) {
    errors.push(error instanceof Error ? error.stack || error.message : String(error));
  }
  const defaultScenarios = selectedScenarios(parseArgs([]));
  console.log(JSON.stringify({
    selfCheck: true,
    browserMeasured: false,
    defaultScenarios,
    defaultBuild: selectedBuild(parseArgs([])),
    defaultNotNarrowed: defaultScenarios.length === 2 && defaultScenarios[0] === 'A' && defaultScenarios[1] === 'B',
    errors,
  }, null, 2));
  return errors.length === 0;
}

const args = parseArgs(process.argv.slice(2));
if (args.has('--self-check')) {
  const ok = await runSelfCheck();
  process.exit(ok ? 0 : 1);
}
let scenarios;
try {
  scenarios = selectedScenarios(args);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
let buildMode;
try {
  buildMode = selectedBuild(args);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
const planned = scenarios.map((scenarioKey) => {
  const spec = SCENARIOS[scenarioKey];
  if (!spec?.name || !spec.config) throw new Error(`missing scenario ${scenarioKey}`);
  return { scenarioKey, name: spec.name, config: spec.config };
});
// Self-check observes this same entry before Vite or Chromium. Without the flag, the run is unchanged.
if (args.has('--plan-only')) {
  writeSync(1, `${JSON.stringify({
    planOnly: true,
    scenarios: planned.map((item) => item.scenarioKey),
    names: planned.map((item) => item.name),
    build: buildMode,
    viteBuildStarted: false,
    realProductionBundle: false,
    browserStarted: false,
  })}\n`);
  process.exit(0);
}
if (productionRefused(scenarios, buildMode)) {
  console.error('production build refuses scenario other than explicit A');
  process.exit(1);
}
const candidateRoot = path.resolve(args.get('--root') || path.resolve(probeDir, '..'));
const reportPath = args.get('--report') || '';
const owned = await mkdtemp(path.join(scratch, 'stream-perf-react-'));

let server;
let browser;
let socket;
let sequence = 0;
const pending = new Map();
let releaseLock;
let productionNodeModulesLink = '';
let fixturePath = '/tests/stream-perf-react-fixture.html';
let buildInfo = null;

const fixtureHtml = path.join(probeDir, 'stream-perf-react-fixture.html');
const fixtureTsx = path.join(probeDir, 'stream-perf-react-fixture.tsx');

async function mark(step) {
  await writeFile('/root/hermes-workspace/fullgate-evidence/integration2/perf-progress.txt', new Date().toISOString() + ' ' + step + '\n', { flag: 'a' });
}

const call = (method, params = {}, sessionId, timeoutMs = 60000) => new Promise((resolve, reject) => {
  const id = ++sequence;
  const timer = setTimeout(() => {
    pending.delete(id);
    reject(new Error(`${method} timeout`));
  }, timeoutMs);
  pending.set(id, { resolve, reject, timer });
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});

try {
  releaseLock = await acquireBrowserLock();
  if (buildMode === 'production') {
    const evidenceDir = reportPath ? path.dirname(path.resolve(reportPath)) : productionEvidenceFallback;
    const built = await buildProductionFixture({
      candidateRoot,
      owned,
      fixtureHtml,
      evidenceDir,
      reportPath,
    });
    productionNodeModulesLink = built.nodeModulesLink;
    fixturePath = built.urlPath;
    buildInfo = {
      requested: 'production',
      method: 'vite-build+static-server',
      modeStringOnly: false,
      realProductionBundle: true,
      legacyGate: false,
      note: '生产构建对照，不是旧开发门禁通过证明。阈值、采样间隔、CPU 4倍与(program)计入规则未改。',
      command: 'build',
      mode: built.proof.resolved?.mode ?? null,
      isProduction: built.proof.resolved?.isProduction ?? null,
      envReplacement: 'process.env.NODE_ENV="production" during vite.build only',
      nodeEnvDefine: 'production',
      aliasRoot: built.aliasRoot,
      aliasSamples: built.proof.aliasSamples,
      configRealpath: built.configReal,
      candidateKeyHashes: built.keyHashes,
      outDir: built.evidenceDist,
      urlPath: built.urlPath,
      buildLogPath: built.logPath,
      assetScanPath: built.scanPath,
      jsxDevRuntimePresent: false,
      devRuntimeMarkers: DEV_RUNTIME_MARKERS,
      assets: built.assets.map((row) => ({ path: row.path, bytes: row.bytes, sha256: row.sha256 })),
      lockPath: browserLockPath,
      argv: process.argv.slice(2),
    };
    server = await startStaticServer(built.outDir);
  } else {
    server = await createServer({
      configFile: path.join(candidateRoot, 'vite.config.ts'),
      root: candidateRoot,
      cacheDir: path.join(owned, 'vite'),
      server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
      logLevel: 'error',
      plugins: [{
        name: 'stream-perf-react-fixture',
        enforce: 'pre',
        resolveId(id) {
          if (id === '/tests/stream-perf-react-fixture.tsx' || id.endsWith('/stream-perf-react-fixture.tsx')) return fixtureTsx;
          return null;
        },
        load(id) {
          if (id === fixtureTsx) return readFile(fixtureTsx, 'utf8');
          return null;
        },
        configureServer(vite) {
          vite.middlewares.use(async (request, response, next) => {
            const url = new URL(request.url || '/', 'http://fixture.invalid');
            if (url.pathname !== '/tests/stream-perf-react-fixture.html') return next();
            const source = await readFile(fixtureHtml, 'utf8');
            response.setHeader('Content-Type', 'text/html; charset=utf-8');
            response.end(await vite.transformIndexHtml(url.pathname, source));
          });
        },
      }],
    });
    await server.listen();
  }
  const port = server.httpServer.address().port;
  const origin = `http://127.0.0.1:${port}`;

  if (args.has('--plan-only')) throw new Error('plan-only reached browser spawn');
  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--remote-debugging-port=0',
    `--user-data-dir=${path.join(owned, 'profile')}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output.slice(-400)}`)), 15000);
    browser.stderr.on('data', (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.once('error', (error) => { clearTimeout(timer); reject(error); });
    browser.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Chromium exited ${code}`)); });
  });
  socket = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  const pageLogs = [];
  socket.addEventListener('message', ({ data }) => {
    let message;
    try { message = JSON.parse(data); } catch { return; }
    try {
      if (message.method === 'Runtime.consoleAPICalled') {
        pageLogs.push((message.params?.args || []).map((arg) => arg.value ?? arg.description ?? '').join(' '));
      }
      if (message.method === 'Runtime.exceptionThrown') {
        pageLogs.push(message.params?.exceptionDetails?.exception?.description || message.params?.exceptionDetails?.text || 'exception');
      }
    } catch { /* keep CDP responses flowing */ }
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
  });

  const { browserContextId } = await call('Target.createBrowserContext');
  const { targetId } = await call('Target.createTarget', { url: 'about:blank', browserContextId });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  await call('Page.enable', {}, sessionId);
  await call('Runtime.enable', {}, sessionId);

  const evaluate = async (expression, timeoutMs = 30000) => {
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId, timeoutMs);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || 'evaluate failed');
    return result.result.value;
  };

  async function scenario(scenarioKey, name, config) {
    await mark(`scenario ${name}`);
    await call('Emulation.setCPUThrottlingRate', { rate: 1 }, sessionId);
    await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
    await call('Page.navigate', { url: `${origin}${fixturePath}` }, sessionId);
    const readyAt = Date.now();
    let ready = false;
    let last = '';
    while (Date.now() - readyAt < 45000) {
      try {
        last = await evaluate(`JSON.stringify({ ok: window.fixtureReady === true, err: window.fixtureError || '', href: location.href })`, 5000);
        const parsed = JSON.parse(last);
        if (parsed.err) throw new Error(`${parsed.err}\n${pageLogs.slice(-8).join('\n')}`);
        if (parsed.ok) { ready = true; break; }
      } catch (error) {
        last = String(error);
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!ready) throw new Error(`fixture not ready: ${last}\nlogs:\n${pageLogs.slice(-12).join('\n')}`);
    await mark(`${name} ready`);
    const prepared = await evaluate(`window.__prepare(${JSON.stringify(config)})`);
    assert.ok(prepared.streamChars >= config.streamTargetChars, `${name} stream chars ${prepared.streamChars}`);
    if (config.includeCode) assert.equal(prepared.codeLines, 300);
    const paintedAt = Date.now();
    let painted = false;
    let paintLast = '';
    while (Date.now() - paintedAt < 20000) {
      paintLast = await evaluate(`JSON.stringify({ ok: document.body.innerText.includes(${JSON.stringify(prepared.visibleMark)}), err: window.fixtureError || '', text: (document.body.innerText || '').slice(0, 180) })`);
      const parsed = JSON.parse(paintLast);
      if (parsed.err) throw new Error(parsed.err);
      if (parsed.ok) { painted = true; break; }
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    if (!painted) throw new Error(`${name} history not painted: ${paintLast}`);
    await call('Emulation.setCPUThrottlingRate', { rate: 4 }, sessionId);
    await call('Profiler.enable', {}, sessionId);
    await call('Profiler.setSamplingInterval', { interval: 100 }, sessionId);
    await call('Profiler.start', {}, sessionId);
    await mark(`${name} run`);
    const raw = await evaluate('window.__run()', 300000);
    await mark(`${name} ran ${raw && raw.elapsedMs}`);
    const profile = await call('Profiler.stop', {}, sessionId);
    await call('Profiler.disable', {}, sessionId);
    const profilePath = await persistRawProfile(reportPath, scenarioKey, profile);
    const profileSummary = summarizeProfile(profile);
    const scriptUs = profileSummary.scriptUs;
    const totalScriptMs = scriptUs / 1000;
    const lag = calculateLagMetrics(raw.receiveTimeline, raw.domCommitTimeline);
    const intervals = raw.rafIntervals;
    const settle = intervals.slice(raw.streamFrameCount);
    const frameP95 = percentile(intervals, 0.95) ?? 0;
    const metrics = {
      name,
      historyTurns: config.historyTurns,
      streamChars: prepared.streamChars,
      renderedChars: raw.streamChars,
      chunkCount: raw.chunkCount,
      chunksSent: raw.chunksSent,
      completed: raw.completed === true,
      elapsedMs: round(raw.elapsedMs),
      frames: {
        count: intervals.length,
        p95Ms: round(frameP95),
        maxMs: round(intervals.length ? Math.max(...intervals) : 0),
        longTasksCount: raw.longTasks.length,
        longTasksMaxMs: round(raw.longTasks.reduce((max, task) => Math.max(max, task.duration), 0)),
      },
      scriptTimes: {
        totalScriptMs: round(totalScriptMs),
        avgScriptPerFrameMs: avgScriptPerFrameMs(totalScriptMs, intervals.length),
        denominator: 'rafIntervals.length',
        includesProgram: true,
        excludedFromScript: ['(idle)', '(root)', '(garbage collector)'],
        programPerFrameMs: avgScriptPerFrameMs(profileSummary.selfUs.program / 1000, intervals.length),
        selfMs: {
          program: round(profileSummary.selfUs.program / 1000),
          idle: round(profileSummary.selfUs.idle / 1000),
          gc: round(profileSummary.selfUs.gc / 1000),
          root: round(profileSummary.selfUs.root / 1000),
          named: round(profileSummary.selfUs.named / 1000),
          anonymous: round(profileSummary.selfUs.anonymous / 1000),
        },
        selfUs: profileSummary.selfUs,
        top15: profileSummary.top15,
        lineNumberBase: 0,
        profilePath,
        profileCounts: profileSummary.counts,
        reconciled: profileSummary.reconciled,
      },
      lag: {
        sampledCount: lag.sampledCount,
        matchedCount: lag.matchedCount,
        unmatchedCount: lag.unmatchedCount,
        ambiguousCount: lag.ambiguousCount,
        p95LagMs: lag.p95LagMs,
        maxLagMs: lag.maxLagMs,
      },
      settleDurationMs: round(raw.settleDurationMs),
      settleFrames: {
        count: settle.length,
        maxMs: round(settle.length ? Math.max(...settle) : null),
      },
    };
    const failures = [];
    if (raw.completed !== true || !(raw.elapsedMs <= THRESHOLDS.budgetMs)) failures.push(`budget ${raw.elapsedMs}ms completed=${raw.completed} sent=${raw.chunksSent}/${raw.chunkCount}`);
    if (!(metrics.frames.p95Ms <= THRESHOLDS.frameP95Ms)) failures.push(`frame p95 ${metrics.frames.p95Ms} > ${THRESHOLDS.frameP95Ms}`);
    if (metrics.frames.longTasksCount !== THRESHOLDS.longTasks) failures.push(`long tasks ${metrics.frames.longTasksCount} !== 0`);
    if (!(metrics.scriptTimes.avgScriptPerFrameMs <= THRESHOLDS.scriptPerFrameMs)) failures.push(`script/frame ${metrics.scriptTimes.avgScriptPerFrameMs} > ${THRESHOLDS.scriptPerFrameMs}`);
    if (!(metrics.lag.sampledCount > 0 && metrics.lag.unmatchedCount === 0 && metrics.lag.ambiguousCount === 0 && metrics.lag.p95LagMs != null && metrics.lag.p95LagMs <= THRESHOLDS.lagP95Ms)) {
      failures.push(`lag ${JSON.stringify(metrics.lag)} exceeds ${THRESHOLDS.lagP95Ms}`);
    }
    if (!(metrics.settleFrames.count > 0 && metrics.settleFrames.maxMs != null && metrics.settleFrames.maxMs <= THRESHOLDS.settleMaxMs)) {
      failures.push(`settle ${JSON.stringify(metrics.settleFrames)} exceeds ${THRESHOLDS.settleMaxMs}`);
    }
    return { metrics, failures };
  }

  const results = {};
  for (const item of planned) {
    results[item.scenarioKey] = await scenario(item.scenarioKey, item.name, item.config);
  }
  if (buildInfo && results.A) {
    const programPerFrameMs = results.A.metrics.scriptTimes.programPerFrameMs;
    buildInfo.programPerFrameMs = programPerFrameMs;
    buildInfo.programAloneExceeds4ms = programPerFrameMs > THRESHOLDS.scriptPerFrameMs;
    buildInfo.productJsOptimization = buildInfo.programAloneExceeds4ms ? 'stop' : 'not-required-by-program-alone';
    buildInfo.programExcluded = false;
  }
  const report = buildReport({ candidateRoot, scenarios, results, buildInfo });
  const json = JSON.stringify(report, null, 2);
  console.log(json);
  if (reportPath) {
    await mkdir(path.dirname(reportPath), { recursive: true });
    await writeFile(reportPath, json);
  }
  if (!report.passed) process.exitCode = 1;
} catch (error) {
  await writeFile('/root/hermes-workspace/fullgate-evidence/integration2/perf-progress.txt', `${new Date().toISOString()} FAIL ${error}\n`, { flag: 'a' }).catch(() => undefined);
  console.error(error);
  process.exitCode = 1;
} finally {
  socket?.close();
  for (const waiter of pending.values()) clearTimeout(waiter.timer);
  if (browser && browser.exitCode === null) {
    browser.kill('SIGTERM');
    await new Promise((resolve) => browser.once('exit', resolve));
  }
  await server?.close();
  if (productionNodeModulesLink) {
    await rm(productionNodeModulesLink, { force: true }).catch(() => undefined);
  }
  if (releaseLock) await releaseLock().catch(() => undefined);
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
}
