import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer as createHTTPServer } from 'node:http';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'vite';

test('development proxy forwards API, sidecar and same-origin renewal cookies to configured upstream', async () => {
  const requests = [];
  const upstream = createHTTPServer((req, res) => {
    requests.push({ path: req.url, method: req.method, cookie: req.headers.cookie });
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/__fixture/panel-auth/renew') {
      res.setHeader('Set-Cookie', 'panel_session=synthetic-renewed; Path=/; HttpOnly; SameSite=Lax');
    }
    res.end(JSON.stringify({ path: req.url }));
  });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'proxy-'));
  const prior = process.env.PANEL_DEV_UPSTREAM;
  process.env.PANEL_DEV_UPSTREAM = `http://127.0.0.1:${upstream.address().port}/__fixture`;
  let vite;
  try {
    vite = await createServer({ root: path.resolve(import.meta.dirname, '..'), cacheDir,
      server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'silent' });
    const config = vite.config.server.proxy;
    for (const prefix of ['/panel-api', '/panel-sidecar', '/panel-auth']) {
      assert.equal(config[prefix]?.target, process.env.PANEL_DEV_UPSTREAM, `missing configurable route ${prefix}`);
    }
    await vite.listen();
    const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
    for (const [route, method] of [['/panel-api/health', 'GET'], ['/panel-sidecar/health', 'GET'], ['/panel-auth/renew', 'POST']]) {
      const response = await fetch(base + route, { method, headers: { Cookie: 'panel_session=synthetic-original' } });
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), { path: '/__fixture' + route });
      if (method === 'POST') assert.match(response.headers.get('set-cookie'), /panel_session=synthetic-renewed/);
    }
    assert.deepEqual(requests.map(r => r.method), ['GET', 'GET', 'POST']);
    assert.ok(requests.every(r => r.cookie === 'panel_session=synthetic-original'));
  } finally {
    if (prior === undefined) delete process.env.PANEL_DEV_UPSTREAM; else process.env.PANEL_DEV_UPSTREAM = prior;
    await vite?.close();
    upstream.closeAllConnections();
    await new Promise(resolve => upstream.close(resolve));
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test('unset PANEL_DEV_UPSTREAM does not proxy to production', async () => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'proxy-unset-'));
  const prior = process.env.PANEL_DEV_UPSTREAM;
  delete process.env.PANEL_DEV_UPSTREAM;
  let vite;
  try {
    vite = await createServer({ root: path.resolve(import.meta.dirname, '..'), cacheDir,
      server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'silent' });
    const config = vite.config.server.proxy;
    for (const prefix of ['/panel-api', '/panel-sidecar', '/panel-auth']) {
      const target = config[prefix]?.target;
      assert.equal(target, 'http://127.0.0.1:9', `${prefix} must fail closed on loopback`);
      assert.equal(String(target).includes('hermes.20051212.xyz'), false);
    }
  } finally {
    if (prior === undefined) delete process.env.PANEL_DEV_UPSTREAM; else process.env.PANEL_DEV_UPSTREAM = prior;
    await vite?.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
});
