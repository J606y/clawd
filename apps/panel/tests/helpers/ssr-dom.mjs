// Node SSR fixtures for the panel React modules. Theme init reads
// document.documentElement.dataset; an EventTarget without that mock crashes
// module evaluation. Do not paper over the missing fixture in product code.
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const panelRoot = path.resolve(here, '../..');

export function installSsrDom({ matchMedia = true, timers = false } = {}) {
  const keys = ['document', 'window', 'navigator', 'localStorage'];
  const previous = Object.fromEntries(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const documentElement = { dataset: {}, style: {} };
  const browser = new EventTarget();
  if (timers) {
    browser.setTimeout = setTimeout;
    browser.clearTimeout = clearTimeout;
    browser.setInterval = setInterval;
    browser.clearInterval = clearInterval;
  }
  browser.matchMedia = () => ({
    matches: matchMedia,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
  const document = new EventTarget();
  document.baseURI = 'https://panel.example.test/';
  document.visibilityState = 'visible';
  document.documentElement = documentElement;
  document.createElement = () => ({ style: {}, setAttribute() {}, appendChild() {}, innerHTML: '' });
  const storage = { getItem: () => null, setItem() {}, removeItem() {} };
  browser.document = document;
  browser.localStorage = storage;
  browser.sessionStorage = storage;
  Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true, userAgent: 'test' } });
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
  return function restoreSsrDom() {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  };
}

// Pin root to the panel. Unset root follows cwd and can watch the parent apps/
// directory or, from a higher cwd, the whole clawd tree. watch:null stops that
// chokidar crawl; noDiscovery skips the optimizeDeps walk of every test fixture.
export function ssrViteOptions({ cacheName, stub }) {
  const cacheRoot = process.env.TMPDIR || path.join(panelRoot, '.vite-test-cache');
  return {
    root: panelRoot,
    configFile: path.join(panelRoot, 'vite.config.ts'),
    cacheDir: path.join(cacheRoot, 'vite', cacheName),
    appType: 'custom',
    server: { middlewareMode: true, hmr: false, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { dompurify: stub } },
  };
}
