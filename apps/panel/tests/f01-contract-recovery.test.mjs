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
const stoppedPaths = [];
let startRequests = [];

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
      res.end(JSON.stringify({ run_id: 'f01-run-123', status: 'running' }));
    }
  } else if (pathname.endsWith('/stop') && req.method === 'POST') {
    stopCalls++;
    stoppedPaths.push(pathname);
    backendRunStatus = 'cancelled';
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'cancelled', run_id: 'f01-run-123', ...(admittedSessionId ? { session_id: admittedSessionId } : {}) }));
  } else if (pathname.startsWith('/v1/runs/')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: backendRunStatus, run_id: 'f01-run-123', ...(admittedSessionId ? { session_id: admittedSessionId } : {}) }));
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

const cacheDir = await mkdtemp(join(tmpdir(), 'hp-f01-boundary-'));
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
      run_id: 'f01-run-123',
      session_id: sid,
      status: backendRunStatus,
      created_at: 100,
      updated_at: 101,
    } : null),
  });

  await test('F01-B1: lost response marks run as unverified starting, preserves idempotencyKey in storage, and emits NO RUN_FINISHED', async () => {
    storage.clear();
    const sid = 'session-f01-b1';
    let finishes = 0;
    const onFinished = e => { if (e.detail === sid) finishes++; };
    runs.addEventListener('hp-run-finished', onFinished);

    const originalStartRun = api.startRun;
    api.startRun = async () => {
      throw new Error('network failed: drop response');
    };

    try {
      runs.start(sid, 'B1 probe text', [], 'B1 probe text');
      await delay(40);

      const run = runs.get(sid);
      assert.ok(run, 'LiveRun must be created in store');
      assert.equal(run.unverified, true, 'lost response must be marked unverified');
      assert.equal(run.status, 'starting', 'status must remain nonterminal starting');
      assert.notEqual(run.status, 'failed', 'must not fabricate failed');
      assert.notEqual(run.status, 'cancelled', 'must not fabricate cancelled');
      assert.match(run.error, /待核验/);
      assert.equal(runs.isActive(sid), true, 'admission lock must be preserved');
      assert.equal(finishes, 0, 'RUN_FINISHED must not be emitted for unverified run');

      const stored = JSON.parse(storage.get('hp_active_runs') || '{}')[sid];
      assert.ok(stored, 'storage entry must NOT be cleared on unverified');
      assert.ok(stored.idempotencyKey, 'idempotencyKey must be recorded in storage');
      assert.equal(stored.runId, '', 'runId must remain empty until server confirms');
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

  await test('F01-B2: reload with only idempotencyKey preserves starting+unverified and single admission lock without guessing other run', async () => {
    storage.clear();
    const sid = 'session-f01-b2';
    const key = 'test-idemp-key-b2';
    mockSidecarRun = {
      run_id: 'sidecar-foreign-run-999',
      session_id: sid,
      status: 'running',
      created_at: Date.now() - 5000,
      updated_at: Date.now() - 4000,
    };
    backendRunStatus = 'running';

    storage.set('hp_active_runs', JSON.stringify({
      [sid]: {
        runId: '',
        idempotencyKey: key,
        userText: 'persisted text b2',
        startedAt: Date.now() - 1000,
      }
    }));

    const resumed = runs.resumeAll();
    assert.ok(resumed.includes(sid), 'session must be resumed');
    await delay(40);

    const run = runs.get(sid);
    assert.ok(run, 'run must exist in memory');
    assert.equal(run.runId, '', 'must NOT adopt foreign runId without key query contract');
    assert.equal(run.unverified, true, 'must be unverified');
    assert.equal(run.status, 'starting', 'must remain starting');
    assert.equal(runs.isActive(sid), true, 'single admission lock must be held');
    assert.match(run.error, /待核验/);

    // Admission lock prevents concurrent createSession / startRun
    let duplicateStartCalls = 0;
    const originalStartRun = api.startRun;
    api.startRun = async () => {
      duplicateStartCalls++;
      return { run_id: 'wrong-run', status: 'started' };
    };

    try {
      runs.start(sid, 'Second start attempt', [], 'Second start attempt');
      await delay(20);
      assert.equal(duplicateStartCalls, 0, 'admission lock must reject duplicate start');
    } finally {
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

  await test('F01-B3: exact run ownership check rejects older stale runs and does not stop foreign run', async () => {
    storage.clear();
    const sid = 'session-f01-b3';
    mockSidecarRun = {
      run_id: 'old-stale-run-historical',
      session_id: sid,
      status: 'completed',
      created_at: 10,
      updated_at: 20,
    };
    backendRunStatus = 'completed';

    const originalStartRun = api.startRun;
    api.startRun = async () => {
      throw new Error('network failed: connection reset');
    };

    try {
      runs.start(sid, 'Test stale rejection', [], 'Test stale rejection');
      await delay(40);

      const run = runs.get(sid);
      assert.notEqual(run?.runId, 'old-stale-run-historical', 'must not adopt stale run');
      assert.equal(run?.unverified, true);

      // Explicit stop must release the local lock without stopping old-stale-run-historical or any unknown id.
      let stoppedForeignRun = false;
      const originalStopRun = api.stopRun;
      api.stopRun = async (targetRunId) => {
        stoppedForeignRun = true;
        assert.notEqual(targetRunId, 'old-stale-run-historical');
        return { status: 'cancelled' };
      };

      try {
        await runs.stop(sid);
        assert.equal(stoppedForeignRun, false, 'must NEVER call stopRun on an unverified or older runId');
        assert.equal(runs.isActive(sid), false, 'explicit stop releases the local lock');
        assert.equal(run?.stopping, false);
        assert.equal(run?.status, 'starting', 'must not fabricate a terminal status');
        assert.equal(run?.unverified, true);
      } finally {
        api.stopRun = originalStopRun;
      }
    } finally {
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

  await test('F01-B4: late reply after client stop awaits exact runId and cancels backend run', async () => {
    runAccepted = deferred();
    runDelayedRes = deferred();
    backendRunStatus = 'none';
    mockSidecarRun = null;
    stopCalls = 0;
    startRequests = [];

    const sid = 'session-f01-b4';
    runs.start(sid, 'Late reply test', [], 'Late reply test');

    await runAccepted.promise;
    assert.equal(startRequests.length, 1);
    const sentKey = startRequests[0].idempotencyKey;
    assert.ok(sentKey, 'Idempotency-Key must be sent in header');

    // Trigger stop while creation request is in-flight
    const stopPromise = runs.stop(sid);
    const pendingRun = runs.get(sid);
    assert.equal(pendingRun.stopping, true);
    assert.equal(pendingRun.status, 'starting');

    // Release late reply from backend
    runDelayedRes.resolve();
    await stopPromise;

    assert.equal(stopCalls, 1, 'must have called stopRun for the exact admitted run');
    assert.deepEqual(stoppedPaths, ['/v1/runs/f01-run-123/stop'], 'stop must address the exact admitted ID');
    assert.equal(pendingRun.runId, 'f01-run-123', 'exact runId must be bound');
    assert.equal(pendingRun.status, 'cancelled');
    assert.equal(runs.isActive(sid), false);

    runs.ctrls.get(sid)?.abort();
    runs.clear(sid);
  });

  await test('F01-B5: stop on unverified run resets stopping to false, dispatches changed, and observer snapshots stopping=false', async () => {
    storage.clear();
    const sid = 'session-f01-b5';
    let finishes = 0;
    const snapshots = [];
    const onFinished = e => { if (e.detail === sid) finishes++; };
    const onChanged = e => {
      if (e.detail === sid) {
        const r = runs.get(sid);
        snapshots.push({ stopping: r?.stopping, status: r?.status, unverified: r?.unverified });
      }
    };
    runs.addEventListener('hp-run-finished', onFinished);
    runs.addEventListener('hp-run-changed', onChanged);

    const originalStartRun = api.startRun;
    api.startRun = async () => {
      throw new Error('network failed: drop response');
    };

    try {
      runs.start(sid, 'B5 probe text', [], 'B5 probe text');
      await delay(40);

      const run = runs.get(sid);
      assert.ok(run, 'LiveRun must exist');
      assert.equal(run.unverified, true, 'run must be unverified');
      assert.equal(run.status, 'starting');
      snapshots.length = 0;
      const storedBefore = JSON.parse(storage.get('hp_active_runs'))[sid];
      const callsBefore = stopCalls;

      await runs.stop(sid);

      assert.equal(run.stopping, false, 'run.stopping must be false after local release');
      assert.ok(snapshots.length > 0, 'RUN_CHANGED must be dispatched when stopping is reset to false');
      const lastSnapshot = snapshots[snapshots.length - 1];
      assert.equal(lastSnapshot.stopping, false, 'latest public snapshot must have stopping=false');
      assert.equal(lastSnapshot.status, 'starting', 'must not fabricate a terminal status');
      assert.notEqual(lastSnapshot.status, 'cancelled');
      assert.notEqual(lastSnapshot.status, 'completed');
      assert.notEqual(lastSnapshot.status, 'failed');
      assert.equal(lastSnapshot.unverified, true);
      assert.deepEqual(JSON.parse(storage.get('hp_active_runs'))[sid], storedBefore, 'preserve original recovery record');
      assert.equal(run.idempotencyKey, storedBefore.idempotencyKey);
      assert.equal(runs.isActive(sid), false, 'explicit stop releases the local admission lock');
      assert.equal(stopCalls, callsBefore, 'no stop request for an unknown run');
      assert.equal(finishes, 0, 'zero false RUN_FINISHED');
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
      runs.removeEventListener('hp-run-changed', onChanged);
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });


  await test('F01-B6: transition to unverified while waiting for run_id resets stopping to false, dispatches changed, and observer snapshots stopping=false', async () => {
    storage.clear();
    const sid = 'session-f01-b6';
    let finishes = 0;
    const snapshots = [];
    const onFinished = e => { if (e.detail === sid) finishes++; };
    const onChanged = e => {
      if (e.detail === sid) {
        const r = runs.get(sid);
        snapshots.push({ stopping: r?.stopping, status: r?.status, unverified: r?.unverified });
      }
    };
    runs.addEventListener('hp-run-finished', onFinished);
    runs.addEventListener('hp-run-changed', onChanged);

    let slowStartDeferred = deferred();
    const originalStartRun = api.startRun;
    api.startRun = async () => {
      await slowStartDeferred.promise;
      throw new Error('network failed: drop response after start');
    };

    try {
      runs.start(sid, 'B6 probe text', [], 'B6 probe text');
      await delay(20);

      const run = runs.get(sid);
      assert.ok(run, 'LiveRun must exist');
      assert.equal(Boolean(run.unverified), false, 'initially not unverified');
      assert.equal(run.status, 'starting');
      const storedBefore = JSON.parse(storage.get('hp_active_runs'))[sid];
      const callsBefore = stopCalls;

      // Call stop while startRun is in-flight and runId is not yet available
      const stopPromise = runs.stop(sid);
      assert.equal(run.stopping, true, 'run.stopping must be true while stop is pending');

      snapshots.length = 0;

      // Now release startRun with error -> transition to unverified
      slowStartDeferred.resolve();

      await stopPromise;

      assert.equal(run.stopping, false, 'run.stopping must be false after local release in wait phase');
      assert.ok(snapshots.length > 0, 'RUN_CHANGED must be dispatched when stopping is reset to false');
      const lastSnapshot = snapshots[snapshots.length - 1];
      assert.equal(lastSnapshot.stopping, false, 'latest public snapshot must have stopping=false');
      assert.equal(lastSnapshot.unverified, true, 'snapshot reflects unverified=true');
      assert.equal(lastSnapshot.status, 'starting', 'status remains nonterminal starting');
      assert.notEqual(lastSnapshot.status, 'cancelled');
      assert.notEqual(lastSnapshot.status, 'completed');
      assert.notEqual(lastSnapshot.status, 'failed');
      assert.deepEqual(JSON.parse(storage.get('hp_active_runs'))[sid], storedBefore, 'preserve original recovery record');
      assert.equal(run.idempotencyKey, storedBefore.idempotencyKey);
      assert.equal(runs.isActive(sid), false, 'explicit stop releases the local admission lock');
      assert.equal(stopCalls, callsBefore, 'no stop request for an unknown run');
      assert.equal(finishes, 0, 'zero false RUN_FINISHED');
    } finally {
      runs.removeEventListener('hp-run-finished', onFinished);
      runs.removeEventListener('hp-run-changed', onChanged);
      api.startRun = originalStartRun;
      runs.ctrls.get(sid)?.abort();
      runs.clear(sid);
    }
  });

} finally {
  for (const sid of ['session-f01-b1', 'session-f01-b2', 'session-f01-b3', 'session-f01-b4']) {
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
  }
}
