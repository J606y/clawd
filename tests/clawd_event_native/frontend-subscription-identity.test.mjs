// 未运行配套：只加载真实 apps/panel/src/react/lib/runs.ts。
// 不 listen，不访问模型，不改原 tests/clawd_event_recovery_cases。
// 原准备用例仍是权威契约，这里不能拿来放宽或替代它们。

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

const windowStub = new EventTarget();
windowStub.setTimeout = setTimeout;
windowStub.clearTimeout = clearTimeout;
windowStub.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'window', { configurable: true, value: windowStub });
Object.defineProperty(globalThis, 'document', { configurable: true, value: { baseURI: 'https://panel.example.test/' } });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: windowStub.localStorage });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });
globalThis.fetch = async (input) => { throw new Error(`unexpected network ${input}`); };

let server;
let cacheDir;
let runs;

function frame(obj) {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
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

test.before('load real reducer', { timeout: 60_000 }, async () => {
  cacheDir = await mkdtemp(path.join(tmpdir(), 'clawd-event-native-'));
  server = await createServer({
    configFile: false,
    root: panelRoot,
    cacheDir,
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  if (typeof server.listen === 'function') {
    server.listen = () => { throw new Error('event native case must not listen'); };
  }
  ({ runs } = await server.ssrLoadModule('/src/react/lib/runs.ts'));
});

test.after(async () => {
  if (server) await server.close();
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true });
});

test('a foreign event does not advance seq or erase thinking', () => {
  const run = fresh('sess-think', 'run-think');
  runs.applyRunEvent(run, { event: 'reasoning.available', seq: 1, text: 'keep-thought', run_id: 'run-think' });
  runs.applyRunEvent(run, { event: 'message.delta', seq: 1, delta: 'nope', run_id: 'run-other' });
  runs.applyRunEvent(run, { event: 'message.delta', seq: 2, delta: 'B', run_id: 'run-think' });
  assert.equal(run.thinking, 'keep-thought');
  assert.equal(run.text, 'B');
  assert.equal(run.runId, 'run-think');
  assert.equal(run.usage.lastSeq, 2);
});

test('a finished run is not revived by a later started event or a late busy reconcile', async () => {
  const sessionId = 'sess-revive';
  const runId = 'run-revive';
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      return new Response(new ReadableStream({ start() {} }), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  assert.equal(runs.observe(sessionId, runId, 10_000), true);
  const run = runs.get(sessionId);
  runs.applyRunEvent(run, { event: 'run.completed', seq: 1, run_id: runId });
  runs.applyRunEvent(run, { event: 'run.started', seq: 2, run_id: runId });
  runs.applyRunEvent(run, { event: 'message.delta', seq: 3, delta: 'late', run_id: runId });
  assert.equal(run.status, 'completed');
  assert.equal(run.text, '');
  await runs.reconcile(sessionId, {
    busy: true,
    run: { run_id: runId, session_id: sessionId, status: 'running', created_at: 1, updated_at: 2 },
  });
  assert.equal(run.status, 'completed');
  assert.equal(runs.observe(sessionId, 'run-next', 11_000), true, 'a different run in the same session must still be observable');
  assert.equal(runs.get(sessionId).runId, 'run-next');
  assert.equal(runs.get(sessionId).status, 'running');
  runs.ctrls.get(sessionId)?.abort();
  runs.ctrls.get('sess-revive')?.abort();
});

test('the first events 404 is retried before the stream is abandoned', { timeout: 10_000 }, async () => {
  const run = fresh('sess-wait', 'run-wait');
  let eventAttempts = 0;
  const ctrl = new AbortController();
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      eventAttempts += 1;
      assert.equal(url.search, eventAttempts === 1 ? '?last_seq=-1' : '?last_seq=-1');
      assert.equal(url.searchParams.has('session_id'), false);
      if (eventAttempts === 1) {
        return new Response(JSON.stringify({ error: { message: 'not registered' } }), { status: 404 });
      }
      const enc = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(enc.encode(frame({ event: 'message.delta', seq: 1, delta: 'A', run_id: run.runId })));
          controller.close();
        },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      ctrl.abort();
      return json({ status: 'running', run_id: run.runId, session_id: run.sessionId });
    }
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  await runs.follow(run, ctrl.signal);
  assert.equal(eventAttempts, 2);
  assert.equal(run.text, 'A');
  assert.equal(run.thinking, '');
  assert.notEqual(run.streamGone, true);
  assert.equal(run.status, 'running');
  assert.equal(run.runId, 'run-wait');
});

