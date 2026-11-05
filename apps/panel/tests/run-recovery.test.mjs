import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const names = ['window', 'document', 'navigator', 'localStorage', 'fetch'];
const globals = new Map(names.map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
const storage = new Map();
const window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) } });
const document = Object.assign(new EventTarget(), { baseURI: 'https://fixture.invalid/', visibilityState: 'visible' });
for (const [key, value] of Object.entries({ window, document, localStorage: window.localStorage, navigator: { onLine: true } })) Object.defineProperty(globalThis, key, { configurable: true, value });
const cacheDir = await mkdtemp(join(tmpdir(), 'hp-run-recovery-'));
const server = await createServer({ configFile: false, cacheDir, server: { middlewareMode: true }, appType: 'custom' });
const tick = () => new Promise(r => setImmediate(r));
try {
  const { runs, RUN_FINISHED } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { api, ApiError } = await server.ssrLoadModule('/src/lib/api.ts');
  const { sidecar } = await server.ssrLoadModule('/src/lib/sidecar.ts');
  const original = { stop: api.stopRun, status: api.runStatus, discovery: sidecar.sessionRun };
  function fixture(sid) {
    globalThis.fetch = async (_url, init) => new Response(new ReadableStream({ start(c) {
      init.signal.addEventListener('abort', () => c.error(new DOMException('Aborted', 'AbortError')), { once: true });
    } }), { headers: { 'Content-Type': 'text/event-stream' } });
    runs.observe(sid, `run-${sid}`, Date.now());
    return runs.get(sid);
  }
  function cleanup(sid) {
    runs.ctrls.get(sid)?.abort();
    api.stopRun = original.stop; api.runStatus = original.status; sidecar.sessionRun = original.discovery;
  }
  await test('stop conflict settles a verified orphan despite cached native running status', async () => {
    const sid = 'orphan-stop'; const run = fixture(sid);
    let finishes = 0;
    const done = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener(RUN_FINISHED, done);
    api.stopRun = async () => { throw new ApiError('server', 409, 'no executor', 'run_not_active'); };
    api.runStatus = async () => ({ status: 'running', run_id: run.runId, session_id: sid });
    sidecar.sessionRun = async () => ({ busy: false, run: { run_id: run.runId, session_id: sid, status: 'interrupted', created_at: 1, updated_at: 2 } });
    try {
      await runs.stop(sid); await tick();
      assert.equal(run.status, 'failed');
      assert.match(run.error, /中断/);
      assert.equal(run.stopping, false);
      assert.equal(finishes, 1);
      assert.equal(JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid], undefined);
    } finally { cleanup(sid); await tick(); runs.clear(sid); runs.removeEventListener(RUN_FINISHED, done); }
  });
  await test('terminal stop acknowledgement closes a quiet SSE without waiting for a new event', async () => {
    const sid = 'terminal-stop'; const run = fixture(sid);
    api.stopRun = async () => ({ status: 'cancelled', run_id: run.runId, session_id: sid });
    sidecar.sessionRun = async () => ({ busy: false, run: null });
    try { await runs.stop(sid); await tick(); assert.equal(run.status, 'cancelled'); assert.equal(runs.isActive(sid), false); }
    finally { cleanup(sid); await tick(); runs.clear(sid); }
  });
  await test('native running plus lost executor settles automatically after stream loss', async () => {
    const sid = 'orphan-poll';
    globalThis.fetch = async () => new Response('{}', { status: 404 });
    api.runStatus = async () => ({ status: 'running', run_id: `run-${sid}`, session_id: sid });
    sidecar.sessionRun = async () => ({ busy: false, run: { run_id: `run-${sid}`, session_id: sid, status: 'interrupted', created_at: 1, updated_at: 2 } });
    runs.observe(sid, `run-${sid}`, Date.now());
    const run = runs.get(sid);
    try {
      for (let n = 0; n < 30 && run.status === 'running'; n++) await tick();
      assert.equal(run.status, 'failed');
      assert.equal(JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid], undefined);
    } finally { cleanup(sid); await tick(); runs.clear(sid); }
  });
  for (const scenario of ['wrong-run', 'wrong-session', 'busy', 'unknown', 'unrelated-conflict']) {
    await test(`stop conflict must not falsely finish ${scenario}`, async () => {
      const sid = `preserve-${scenario}`; const run = fixture(sid);
      api.stopRun = async () => { throw new ApiError('server', 409, 'conflict', scenario === 'unrelated-conflict' ? 'other_conflict' : 'run_not_active'); };
      api.runStatus = async () => ({ status: 'running' });
      sidecar.sessionRun = async () => ({ busy: scenario === 'busy', run: scenario === 'unknown' ? null : { run_id: scenario === 'wrong-run' ? 'other' : run.runId, session_id: scenario === 'wrong-session' ? 'other' : sid, status: 'interrupted', created_at: 1, updated_at: 2 } });
      try { await runs.stop(sid); assert.equal(run.status, 'running'); assert.equal(run.stopping, false); assert.match(run.error, /停止失败/); }
      finally { cleanup(sid); await tick(); runs.clear(sid); }
    });
  }
} finally {
  await server.close(); await rm(cacheDir, { recursive: true, force: true });
  for (const [key, descriptor] of globals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
}
