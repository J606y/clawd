import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const keys = ['document', 'window', 'fetch', 'navigator', 'localStorage'];
const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const storage = new Map();
const browser = new EventTarget();
Object.assign(browser, { setTimeout, clearTimeout, localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) } });
const doc = new EventTarget();
Object.assign(doc, { baseURI: 'https://panel.example.test/', visibilityState: 'visible' });
for (const [key, value] of Object.entries({ document: doc, window: browser, localStorage: browser.localStorage, navigator: { onLine: true } })) {
  Object.defineProperty(globalThis, key, { configurable: true, value });
}
const cacheDir = await mkdtemp(join(tmpdir(), 'panel-terminal-test-'));
const server = await createServer({ configFile: false, cacheDir, server: { middlewareMode: true }, appType: 'custom' });
const tick = () => new Promise(resolve => setImmediate(resolve));
try {
  const { runs, RUN_FINISHED } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { api, ApiError } = await server.ssrLoadModule('/src/lib/api.ts');
  const nativeApi = { approve: api.approve, runStatus: api.runStatus };
  const { sidecar } = await server.ssrLoadModule('/src/lib/sidecar.ts');

  await test('follow status 404 uses the same run completed result from panel-core', async () => {
    const sid = 'follow-missing-completed';
    const nativeSessionRun = sidecar.sessionRun;
    let reads = 0;
    let statusSignal;
    globalThis.fetch = async () => new Response('', { headers: { 'Content-Type': 'text/event-stream' } });
    api.runStatus = async (_id, signal) => {
      statusSignal = signal;
      throw new ApiError('server', 404, 'not found');
    };
    sidecar.sessionRun = async (sessionId, signal) => {
      reads++;
      assert.equal(sessionId, sid);
      assert.equal(signal, statusSignal);
      return { busy: false, run: { run_id: `run-${sid}`, session_id: sid, status: 'completed', created_at: 1, updated_at: 2 } };
    };
    runs.observe(sid, `run-${sid}`, Date.now());
    const run = runs.get(sid);
    try {
      for (let n = 0; n < 30 && run.status === 'running'; n++) await tick();
      assert.equal(run.status, 'completed');
      assert.equal(run.error, '');
      assert.equal(reads, 1);
    } finally {
      runs.clear(sid);
      sidecar.sessionRun = nativeSessionRun;
      api.runStatus = nativeApi.runStatus;
    }
  });
  const uncertainResult = '无法确认这次运行的结果（运行记录已被清理或服务已重启），请以上方历史为准';
  for (const scenario of [
    { name: 'missing', record: null },
    { name: 'throws', throws: true },
    { name: 'different-run', record: { run_id: 'another-run', status: 'completed' } },
    { name: 'running', status: 'running' },
    { name: 'cancelled', status: 'cancelled', expected: 'cancelled', error: '' },
    { name: 'failed', status: 'failed', expected: 'failed', error: '执行失败' },
    { name: 'interrupted', status: 'interrupted', expected: 'failed', error: '回合被中断' },
    { name: 'abort-reject', abort: true, throws: true, expected: 'cancelled', error: '' },
    { name: 'abort-resolve', abort: true, status: 'completed', expected: 'cancelled', error: '' },
  ]) {
    await test(`follow status 404 sidecar ${scenario.name}`, async () => {
      const sid = `follow-missing-${scenario.name}`;
      const nativeSessionRun = sidecar.sessionRun;
      let reads = 0;
      let statusSignal;
      let lookupSignal;
      let lookupSession;
      let finished = 0;
      const onFinish = e => { if (e.detail === sid) finished++; };
      runs.addEventListener(RUN_FINISHED, onFinish);
      globalThis.fetch = async () => new Response('', { headers: { 'Content-Type': 'text/event-stream' } });
      api.runStatus = async (_id, signal) => {
        statusSignal = signal;
        throw new ApiError('server', 404, 'not found');
      };
      sidecar.sessionRun = async (sessionId, signal) => {
        reads++;
        lookupSession = sessionId;
        lookupSignal = signal;
        if (scenario.abort) runs.ctrls.get(sid).abort();
        if (scenario.throws) throw new Error('sidecar unavailable');
        return { busy: scenario.status === 'running', run: scenario.status
          ? { run_id: `run-${sid}`, session_id: sid, status: scenario.status, created_at: 1, updated_at: 2 }
          : scenario.record };
      };
      runs.observe(sid, `run-${sid}`, Date.now());
      const run = runs.get(sid);
      try {
        for (let n = 0; n < 30 && run.status === 'running'; n++) await tick();
        assert.equal(run.status, scenario.expected ?? 'failed');
        assert.equal(run.error, scenario.error ?? uncertainResult);
        assert.equal(run.approval, null);
        assert.equal(reads, 1);
        assert.equal(lookupSession, sid);
        assert.equal(lookupSignal, statusSignal);
        assert.equal(finished, 1);
        assert.equal(JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid], undefined);
      } finally {
        runs.clear(sid);
        runs.removeEventListener(RUN_FINISHED, onFinish);
        sidecar.sessionRun = nativeSessionRun;
        api.runStatus = nativeApi.runStatus;
      }
    });
  }

  function streamFixture(sessionId) {
    let streamController;
    let streamClosed = false;
    let aborted = false;
    globalThis.fetch = async (_url, init) => {
      const body = new ReadableStream({ start(controller) {
        streamController = controller;
        init.signal.addEventListener('abort', () => {
          aborted = true;
          if (!streamClosed) { streamClosed = true; controller.error(new DOMException('Aborted', 'AbortError')); }
        }, { once: true });
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ event: 'approval.request', command: 'synthetic command', choices: ['once', 'deny'], request_id: 'synthetic-approval', execution_generation: 4 })}\n\n`));
      } });
      return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
    };
    runs.observe(sessionId, `run-${sessionId}`, Date.now());
    return {
      run: runs.get(sessionId),
      get aborted() { return aborted; },
      send(data) { streamController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`)); },
      close() { if (!streamClosed) { streamClosed = true; streamController.close(); } },
    };
  }
  async function waitApproval(run) {
    for (let n = 0; n < 30 && !run.approval; n++) await tick();
    assert.ok(run.approval, 'the real SSE consumer must receive the synthetic approval');
  }

  await test('approval 409 confirmed completed closes a quiet SSE and reconciles exactly once', async () => {
    const sid = 'terminal-completed';
    const fixture = streamFixture(sid);
    let finished = 0;
    const onFinish = e => { if (e.detail === sid) finished++; };
    runs.addEventListener(RUN_FINISHED, onFinish);
    api.approve = async () => { throw new ApiError('server', 409, 'already resolved'); };
    api.runStatus = async () => ({ run_id: `run-${sid}`, session_id: sid, status: 'completed' });
    try {
      await waitApproval(fixture.run);
      await runs.respondApproval(sid, 'once');
      await tick();
      assert.equal(fixture.aborted, true, 'confirmed terminal status must close the old subscription');
      assert.equal(fixture.run.status, 'completed', 'subscription abort must not overwrite the confirmed terminal status');
      assert.equal(finished, 1, 'terminal reconciliation must not depend on a new SSE frame');
      assert.equal(JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid], undefined);
      fixture.close();
      await tick();
      assert.equal(finished, 1, 'consumer cleanup must not emit another terminal event');
    } finally {
      fixture.close();
      await tick();
      runs.clear(sid);
      runs.removeEventListener(RUN_FINISHED, onFinish);
    }
  });

  await test('approval 409 followed by status 404 finishes as failed without waiting for SSE', async () => {
    const sid = 'terminal-missing';
    const fixture = streamFixture(sid);
    let finished = 0;
    const onFinish = e => { if (e.detail === sid) finished++; };
    runs.addEventListener(RUN_FINISHED, onFinish);
    api.approve = async () => { throw new ApiError('server', 409, 'expired'); };
    api.runStatus = async () => { throw new ApiError('server', 404, 'not found'); };
    try {
      await waitApproval(fixture.run);
      await runs.respondApproval(sid, 'once');
      await tick();
      assert.equal(fixture.aborted, true);
      assert.equal(fixture.run.status, 'failed');
      assert.match(fixture.run.error, /运行记录已不存在/);
      assert.equal(finished, 1);
      assert.equal(JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid], undefined);
    } finally {
      fixture.close();
      await tick();
      runs.clear(sid);
      runs.removeEventListener(RUN_FINISHED, onFinish);
    }
  });

  await test('late approval status cannot replace a terminal outcome already confirmed by SSE', async () => {
    const sid = 'terminal-late-status';
    const fixture = streamFixture(sid);
    let resolveStatus;
    api.approve = async () => { throw new ApiError('server', 409, 'expired'); };
    api.runStatus = () => new Promise(resolve => { resolveStatus = resolve; });
    try {
      await waitApproval(fixture.run);
      const replying = runs.respondApproval(sid, 'once');
      for (let n = 0; n < 30 && !resolveStatus; n++) await tick();
      assert.ok(resolveStatus);
      fixture.send({ event: 'run.completed' });
      await tick();
      assert.equal(fixture.run.status, 'completed');
      resolveStatus({ status: 'failed', error: 'stale result' });
      await replying;
      assert.equal(fixture.run.status, 'completed');
      assert.equal(fixture.run.error, '');
    } finally {
      resolveStatus?.({ status: 'completed' });
      fixture.close();
      await tick();
      runs.clear(sid);
    }
  });

  await test('approval conflict with an active status keeps the subscription and does not publish finish', async () => {
    const sid = 'terminal-still-active';
    const fixture = streamFixture(sid);
    let finished = 0;
    const onFinish = e => { if (e.detail === sid) finished++; };
    runs.addEventListener(RUN_FINISHED, onFinish);
    api.approve = async () => { throw new ApiError('server', 409, 'resolved elsewhere'); };
    api.runStatus = async () => ({ status: 'running' });
    try {
      await waitApproval(fixture.run);
      await runs.respondApproval(sid, 'once');
      assert.equal(fixture.run.approval, null);
      assert.equal(fixture.run.status, 'running');
      assert.equal(fixture.aborted, false);
      assert.equal(finished, 0);
    } finally {
      api.runStatus = async () => ({ status: 'completed' });
      fixture.close();
      await tick();
      runs.clear(sid);
      runs.removeEventListener(RUN_FINISHED, onFinish);
    }
  });

  await test('late approval lookup cannot clear a replacement run in the same session', async () => {
    const sid = 'terminal-replaced';
    const fixture = streamFixture(sid);
    let resolveStatus;
    api.approve = async () => { throw new ApiError('server', 409, 'expired'); };
    api.runStatus = () => new Promise(resolve => { resolveStatus = resolve; });
    let replacement;
    try {
      await waitApproval(fixture.run);
      const replying = runs.respondApproval(sid, 'once');
      for (let n = 0; n < 30 && !resolveStatus; n++) await tick();
      fixture.send({ event: 'run.completed' });
      await tick();
      runs.clear(sid);
      replacement = streamFixture(sid);
      await waitApproval(replacement.run);
      const replacementController = runs.ctrls.get(sid);
      resolveStatus({ status: 'failed', error: 'stale result' });
      await replying;
      assert.equal(runs.get(sid), replacement.run);
      assert.equal(replacement.run.status, 'running');
      assert.equal(runs.ctrls.get(sid), replacementController);
      assert.equal(replacement.aborted, false);
      assert.equal(JSON.parse(storage.get('hp_active_runs'))[sid].runId, replacement.run.runId);
    } finally {
      resolveStatus?.({ status: 'completed' });
      api.runStatus = async () => ({ status: 'completed' });
      fixture.close();
      replacement?.close();
      await tick();
      runs.clear(sid);
    }
  });

  await test('late follow response cannot overwrite a terminal already published by approval reconciliation', async () => {
    const sid = 'terminal-native-follow';
    const runId = `run-${sid}`;
    api.approve = nativeApi.approve;
    api.runStatus = nativeApi.runStatus;
    let statusReads = 0;
    let staleBody;
    let delivered = false;
    let finished = 0;
    const onFinish = e => { if (e.detail === sid) finished++; };
    runs.addEventListener(RUN_FINISHED, onFinish);
    globalThis.fetch = async (input) => {
      const url = new URL(input);
      if (url.pathname.endsWith('/events')) {
        return new Response(`data: ${JSON.stringify({ event: 'approval.request', command: 'synthetic', choices: ['once'], request_id: 'native-request', execution_generation: 4 })}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } });
      }
      if (url.pathname.endsWith('/approval')) return new Response(JSON.stringify({ error: 'expired' }), { status: 409 });
      statusReads++;
      if (statusReads === 1) return new Response(new ReadableStream({ start(controller) { staleBody = controller; } }));
      const response = new Response(JSON.stringify({ status: 'completed', run_id: runId, session_id: sid }));
      const readText = response.text.bind(response);
      response.text = () => {
        const pending = readText();
        pending.then(() => {
          if (!delivered) {
            delivered = true;
            staleBody.enqueue(new TextEncoder().encode(JSON.stringify({ status: 'failed', error: 'stale native response' })));
            staleBody.close();
          }
        });
        return pending;
      };
      return response;
    };
    runs.observe(sid, runId, Date.now());
    const run = runs.get(sid);
    try {
      await waitApproval(run);
      for (let n = 0; n < 30 && statusReads < 1; n++) await tick();
      assert.equal(statusReads, 1);
      await runs.respondApproval(sid, 'once');
      await tick();
      await tick();
      assert.equal(run.status, 'completed');
      assert.equal(run.error, '');
      assert.equal(finished, 1);
    } finally {
      if (!delivered && staleBody) { staleBody.close(); delivered = true; }
      await tick();
      runs.clear(sid);
      runs.removeEventListener(RUN_FINISHED, onFinish);
    }
  });

  await test('canonical approval uses prompt_id and execution_generation and does not widen the choice', async () => {
    const sid = 'canonical-approval-body';
    const posted = [];
    api.approve = async (_runId, choice, requestId, generation) => {
      posted.push({ choice, requestId, generation });
    };
    globalThis.fetch = async () => new Response(
      `data: ${JSON.stringify({ event: 'approval.request', prompt_id: 'prompt-node', execution_generation: 4, command: 'synthetic', choices: ['once', 'deny'] })}\n\n`,
      { headers: { 'Content-Type': 'text/event-stream' } },
    );
    runs.observe(sid, `run-${sid}`, Date.now());
    const run = runs.get(sid);
    try {
      for (let n = 0; n < 30 && !run.approval; n++) await tick();
      assert.equal(run.approval.requestId, 'prompt-node');
      assert.equal(run.approval.executionGeneration, 4);
      await runs.respondApproval(sid, 'session');
      assert.equal(posted.length, 0, 'a choice outside the offered set must not be submitted');
      assert.ok(run.approval, 'refusing an unoffered choice keeps the card');
      await runs.respondApproval(sid, 'once');
      assert.deepEqual(posted, [{ choice: 'once', requestId: 'prompt-node', generation: 4 }]);
      assert.equal(run.approval, null);
    } finally {
      runs.ctrls.get(sid)?.abort();
      await tick();
      runs.clear(sid);
      api.approve = nativeApi.approve;
    }
  });
} finally {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
  for (const [key, descriptor] of Object.entries(previous)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
