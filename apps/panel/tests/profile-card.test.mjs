import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const storage = new Map();
const localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
const documentElement = { dataset: {}, style: {} };
const document = {
  baseURI: 'http://panel.test/',
  visibilityState: 'visible',
  documentElement,
  addEventListener() {},
  removeEventListener() {},
};
const windowShim = {
  document,
  localStorage,
  sessionStorage: localStorage,
  setTimeout,
  clearTimeout,
  dispatchEvent() {},
  addEventListener() {},
  removeEventListener() {},
};
Object.assign(globalThis, { document, window: windowShim, localStorage });

const server = await createServer({
  configFile: false,
  root,
  resolve: { alias: { '@': fileURLToPath(new URL('../src/react', import.meta.url)) } },
  esbuild: { jsx: 'automatic' },
  server: { middlewareMode: true },
  appType: 'custom',
});

try {
  const { PluginsTab } = await server.ssrLoadModule('/src/react/pages/settings/plugins-tab.tsx');

  test('profile card hides the retired default model and keeps gateway state', () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchOnMount: false } },
    });
    client.setQueryData(['profiles'], {
      data: [{ name: 'default', active: true, model: 'gemini-3.8-flash', gateway: 'running', alias: 'coder' }],
      raw: null,
    });
    client.setQueryData(['plugins'], []);
    const html = renderToStaticMarkup(createElement(QueryClientProvider, { client }, createElement(PluginsTab)));
    assert.equal(html.includes('模型：'), false);
    assert.equal(html.includes('gemini-3.8-flash'), false);
    assert.equal(html.includes('网关：running'), true);
    assert.equal(html.includes('别名：coder'), true);
    assert.equal(html.includes('>default<'), true);
    assert.equal(html.includes('当前'), true);
  });
} finally {
  await server.close();
}
