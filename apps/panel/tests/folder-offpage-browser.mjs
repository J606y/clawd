import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const profile = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'panel-folder-offpage-'));
const server = await createServer({ server: { host: '127.0.0.1', port: 0 } });
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
    const timer = setTimeout(() => reject(new Error('Chromium CDP startup timeout')), 20000);
    browser.stderr.on('data', (chunk) => {
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
  socket.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const waiter = pending.get(msg.id);
      pending.delete(msg.id);
      clearTimeout(waiter.timer);
      if (msg.error) waiter.reject(new Error(JSON.stringify(msg.error)));
      else waiter.resolve(msg.result);
    }
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const evaluate = async (expression) => {
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let n = 0; n < 80; n++) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const state = await evaluate(`JSON.stringify({ready: window.ready, unmatched: window.unmatched, text: document.body?.innerText, html: document.body?.innerHTML?.slice(0, 2000)})`);
    throw new Error(`wait failed: ${expression}; state=${state}`);
  };
  const rootText = (selector) => `(document.querySelector(${JSON.stringify(selector)})?.textContent || '')`;

  const click = async (selector) => {
    const point = await evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element || element.disabled) throw new Error('Missing or disabled control');
      element.scrollIntoView({ block: 'center' });
      const rect = element.getBoundingClientRect();
      const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
      if (rect.width <= 0 || rect.height <= 0 || !element.contains(document.elementFromPoint(x, y))) {
        throw new Error('Control is not visibly hittable');
      }
      return { x, y };
    })()`);
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 }, sessionId);
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 }, sessionId);
  };
  const assertFolders = async () => {
    assert.equal(await evaluate(`document.querySelectorAll('[data-folder-id="folder-on"] .item').length`), 1);
    assert.match(await evaluate(rootText('[data-folder-id="folder-on"]')), /页内归属/);
    assert.equal(await evaluate(`document.querySelectorAll('[data-folder-id="folder-off"] .item').length`), 2);
    const titles = await evaluate(`[...document.querySelectorAll('[data-folder-id="folder-off"] .item .title')].map((item) => item.textContent)`);
    assert.deepEqual(titles, ['页外归属', '定时归属']);
    assert.match(await evaluate(rootText('[data-folder-id="folder-off"] .folder-toggle')), /页外夹 \(2\)/);
    assert.doesNotMatch(await evaluate(rootText('.session-list > ul')), /页外归属|页内归属|定时归属/);
  };

  await call('Page.navigate', { url: `http://127.0.0.1:${port}/tests/folder-offpage-fixture.html` }, sessionId);
  await wait(`window.ready && ${rootText('.session-list > ul')}.includes('主列表对话') && ${rootText('[data-folder-id="folder-on"]')}.includes('页内归属')`);

  const offToggle = await evaluate(rootText('[data-folder-id="folder-off"] .folder-toggle'));
  assert.match(offToggle, /页外夹 \(2\)/, 'folder count must include all assigned sources, even off the loaded page');
  assert.doesNotMatch(offToggle, /页外夹 \(0\)|页外夹 \(1\)/);
  const mainBefore = await evaluate(rootText('.session-list > ul'));
  assert.match(mainBefore, /主列表对话/);
  assert.doesNotMatch(mainBefore, /页外归属|页内归属|更早未归属|定时归属/);
  assert.equal(await evaluate(`document.querySelectorAll('[data-folder-id="folder-on"] .item').length`), 1);

  await click('[data-folder-id="folder-off"] [aria-expanded]');
  await wait(`!!document.querySelector('[data-folder-id="folder-off"] [aria-expanded="true"]')`);
  const expanded = await evaluate(rootText('[data-folder-id="folder-off"]'));
  assert.match(expanded, /页外归属/);
  assert.match(expanded, /定时归属/);
  assert.doesNotMatch(expanded, /主列表对话|当前筛选及已加载页中暂无对话/);
  assert.equal(await evaluate(`!!document.querySelector('[data-folder-id="folder-off"] .pin')`), false, 'project session payload has no pin; loaded rows still override');
  await assertFolders();
  const mainAfterExpand = await evaluate(rootText('.session-list > ul'));
  assert.match(mainAfterExpand, /主列表对话/);
  assert.doesNotMatch(mainAfterExpand, /页外归属|更早未归属/);
  console.log('PASS expanded folder lists assigned session that is not on the loaded page');

  const callsBeforeSearch = await evaluate('calls.length');
  await evaluate(`(() => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '页外');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${rootText('[data-folder-id="folder-off"]')}.includes('页外归属') && !${rootText('.session-list > ul')}.includes('主列表对话')`);
  assert.equal(await evaluate(`calls.slice(${callsBeforeSearch}).filter((item) => item.action === 'collapse').length`), 0);
  assert.doesNotMatch(await evaluate(rootText('[data-folder-id="folder-off"]')), /定时归属|主列表对话/);
  await evaluate(`(() => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '主列表');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${rootText('.session-list > ul')}.includes('主列表对话') && !${rootText('[data-folder-id="folder-off"]')}.includes('页外归属')`);
  await evaluate(`(() => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${rootText('.session-list > ul')}.includes('主列表对话') && ${rootText('[data-folder-id="folder-off"]')}.includes('页外归属')`);
  console.log('PASS search still filters folder and main list without rewriting collapse');

  const writesBeforeFilter = await evaluate('calls.length');
  await click('.seg button:nth-child(3)');
  await wait(`${rootText('.session-list > ul')}.includes('主列表定时') && !${rootText('.session-list > ul')}.includes('主列表对话')`);
  await assertFolders();
  await click('.seg button:nth-child(1)');
  await wait(`${rootText('.session-list > ul')}.includes('主列表实时') && !${rootText('.session-list > ul')}.includes('主列表对话') && !${rootText('.session-list > ul')}.includes('主列表定时')`);
  await assertFolders();
  assert.equal(await evaluate('calls.length'), writesBeforeFilter, 'filter switches must not rewrite folder state');
  console.log('PASS live and auto filter only the unfiled list; folder rows/counts/pins stay intact');

  await evaluate(`(() => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '页外');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${rootText('[data-folder-id="folder-off"]')}.includes('页外归属') && !${rootText('[data-folder-id="folder-off"]')}.includes('定时归属') && !${rootText('.session-list > ul')}.includes('主列表实时')`);
  assert.match(await evaluate(rootText('[data-folder-id="folder-off"] .folder-toggle')), /页外夹 \(1\)/);
  await evaluate(`(() => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${rootText('.session-list > ul')}.includes('主列表实时') && ${rootText('[data-folder-id="folder-off"]')}.includes('定时归属')`);
  await assertFolders();
  console.log('PASS search still finds inactive folder conversations while live is selected');

  await click('[data-folder-id="folder-off"] .folder-toggle');
  await wait(`!!document.querySelector('[data-folder-id="folder-off"] [aria-expanded="false"]')`);
  await click('.seg button:nth-child(2)');
  await wait(`${rootText('.session-list > ul')}.includes('主列表对话') && !${rootText('.session-list > ul')}.includes('主列表实时') && !${rootText('.session-list > ul')}.includes('主列表定时')`);
  assert.equal(await evaluate(`!!document.querySelector('[data-folder-id="folder-off"] [aria-expanded="false"]')`), true);
  assert.match(await evaluate(rootText('[data-folder-id="folder-off"] .folder-toggle')), /页外夹 \(2\)/);
  await click('[data-folder-id="folder-off"] .folder-toggle');
  await wait(`!!document.querySelector('[data-folder-id="folder-off"] [aria-expanded="true"]')`);
  await assertFolders();
  assert.deepEqual(await evaluate('window.unmatched'), []);
  console.log('PASS returning to chats preserves folder collapse, membership and counts');
} finally {
  socket?.close();
  if (browser) {
    browser.kill('SIGTERM');
    await new Promise((resolve) => {
      if (browser.exitCode !== null) resolve();
      else browser.once('exit', resolve);
    });
  }
  await server.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
