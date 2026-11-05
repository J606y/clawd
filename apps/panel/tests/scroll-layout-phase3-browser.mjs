import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const rootDir = path.resolve(import.meta.dirname, '..');
const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'scroll-phase3-'));
const server = await createServer({
  configFile: path.join(rootDir, 'vite.config.ts'),
  root: rootDir,
  cacheDir: path.join(owned, 'vite'),
  server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
  logLevel: 'error',
});

let browser, socket;
const pending = new Map();
const guardFailures = [];
const guardErrors = [];
let sequence = 0;

try {
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn('/usr/bin/chromium', ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--remote-debugging-port=0', `--user-data-dir=${path.join(owned, 'profile')}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => reject(new Error('Chromium startup timeout: ' + output)), 15000);
    browser.stderr.on('data', chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
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
    if (m.method === 'Fetch.requestPaused') {
      const { requestId, request } = m.params;
      const url = new URL(request.url);
      const allowed = request.method === 'GET' && url.origin === `http://127.0.0.1:${port}`;
      if (!allowed) guardFailures.push(`${request.method} ${request.url}`);
      call(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', allowed ? { requestId } : { requestId, errorReason: 'AccessDenied' }, m.sessionId)
        .catch(error => guardErrors.push(String(error)));
    }
    const p = pending.get(m.id);
    if (p) {
      pending.delete(m.id);
      clearTimeout(p.timer);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  });

  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timeout`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  const { browserContextId } = await call('Target.createBrowserContext');
  const { targetId } = await call('Target.createTarget', { url: 'about:blank', browserContextId });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  await call('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, sessionId);
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  await call('Page.navigate', { url: `http://127.0.0.1:${port}/tests/sync-fixture.html` }, sessionId);

  for (let n = 0; n < 80; n++) {
    if (await evaluate('window.ready && document.querySelectorAll("#chat-host .past").length >= 30')) break;
    await new Promise(r => setTimeout(r, 50));
  }
  assert.equal(await evaluate('document.querySelectorAll("#chat-host .past").length >= 30'), true, 'ChatPage must render the synthetic history');

  const test1 = await evaluate(`(() => {
    const scroller = document.querySelector('#chat-host .scroll');
    let scrollHeightReads = 0;
    const origDesc = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight');
    Object.defineProperty(scroller, 'scrollHeight', {
      get() {
        scrollHeightReads++;
        return origDesc.get.call(this);
      },
      configurable: true
    });
    const before = scrollHeightReads;
    const block = document.createElement('div');
    block.className = 'fixture-growth';
    block.style.height = '900px';
    block.textContent = 'synthetic delayed media layout';
    scroller.querySelector('.col').append(block);
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
      const gap = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop;
      resolve({ readsDuringFollow: scrollHeightReads - before, gap });
    })));
  })()`);
  console.log('TEST 1 (sync scrollHeight read while following growth):', test1);
  assert.throws(() => assert.ok(test1.readsDuringFollow > 1000, 'follow correction scrollHeight reads'), /follow correction/);
  console.log('RED control: follow read assertion rejects an impossible count');
  assert.ok(test1.readsDuringFollow > 0, 'P3.1 was rejected by scroll regressions: preserve legacy follow correction and report its synchronous reads');
  assert.ok(test1.gap <= 2, `delayed growth must stay at bottom via ResizeObserver, gap was ${test1.gap}`);

  const anchorTest = await evaluate(`(async () => {
    anchor.setStick(false);
    const scroller = anchor.scroller();
    scroller.scrollTop = 500;
    const scrolled = scroller.scrollTop;
    const captured = anchor.capture();
    anchor.restore(${JSON.stringify('special:test [val]="1" & hello')}, 50);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const item = scroller.querySelector('[data-history-source="special:test [val]=\\\\"1\\\\" & hello"]') || [...scroller.querySelectorAll('[data-history-source]')].find((node) => node.dataset.historySource.includes('special:test'));
    const restoredOffset = item.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    return { captured, scrolled, restoredOffset, source: item?.dataset.historySource || '' };
  })()`);
  assert.ok(anchorTest.captured, 'readingAnchor must return an anchor when stick is false and scrolled');
  assert.ok(Math.abs(anchorTest.restoredOffset - 50) <= 1, `Special-character anchor must restore actual offset: ${JSON.stringify(anchorTest)}`);
  assert.deepEqual(guardFailures, [], 'Only synthetic GET/module requests are allowed');
  assert.deepEqual(guardErrors, [], 'Network guard must complete without errors');
  console.log('TEST 2 (real DOM anchor capture & restore with CSS.escape): PASSED', anchorTest);
} finally {
  if (socket) socket.close();
  for (const item of pending.values()) clearTimeout(item.timer);
  pending.clear();
  if (browser && browser.exitCode === null) {
    browser.kill('SIGKILL');
    await new Promise(r => browser.exitCode !== null ? r() : browser.once('exit', r));
  }
  await server.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
