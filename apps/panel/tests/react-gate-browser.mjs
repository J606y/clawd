import assert from 'node:assert/strict';
import path from 'node:path';
import { withPage, waitFor } from './fixtures/cdp-harness.mjs';

const root = path.resolve(import.meta.dirname, '..');
const name = process.argv[2];
if (!name) throw new Error('missing case');

function rows(n, start = 1) {
  return Array.from({ length: n }, (_, index) => {
    const id = start + index;
    return { id, role: id % 2 ? 'user' : 'assistant', content: `row ${id}`, timestamp: id, ...(id % 2 ? {} : { reasoning: `thought ${id}` }) };
  });
}

const reducedMotion = `(() => {
  const original = window.matchMedia.bind(window);
  window.matchMedia = (query) => {
    if (String(query).includes('prefers-reduced-motion')) {
      return { matches: true, media: String(query), addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; } };
    }
    return original(query);
  };
})();`;

async function open(gate, run, extraInit = '') {
  await withPage(root, '/tests/fixtures/react-gate.html', async (page) => {
    await waitFor(page.evaluate, 'window.fixtureReady === true', 'fixture ready', 20000);
    if (gate.mode !== 'list') {
      await waitFor(page.evaluate, 'document.body.innerText.length > 0', 'chat painted', 15000);
    } else {
      await waitFor(page.evaluate, 'typeof window.__capture === "function"', 'list capture', 15000);
    }
    await run(page);
  }, `${extraInit};window.__gate = ${JSON.stringify(gate)};`);
}

async function text(page) {
  return page.evaluate('document.body.innerText');
}

async function messageRequests(page) {
  return page.evaluate(`window.__requests.filter((item) => item.url.includes('/messages') && !item.url.includes('message-origins')).map((item) => item.url)`);
}

