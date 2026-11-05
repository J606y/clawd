import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const keys = ['document', 'window', 'fetch', 'navigator', 'localStorage'];
const globals = Object.fromEntries(keys.map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));

const delay = ms => new Promise(r => setTimeout(r, ms));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

const storage = new Map();
const windowObj = Object.assign(new EventTarget(), {
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  localStorage: {
    getItem: k => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, v),
    removeItem: k => storage.delete(k),
  },
});

let server;
let serverPort = 0;
let requestCount = 0;
let requests = [];

let slowBodyStarted;
let slowBodyRelease;
let slowErrorStarted;
let slowErrorRelease;

server = http.createServer(async (req, res) => {
  requestCount++;
  const reqRecord = { url: req.url, method: req.method, headers: req.headers };
  requests.push(reqRecord);

  const pathname = req.url?.replace(/^\/panel-api/, '') || req.url;
  if (pathname === '/slow-body') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.write('{"status":"ok",');
    if (slowBodyStarted) slowBodyStarted.resolve();
    if (slowBodyRelease) await slowBodyRelease.promise;
    res.end('"finished":true}');
  } else if (pathname === '/slow-error-body') {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.write('{"error":{"code":"internal",');
    if (slowErrorStarted) slowErrorStarted.resolve();
    if (slowErrorRelease) await slowErrorRelease.promise;
    res.end('"message":"slow failure"}}');
  } else if (pathname === '/fast-success') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end('{"success":true}');
  } else if (pathname === '/fast-error') {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end('{"error":{"message":"bad request","code":"invalid_param"}}');
  } else {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end('{}');
  }
});

await new Promise(resolve => server.listen(0, '127.0.0.1', () => {
  serverPort = server.address().port;
  resolve();
}));

const baseUri = `http://127.0.0.1:${serverPort}/`;
const docObj = Object.assign(new EventTarget(), { baseURI: baseUri, visibilityState: 'visible' });

for (const [k, v] of Object.entries({
  document: docObj,
  window: windowObj,
  localStorage: windowObj.localStorage,
  navigator: { onLine: true },
})) {
  Object.defineProperty(globalThis, k, { configurable: true, value: v });
}

const cacheDir = await mkdtemp(join(tmpdir(), 'hp-audit-req-'));
const vite = await createServer({
  root: new URL('..', import.meta.url).pathname,
  configFile: false,
  cacheDir,
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
});

try {
  const { request, ApiError } = await vite.ssrLoadModule('/src/lib/api.ts');

  await test('F04: request throws synchronously / pre-aborted signal emits 0 network requests', async () => {
    const beforeCount = requestCount;
    const ctrl = new AbortController();
    ctrl.abort();

    await assert.rejects(
      () => request('fast-success', { signal: ctrl.signal }, 5000),
      (err) => {
        assert.equal(err instanceof DOMException, true, 'pre-aborted must throw DOMException');
        assert.equal(err.name, 'AbortError');
        return true;
      }
    );

    assert.equal(requestCount, beforeCount, 'pre-aborted signal MUST NOT send any HTTP request (0 sent)');
  });

  await test('F04: timeout aborts slow response body and distinguishes from external abort', async () => {
    slowBodyStarted = deferred();
    slowBodyRelease = deferred();

    try {
      const promise = request('slow-body', {}, 50); // 50ms timeout
      await slowBodyStarted.promise;

      await assert.rejects(
        promise,
        (err) => {
          assert.equal(err instanceof ApiError, true, 'timeout must be an ApiError');
          assert.equal(err.kind, 'network');
          assert.equal(err.message, '请求超时');
          return true;
        }
      );
    } finally {
      slowBodyRelease.resolve();
    }
  });

  await test('F04: external abort during slow response body throws DOMException AbortError, not timeout', async () => {
    slowBodyStarted = deferred();
    slowBodyRelease = deferred();
    const ctrl = new AbortController();

    try {
      const promise = request('slow-body', { signal: ctrl.signal }, 10000);
      await slowBodyStarted.promise;

      // Now cancel externally while body is streaming
      ctrl.abort();

      await assert.rejects(
        promise,
        (err) => {
          assert.equal(err instanceof ApiError, false, 'external abort must NOT be ApiError');
          assert.equal(err instanceof DOMException, true, 'external abort must remain DOMException');
          assert.equal(err.name, 'AbortError');
          return true;
        }
      );
    } finally {
      slowBodyRelease.resolve();
    }
  });

  await test('F04: timeout during slow error body throws ApiError timeout', async () => {
    slowErrorStarted = deferred();
    slowErrorRelease = deferred();

    try {
      const promise = request('slow-error-body', {}, 50); // 50ms timeout
      await slowErrorStarted.promise;

      await assert.rejects(
        promise,
        (err) => {
          assert.equal(err instanceof ApiError, true, 'timeout must be an ApiError');
          assert.equal(err.kind, 'network');
          assert.equal(err.message, '请求超时');
          return true;
        }
      );
    } finally {
      slowErrorRelease.resolve();
    }
  });

  await test('F04: external abort during slow error body throws DOMException AbortError', async () => {
    slowErrorStarted = deferred();
    slowErrorRelease = deferred();
    const ctrl = new AbortController();

    try {
      const promise = request('slow-error-body', { signal: ctrl.signal }, 10000);
      await slowErrorStarted.promise;

      ctrl.abort();

      await assert.rejects(
        promise,
        (err) => {
          assert.equal(err instanceof ApiError, false, 'external abort must NOT be ApiError');
          assert.equal(err instanceof DOMException, true, 'external abort must remain DOMException');
          assert.equal(err.name, 'AbortError');
          return true;
        }
      );
    } finally {
      slowErrorRelease.resolve();
    }
  });
} finally {
  await vite.close();
  await rm(cacheDir, { recursive: true, force: true });
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  for (const [k, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, k, descriptor);
    else delete globalThis[k];
  }
}
