import test from 'node:test';
import assert from 'node:assert/strict';
import * as tools from '../src/lib/tools.ts';
const step = (name, args = null) => ({ kind: 'tool', id: name, name, args, preview: null, status: 'done', output: null });

test('browser task recognition uses real tool identities, including deferred calls, not prose', () => {
  assert.equal(typeof tools.isBrowserStep, 'function', 'shared browser recognition exists');
  for (const name of ['browser_exec', 'functions.browser_exec', 'mcp__chrome_devtools__navigate_page', 'mcp__chrome_devtools__take_snapshot']) {
    assert.equal(tools.isBrowserStep(step(name)), true, name);
  }
  for (const name of ['web_search', 'web_extract', 'browser_vault_list', 'search_chrome_news', 'chrome_fake', 'terminal']) {
    assert.equal(tools.isBrowserStep(step(name, { command: 'chrome browser' })), false, name);
  }
  assert.equal(tools.isBrowserStep({ kind: 'note', text: 'open browser' }), false);
  assert.equal(tools.isBrowserStep(step('tool_call', { calls: [{ name: 'mcp__chrome_devtools__click', arguments: {} }] })), true);
  assert.equal(tools.isBrowserStep(step('multi_tool_use.parallel', { tool_uses: [{ recipient_name: 'functions.browser_exec', parameters: {} }] })), true);
  assert.equal(tools.isBrowserStep(step('tool_call', { calls: [{ name: 'web_search', arguments: { query: 'chrome' } }] })), false);
});

test('refresh state restores only a bounded spectator view, never a controller credential', async () => {
  const view = await import('../src/lib/live-view.ts').catch(error => {
    if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
    throw error;
  });
  assert.equal(typeof view.saveLiveView, 'function', 'spectator persistence exists');
  const items = new Map();
  const storage = { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value), removeItem: key => items.delete(key) };
  const state = { sessionId: 'session-test-1', sessionTitle: '浏览器任务', sessionSource: 'api_server' };
  assert.equal(view.saveLiveView(state, storage), true);
  assert.deepEqual(view.loadLiveView(storage), state);
  const raw = JSON.parse([...items.values()][0]);
  assert.deepEqual(Object.keys(raw).sort(), ['sessionId', 'sessionSource', 'sessionTitle', 'version']);
  assert.equal('clientId' in raw || 'control' in raw, false);
  assert.equal(view.saveLiveView(null, storage), true);
  assert.equal(view.loadLiveView(storage), null);
  items.set(view.LIVE_VIEW_KEY, '{broken');
  assert.equal(view.loadLiveView(storage), null);
  items.set(view.LIVE_VIEW_KEY, JSON.stringify({ ...state, version: 1, sessionId: '../unsafe' }));
  assert.equal(view.loadLiveView(storage), null);
  assert.equal(view.saveLiveView(state, { setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } }), false);
  assert.equal(view.loadLiveView({ getItem() { throw new Error('blocked'); } }), null);
});
