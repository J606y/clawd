import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'message-menu-size-'));
const fixture = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>消息操作菜单尺寸</title>
  <style>
    html, body, #root, .stage { height: 100%; margin: 0; }
    .stage { position: relative; }
    .dock { position: absolute; right: 12px; width: min(78%, 260px); }
    .stage[data-anchor="bottom"] .dock { bottom: 8px; top: auto; }
    .stage[data-anchor="band"] .dock { top: 108px; bottom: auto; }
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    window.fixtureErrors = [];
    window.addEventListener('error', (event) => window.fixtureErrors.push(String(event.message)));
    window.addEventListener('unhandledrejection', (event) => window.fixtureErrors.push(String(event.reason)));
    const nativeMatch = window.matchMedia.bind(window);
    window.matchMedia = (query) => {
      const text = String(query);
      if (text.includes('pointer: coarse') && !text.includes('pointer: fine')) {
        return {
          matches: true,
          media: text,
          onchange: null,
          addListener() {},
          removeListener() {},
          addEventListener() {},
          removeEventListener() {},
          dispatchEvent() { return false; },
        };
      }
      return nativeMatch(query);
    };
  </script>
  <script type="module">
    import { createElement as h, useEffect } from 'react';
    import { createRoot } from 'react-dom/client';
    import '/src/react/styles/index.css';
    import { ChatStyles } from '/src/react/pages/chat/chat-styles.tsx';
    import { HistoryTurn } from '/src/react/pages/chat/turns.tsx';
    const turn = {
      role: 'user',
      message: { id: 'fixture-message' },
      text: '已发送的消息',
      images: [],
      files: [],
      uploads: [],
      droppedImages: 0,
    };
    function App() {
      useEffect(() => { window.fixtureReady = true; }, []);
      return h('div', { className: 'stage hp-chat', 'data-anchor': 'bottom' },
        h(ChatStyles),
        h('div', { className: 'dock' }, h(HistoryTurn, {
          turn,
          pending: false,
          outcome: '',
          editDisabled: '',
          editing: false,
          messageKey: 'fixture',
          onEditStart() {},
          onEditEnd() {},
          onEditSend: async () => {},
        })));
    }
    createRoot(document.getElementById('root')).render(h(App));
  </script>
