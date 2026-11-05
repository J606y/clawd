import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const rootDir = path.resolve(import.meta.dirname, '..');
const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'composer-sizing-'));
const server = await createServer({
  configFile: path.join(rootDir, 'vite.config.ts'),
  root: rootDir,
  cacheDir: path.join(owned, 'vite'),
  server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
  logLevel: 'error',
});
let browser, socket, failure;
const pending = new Map(); let sequence = 0;
try {
  await server.listen();
  browser = spawn('/usr/bin/chromium', ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--remote-debugging-port=0', `--user-data-dir=${path.join(owned, 'profile')}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => reject(new Error('Chromium startup timeout')), 15000);
    browser.stderr.on('data', chunk => { output += chunk; const match = output.match(/DevTools listening on (ws:\/\/\S+)/); if (match) { clearTimeout(timer); resolve(match[1]); } });
    browser.once('error', error => { clearTimeout(timer); reject(error); });
  });
  socket = new WebSocket(ws);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once:true }); socket.addEventListener('error', reject, { once:true }); });
  socket.addEventListener('message', ({ data }) => { const m = JSON.parse(data); const p = pending.get(m.id); if (p) { pending.delete(m.id); clearTimeout(p.timer); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); } });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timeout`)); }, 15000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  for (const width of [390, 1280]) {
    const { browserContextId } = await call('Target.createBrowserContext');
    const { targetId } = await call('Target.createTarget', { url:'about:blank', browserContextId });
    const { sessionId } = await call('Target.attachToTarget', { targetId, flatten:true });
    const evaluate = async expression => { const result = await call('Runtime.evaluate', { expression, awaitPromise:true, returnByValue:true }, sessionId); if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result.value; };
    await call('Emulation.setDeviceMetricsOverride', { width, height:800, deviceScaleFactor:1, mobile:width < 500 }, sessionId);
    await call('Page.navigate', { url:`http://127.0.0.1:${server.httpServer.address().port}/tests/composer-sizing-fixture.html` }, sessionId);
    let ready = false;
    for (let n = 0; n < 100; n++) { if (await evaluate('window.ready === true')) { ready = true; break; } await new Promise(resolve => setTimeout(resolve, 50)); }
    assert.ok(ready, 'composer fixture loads');
    await evaluate('ta.focus(); input("第一行\n第二行\n第三行")'.replaceAll('\n', '\\n'));
    await evaluate('historyBox.scrollTop=historyBox.scrollHeight; true');
    const before = await evaluate('({height:ta.clientHeight,top:historyBox.scrollTop})');
    await evaluate(`window.measurements=[]; window.nativeScrollHeight=Object.getOwnPropertyDescriptor(Element.prototype,'scrollHeight').get;
      for (const field of document.querySelectorAll('.hp-chat textarea')) {
        Object.defineProperty(field,'scrollHeight',{configurable:true,get(){const height=nativeScrollHeight.call(this); measurements.push({height:historyBox.clientHeight,top:historyBox.scrollTop}); return height;}});
      } true`);
    await evaluate('input(ta.value + "字")');
    const measurements = await evaluate('measurements');
    const after = await evaluate('({height:ta.clientHeight,top:historyBox.scrollTop})');
    const historyHeight = await evaluate('historyBox.clientHeight');
    assert.ok(measurements.length > 0, 'record real textarea layout measurements');
    assert.ok(measurements.every(sample => sample.height === historyHeight), `same-line measurement must not temporarily resize conversation: ${JSON.stringify(measurements)} vs ${historyHeight}`);
    assert.equal(after.height, before.height, 'same-line typing must not resize composer');
    assert.equal(after.top, before.top, 'same-line typing must not move conversation scroll position');
    console.log(`PASS ${width}px same-line typing preserves conversation position`);
    await call('Input.insertText', { text:'中文输入🙂' }, sessionId);
    await evaluate('frames()');
    assert.equal(await evaluate('historyBox.scrollTop'), before.top, 'native typing must preserve conversation position');
    assert.equal(await evaluate('document.activeElement === ta'), true, 'measurement must not steal focus');
    assert.equal(await evaluate('ta.selectionStart === ta.value.length'), true, 'native typing preserves caret');
    await evaluate('input("第一行\\n第二行\\n第三行\\n第四行")');
    assert.equal(await evaluate('ta.clientHeight'), before.height + 24, 'newline grows by exactly one row');
    await evaluate('input("单行")');
    assert.equal(await evaluate('ta.clientHeight'), 36, 'deleting lines shrinks to one row');
    await evaluate('input("中文🙂".repeat(100))');
    assert.ok(await evaluate('ta.clientHeight > 36'), 'soft wrapping grows the composer');
    await evaluate('input(Array.from({length:20},()=>"长内容").join("\\n"))');
    assert.equal(await evaluate('ta.clientHeight'), 240, 'long content stays capped');
    assert.equal(await evaluate('getComputedStyle(ta).overflowY'), 'auto', 'capped content scrolls internally');
    await evaluate('input("")');
    assert.equal(await evaluate('ta.clientHeight'), 36, 'clearing content returns to one row');
    assert.equal(await evaluate('getComputedStyle(ta).overflowY'), 'hidden', 'clearing removes internal scrolling');
    await evaluate(`ta.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); input('中文组字中')`);
    await evaluate(`ta.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,isComposing:true,data:'文'})); frames()`);
    assert.equal(await evaluate('document.activeElement === ta'), true, 'composition keeps focus on real input');
    await evaluate(`ta.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中文组字中'})); frames()`);
    console.log(`PASS ${width}px native typing/caret, newline, deletion, wrapping, height cap, clear and composition`);
    await call('Target.disposeBrowserContext', { browserContextId });
  }
} catch (error) { failure = error; }
finally {
  socket?.close();
  for (const p of pending.values()) clearTimeout(p.timer);
  if (browser) { browser.kill('SIGTERM'); await new Promise(resolve => browser.exitCode !== null ? resolve() : browser.once('exit', resolve)); }
  await server.close(); await rm(owned, { recursive:true, force:true, maxRetries:5, retryDelay:100 });
}
if (failure) throw failure;
