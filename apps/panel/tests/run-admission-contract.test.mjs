import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';

const SCREENSHOT = '创建结果待核验：尚未确认运行标识，已保留请求标识；不会重复创建或停止未知任务。';
const home = await mkdtemp(join(tmpdir(), 'hp-admission-contract-'));
const storage = new Map();
const creates = [];
const stops = [];
const resolves = [];
const saved = new Map(['document', 'window', 'navigator', 'localStorage', 'fetch'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn, message) {
  const end = performance.now() + 2500;
  while (!fn() && performance.now() < end) await sleep(10);
  assert.ok(fn(), message);
}
const win = Object.assign(new EventTarget(), {
  setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
});
let server;
let vite;
let runs;
let mode = 'accept';
const admitted = new Map();

function parked(sid, key) {
  const raw = storage.get('hp_pending_admissions');
  const list = raw ? JSON.parse(raw) : [];
  return Array.isArray(list) && list.some((item) => item && item.sessionId === sid && item.idempotencyKey === key);
}

function reload(sid) {
  runs.ctrls.get(sid)?.abort();
  runs.runs.delete(sid);
  runs.ctrls.delete(sid);
  return runs.resumeAll();
}

try {
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture');
    const path = url.pathname.replace(/^\/panel-api/, '').replace(/^\/panel-sidecar/, '');
    if (path === '/sessions/run/resolve') {
      const key = url.searchParams.get('idempotency_key');
      const sid = url.searchParams.get('session_id');
      resolves.push({ sid, key });
      const record = admitted.get(`${sid}:${key}`);
      const body = record
        ? { busy: false, run: record, resolution: { idempotency_key: key, state: 'found' } }
        : { busy: false, run: null, resolution: { idempotency_key: key, state: 'not_found' } };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
      return;
    }
    if (path === '/v1/runs' && req.method === 'POST') {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      const key = req.headers['idempotency-key'];
      creates.push({ sid: body.session_id, key });
      if (mode === 'drop') {
        const runId = `run_${'a'.repeat(32)}`;
        admitted.set(`${body.session_id}:${key}`, {
          run_id: runId, session_id: body.session_id, status: 'running', created_at: 10, updated_at: 11,
        });
        res.destroy();
        return;
      }
      const runId = `run_${'b'.repeat(32)}`;
      admitted.set(`${body.session_id}:${key}`, {
        run_id: runId, session_id: body.session_id, status: 'running', created_at: 10, updated_at: 11,
      });
      res.writeHead(202, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ run_id: runId, status: 'queued' }));
      return;
    }
    const statusMatch = path.match(/^\/v1\/runs\/(run_[0-9a-f]{32})$/);
    if (statusMatch && req.method === 'GET') {
      const record = [...admitted.values()].find((item) => item.run_id === statusMatch[1]);
      if (!record) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end('{}');
        return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(record));
      return;
    }
    if (path.endsWith('/stop') && req.method === 'POST') {
      stops.push(path);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'cancelled' }));
      return;
    }
    if (path.endsWith('/events')) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write(': connected\n\n');
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end('{}');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const doc = Object.assign(new EventTarget(), {
    baseURI: `http://127.0.0.1:${server.address().port}/`,
    visibilityState: 'visible',
  });
  for (const [key, value] of Object.entries({ document: doc, window: win, localStorage: win.localStorage, navigator: { onLine: true } })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  vite = await createServer({
    root: new URL('..', import.meta.url).pathname,
    configFile: false,
    cacheDir: join(home, 'vite-cache'),
    server: { middlewareMode: true, hmr: false, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] },
    appType: 'custom',
    logLevel: 'error',
  });
  ({ runs } = await vite.ssrLoadModule('/src/lib/runs.ts'));
  const { api } = await vite.ssrLoadModule('/src/lib/api.ts');
  api.runModelFields = async () => ({});

  await test('explicit 202 with run_id enters the verified run and does not stay on the screenshot message', async () => {
    mode = 'accept';
    const sid = 'session-admission-accept';
    const before = creates.length;
    runs.start(sid, 'accepted', [], 'accepted');
    await until(() => runs.get(sid)?.runId?.startsWith('run_'), 'valid create must bind run_id');
    const run = runs.get(sid);
    assert.equal(run.unverified, undefined);
    assert.equal(run.status, 'running');
    assert.notEqual(run.error, SCREENSHOT);
    assert.equal(creates.length, before + 1);
    assert.equal(stops.length, 0);
    runs.ctrls.get(sid)?.abort();
  });

  await test('screenshot resume of an old key with repeated not_found stays uncertain, and explicit abandon survives reload', async () => {
    mode = 'not_found';
    const sid = 'session-screenshot-resume';
    const key = 'old-request-01-20';
    const text = '尚未入库的原话';
    storage.set('hp_active_runs', JSON.stringify({
      [sid]: { runId: '', idempotencyKey: key, userText: text, startedAt: 1_700_000_000_000 },
    }));
    const createsBefore = creates.length;
    const stopsBefore = stops.length;
    let finishes = 0;
    const onFinished = (event) => { if (event.detail === sid) finishes += 1; };
    runs.addEventListener('hp-run-finished', onFinished);
    try {
      runs.resumeAll();
      const locked = runs.get(sid);
      assert.equal(locked.error, SCREENSHOT);
      assert.equal(locked.status, 'starting');
      assert.equal(locked.unverified, true);
      assert.equal(locked.runId, '');
      assert.equal(runs.isActive(sid), true, 'unabandoned resume keeps the admission lock');
      await until(() => resolves.some((item) => item.key === key), 'resume must query the original key');
      win.dispatchEvent(new Event('online'));
      await until(() => resolves.filter((item) => item.key === key).length >= 2, 'not_found must be queried again, not treated as terminal');
      assert.equal(locked.status, 'starting');
      assert.notEqual(locked.status, 'cancelled');
      assert.notEqual(locked.status, 'failed');
      assert.notEqual(locked.status, 'completed');
      assert.equal(creates.length, createsBefore, 'not_found must not resend create');
      assert.equal(stops.length, stopsBefore, 'not_found must not stop an unknown run');
      assert.equal(finishes, 0);

      await runs.stop(sid);
      assert.equal(runs.isActive(sid), false, 'explicit stop releases the local lock');
      assert.equal(locked.stopping, false);
      assert.equal(locked.status, 'starting', 'abandon must not fabricate a terminal status');
      assert.equal(locked.unverified, true);
      assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, key);
      assert.equal(parked(sid, key), true, 'abandon parks the key instead of deleting it');
      assert.equal(stops.length, stopsBefore);
      assert.equal(creates.length, createsBefore);
      assert.equal(finishes, 0);

      const resumed = reload(sid);
      assert.ok(resumed.includes(sid), 'the retained key is still recoverable after reload');
      const returned = runs.get(sid);
      assert.equal(runs.isActive(sid), false, 'explicit abandon must not re-lock the composer after refresh');
      assert.equal(returned.locallyReleased, true);
      assert.equal(returned.runId, '');
      assert.equal(returned.unverified, true);
      assert.equal(returned.status, 'starting');
      assert.notEqual(returned.status, 'cancelled');
      assert.match(returned.error, /待核验/);
      assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, key);
      assert.equal(creates.filter((item) => item.key === key).length, 0);
      assert.equal(stops.length, stopsBefore);
      assert.equal(finishes, 0);
      const createsAtReload = creates.length;
      runs.start(sid, 'linux xray follow-up', [], 'linux xray follow-up');
      await until(() => creates.length === createsAtReload + 1, 'after abandon, a new message may create once');
      assert.notEqual(creates.at(-1).key, key, 'the retained key must not be resent');
      assert.equal(creates.filter((item) => item.key === key).length, 0);
      assert.equal(stops.length, stopsBefore);
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
    }
  });
} finally {
  if (runs) for (const ctrl of runs.ctrls.values()) ctrl.abort();
  if (server) {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
  if (vite) await vite.close();
  for (const [key, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
  await rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