test('a status body without identity is still settled; that is existing behavior, not a new rejection', { timeout: 10_000 }, async () => {
  const run = fresh('sess-absent', 'run-absent');
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes('/events')) {
      return new Response(new ReadableStream({
        start(controller) { controller.close(); },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) return json({ status: 'completed' });
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  await runs.follow(run, new AbortController().signal);
  assert.equal(run.status, 'completed');
  assert.equal(run.runId, 'run-absent');
});

test('a mismatched follow can resubscribe only the same run, with a bound and no foreign body', { timeout: 10_000 }, async () => {
  const sessionId = 'sess-resub';
  const runId = 'run-resub';
  let eventCalls = 0;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    assert.equal(url.searchParams.has('session_id'), false);
    if (url.pathname.includes('/events')) {
      eventCalls += 1;
      return new Response(new ReadableStream({
        start(controller) { controller.close(); },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      return json({
        status: 'completed',
        run_id: 'run-other',
        session_id: 'sess-other',
        output_text: 'foreign-body',
        error: 'foreign-error',
      });
    }
    if (url.pathname.includes('/sessions/run')) return json({ busy: true, run: null });
    throw new Error(`unexpected fetch ${url}`);
  };
  try {
    assert.equal(runs.observe(sessionId, runId, 20_000), true);
    const run = runs.get(sessionId);
    run.thinking = 'keep-thought';
    run.text = 'keep';
    await new Promise((resolve) => {
      const check = () => {
        if (!run.followDropped) return;
        runs.removeEventListener('hp-run-changed', check);
        resolve();
      };
      runs.addEventListener('hp-run-changed', check);
      check();
    });
    assert.equal(runs.resubscribe(sessionId, 'run-other', run.startedAt), false);
    assert.equal(runs.resubscribe(sessionId, runId, run.startedAt + 1), false);
    assert.equal(run.status, 'running');
    assert.equal(run.thinking, 'keep-thought');
    assert.equal(run.text, 'keep');
    assert.notEqual(run.streamGone, true);
    const before = eventCalls;
    assert.equal(runs.resubscribe(sessionId, runId, run.startedAt), true);
    await new Promise((resolve) => {
      const check = () => {
        if (!String(run.error).includes('身份不一致')) return;
        runs.removeEventListener('hp-run-changed', check);
        resolve();
      };
      runs.addEventListener('hp-run-changed', check);
      check();
    });
    assert.equal(run.status, 'running');
    assert.equal(run.runId, runId);
    assert.equal(run.thinking, 'keep-thought');
    assert.equal(run.text, 'keep');
    assert.ok(eventCalls > before);
    assert.ok(eventCalls <= before + 3);
    const after = eventCalls;
    assert.equal(runs.resubscribe(sessionId, runId, run.startedAt), false);
    assert.equal(eventCalls, after);
    runs.applyRunEvent(run, { event: 'run.completed', seq: 1, run_id: runId });
    assert.equal(run.status, 'completed');
    assert.equal(runs.resubscribe(sessionId, runId, run.startedAt), false);
    runs.applyRunEvent(run, { event: 'run.started', seq: 2, run_id: runId });
    assert.equal(run.status, 'completed');
    assert.equal(runs.observe(sessionId, 'run-next', 21_000), true);
    assert.equal(runs.get(sessionId).runId, 'run-next');
    assert.equal(runs.get(sessionId).status, 'running');
  } finally {
    runs.ctrls.get(sessionId)?.abort();
  }
});

test('aborting a resubscribe does not apply the foreign status body', { timeout: 10_000 }, async () => {
  const sessionId = 'sess-resub-cancel';
  const runId = 'run-resub-cancel';
  let eventCalls = 0;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    assert.equal(url.searchParams.has('session_id'), false);
    if (url.pathname.includes('/events')) {
      eventCalls += 1;
      return new Response(new ReadableStream({
        start(controller) { controller.close(); },
      }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.pathname.includes('/v1/runs/')) {
      return json({ status: 'completed', run_id: 'run-other', session_id: 'sess-other', output_text: 'nope' });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  try {
    assert.equal(runs.observe(sessionId, runId, 22_000), true);
    const run = runs.get(sessionId);
    run.thinking = 'keep-thought';
    await new Promise((resolve) => {
      const check = () => {
        if (!run.followDropped) return;
        runs.removeEventListener('hp-run-changed', check);
        resolve();
      };
      runs.addEventListener('hp-run-changed', check);
      check();
    });
    const before = eventCalls;
    const cancel = new AbortController();
    assert.equal(runs.resubscribe(sessionId, runId, run.startedAt, cancel.signal), true);
    cancel.abort();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(eventCalls, before);
    assert.equal(run.status, 'running');
    assert.equal(run.thinking, 'keep-thought');
    assert.equal(run.text, '');
    assert.notEqual(run.status, 'completed');
  } finally {
    runs.ctrls.get(sessionId)?.abort();
  }
});