const cases = {
  async 'snapshot-coalesce'() {
    await open({ sessionId: 'burst', messages: { burst: rows(4) } }, async (page) => {
      const full = 'Synthetic burst '.repeat(20) + ' complete tail';
      await page.evaluate(`window.__installRun('burst', { text: 'Synthetic burst ', userText: 'row 1' }); window.__patchRun('burst', { text: ${JSON.stringify(full)} });`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const shown = await page.evaluate(`(() => {
        const live = document.querySelector('.mount-row');
        return live ? live.innerText : '';
      })()`);
      assert.match(shown, /complete tail/, 'coalesced burst must commit the latest source, not a stale prefix');
      assert.doesNotMatch(shown, /SYNTHETIC_PRIVATE_REASONING/);
      const before = shown;
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const after = await page.evaluate(`document.querySelector('.mount-row')?.innerText || ''`);
      assert.equal(after, before, 'no display-only source frame after the commit');
    }, reducedMotion);
  },

  async 'snapshot-monotonic'() {
    const previous = 'Already visible synthetic answer';
    await open({ sessionId: 'mono', messages: { mono: rows(2) } }, async (page) => {
      await page.evaluate(`window.__installRun('mono', { text: ${JSON.stringify(previous)}, userText: 'row 1' })`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const scroll = await page.evaluate(`(() => {
        const el = document.querySelector('.scroll');
        if (!el) return false;
        el.scrollTop = 0;
        el.dispatchEvent(new Event('scroll', { bubbles: true }));
        return true;
      })()`);
      assert.equal(scroll, true);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const first = await page.evaluate(`document.querySelector('.mount-row')?.innerText || ''`);
      assert.match(first, /Already visible synthetic answer/);
      const next = `${previous} followed by new output`;
      await page.evaluate(`window.__patchRun('mono', { text: ${JSON.stringify(next)} })`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const second = await page.evaluate(`document.querySelector('.mount-row')?.innerText || ''`);
      assert.ok(second.includes(previous), 'visible snapshot must stay monotonic');
      assert.match(second, /followed by new output/);
    }, reducedMotion);
  },

  async 'snapshot-hidden'() {
    await open({ sessionId: 'hide', messages: { hide: rows(2) } }, async (page) => {
      await page.evaluate(`window.__installRun('hide', { text: 'before', userText: 'row 1' })`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      await page.evaluate(`document.getElementById('chat-slot').setAttribute('hidden', '')`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      await page.evaluate(`window.__patchRun('hide', { text: 'after hidden' })`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const hidden = await page.evaluate(`document.querySelector('.mount-row')?.innerText || ''`);
      assert.match(hidden, /before/);
      assert.doesNotMatch(hidden, /after hidden/, 'hidden slot must not publish the next source frame');
      await page.evaluate(`document.getElementById('chat-slot').removeAttribute('hidden')`);
      await page.evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const visible = await page.evaluate(`document.querySelector('.mount-row')?.innerText || ''`);
      assert.match(visible, /after hidden/, 'visible chat still commits the latest source');
    }, reducedMotion);
  },

  async 'scroll-parity'() {
    await open({ mode: 'list', count: 10, itemHeight: 50, itemGap: 10, stick: false, special: 'a"b\'c [test]=1 :hover' }, async (page) => {
      const result = await page.evaluate(`(() => {
        const el = document.querySelector('.scroll');
        const nodes = [...el.querySelectorAll('[data-history-source]')];
        const linear = (stick) => {
          if (stick) return null;
          const bounds = el.getBoundingClientRect();
          for (const node of nodes) {
            const rect = node.getBoundingClientRect();
            if (rect.bottom > bounds.top && rect.top < bounds.bottom) {
              return { key: node.dataset.historySource, offset: rect.top - bounds.top };
            }
          }
          return null;
        };
        const read = () => window.__capture();
        el.scrollTop = 0;
        const top = { capture: read(), linear: linear(false) };
        el.scrollTop = 180;
        const middle = { capture: read(), linear: linear(false) };
        el.scrollTop = el.scrollHeight;
        const end = { capture: read(), linear: linear(false) };
        const special = nodes.find((node) => node.dataset.historySource.includes('test'));
        return { top, middle, end, special: special?.dataset.historySource || '', empty: nodes.length };
      })()`);
      assert.deepEqual(result.top.capture, result.top.linear);
      assert.deepEqual(result.middle.capture, result.middle.linear);
      assert.equal(result.middle.capture.key, result.middle.linear.key);
      assert.equal(result.special, 'a"b\'c [test]=1 :hover');
      assert.ok(result.empty > 0);
    });
  },

  async 'scroll-rects'() {
    await open({ mode: 'list', count: 1000, itemHeight: 40, itemGap: 10, stick: false }, async (page) => {
      const result = await page.evaluate(`(() => {
        const el = document.querySelector('.scroll');
        el.scrollTop = 25000;
        const proto = Element.prototype.getBoundingClientRect;
        let reads = 0;
        Element.prototype.getBoundingClientRect = function counted() {
          if (this.dataset && this.dataset.historySource) reads += 1;
          return proto.call(this);
        };
        const capture = window.__capture();
        Element.prototype.getBoundingClientRect = proto;
        const bounds = el.getBoundingClientRect();
        let expected = null;
        for (const node of el.querySelectorAll('[data-history-source]')) {
          const rect = node.getBoundingClientRect();
          if (rect.bottom > bounds.top && rect.top < bounds.bottom) {
            expected = { key: node.dataset.historySource, offset: rect.top - bounds.top };
            break;
          }
        }
        return { reads, capture, expected };
      })()`);
      assert.deepEqual(result.capture, result.expected);
      assert.ok(result.reads <= 15, `rectReads (${result.reads}) must be <= 15`);
    });
  },

  async 'history-within-cap'() {
    const all = rows(600);
    await open({ sessionId: 'wide', messages: { wide: all } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 600")', 'latest row', 15000);
      const before = (await messageRequests(page)).length;
      await page.evaluate(`document.querySelector('.earlier-btn')?.click()`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 401")', 'expanded row', 15000);
      const loaded = await page.evaluate(`document.querySelectorAll('[data-history-source]').length`);
      assert.ok(loaded >= 2, 'expanded window is on screen');
      await page.evaluate('window.__requests = []');
      await page.evaluate(`window.__finish('wide')`);
      await waitFor(page.evaluate, `window.__requests.some((item) => item.url.includes('limit=400') || item.url.includes('limit=500') || item.url.includes('/messages'))`, 'reload', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 300))');
      const calls = await messageRequests(page);
      const body = await text(page);
      assert.match(body, /row 600/);
      assert.match(body, /row 401/, 'refresh keeps the expanded window that fits in one page');
      assert.ok(calls.some((url) => /offset=0/.test(url) && /limit=(400|500)/.test(url)), calls.join('\n'));
    });
  },

  async 'history-failure'() {
    await open({
      sessionId: 'failure',
      messages: { failure: [{ id: 2, role: 'assistant', content: 'kept reply', timestamp: 2, reasoning: 'kept thought' }, { id: 3, role: 'user', content: 'kept question', timestamp: 3 }] },
    }, async (page) => {
      await page.evaluate(`window.__commentary = { "2": ["saved note"] }`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("kept reply")', 'history', 15000);
      await page.evaluate('window.__failMessages = true; window.__requests = []');
      await page.evaluate(`window.__finish('failure')`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("历史重新加载失败")', 'failure notice', 15000);
      const body = await text(page);
      assert.match(body, /kept reply/);
      assert.match(body, /kept question/);
      assert.match(body, /历史重新加载失败/);
    });
  },

  async 'history-switch'() {
    await open({
      sessionId: 'old',
      sessions: [{ id: 'old', title: 'old' }, { id: 'new', title: 'new' }],
      messages: {
        old: [{ id: 1, role: 'user', content: 'old session text', timestamp: 1 }],
        new: [{ id: 2, role: 'user', content: 'new session text', timestamp: 2 }],
      },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("old session text")', 'old history', 15000);
      await page.evaluate('window.__holdMessages = true');
      await page.evaluate(`window.__finish('old')`);
      await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'held reload', 15000);
      await page.evaluate('window.__oldReload = window.__heldMessages; window.__holdMessages = false; window.__heldMessages = null');
      await page.evaluate(`window.__select('new')`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("new session text")', 'new history', 15000);
      await page.evaluate(`window.__oldReload.resolve(200, { data: [{ id: 9, role: 'user', content: 'poison page', timestamp: 9 }], pagination: { returned: 1 } })`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 250))');
      const body = await text(page);
      assert.match(body, /new session text/);
      assert.doesNotMatch(body, /poison page/, 'late old-session refresh must not publish into the new view');
    });
  },

  async 'history-late-earlier'() {
    const all = rows(420);
    await open({ sessionId: 'late', messages: { late: all } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 420")', 'latest', 15000);
      await page.evaluate('window.__holdMessages = true');
      await page.evaluate(`document.querySelector('.earlier-btn')?.click()`);
      await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'held earlier', 15000);
      const held = await page.evaluate('window.__heldMessages.url');
      assert.match(held, /offset=200/);
      await page.evaluate('window.__holdMessages = false');
      await page.evaluate(`window.__finish('late')`);
      await waitFor(page.evaluate, `window.__requests.some((item) => item.url.includes('offset=0') && item.url.includes('limit='))`, 'reload started', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 400))');
      const before = await page.evaluate(`document.body.innerText.includes('row 1')`);
      await page.evaluate(`window.__heldMessages.resolve(200, { data: [{ id: 1, role: 'user', content: 'POISON_EARLIER_ROW', timestamp: 1 }], pagination: { returned: 1 } })`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 800))');
      const body = await text(page);
      assert.equal(before, false);
      assert.doesNotMatch(body, /POISON_EARLIER_ROW/, 'a late earlier page must not append into the replaced latest window');
      assert.match(body, /row 420/);
    });
  },
};

const run = cases[name];
if (!run) throw new Error(`unknown case ${name}`);
await run();
console.log(`ok ${name}`);
