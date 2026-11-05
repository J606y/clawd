import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'fetch'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
Object.defineProperty(globalThis, 'document', { configurable: true, value: { baseURI: 'https://panel.example.test/' } });
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { api } = await server.ssrLoadModule('/src/lib/api.ts');
  await test('chat history retains original user messages after model-context compaction', async () => {
    const first = { id: 1, role: 'user', content: '检查面板', timestamp: 1 };
    const compacted = { id: 2, role: 'user', content: '先打磨实时画面', timestamp: 2 };
    const latest = { id: 3, role: 'assistant', content: '已收到', timestamp: 3 };
    const history = [first, compacted, latest];
    globalThis.fetch = async (input) => {
      const url = new URL(input);
      const data = url.searchParams.get('include_compacted') === 'true' ? history : [first, latest];
      return new Response(JSON.stringify({ data, pagination: { returned: data.length } }), { status: 200 });
    };
    const page = await api.messages('history-sample', { limit: 100, offset: 0 });
    assert.equal(page.messages.some((message) => message.id === compacted.id), true, 'the saved compacted user message must remain in display history');
    assert.deepEqual(page.messages, history);
  });
  await test('older pages use the same complete history and preserve order and pagination', async () => {
    const history = Array.from({ length: 5 }, (_, index) => ({ id: index + 1, role: 'user', content: `原话 ${index + 1}`, timestamp: index + 1 }));
    const requests = [];
    globalThis.fetch = async (input, init) => {
      const url = new URL(input);
      requests.push(url);
      assert.equal(url.searchParams.get('include_compacted'), 'true');
      assert.equal(url.searchParams.get('order'), 'latest');
      assert.equal(init.credentials, 'same-origin');
      assert.equal(init.body, undefined, 'display history must not submit a new task');
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset'));
      const end = Math.max(0, history.length - offset);
      const data = history.slice(Math.max(0, end - limit), end);
      return new Response(JSON.stringify({ data, pagination: { returned: data.length } }), { status: 200 });
    };
    const latest = await api.messages('history-sample', { limit: 2, offset: 0 });
    const earlier = await api.messages('history-sample', { limit: 2, offset: latest.messages.length });
    const oldest = await api.messages('history-sample', { limit: 2, offset: latest.messages.length + earlier.messages.length });
    assert.deepEqual([...oldest.messages, ...earlier.messages, ...latest.messages], history);
    assert.deepEqual([latest.hasMore, earlier.hasMore, oldest.hasMore], [true, true, false]);
    assert.equal(requests.length, 3);
  });
  await test('complete-history requests retain URL encoding and authorization failures', async () => {
    const rejected = [];
    const onExpired = () => rejected.push('expired');
    browser.addEventListener('hp-auth-expired', onExpired);
    try {
      globalThis.fetch = async (input) => {
        const url = new URL(input);
        assert.equal(url.pathname, '/panel-api/api/sessions/session%2Fwith%20space/messages');
        assert.equal(url.searchParams.get('include_compacted'), 'true');
        return new Response(JSON.stringify({ error: 'not authorized' }), { status: 401 });
      };
      await assert.rejects(() => api.messages('session/with space', { limit: 100, offset: 0 }), (error) => error.kind === 'unauthorized' && error.status === 401);
      assert.deepEqual(rejected, ['expired']);
    } finally {
      browser.removeEventListener('hp-auth-expired', onExpired);
    }
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
