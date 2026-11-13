import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { createServer } from 'vite';

const home = await mkdtemp(join(tmpdir(), 'hp-f01-exact-'));
const repoRoot = join(import.meta.dirname, '..', '..', '..');
const venvPython = join(repoRoot, '.venv', 'bin', 'python');
const pythonBin = existsSync(venvPython) ? venvPython : 'python3';
const python = spawn(pythonBin, ['tests/fixtures/f01-admission-service.py', home], { stdio: ['ignore', 'pipe', 'pipe'] });
let pythonError = '';
python.stderr.on('data', x => { pythonError += x; });
let vite, server, runs, api;
const storage = new Map(), nativeRuns = new Map(), streams = new Set(), admissions = [], stops = [], queries = [];
const saved = new Map(['document', 'window', 'navigator', 'localStorage'].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, message) {
  const end = performance.now() + 2500;
  while (!fn() && performance.now() < end) await sleep(10);
  assert.ok(fn(), message);
}
const win = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) } });
let fixtureUrl, resolutionMode = 'normal', heldResolution = null;
try {
  const ready = await new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error(`fixture startup timed out: ${pythonError}`)), 5000);
    python.once('exit', code => { clearTimeout(timer); reject(new Error(`fixture exit ${code}: ${pythonError}`)); });
    python.stdout.on('data', chunk => {
      text += chunk;
      if (text.includes('\n')) { clearTimeout(timer); resolve(JSON.parse(text.split('\n')[0])); }
    });
  });
  fixtureUrl = `http://127.0.0.1:${ready.port}`;
  server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://fixture');
      if (url.pathname.startsWith('/panel-core/')) {
        queries.push(url.pathname + url.search);
        if (heldResolution) await heldResolution;
        if (resolutionMode === 'error') { res.writeHead(503); res.end('{}'); return; }
        const response = await fetch(fixtureUrl + url.pathname.replace('/panel-core', '') + url.search,
          { headers: { 'X-Panel-Key': 'synthetic-fixture-auth' } });
        let body = await response.json();
        if (resolutionMode === 'legacy') body = { busy: false, run: body.run };
        if (resolutionMode === 'wrong-key') body.resolution.idempotency_key = 'foreign-request';
        if (resolutionMode === 'wrong-session' && body.run) body.run.session_id = 'foreign-session';
        res.writeHead(response.status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); return;
      }
      const pathname = url.pathname.replace('/panel-api', '');
      if (pathname === '/v1/runs' && req.method === 'POST') {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const body = JSON.parse(Buffer.concat(chunks).toString());
        const key = req.headers['idempotency-key'];
        admissions.push({ sid: body.session_id, key });
        const admitted = await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST',
          body: JSON.stringify({ session_id: body.session_id, key }) }).then(r => r.json());
        nativeRuns.set(admitted.run_id, admitted);
        res.destroy(); return; // Accepted durably; intentionally lose the create response.
      }
      const match = pathname.match(/^\/v1\/runs\/(run_[0-9a-f]{32})(\/stop|\/events)?$/);
      if (match && nativeRuns.has(match[1])) {
        const record = nativeRuns.get(match[1]);
        if (match[2] === '/stop' && req.method === 'POST') {
          stops.push(match[1]); record.status = 'cancelled';
          res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ status: record.status }));
          for (const stream of streams) if (stream.id === match[1]) stream.res.end(`data: ${JSON.stringify({ event: 'run.cancelled', run_id: match[1], seq: 1 })}\n\n`);
        } else if (match[2] === '/events') {
          res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write(': connected\n\n');
          const stream = { id: match[1], res }; streams.add(stream); req.on('close', () => streams.delete(stream));
        } else { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(record)); }
        return;
      }
      res.writeHead(404, { 'Content-Type': 'application/json' }); res.end('{}');
    } catch (error) { res.writeHead(500); res.end(String(error)); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const doc = Object.assign(new EventTarget(), { baseURI: `http://127.0.0.1:${server.address().port}/`, visibilityState: 'visible' });
  for (const [key, value] of Object.entries({ document: doc, window: win, localStorage: win.localStorage, navigator: { onLine: true } }))
    Object.defineProperty(globalThis, key, { configurable: true, value });
  vite = await createServer({ root: new URL('..', import.meta.url).pathname, configFile: false,
    cacheDir: join(home, 'vite-cache'), server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' });
  ({ runs } = await vite.ssrLoadModule('/src/lib/runs.ts'));
  ({ api } = await vite.ssrLoadModule('/src/lib/api.ts'));
  api.runModelFields = async () => ({}); // Model selection is outside this admission/recovery contract.

  await test('lost create response automatically resolves original key and stop uses exact recovered identity', async () => {
    const sid = 'session-exact-lost';
    runs.start(sid, 'synthetic', [], 'synthetic');
    await until(() => admissions.length === 1 && runs.get(sid)?.runId, 'must automatically bind the durable exact run');
    const run = runs.get(sid), admitted = admissions[0];
    assert.equal(run.unverified, false);
    assert.equal(run.status, 'running');
    assert.equal(run.idempotencyKey, admitted.key);
    assert.ok(nativeRuns.has(run.runId));
    const stored = JSON.parse(storage.get('hp_active_runs'))[sid];
    assert.equal(stored.runId, run.runId); assert.equal(stored.idempotencyKey, admitted.key);
    assert.ok(queries.some(q => new URL(q, 'http://fixture').searchParams.get('idempotency_key') === admitted.key));
    runs.start(sid, 'duplicate', [], 'duplicate'); await sleep(30);
    assert.equal(admissions.length, 1, 'never resend the create request');
    await Promise.all([runs.stop(sid), runs.stop(sid)]);
    assert.deepEqual(stops, [run.runId]);
    assert.equal(run.status, 'cancelled');
    assert.equal(JSON.parse(storage.get('hp_active_runs') || '{}')[sid], undefined);
  });

  async function seed(sid, key, status = 'running') {
    const record = await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key, status }) }).then(r => r.json());
    nativeRuns.set(record.run_id, record);
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: '', idempotencyKey: key, userText: 'restored synthetic', startedAt: Date.now() } }));
    return record;
  }

  await test('refresh recovers a persisted original key without creating or substituting a newer run', async () => {
    const sid = 'session-exact-reload', key = 'reload-original';
    const original = await seed(sid, key);
    await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key: 'newer-unrelated-key' }) });
    const creates = admissions.length;
    runs.resumeAll();
    await until(() => runs.get(sid)?.runId, 'refresh must resolve the original request');
    assert.equal(runs.get(sid).runId, original.run_id);
    assert.equal(admissions.length, creates);
    await runs.stop(sid);
  });

  await test('failed query, missing proof, mismatched key/session/native identity never release ownership or send stop', async () => {
    const sid = 'session-exact-unsafe', key = 'unsafe-original';
    const record = await seed(sid, key);
    resolutionMode = 'error';
    let finishes = 0;
    const onFinished = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener('hp-run-finished', onFinished);
    const writes = admissions.length, stopCount = stops.length;
    runs.resumeAll();
    await until(() => queries.some(q => q.includes(sid)), 'automatic recovery query must run');
    await runs.recover(sid);
    for (const mode of ['error', 'legacy', 'wrong-key', 'wrong-session']) {
      resolutionMode = mode;
      assert.equal(await runs.recover(sid), false, mode);
      assert.equal(runs.get(sid).runId, '');
      assert.equal(runs.get(sid).unverified, true);
      assert.equal(runs.isActive(sid), true, 'unsafe evidence must keep the lock until an explicit stop');
      runs.start(sid, 'duplicate', [], 'duplicate');
      assert.ok(JSON.parse(storage.get('hp_active_runs'))[sid]);
      assert.equal(admissions.length, writes, 'unsafe evidence must not allow a second create');
      assert.equal(stops.length, stopCount, 'unsafe evidence must not send stop');
    }
    const storedKey = JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey;
    resolutionMode = 'normal';
    await runs.stop(sid);
    assert.equal(runs.isActive(sid), false, 'explicit stop releases the local lock');
    assert.equal(runs.get(sid)?.stopping, false);
    assert.equal(runs.get(sid)?.status, 'starting', 'must not fabricate a terminal status');
    assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, storedKey, 'explicit stop must not drop the request identity');
    assert.equal(stops.length, stopCount, 'explicit stop must not send stopRun without a run id');
    assert.equal(finishes, 0);
    assert.equal(admissions.length, writes);
    record.session_id = 'foreign-native-session';
    assert.equal(await runs.recover(sid), false, 'native API identity must agree');
    record.session_id = sid;
    assert.equal(finishes, 0); assert.equal(stops.length, stopCount); assert.equal(admissions.length, writes);
    assert.equal(await runs.recover(sid), true);
    await runs.stop(sid);
    assert.equal(finishes, 1);
    runs.removeEventListener('hp-run-finished', onFinished);
  });

  await test('authoritative not-found remains unverified, then online wake recovers a delayed durable record', async () => {
    const sid = 'session-exact-delayed', key = 'delayed-original';
    await seed(sid, 'unrelated-existing-key');
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: '', idempotencyKey: key, userText: '', startedAt: Date.now() } }));
    runs.resumeAll();
    assert.equal(await runs.recover(sid), false);
    const run = runs.get(sid);
    assert.equal(run.runId, ''); assert.equal(run.status, 'starting'); assert.equal(run.unverified, true);
    assert.ok(JSON.parse(storage.get('hp_active_runs'))[sid]);
    const record = await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key }) }).then(r => r.json());
    nativeRuns.set(record.run_id, record);
    win.dispatchEvent(new Event('online'));
    await until(() => run.runId === record.run_id, 'network wake must requery without create');
    await runs.stop(sid);
  });

  await test('concurrent recovery is deduplicated and confirmed terminal cleans storage once', async () => {
    const sid = 'session-exact-concurrent', key = 'concurrent-original';
    const record = await seed(sid, key, 'completed');
    let release;
    heldResolution = new Promise(resolve => { release = resolve; });
    const before = queries.length;
    let finishes = 0;
    const listener = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener('hp-run-finished', listener);
    runs.resumeAll();
    await until(() => queries.length > before, 'query reaches synthetic transport');
    const a = runs.recover(sid), b = runs.recover(sid);
    assert.equal(a, b, 'same instance shares the exact in-flight promise');
    heldResolution = null; release();
    assert.deepEqual(await Promise.all([a,b]), [true,true]);
    assert.equal(queries.length - before, 1);
    assert.equal(runs.get(sid).runId, record.run_id);
    assert.equal(runs.get(sid).status, 'completed'); assert.equal(finishes, 1);
    assert.equal(JSON.parse(storage.get('hp_active_runs') || '{}')[sid], undefined);
    runs.removeEventListener('hp-run-finished', listener);
  });

  await test('late recovery cannot mutate a replacement or erase its persisted identity', async () => {
    const sid = 'session-exact-replaced', key = 'replaced-original';
    await seed(sid, key);
    let release;
    heldResolution = new Promise(resolve => { release = resolve; });
    runs.resumeAll();
    const old = runs.get(sid), pending = runs.recover(sid);
    await until(() => queries.some(q => q.includes(sid)), 'old lookup is in flight');
    const replacement = { ...old, runId: 'run_' + 'b'.repeat(32), unverified: false, status: 'running' };
    // Inject the ownership boundary directly; no synthetic terminal event authorizes a real task stop.
    runs.runs.set(sid, replacement);
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: replacement.runId, userText: 'replacement' } }));
    heldResolution = null; release();
    assert.equal(await pending, false);
    await sleep(20);
    assert.equal(runs.get(sid), replacement); assert.equal(old.runId, '');
    assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].runId, replacement.runId);
    runs.ctrls.get(sid).abort();
  });

  function parked(sid, key) {
    const raw = storage.get('hp_pending_admissions');
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) && list.some((item) => item && item.sessionId === sid && item.idempotencyKey === key);
  }

  await test('released admission parks the original key and a new start does not resend or delete it', async () => {
    resolutionMode = 'normal';
    const sid = 'session-exact-abandon';
    const key = 'abandon-original';
    await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key: 'abandon-anchor' }) });
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: '', idempotencyKey: key, userText: 'park me', startedAt: 1000 } }));
    const creates = admissions.length;
    const stopCount = stops.length;
    let finishes = 0;
    const onFinished = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener('hp-run-finished', onFinished);
    try {
      runs.resumeAll();
      await until(() => runs.get(sid)?.unverified === true, 'unverified lock before stop');
      assert.equal(runs.isActive(sid), true);
      await runs.stop(sid);
      assert.equal(runs.isActive(sid), false, 'input lock released');
      assert.equal(runs.get(sid)?.stopping, false);
      assert.equal(runs.get(sid)?.status, 'starting');
      assert.equal(stops.length, stopCount, 'no server stop');
      assert.equal(finishes, 0, 'no fabricated finish');
      assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, key);

      runs.start(sid, 'newer message', [], 'newer message');
      await until(() => admissions.length === creates + 1, 'new message may create once');
      assert.equal(parked(sid, key), true, 'original identity must be saved aside');
      assert.equal(admissions.filter((item) => item.key === key).length, 0, 'original key must not be resent');
      assert.notEqual(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, key);
      assert.equal(parked(sid, key), true, 'new start must not delete the parked identity');
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
    }
  });

  await test('released admission attaches a delayed record when no newer turn is active, without creating or stopping', async () => {
    resolutionMode = 'normal';
    const sid = 'session-exact-release-attach';
    const key = 'release-attach-original';
    await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key: 'release-attach-anchor' }) });
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: '', idempotencyKey: key, userText: '', startedAt: Date.now() } }));
    runs.resumeAll();
    assert.equal(await runs.recover(sid), false, 'single not_found must not count as create failure');
    assert.equal(runs.isActive(sid), true, 'not_found before stop keeps the lock');
    const creates = admissions.length;
    const stopCount = stops.length;
    await runs.stop(sid);
    assert.equal(runs.isActive(sid), false);
    assert.equal(runs.get(sid)?.runId, '');
    assert.equal(runs.get(sid)?.unverified, true);
    const record = await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key }) }).then(r => r.json());
    nativeRuns.set(record.run_id, record);
    win.dispatchEvent(new Event('online'));
    await until(() => runs.get(sid)?.runId === record.run_id, 'delayed record attaches without a second create');
    assert.equal(admissions.length, creates);
    assert.equal(stops.length, stopCount);
    assert.equal(runs.get(sid).unverified, false);
  });

  await test('parked admission does not steal a newer turn, then observes once idle if the original is still running', async () => {
    resolutionMode = 'normal';
    const sid = 'session-exact-parked';
    const key = 'parked-original';
    await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key: 'parked-anchor' }) });
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: '', idempotencyKey: key, userText: 'original', startedAt: Date.now() - 1000 } }));
    runs.resumeAll();
    await until(() => runs.get(sid)?.unverified === true && runs.isActive(sid), 'original admission locked');
    await runs.stop(sid);
    assert.equal(runs.isActive(sid), false);
    const creates = admissions.length;
    runs.start(sid, 'newer turn', [], 'newer turn');
    await until(() => {
      const run = runs.get(sid);
      return !!run?.runId && run.idempotencyKey !== key && run.status === 'running';
    }, 'newer turn must become the active run');
    const newerId = runs.get(sid).runId;
    assert.equal(parked(sid, key), true, 'original key survives the new start');
    assert.equal(admissions.filter((item) => item.key === key).length, 0, 'original key must not be resent');
    const original = await fetch(fixtureUrl + '/__fixture/admit', { method: 'POST', body: JSON.stringify({ session_id: sid, key, status: 'running' }) }).then(r => r.json());
    nativeRuns.set(original.run_id, original);
    const beforeQueries = queries.length;
    win.dispatchEvent(new Event('online'));
    await until(() => queries.slice(beforeQueries).some((q) => q.includes(encodeURIComponent(key)) || q.includes(key)), 'pending resolve runs while the newer turn is active');
    await sleep(40);
    assert.equal(runs.get(sid).runId, newerId, 'must not steal the newer turn');
    assert.equal(stops.includes(original.run_id), false);
    await runs.stop(sid);
    await until(() => runs.get(sid)?.runId === original.run_id && !runs.get(sid)?.stopping, 'after the newer turn ends, a still-running original is observed');
    assert.equal(admissions.filter((item) => item.key === key).length, 0);
    assert.equal(stops.includes(original.run_id), false, 'observe must not stop the original');
    assert.equal(admissions.length, creates + 1, 'only the newer message is created');
  });
} finally {
  if (runs) for (const ctrl of runs.ctrls.values()) ctrl.abort();
  for (const stream of streams) stream.res.destroy();
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  if (vite) await vite.close();
  if (python.exitCode === null) { const exited = once(python, 'exit'); python.kill('SIGTERM'); await exited; }
  for (const [key, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
  }
  await rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
