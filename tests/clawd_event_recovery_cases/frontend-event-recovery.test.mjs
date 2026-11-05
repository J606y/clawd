// 未运行契约：加载真实 apps/panel/src/react/lib reducer。
// 不调用 listen，不访问模型，不把夹具成功体当成核心回合成功。
// 审批/续跑竞态不在这里。

import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const panelRoot = fileURLToPath(new URL('../../apps/panel/', import.meta.url));
const require = createRequire(path.join(panelRoot, 'package.json'));
const { createServer } = await import(pathToFileURL(require.resolve('vite')).href);

const storage = new Map();
const localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
const windowStub = new EventTarget();
windowStub.setTimeout = setTimeout;
windowStub.clearTimeout = clearTimeout;
windowStub.localStorage = localStorage;
Object.defineProperty(globalThis, 'window', { configurable: true, value: windowStub });
Object.defineProperty(globalThis, 'document', { configurable: true, value: { baseURI: 'https://panel.example.test/' } });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: localStorage });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });
globalThis.fetch = async (input) => {
  throw new Error(`unexpected network ${input}`);
};

let server;
let cacheDir;
let runs;
let getSse;
let RUN_FINISHED;

function frame(obj) {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function hanging() {
  return new Response(new ReadableStream({ start() {} }), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function openStream() {
  let controller;
  const stream = new ReadableStream({
    start(control) { controller = control; },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    push(obj) { controller.enqueue(enc.encode(frame(obj))); },
    close() { controller.close(); },
  };
}

function fresh(sessionId, runId) {
  return {
    sessionId,
    runId,
    status: 'running',
    userText: '',
    attachments: [],
    steers: [],
    thinking: '',
    steps: [],
    text: '',
    approval: null,
    clarify: null,
    error: '',
    stopping: false,
    startedAt: Date.now(),
    lastEventAt: Date.now(),
    model: '',
    provider: '',
  };
}

async function waitFor(predicate, ms = 3000) {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > ms) throw new Error('timed out waiting for reducer');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

function stop(sessionId) {
  runs.ctrls.get(sessionId)?.abort();
}

test.before('load real reducer', { timeout: 60_000 }, async () => {
  cacheDir = await mkdtemp(path.join(tmpdir(), 'clawd-event-recovery-'));
  server = await createServer({
    configFile: false,
    root: panelRoot,
    cacheDir,
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  if (typeof server.listen === 'function') {
    server.listen = () => { throw new Error('event recovery case must not listen'); };
  }
  ({ runs, RUN_FINISHED } = await server.ssrLoadModule('/src/react/lib/runs.ts'));
  ({ getSse } = await server.ssrLoadModule('/src/react/lib/sse.ts'));
  assert.equal(typeof runs.follow, 'function', 'runs.follow missing; do not guess a new subscription API');
  assert.equal(typeof runs.applyRunEvent, 'function', 'runs.applyRunEvent missing; do not guess a new reducer API');
  assert.equal(typeof runs.reconcile, 'function');
  assert.equal(typeof runs.recover, 'function');
  assert.equal(typeof runs.observe, 'function');
  assert.equal(typeof runs.start, 'function');
  assert.equal(typeof runs.ctrls?.get, 'function', 'emitted RunStore.ctrls missing; do not guess a cancel API');
  assert.equal(typeof getSse, 'function');
});

test.after(async () => {
  if (server) await server.close();
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true });
});

test('getSse yields the first frame before the rest of the body exists', async () => {
  let release = () => {};
  const gate = new Promise((resolve) => { release = resolve; });
  let secondWritten = false;
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      controller.enqueue(enc.encode(frame({ event: 'message.delta', seq: 1, delta: 'one' })));
      await gate;
      secondWritten = true;
      controller.enqueue(enc.encode(frame({ event: 'message.delta', seq: 2, delta: 'two' })));
      controller.close();
    },
  });
  globalThis.fetch = async () => new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  const gen = getSse('v1/runs/run_live/events?last_seq=-1', new AbortController().signal);
  const first = await gen.next();
  assert.equal(JSON.parse(first.value.data).delta, 'one');
  assert.equal(secondWritten, false, 'stream buffered the unread tail before yielding');
  release();
  const second = await gen.next();
  assert.equal(JSON.parse(second.value.data).delta, 'two');
  await gen.return();
});

test('duplicate seq and older seq are not applied; same terminal seq is a no-op', () => {
  const run = fresh('sess-dup', 'run-dup');
  const delta = { event: 'message.delta', seq: 1, delta: 'A', run_id: 'run-dup' };
  runs.applyRunEvent(run, delta);
  runs.applyRunEvent(run, delta);
  runs.applyRunEvent(run, { event: 'message.delta', seq: 2, delta: 'B', run_id: 'run-dup' });
  runs.applyRunEvent(run, { event: 'message.delta', seq: 1, delta: 'old', run_id: 'run-dup' });
  runs.applyRunEvent(run, { event: 'run.completed', seq: 3, run_id: 'run-dup' });
  runs.applyRunEvent(run, { event: 'run.completed', seq: 3, run_id: 'run-dup' });
  runs.applyRunEvent(run, { event: 'message.delta', seq: 2, delta: 'replay', run_id: 'run-dup' });
  assert.equal(run.text, 'AB');
  assert.equal(run.status, 'completed');
  assert.equal(run.usage.lastSeq, 3);
});

test('an older terminal does not complete, and a gap does not pretend the replay is complete', () => {
  const stale = fresh('sess-order', 'run-order');
  runs.applyRunEvent(stale, { event: 'message.delta', seq: 2, delta: 'new', run_id: 'run-order' });
  runs.applyRunEvent(stale, { event: 'run.completed', seq: 1, run_id: 'run-order' });
  assert.equal(stale.status, 'running');
  assert.equal(stale.text, 'new');

  const gap = fresh('sess-gap', 'run-gap');
  runs.applyRunEvent(gap, { event: 'message.delta', seq: 1, delta: 'A', run_id: 'run-gap' });
  runs.applyRunEvent(gap, { event: 'message.delta', seq: 3, delta: 'C', run_id: 'run-gap' });
  assert.equal(gap.text, 'AC');
  assert.equal(gap.usage.available, false, 'a seq gap must not keep a complete usage estimate');
  assert.equal(gap.status, 'running');
});

test('closing the event stream does not become completed', { timeout: 10_000 }, async () => {
  const run = fresh('sess-drop', 'run-drop');
  const ctrl = new AbortController();
  let polled = false;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      return new Response(new ReadableStream({
        start(controller) { controller.close(); },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      polled = true;
      ctrl.abort();
      return json({ status: 'running', run_id: run.runId, session_id: run.sessionId });
    }
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  await runs.follow(run, ctrl.signal);
  assert.equal(polled, true);
  assert.equal(run.status, 'running');
  assert.notEqual(run.status, 'completed');
  assert.equal(run.text, '');
});

test('reconnect resumes from last_seq and completes only on the later terminal event', { timeout: 20_000 }, async () => {
  const run = fresh('sess-cursor', 'run-cursor');
  const searches = [];
  let statusAtPoll = '';
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      searches.push(url.search);
      const seq = url.searchParams.get('last_seq');
      const enc = new TextEncoder();
      const body = seq === '1'
        ? frame({ event: 'message.delta', seq: 2, delta: 'B', run_id: run.runId })
          + frame({ event: 'run.completed', seq: 3, run_id: run.runId })
        : frame({ event: 'message.delta', seq: 1, delta: 'A', run_id: run.runId });
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(enc.encode(body));
          controller.close();
        },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      statusAtPoll = run.status;
      return json({ status: 'running', run_id: run.runId, session_id: run.sessionId });
    }
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  await runs.follow(run, new AbortController().signal);
  assert.deepEqual(searches, ['?last_seq=-1', '?last_seq=1']);
  assert.equal(statusAtPoll, 'running');
  assert.equal(run.text, 'AB');
  assert.equal(run.status, 'completed');
});

test('aborting one session subscription does not pollute the other session', { timeout: 10_000 }, async () => {
  const left = fresh('sess-left', 'run-left');
  const right = fresh('sess-right', 'run-right');
  const streams = new Map([
    ['run-left', openStream()],
    ['run-right', openStream()],
  ]);
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      const id = [...streams.keys()].find((runId) => url.pathname.includes(runId));
      assert.ok(id, url.pathname);
      return streams.get(id).response;
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  const leftCtrl = new AbortController();
  const rightCtrl = new AbortController();
  const leftDone = runs.follow(left, leftCtrl.signal);
  const rightDone = runs.follow(right, rightCtrl.signal);
  streams.get('run-left').push({ event: 'message.delta', seq: 1, delta: 'L', run_id: 'run-left' });
  streams.get('run-right').push({ event: 'message.delta', seq: 1, delta: 'R', run_id: 'run-right' });
  await waitFor(() => left.text === 'L' && right.text === 'R');
  leftCtrl.abort();
  await leftDone;
  streams.get('run-left').push({ event: 'run.completed', seq: 2, run_id: 'run-left' });
  streams.get('run-left').push({ event: 'message.delta', seq: 3, delta: 'nope', run_id: 'run-right' });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(left.status, 'running');
  assert.equal(left.text, 'L');
  assert.equal(right.status, 'running');
  assert.equal(right.text, 'R');
  assert.equal(right.runId, 'run-right');
  streams.get('run-right').push({ event: 'message.delta', seq: 2, delta: '2', run_id: 'run-right' });
  await waitFor(() => right.text === 'R2');
  rightCtrl.abort();
  await rightDone;
  assert.equal(left.text, 'L');
  assert.notEqual(left.status, 'completed');
  assert.notEqual(right.status, 'completed');
});

test('replacing a session aborts the old subscription and late events stay on the old object', { timeout: 10_000 }, async () => {
  const sessionId = 'sess-replace';
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) return hanging();
    throw new Error(`unexpected fetch ${url}`);
  };
  assert.equal(runs.observe(sessionId, 'run_old', Date.now()), true);
  const previous = runs.get(sessionId);
  const oldCtrl = runs.ctrls.get(sessionId);
  assert.ok(previous && oldCtrl);
  previous.status = 'cancelled';
  let finished = 0;
  const onFinished = (event) => { if (event.detail === sessionId) finished += 1; };
  runs.addEventListener(RUN_FINISHED, onFinished);
  try {
    runs.start(sessionId, 'next', [], 'next', '', '', { run_id: 'run_new', status: 'running' });
    assert.equal(oldCtrl.signal.aborted, true);
    assert.notEqual(runs.ctrls.get(sessionId), oldCtrl);
    const current = runs.get(sessionId);
    assert.equal(current.runId, 'run_new');
    assert.equal(current.status, 'running');
    runs.applyRunEvent(previous, { event: 'run.completed', seq: 4, delta: 'late', run_id: 'run_new' });
    assert.equal(runs.get(sessionId), current);
    assert.equal(current.status, 'running');
    assert.equal(current.text, '');
    assert.equal(current.runId, 'run_new');
    assert.equal(finished, 0, 'old subscription emitted a terminal event into the replacement');
  } finally {
    runs.removeEventListener(RUN_FINISHED, onFinished);
    stop(sessionId);
  }
});

