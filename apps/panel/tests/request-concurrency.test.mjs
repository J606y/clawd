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
  const { request, ApiError } = await server.ssrLoadModule('/src/lib/api.ts');
  const { loadCommentary } = await server.ssrLoadModule('/src/lib/commentary.ts');

  await test('request rethrows external signal abort without masking as timeout', async () => {
    const ctrl = new AbortController();
    globalThis.fetch = async (_url, init) => {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const err = new DOMException('The operation was aborted.', 'AbortError');
          reject(err);
        });
      });
    };

    const promise = request('api/test', { signal: ctrl.signal }, 10000);
    ctrl.abort();

    await assert.rejects(
      promise,
      (err) => {
        assert.equal(err instanceof ApiError, false, 'external abort must NOT be wrapped in ApiError');
        assert.equal(err instanceof DOMException, true, 'external abort must remain a DOMException');
        assert.equal(err.name, 'AbortError');
        return true;
      },
      'should throw AbortError on external cancellation'
    );
  });

  await test('request wraps internal timeout into ApiError("network", 0, "请求超时")', async () => {
    globalThis.fetch = async (_url, init) => {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        });
      });
    };

    const promise = request('api/test', {}, 50);

    await assert.rejects(
      promise,
      (err) => {
        assert.equal(err instanceof ApiError, true, 'timeout must be an ApiError');
        assert.equal(err.kind, 'network');
        assert.equal(err.message, '请求超时');
        return true;
      },
      'should report timeout when timer expires'
    );
  });

  await test('loadCommentary rethrows when caller signal is aborted and does not poison none cache', async () => {
    const ctrl = new AbortController();
    globalThis.fetch = async (_url, init) => {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        });
      });
    };

    const messages = [
      { id: 9999, role: 'assistant', content: 'hello', reasoning: 'some reasoning', timestamp: 1 }
    ];

    const promise = loadCommentary(messages, ctrl.signal);
    ctrl.abort();

    await assert.rejects(
      promise,
      (err) => {
        assert.equal(err instanceof DOMException, true);
        assert.equal(err.name, 'AbortError');
        return true;
      }
    );
  });

  await test('only 401 means auth expired; 403 is a business rejection that keeps the session', async () => {
    const { AUTH_EXPIRED_EVENT } = await server.ssrLoadModule('/src/lib/api.ts');
    let expired = 0;
    const onExpired = () => { expired++; };
    browser.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    try {
      for (const [status, kind, fired] of [[403, 'server', 0], [401, 'unauthorized', 1]]) {
        globalThis.fetch = async () => new Response(JSON.stringify({ error: { message: 'nope', code: 'secret_key' } }), { status });
        await assert.rejects(request('api/test'), (err) => {
          assert.equal(err instanceof ApiError, true);
          assert.equal(err.kind, kind);
          assert.equal(err.status, status);
          return true;
        });
        assert.equal(expired, fired, `status ${status}`);
      }
    } finally {
      browser.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
    }
  });
  await test('background process query treats an unexpected shape as a failure, not as "nothing running"', async () => {
    const { sidecar } = await server.ssrLoadModule('/src/lib/sidecar.ts');
    for (const body of [{}, { processes: null }, { error: 'proxy page' }]) {
      globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 200 });
      await assert.rejects(sidecar.sessionBackground('api_1_parent'), (err) => err instanceof ApiError && /格式异常/.test(err.message));
    }
    globalThis.fetch = async () => new Response(JSON.stringify({ processes: [{ id: 'p', command: 'sleep 9', startedAt: null }] }), { status: 200 });
    assert.deepEqual(await sidecar.sessionBackground('api_1_parent'), [{ id: 'p', command: 'sleep 9', startedAt: null }]);
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
