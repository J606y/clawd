import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const keys = ['document', 'localStorage', 'fetch'];
const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const store = new Map();
const doc = { baseURI: 'https://panel.example.test/', cookie: '' };
for (const [key, value] of Object.entries({
  document: doc,
  localStorage: { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
})) Object.defineProperty(globalThis, key, { configurable: true, value });
const cacheDir = await mkdtemp(join(tmpdir(), 'panel-auth-test-'));
const server = await createServer({ configFile: false, cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true }, appType: 'custom' });

try {
  const { renewCredential, clearCredential, storeCredential } = await server.ssrLoadModule('/src/lib/auth.ts');
  const calls = [];
  let status = 204;
  globalThis.fetch = async (url, init) => { calls.push([String(url), init.method, init.credentials]); return new Response(null, { status }); };
  const warn = console.warn;
  console.warn = () => {};
  try {
    await test('script cookie is only a ten-minute bootstrap', async () => {
      storeCredential('abc');
      assert.match(doc.cookie, /hp_auth=abc/);
      assert.match(doc.cookie, /Max-Age=600/);
      assert.match(doc.cookie, /Secure/);
      assert.match(doc.cookie, /SameSite=Strict/);
      assert.equal(doc.cookie.includes('2592000'), false);
    });
    await test('login forces a renewal through the nginx endpoint', async () => {
      await renewCredential(true);
      assert.deepEqual(calls, [['https://panel.example.test/panel-auth/renew', 'POST', 'same-origin']]);
    });
    await test('app start renews at most once a day', async () => {
      await renewCredential();
      assert.equal(calls.length, 1, 'renewed a moment ago');
      store.set('hp_auth_renewed_at', String(Date.now() - 25 * 3600 * 1000));
      await renewCredential();
      assert.equal(calls.length, 2);
    });
    await test('an endpoint that is not deployed yet does not mark the cookie as renewed', async () => {
      store.delete('hp_auth_renewed_at');
      status = 405;
      await renewCredential();
      assert.equal(store.has('hp_auth_renewed_at'), false);
      await renewCredential();
      assert.equal(calls.length, 4, 'tries again next time');
      status = 204;
    });
    await test('network failure is swallowed into a warning, logout forgets the renewal', async () => {
      globalThis.fetch = async () => { throw new TypeError('offline'); };
      await renewCredential(true);
      store.set('hp_auth_renewed_at', String(Date.now()));
      clearCredential();
      assert.equal(store.has('hp_auth_renewed_at'), false);
      assert.match(doc.cookie, /hp_auth=; Path=\/; Max-Age=0/);
    });
  } finally {
    console.warn = warn;
  }
} finally {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
  for (const [key, descriptor] of Object.entries(previous)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