test('reconcile rejects a run/session mismatch and still accepts an exact match', async () => {
  const sessionId = 'sess-reconcile';
  const runId = 'run_reconcile';
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) return hanging();
    throw new Error(`unexpected fetch ${url}`);
  };
  assert.equal(runs.observe(sessionId, runId, Date.now()), true);
  const run = runs.get(sessionId);
  run.text = 'keep';
  try {
    await runs.reconcile(sessionId, { busy: false, run: { run_id: 'run_other', session_id: sessionId, status: 'completed' } });
    await runs.reconcile(sessionId, { busy: false, run: { run_id: runId, session_id: 'sess-other', status: 'completed' } });
    await runs.reconcile(sessionId, { busy: true, run: { run_id: runId, session_id: sessionId, status: 'completed' } });
    assert.equal(run.status, 'running');
    assert.equal(run.text, 'keep');
    await runs.reconcile(sessionId, { busy: false, run: { run_id: runId, session_id: sessionId, status: 'completed' } });
    assert.equal(run.status, 'completed');
    assert.equal(run.text, 'keep');
  } finally {
    stop(sessionId);
  }
});

test('recover rejects a foreign session record and a foreign status identity', async () => {
  const sessionId = 'sess-recover';
  const key = 'idem-recover';
  const runId = `run_${'a'.repeat(32)}`;
  storage.set('hp_active_runs', JSON.stringify({
    [sessionId]: { runId: '', idempotencyKey: key, userText: 'pending' },
  }));
  const calls = [];
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    calls.push(url.pathname + url.search);
    if (url.pathname.includes('/sessions/run/resolve')) {
      return json({
        resolution: { idempotency_key: key, state: 'found' },
        run: { run_id: runId, session_id: 'sess-other', created_at: 10, updated_at: 11 },
      });
    }
    throw new Error(`status must not be read for a foreign session record: ${url}`);
  };
  try {
    runs.resumeAll();
    assert.equal(await runs.recover(sessionId), false);
    const run = runs.get(sessionId);
    assert.equal(run.runId, '');
    assert.equal(run.unverified, true);
    assert.notEqual(run.status, 'completed');
    assert.equal(calls.some((item) => item.includes('/v1/runs/')), false);
  } finally {
    stop(sessionId);
  }

  const other = 'sess-recover-status';
  storage.set('hp_active_runs', JSON.stringify({
    [other]: { runId: '', idempotencyKey: key, userText: 'pending' },
  }));
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/sessions/run/resolve')) {
      return json({
        resolution: { idempotency_key: key, state: 'found' },
        run: { run_id: runId, session_id: other, created_at: 10, updated_at: 11 },
      });
    }
    if (url.pathname.includes('/v1/runs/')) {
      return json({ status: 'completed', run_id: runId, session_id: 'sess-other' });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  try {
    runs.resumeAll();
    assert.equal(await runs.recover(other), false);
    const run = runs.get(other);
    assert.equal(run.runId, '');
    assert.notEqual(run.status, 'completed');
  } finally {
    stop(other);
  }
});

test('contract: a mismatched status poll must not mark the subscribed run completed', { timeout: 10_000 }, async () => {
  const run = fresh('sess-mismatch-poll', 'run-mismatch-poll');
  run.text = 'keep';
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      return new Response(new ReadableStream({
        start(controller) { controller.close(); },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      return json({ status: 'completed', run_id: 'run_other', session_id: 'sess-other' });
    }
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  await runs.follow(run, new AbortController().signal);
  assert.notEqual(run.status, 'completed', '断线后的状态体 run_id/session_id 与订阅不一致，不得写成 completed');
  assert.equal(run.text, 'keep');
  assert.equal(run.runId, 'run-mismatch-poll');
});

test('contract: a foreign run_id event must not be applied to this session', () => {
  const run = fresh('sess-foreign-event', 'run-mine');
  runs.applyRunEvent(run, { event: 'message.delta', seq: 1, delta: 'nope', run_id: 'run-other' });
  assert.equal(run.runId, 'run-mine', '事件 run_id 与订阅不一致，不得改写当前回合');
  assert.equal(run.text, '', '错配事件不得写入当前会话正文');
  assert.equal(run.status, 'running');
});
