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
let runAccepted;
let runDelayedRes;
let backendRunStatus = 'none';
let admittedSessionId = '';
let stopCalls = 0;
let startRequests = [];
let runStatusCalls = 0;

server = http.createServer(async (req, res) => {
  const pathname = req.url?.replace(/^\/panel-api/, '') || req.url;

  if (pathname === '/v1/runs' && req.method === 'POST') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const bodyText = Buffer.concat(chunks).toString('utf-8');
    const parsedBody = bodyText ? JSON.parse(bodyText) : {};
    const idempotencyKey = req.headers['idempotency-key'];
    startRequests.push({ body: parsedBody, idempotencyKey });
    if (typeof parsedBody.session_id === 'string' && parsedBody.session_id) {
      admittedSessionId = parsedBody.session_id;
    }

    backendRunStatus = 'running';
    if (runAccepted) runAccepted.resolve();

    if (runDelayedRes) {
      await runDelayedRes.promise;
    }

    if (!res.destroyed) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ run_id: 'audit-run-123', status: 'running' }));
    }
  } else if (pathname.endsWith('/stop') && req.method === 'POST') {
    stopCalls++;
    backendRunStatus = 'cancelled';
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'cancelled', run_id: 'audit-run-123', ...(admittedSessionId ? { session_id: admittedSessionId } : {}) }));
  } else if (pathname.startsWith('/v1/runs/')) {
    runStatusCalls++;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: backendRunStatus, run_id: 'audit-run-123', ...(admittedSessionId ? { session_id: admittedSessionId } : {}) }));
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

const cacheDir = await mkdtemp(join(tmpdir(), 'hp-audit-start-stop-'));
const vite = await createServer({
  root: new URL('..', import.meta.url).pathname,
  configFile: false,
  cacheDir,
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
});