</body>
</html>`;

let browser;
let socket;
let server;
let failure;
const pending = new Map();
let sequence = 0;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  server = await createServer({
    configFile: path.join(root, 'vite.config.ts'),
    root,
    cacheDir: path.join(owned, 'vite'),
    server: { host: '127.0.0.1', port: 0 },
    logLevel: 'error',
    plugins: [{
      name: 'message-menu-size-fixture',
      configureServer(viteServer) {
        viteServer.middlewares.use(async (req, res, next) => {
          const url = req.url?.split('?')[0];
          if (url !== '/__message-menu-fixture') return next();
          try {
            const html = await viteServer.transformIndexHtml('/__message-menu-fixture.html', fixture);
            res.statusCode = 200;
            res.setHeader('content-type', 'text/html; charset=utf-8');
            res.end(html);
          } catch (error) {
            next(error);
          }
        });
      },
    }],
  });
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--window-size=390,844',
    '--remote-debugging-port=0',
    `--user-data-dir=${path.join(owned, 'profile')}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  const endpoint = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output.slice(-500)}`)), 20000);
    browser.stderr.on('data', (chunk) => {
      output = (output + chunk).slice(-8192);
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    browser.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Chromium exited during startup: ${code}`));
    });
  });
  socket = new WebSocket(endpoint);
  await once(socket, 'open');
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    const job = pending.get(message.id);
    if (!job) return;
    pending.delete(message.id);
    clearTimeout(job.timer);
    message.error ? job.reject(new Error(JSON.stringify(message.error))) : job.resolve(message.result);
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timed out`));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const cdp = (method, params) => call(method, params, sessionId);
  const evaluate = async (expression) => {
    const result = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const frames = () => evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const wait = async (expression) => {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (await evaluate(expression)) return;
      await delay(50);
    }
    const diagnostic = await evaluate('({ready:window.fixtureReady,errors:window.fixtureErrors,text:document.body?document.body.innerText.slice(0,500):""})');
    throw new Error(`Fixture wait failed: ${expression} ${JSON.stringify(diagnostic)}`);
  };

  await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/__message-menu-fixture` });
  await wait('window.fixtureReady === true && !!document.querySelector(".msg-editor")');
  await evaluate('document.fonts ? document.fonts.ready : Promise.resolve()');

  const setViewport = async (height) => {
    await cdp('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height,
      deviceScaleFactor: 1,
      mobile: false,
      screenWidth: 390,
      screenHeight: height,
    });
    await frames();
    const box = await evaluate('({innerWidth:window.innerWidth,innerHeight:window.innerHeight,vv:window.visualViewport.width,vh:window.visualViewport.height})');
    if (Math.abs(box.innerWidth - 390) > 2 || Math.abs(box.vv - 390) > 2 || Math.abs(box.innerHeight - height) > 2 || Math.abs(box.vh - height) > 2) {
      throw new Error(`viewport setup failed: ${JSON.stringify(box)} expected 390x${height}`);
    }
  };
  const placeAnchor = (anchor) => evaluate(`document.querySelector('.stage').dataset.anchor = ${JSON.stringify(anchor)}; true`);
  const closeMenu = async () => {
    await evaluate(`document.querySelector('.msg-editor [data-action="close"]')?.click()`);
    await wait('!document.querySelector(".msg-editor [role=\\"menu\\"]")');
  };
  const sampleExpr = `(() => {
    const menu = document.querySelector('.msg-editor [role="menu"]');
    if (!menu) return { missing: true };
    const rect = menu.getBoundingClientRect();
    const style = getComputedStyle(menu);
    const kids = [...menu.children].filter((el) => el.getClientRects().length);
    const first = kids[0].getBoundingClientRect();
    const last = kids[kids.length - 1].getBoundingClientRect();
    const pad = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const contentHeight = (last.bottom - first.top) + pad + border;
    const vv = window.visualViewport;
    const view = {
      top: vv.offsetTop,
      left: vv.offsetLeft,
      right: vv.offsetLeft + vv.width,
      bottom: vv.offsetTop + vv.height,
      width: vv.width,
      height: vv.height,
    };
    const inside = rect.left >= view.left - 0.5 && rect.top >= view.top - 0.5 && rect.right <= view.right + 0.5 && rect.bottom <= view.bottom + 0.5;
    return {
      height: rect.height,
      contentHeight,
      delta: Math.abs(rect.height - contentHeight),
      inside,
      rect: { top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left, width: rect.width, height: rect.height },
      view,
      top: style.top,
      bottom: style.bottom,
      labels: [...menu.querySelectorAll('[role="menuitem"]')].map((el) => el.textContent.trim()),
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
    };
  })()`;
  const openContext = () => evaluate(`(() => {
    const el = document.querySelector('.msg-editor');
    const r = el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 8, clientY: r.top + 8, button: 2 }));
    return true;
  })()`);

  await setViewport(844);
  await placeAnchor('bottom');
  await frames();
  const width = await evaluate('window.innerWidth');
  assert.ok(Math.abs(width - 390) <= 2, `expected about 390px viewport, got ${width}`);

  const press = await evaluate(`(() => {
    const el = document.querySelector('.msg-editor');
    const r = el.getBoundingClientRect();
    window.__pressDelays = [];
    const orig = window.setTimeout;
    window.setTimeout = function(fn, ms, ...args) {
      window.__pressDelays.push(ms);
      return orig.call(this, fn, ms, ...args);
    };
    el.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true,
      cancelable: true,
      composed: true,
      pointerType: 'touch',
      pointerId: 3,
      isPrimary: true,
      buttons: 1,
      clientX: r.left + r.width / 2,
      clientY: r.top + Math.min(8, r.height / 2),
    }));
    return { delays: window.__pressDelays.slice(), open: !!document.querySelector('.msg-editor [role="menu"]') };
  })()`);
  assert.ok(press.delays.includes(500), `long press must stay 500ms, got ${JSON.stringify(press)}`);
  assert.equal(press.open, false, 'menu must not open on pointerdown');
  await evaluate('new Promise((resolve) => setTimeout(resolve, 420))');
  assert.equal(await evaluate('!!document.querySelector(".msg-editor [role=\\"menu\\"]")'), false, 'menu must still be closed before 500ms');
  await evaluate('new Promise((resolve) => setTimeout(resolve, 220))');
  await frames();
  assert.equal(await evaluate('!!document.querySelector(".msg-editor [role=\\"menu\\"]")'), true, 'long press must open the menu after 500ms');

  const bottom = await evaluate(sampleExpr);
  console.log(`MEASURE bottom-844 ${JSON.stringify(bottom)}`);
  assert.deepEqual(bottom.labels, ['编辑', '复制', '关闭']);
  assert.ok(
    bottom.delta <= 8 && bottom.inside,
    `menu getBoundingClientRect height must be within 8px of content height and all four edges must stay inside visualViewport: ${JSON.stringify(bottom)}`,
  );

  await closeMenu();
  await setViewport(320);
  await placeAnchor('band');
  await frames();
  await openContext();
  await wait('!!document.querySelector(".msg-editor [role=\\"menu\\"]")');
  await frames();
  const band = await evaluate(sampleExpr);
  console.log(`MEASURE band-320 ${JSON.stringify(band)}`);
  assert.deepEqual(band.labels, ['编辑', '复制', '关闭']);
  assert.ok(
    Math.abs(band.innerWidth - 390) <= 2 && band.delta <= 8 && band.inside,
    `menu getBoundingClientRect height must be within 8px of content height and all four edges must stay inside visualViewport: ${JSON.stringify(band)}`,
  );
  console.log('PASS message menu size at 390px');
} catch (error) {
  failure = error;
} finally {
  socket?.close();
  for (const job of pending.values()) clearTimeout(job.timer);
  if (browser) {
    browser.kill('SIGTERM');
    await Promise.race([once(browser, 'exit'), delay(3000)]);
  }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true });
}
if (failure) {
  console.error(failure.stack || failure.message);
  process.exit(1);
}
