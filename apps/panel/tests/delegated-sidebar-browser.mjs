import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const rootDir = path.resolve(import.meta.dirname, '..');
const profile = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'panel-delegated-sidebar-browser-'));
const server = await createServer({
  configFile: path.join(rootDir, 'vite.config.ts'),
  root: rootDir,
  cacheDir: path.join(profile, 'vite'),
  server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
  logLevel: 'error',
});
let browser, socket;

try {
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  const wsUrl = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Chromium CDP startup timeout')), 15000);
    browser.stderr.on('data', chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.on('error', reject);
  });

  socket = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  let seq = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(JSON.stringify(msg.error)));
      else p.resolve(msg.result);
    }
  });

  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });

  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };

  const wait = async (expression, maxAttempts = 60) => {
    for (let n = 0; n < maxAttempts; n++) {
      let ok = false;
      try { ok = await evaluate(expression); } catch { ok = false; }
      if (ok) return;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error(`wait failed: ${expression}; snapshot=${await evaluate('window.root()?.innerHTML')}`);
  };

  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  await call('Page.navigate', { url: `http://127.0.0.1:${port}/tests/sidebar-live-fixture.html` }, sessionId);
  await wait('window.ready && window.list.sessions.length > 0');

  // Verify tab order and switch to live tab
  // First clear initial fixture state before test cases
  await evaluate(`(async () => {
    window.mockTime = 1_000_000;
    window.mockState.activeList = [
      { id: 'sess-parent-task', title: '父会话正在委派后台任务', source: 'api_server', started_at: 1000, last_active: 1050, message_count: 5, preview: null, pinned: false, archived: false }
    ];
    [...window.root().querySelectorAll('.seg button')].find(b => b.textContent.trim() === '实时').click();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  await wait(`window.list.filter === 'live' && window.list.sessions.length === 1`);

  const currentIds = await evaluate(`window.list.sessions.map(s => s.id)`);
  assert.throws(() => assert.deepEqual(currentIds, ['missing-parent'], 'live parent id'), /live parent id/);
  console.log('RED control: live parent id assertion rejects a missing parent, actual', currentIds);
  assert.deepEqual(currentIds, ['sess-parent-task'], 'Parent session appears in live list during delegated subagent execution');
  console.log('PASS parent session appears in live during delegated execution');

  // Verify subagent child does NOT appear in live list even if in raw active payload
  await evaluate(`(async () => {
    window.mockState.activeList = [
      { id: 'subagent-worker', title: '子任务Worker', source: 'subagent', started_at: 1000, last_active: 1050, message_count: 2, preview: null, pinned: false, archived: false },
      { id: 'sess-parent-task', title: '父会话正在委派后台任务', source: 'api_server', started_at: 1000, last_active: 1050, message_count: 5, preview: null, pinned: false, archived: false }
    ];
    await window.list.refreshHead();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  const filteredIds = await evaluate(`window.list.sessions.map(s => s.id)`);
  assert.deepEqual(filteredIds, ['sess-parent-task'], 'Child subagent is strictly excluded from live tab');
  console.log('PASS child subagent strictly excluded');

  // Verify retention: subagent finishes, parent becomes idle at mockTime 1_005_000
  // activeList becomes empty -> parent retained for 600s
  await evaluate(`(async () => {
    window.mockTime = 1_005_000;
    window.mockState.activeList = [];
    await window.list.refreshHead();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  const retainedIds = await evaluate(`window.list.sessions.map(s => s.id)`);
  assert.deepEqual(retainedIds, ['sess-parent-task'], 'Parent conversation retained in live tab for 10 minutes after subagent ends');
  console.log('PASS parent conversation retained during grace period');

  // Verify retention expiry at 1_005_000 + 600_000 = 1_605_000
  await evaluate(`(async () => {
    window.mockTime = 1_605_000;
    await window.list.refreshHead();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  const expiredIds = await evaluate(`window.list.sessions.map(s => s.id)`);
  assert.deepEqual(expiredIds, [], 'Parent conversation cleanly removed after 600s retention expires');
  console.log('PASS parent conversation removed at retention deadline');

  console.log('ALL delegated sidebar browser tests passed successfully.');
} finally {
  if (socket) socket.close();
  if (browser) {
    browser.kill();
    await new Promise(resolve => browser.on('exit', resolve));
  }
  await server.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