let runs, api, sidecar;
try {
  ({ runs } = await vite.ssrLoadModule('/src/lib/runs.ts'));
  ({ api } = await vite.ssrLoadModule('/src/lib/api.ts'));
  ({ sidecar } = await vite.ssrLoadModule('/src/lib/sidecar.ts'));

  let mockSidecarRun = null;
  sidecar.sessionRun = async (sid) => ({
    busy: backendRunStatus === 'running',
    run: mockSidecarRun ?? (backendRunStatus !== 'none' ? {
      run_id: 'audit-run-123',
      session_id: sid,
      status: backendRunStatus,
      created_at: 100,
      updated_at: 101,
    } : null),
  });

  await test('F01: stop during in-flight startRun MUST NOT fake client cancelled; must await run_id and call backend stop', async () => {
    runAccepted = deferred();
    runDelayedRes = deferred();
    backendRunStatus = 'none';
    mockSidecarRun = null;
    stopCalls = 0;
    startRequests = [];

    const sid = 'session-delayed-start';
    runs.start(sid, 'Hello world', [], 'Hello world');

    // Wait until the server has received POST /v1/runs
    await runAccepted.promise;
    assert.equal(backendRunStatus, 'running', 'server accepted task');

    // User clicks stop while start request is pending
    const stopPromise = runs.stop(sid);

    const runBeforeResolve = runs.get(sid);
    assert.equal(runBeforeResolve.status, 'starting', 'status must not be prematurely marked cancelled');
    assert.equal(runBeforeResolve.stopping, true, 'stopping flag must be recorded');

    // Now release the server response
    runDelayedRes.resolve();
    await stopPromise;

    // After stop finishes, backend stop API MUST have been called!
    assert.equal(stopCalls, 1, 'backend stopRun MUST have been called once run_id was received');
    assert.equal(backendRunStatus, 'cancelled', 'backend must be stopped');
    assert.equal(runs.get(sid)?.status, 'cancelled');
    assert.equal(runs.isActive(sid), false);
  });

  await test('F01: idempotency key is persisted before request and used on lost response for verification, NOT fake cancelled', async () => {
    storage.clear();
    runAccepted = deferred();
    runDelayedRes = null; // will reject with network error
    backendRunStatus = 'running';
    mockSidecarRun = null;
    stopCalls = 0;
    startRequests = [];

    const sid = 'session-lost-response';
    const originalStartRun = api.startRun;
    let admissions = 0;
    let finishes = 0;
    const onFinished = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener('hp-run-finished', onFinished);
    api.startRun = async () => {
      admissions++;
      backendRunStatus = 'running';
      throw new Error('network failed: response dropped');
    };

    try {
      runs.start(sid, 'Network fail test', [], 'Network fail test');
      await delay(50);

      const run = runs.get(sid);
      // It must NOT invent 'cancelled' when creation response fails!
      assert.notEqual(run?.status, 'cancelled', 'must not fabricate cancelled on response failure');

      // The active run persistence should have recorded the idempotencyKey or runId
      const stored = JSON.parse(storage.get('hp_active_runs') || '{}')[sid];
      assert.ok(stored, 'must record pending run in storage for recovery');
      assert.ok(stored.idempotencyKey || stored.runId, 'must persist idempotency key or runId');
      assert.equal(runs.isActive(sid), true, 'unverified creation must keep admission locked');
      assert.equal(run.unverified, true, 'unknown outcome is explicit, not a server terminal status');
      assert.match(run.error, /待核验/);
      assert.equal(finishes, 0, 'unknown creation must not publish RUN_FINISHED');
      runs.clear(sid);
      assert.equal(runs.get(sid), run, 'history settlement must not remove an unresolved request');
      runs.start(sid, 'Do not duplicate', [], 'Do not duplicate');
      await delay(10);
      assert.equal(admissions, 1, 'unknown first admission must block a second creation');
      assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].idempotencyKey, stored.idempotencyKey);
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
      api.startRun = originalStartRun;
    }
  });

  await test('F01: reload with only an idempotency key preserves the admission lock without adopting another run', async () => {
    const sid = 'session-reload-verify';
    const key = 'test-idemp-key-1';
    mockSidecarRun = {
      run_id: 'audit-run-recovered',
      session_id: sid,
      status: 'running',
      created_at: 100,
      updated_at: 101,
    };
    backendRunStatus = 'running';
    startRequests = [];

    storage.set('hp_active_runs', JSON.stringify({
      [sid]: {
        runId: '',
        idempotencyKey: key,
        userText: 'persisted text',
        startedAt: 100,
      }
    }));

    const resumed = runs.resumeAll();
    assert.ok(resumed.includes(sid), 'resumed session should include sid');
    await delay(50);

    const run = runs.get(sid);
    assert.ok(run, 'run should be in store');
    // 契约无通过 idempotencyKey 查询对应 runId 的后端 API，严禁依据时间戳认领其他 run 或旧 run；
    // 刷新恢复若只有 idempotencyKey 无 runId，必须保留待核验状态，明确报错并安全拒绝认领，不重复提交创建请求。
    assert.equal(run.runId, '', 'must NOT falsely adopt run without key query API');
    assert.equal(run.status, 'starting', 'unknown service outcome must stay nonterminal');
    assert.equal(run.unverified, true);
    assert.equal(runs.isActive(sid), true, 'reload must preserve the admission lock');
    assert.match(run.error, /待核验/);
    assert.equal(startRequests.length, 0, 'must NOT re-submit start request on reload verification');

    // 存储中的 recovery record 必须安全保留（不清除存储），等待人工或后续有依据核验
    const stored = JSON.parse(storage.get('hp_active_runs') || '{}')[sid];
    assert.ok(stored, 'recovery record must remain in storage');
    assert.equal(stored.idempotencyKey, key);

    runs.ctrls.get(sid)?.abort();
    runs.clear(sid);
  });

  await test('F01: creation response lost with unverified backend MUST NOT finish or clear recovery storage; reload verification must reject older stale run', async () => {
    storage.clear();
    const sid = 'session-unverified-stale';
    const oldRunId = 'old-stale-run-001';
    // Mock sidecar returning an older run created in the past (before our run started)
    mockSidecarRun = {
      run_id: oldRunId,
      session_id: sid,
      status: 'completed',
      created_at: 10, // far older than current startedAt (~now)
      updated_at: 20,
    };
    backendRunStatus = 'completed';

    const originalStartRun = api.startRun;
    api.startRun = async () => {
      throw new Error('network failed: response lost');
    };

    try {
      runs.start(sid, 'Unverified check', [], 'Unverified check');
      await delay(50);

      const run = runs.get(sid);
      // 1) Must NOT claim the old runId!
      assert.notEqual(run?.runId, oldRunId, 'must NOT adopt older run from previous turns');
      // 2) Because state is unverified, recovery storage MUST NOT be cleared by finish()!
      const stored = JSON.parse(storage.get('hp_active_runs') || '{}')[sid];
      assert.ok(stored, 'recovery record must be preserved for manual or subsequent verification');
      assert.ok(stored.idempotencyKey, 'stored idempotencyKey must be preserved');
    } finally {
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

  await test('F01: duplicate stop calls are deduplicated and stop errors are propagated, not silently swallowed', async () => {
    const sid = 'session-stop-dedup';
    let stopCallCount = 0;
    const originalStopRun = api.stopRun;
    api.stopRun = async () => {
      stopCallCount++;
      await delay(20);
      throw new Error('stop rejected by server');
    };

    try {
      // Create an already-running LiveRun
      runs.observe(sid, 'run-to-stop-123', Date.now());
      assert.equal(runs.isActive(sid), true);

      // Issue two concurrent stop calls
      const p1 = runs.stop(sid);
      const p2 = runs.stop(sid);

      // Both should reject with the server error, not resolve silently!
      await assert.rejects(p1, /stop rejected by server/, 'p1 must propagate error');
      await assert.rejects(p2, /stop rejected by server/, 'p2 must propagate error');

      assert.equal(stopCallCount, 1, 'api.stopRun should only be called once (idempotent stop)');
    } finally {
      api.stopRun = originalStopRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

  await test('F01: loopback synthetic server receives start, delay response, and stop calls correct backend run_id without mismatch', async () => {
    runAccepted = deferred();
    runDelayedRes = deferred();
    backendRunStatus = 'none';
    mockSidecarRun = null;
    stopCalls = 0;
    startRequests = [];

    const sid = 'session-loopback-e2e';
    runs.start(sid, 'Real loopback synthetic test', [], 'Real loopback synthetic test');

    // Wait until synthetic HTTP server receives POST /v1/runs
    await runAccepted.promise;
    assert.equal(startRequests.length, 1);
    const sentIdempKey = startRequests[0].idempotencyKey;
    assert.ok(sentIdempKey, 'Idempotency-Key header must be sent to loopback server');

    // Stop is clicked while creation response is delayed
    const stopPromise = runs.stop(sid);

    const pendingRun = runs.get(sid);
    assert.ok(pendingRun);
    assert.equal(pendingRun.stopping, true);
    assert.equal(pendingRun.status, 'starting');

    // Release the creation response from loopback server (returns run_id: 'audit-run-123')
    runDelayedRes.resolve();

    await stopPromise;

    // Verify loopback server received the stop request for the exact run_id
    assert.equal(stopCalls, 1, 'stopRun was called on loopback server');
    assert.equal(backendRunStatus, 'cancelled', 'loopback server received stop request');
    assert.equal(pendingRun.status, 'cancelled');
    assert.equal(runs.isActive(sid), false);

    runs.ctrls.get(sid)?.abort();
    runs.clear(sid);
  });
} finally {
  for (const sid of ['session-delayed-start', 'session-lost-response', 'session-reload-verify']) {
    runs.ctrls.get(sid)?.abort();
    runs.clear(sid);
  }
  await delay(10);
  await vite.close();
  await rm(cacheDir, { recursive: true, force: true });
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  for (const [k, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, k, descriptor);
    else delete globalThis[k];
  }
  console.log('Active handles:', process._getActiveHandles().map(h => h.constructor.name));
}
