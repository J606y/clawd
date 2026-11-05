import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/.hermes/cache/scratch', 'glass-browser-'));
const evidence = process.env.GLASS_EVIDENCE_DIR ? path.resolve(process.env.GLASS_EVIDENCE_DIR) : null;
const failures = [];
const samples = [];
const pending = new Map();
let sequence = 0;
let browser, socket, server, failure;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const surfaces = {
  effort: ['.effort-picker .panel', '.effort-picker .trigger', '14px'],
  model: ['.model-picker .menu', '.model-picker .trigger', '14px'],
  composer: ['.box', null, '14px'],
  jump: ['.jump', null, '50%'],
  session: ['.session-list li .menu', '.more-btn', '14px'],
  context: ['.context-ring .pop', '.context-ring .wrap', '14px'],
};
function check(label, action) {
  try { action(); } catch (error) { failures.push(`${label}: ${error.message}`); }
}
try {
  if (evidence) await mkdir(evidence, { recursive: true });
  server = await createServer({ configFile: path.join(root, 'vite.config.ts'), root, cacheDir: path.join(owned, 'vite'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  await server.listen();
  browser = spawn('/usr/bin/chromium', ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--remote-debugging-port=0', `--user-data-dir=${path.join(owned, 'profile')}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const endpoint = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Chromium startup timeout')), 15000);
    browser.stderr.on('data', chunk => {
      output = (output + chunk).slice(-8192);
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    browser.once('error', error => { clearTimeout(timer); reject(error); });
    browser.once('exit', code => { clearTimeout(timer); reject(new Error(`Chromium exited during startup: ${code}`)); });
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
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const cdp = (method, params) => call(method, params, sessionId);
  const evaluate = async expression => {
    const result = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const frames = () => evaluate('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))');
  const wait = async expression => {
    for (let attempt = 0; attempt < 120; attempt++) {
      if (await evaluate(expression)) return;
      await delay(50);
    }
    const diagnostic = await evaluate('({ready:window.fixtureReady,errors:window.fixtureErrors,unmatched:window.unmatched,body:document.body.innerText.slice(0,1000)})');
    throw new Error(`Fixture wait failed: ${expression} ${JSON.stringify(diagnostic)}`);
  };
  const click = async selector => {
    const point = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) throw Error('Missing click target'); const r=e.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); if (!(e===hit || e.contains(hit))) throw Error('Click target occluded: '+${JSON.stringify(selector)}); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
    await frames();
  };
  const show = async name => {
    await evaluate(`document.body.dataset.stage=${JSON.stringify(name)}`);
    await frames();
    const [surface, trigger] = surfaces[name];
    const prefix = `.fixture-${name} `;
    if (trigger && !await evaluate(`!!document.querySelector(${JSON.stringify(prefix + surface)})`)) await click(prefix + trigger);
    await wait(`!!document.querySelector(${JSON.stringify(prefix + surface)})`);
    await evaluate(`Promise.all(document.querySelector(${JSON.stringify('.fixture-' + name)}).getAnimations({subtree:true}).filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished))`);
    return prefix + surface;
  };
  const measure = selector => evaluate(`(() => {
    const e=document.querySelector(${JSON.stringify(selector)}), s=getComputedStyle(e), r=e.getBoundingClientRect();
    const pseudo=which=>{ const p=getComputedStyle(e,which); return {content:p.content,background:p.backgroundImage,mask:p.maskImage,events:p.pointerEvents,display:p.display,padding:p.padding,animation:p.animationName}; };
    return {background:s.backgroundImage,color:s.backgroundColor,text:s.color,blur:s.backdropFilter,shadow:s.boxShadow,radius:s.borderRadius,
      rim:pseudo('::before'),bevel:pseudo('::after'),opacity:s.opacity,
      rect:{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom},
      solid:getComputedStyle(document.documentElement).getPropertyValue('--bg-elevated').trim()};
  })()`);
  await cdp('Page.navigate', { url: `http://127.0.0.1:${server.httpServer.address().port}/tests/liquid-glass-fixture.html` });
  await wait('window.fixtureReady === true && !!document.querySelector(".session-list .more-btn")');

  for (const width of [320, 390, 1280]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height: 600, deviceScaleFactor: 1, mobile: width < 500 });
    const light = new Map();
    for (const theme of ['light', 'dark']) {
      await evaluate(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
      await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: theme === 'light' ? 'dark' : 'light' }] });
      for (const [name, [, , radius]] of Object.entries(surfaces)) {
        const selector = await show(name);
        const sample = await measure(selector);
        if (name === 'context') sample.ink = await evaluate(`(() => {
          const pop = document.querySelector(${JSON.stringify(selector)});
          const strong = pop.querySelector('.row b');
          const muted = pop.querySelector('h4');
          return { strong: getComputedStyle(strong).color, muted: getComputedStyle(muted).color };
        })()`);
        samples.push({ width, theme, name, ...sample });
        const label = `${width}/${theme}/${name}`;
        check(label, () => {
          assert.equal(sample.blur, name === 'context' ? 'blur(10px) saturate(1.7)' : 'blur(3px) saturate(1.7)', 'context glass is stronger; other glass stays light');
          assert.equal(sample.color, name !== 'context' ? 'rgba(0, 0, 0, 0)' : theme === 'dark' ? 'rgba(0, 0, 0, 0)' : 'rgba(255, 255, 255, 0.72)', 'dark context has no white wash; light context keeps one for black text');
          if (name === 'context') {
            assert.equal(sample.text, theme === 'dark' ? 'rgb(161, 161, 166)' : 'rgb(0, 0, 0)', 'dark context text follows the theme; light context stays black');
            assert.equal(sample.ink.strong, theme === 'dark' ? 'rgb(245, 245, 247)' : 'rgb(0, 0, 0)', 'dark values are light; light values stay black');
            assert.equal(sample.ink.muted, theme === 'dark' ? 'rgb(110, 110, 115)' : 'rgb(0, 0, 0)', 'dark labels follow the muted theme color');
          }
          assert.equal(sample.background, 'none', 'transparent glass has no gradient or image');
          assert.equal(sample.radius, radius, 'existing corner radius preserved');
          assert.equal(sample.opacity, '1', 'text is not made translucent');
          assert.equal(sample.rim.content, 'none', 'glass has no gradient rim');
          assert.equal(sample.bevel.content, 'none', 'glass has no gradient bevel');
          assert.equal(sample.bevel.animation, 'none', 'glass adds no perpetual repaint animation');
          assert.ok(sample.rect.x >= -0.5 && sample.rect.right <= width + 0.5, 'surface stays in viewport');
        });
        if (theme === 'light') light.set(name, sample);
        else check(`${label}/theme`, () => {
          assert.equal(sample.background, light.get(name).background, 'glass fill does not change with theme');
          if (name !== 'context') assert.notEqual(sample.text, light.get(name).text, 'text follows manual theme');
          else {
            assert.equal(sample.color, 'rgba(0, 0, 0, 0)', 'dark context is not a white wash');
            assert.notEqual(sample.text, light.get(name).text, 'dark context text is not the light-theme black');
          }
          assert.notEqual(sample.shadow, light.get(name).shadow, 'edge follows manual theme');
          assert.deepEqual(sample.rect, light.get(name).rect, 'theme switch does not move controls');
        });
        if (evidence && width === 390) {
          const { data } = await cdp('Page.captureScreenshot', { format: 'png' });
          await writeFile(path.join(evidence, `${theme}-${name}.png`), Buffer.from(data, 'base64'));
        }
      }
    }
    await evaluate('delete document.documentElement.dataset.theme');
    await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
    for (const name of Object.keys(surfaces)) {
      const sample = await measure(await show(name));
      const dark = samples.find(s => s.width === width && s.name === name && s.theme === 'dark');
      check(`${width}/system/${name}`, () => {
        assert.equal(sample.background, dark.background);
        assert.equal(sample.text, dark.text);
        assert.equal(sample.shadow, dark.shadow);
      });
    }
    for (const preference of ['prefers-reduced-transparency', 'prefers-contrast', 'forced-colors']) {
      const value = preference === 'prefers-contrast' ? 'more' : preference === 'forced-colors' ? 'active' : 'reduce';
      await cdp('Emulation.setEmulatedMedia', { features: [{ name: preference, value }] });
      for (const theme of ['light', 'dark']) {
        await evaluate(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
        const forcedCanvas = preference === 'forced-colors' ? await evaluate(`(() => {
          const probe = document.createElement('div');
          probe.style.cssText = 'position:fixed;visibility:hidden;background-color:Canvas';
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        })()`) : null;
        for (const name of Object.keys(surfaces)) {
          const sample = await measure(await show(name));
          check(`${width}/${preference}/${theme}/${name}`, () => {
            assert.equal(sample.blur, 'none', 'accessibility removes background distortion');
            assert.equal(sample.background, 'none', 'accessibility removes translucent fill');
            assert.equal(sample.color, preference === 'forced-colors' ? forcedCanvas : theme === 'light' ? 'rgb(255, 255, 255)' : 'rgb(31, 31, 34)', 'fallback uses the opaque theme or forced system surface');
            assert.equal(sample.rim.content, 'none', 'no decorative rim');
            assert.equal(sample.bevel.content, 'none', 'no decorative bevel');
          });
        }
      }
    }
    await cdp('Emulation.setEmulatedMedia', { features: [] });
    await show('model');
    const scroll = await evaluate(`(() => { const e=document.querySelector('.fixture-model .menu'), s=e.querySelector('.sheet'); const top=e.getBoundingClientRect().top; s.scrollTop=s.scrollHeight; return {scrolled:s.scrollTop>0,top,after:e.getBoundingClientRect().top}; })()`);
    assert.equal(scroll.scrolled, true, 'model list actually scrolls');
    assert.equal(scroll.top, scroll.after, 'glass border stays fixed while list scrolls');
    const lastSelected = await evaluate('document.querySelector(".fixture-model .menu button:last-child").getAttribute("aria-checked") === "true"');
    await click(lastSelected ? '.fixture-model .menu button:nth-last-child(2)' : '.fixture-model .menu button:last-child');
    assert.equal(await evaluate('window.actions.at(-1)?.[0]'), 'model', 'real model selection fires');
    await show('effort');
    await evaluate('document.querySelector(".fixture-effort input").focus()');
    await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37 });
    await cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37 });
    await frames();
    assert.equal(await evaluate('window.actions.at(-1)?.[0]'), 'effort', 'real slider changes');
    await show('composer');
    await click('.fixture-composer .box textarea');
    await cdp('Input.insertText', { text: '第一行\n第二行\n第三行' });
    await frames();
    assert.equal(await evaluate('document.querySelector(".fixture-composer .box textarea").clientHeight'), 84, 'real multiline measurement preserves three rows');
    assert.equal(await evaluate('document.querySelector(".fixture-composer textarea.measure").parentElement.className'), 'composer', 'fixed measuring textarea stays outside filtered box');
    await evaluate(`(() => { const e=document.querySelector('.fixture-composer .box textarea'); e.select(); })()`);
    await cdp('Input.insertText', { text: '单行' });
    await frames();
    assert.equal(await evaluate('document.querySelector(".fixture-composer .box textarea").clientHeight'), 36, 'single line returns to original height');
    await show('jump');
    await click('.fixture-jump .jump');
    assert.equal(await evaluate('window.actions.at(-1)?.[0]'), 'jump');
    await show('context');
    const context = await evaluate(`(() => {
      const root = document.querySelector('.fixture-context .context-ring');
      const pop = root.querySelector('.pop');
      const text = pop.textContent;
      return { expanded: root.querySelector('.wrap').getAttribute('aria-expanded'),
        title: pop.querySelector('.row b')?.textContent,
        sections: pop.querySelectorAll('.sec').length,
        legend: pop.querySelectorAll('.legend span').length,
        hasModel: text.includes('fixture-model'),
        hasLastCall: text.includes('最近一次调用'), hasTotal: text.includes('本对话累计') };
    })()`);
    assert.deepEqual(context, { expanded: 'true', title: '上下文窗口', sections: 2, legend: 2, hasModel: true, hasLastCall: true, hasTotal: true }, 'context data and sections remain intact');
    await click('.fixture-context .context-ring .wrap');
    assert.equal(await evaluate('!!document.querySelector(".fixture-context .context-ring .pop")'), false, 'context closes from its trigger');
    await click('.fixture-context .context-ring .wrap');
    await click('.fixture-context .fixture-heading');
    assert.equal(await evaluate('!!document.querySelector(".fixture-context .context-ring .pop")'), false, 'context closes on outside click');
    await click('.fixture-context .context-ring .wrap');
    await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await frames();
    assert.equal(await evaluate('!!document.querySelector(".fixture-context .context-ring .pop")'), false, 'context closes on Escape');
    await show('session');
    await click('.fixture-session .menu button:nth-child(2)');
    assert.equal(await evaluate('!!document.querySelector(".fixture-session input.rename")'), true, 'session rename action remains clickable');
    await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await frames();
    console.log(`PASS interactions ${width}px: menus, scrolling, slider, composer sizing and jump`);
  }
  await evaluate('document.body.dataset.stage="control"');
  await frames();
  await evaluate('document.querySelector(".fixture-control .msg-editor .trigger").focus()');
  await click('.fixture-control .msg-editor .trigger');
  const control = await measure('.fixture-control .msg-editor .menu');
  assert.equal(control.blur, 'none', 'unrequested message menu is not glass');
  assert.equal(control.background, 'none', 'unrequested menu keeps its original background image');
  assert.equal(control.color, 'rgb(31, 31, 34)', 'unrequested menu retains its dark opaque surface');
  assert.equal(control.bevel.content, 'none', 'unrequested message menu has no bevel');
  assert.deepEqual(await evaluate('window.unmatched'), [], 'fixture makes no unexpected API calls');
  assert.deepEqual(await evaluate('window.fixtureErrors'), [], 'real components have no uncaught browser errors');
  if (evidence) await writeFile(path.join(evidence, 'computed.json'), JSON.stringify({ root, samples, failures }, null, 2));
  assert.equal(failures.length, 0, failures.join('\n'));
  console.log(`PASS ${samples.length} normal material samples, system themes, accessibility and negative control`);
} catch (error) { failure = error; }
finally {
  if (evidence) await writeFile(path.join(evidence, 'result.json'), JSON.stringify({ root, failures, error: failure?.message || null }, null, 2));
  for (const job of pending.values()) clearTimeout(job.timer);
  socket?.close();
  if (browser && browser.exitCode === null) { const exited = once(browser, 'exit'); browser.kill('SIGTERM'); await exited; }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
if (failure) throw failure;
