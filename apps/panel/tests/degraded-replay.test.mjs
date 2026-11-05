import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'fetch', 'localStorage', 'navigator'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
Object.defineProperty(globalThis, 'document', { configurable: true, value: { baseURI: 'https://panel.example.test/' } });
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem: () => {}, removeItem: () => {} } });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });

try {
  const { runs, RUN_CHANGED } = await server.ssrLoadModule('/src/lib/runs.ts');

  function makeRun(sessionId, runId) {
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
      error: '',
      stopping: false,
      startedAt: Date.now(),
      lastEventAt: Date.now(),
      model: '',
      provider: ''
    };
  }

  await test('reasoning.available SSE event sets live run thinking field (B1)', async () => {
    const run = makeRun('sess-b1', 'run-b1');

    globalThis.fetch = async (input) => {
      const url = new URL(input);
      if (url.pathname.includes('/events')) {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: message\ndata: {"event":"reasoning.available","text":"先分析用户意图"}\n\n'));
            controller.close();
          }
        });
        return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
      }
      return new Response(JSON.stringify({ status: 'completed', run_id: 'run-b1', session_id: 'sess-b1' }), { status: 200 });
    };

    const ctrl = new AbortController();
    await runs.follow(run, ctrl.signal);
    assert.equal(run.thinking, '先分析用户意图', 'thinking should be populated from reasoning.available');
  });

  await test('replay.truncated SSE event marks run.replayTruncated as true (B2)', async () => {
    const run = makeRun('sess-b2', 'run-b2');

    globalThis.fetch = async (input) => {
      const url = new URL(input);
      if (url.pathname.includes('/events')) {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: message\ndata: {"event":"replay.truncated"}\n\n'));
            controller.close();
          }
        });
        return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
      }
      return new Response(JSON.stringify({ status: 'completed', run_id: 'run-b2', session_id: 'sess-b2' }), { status: 200 });
    };

    const ctrl = new AbortController();
    await runs.follow(run, ctrl.signal);
    assert.equal(run.replayTruncated, true, 'replay.truncated event must set run.replayTruncated');
  });

  await test('stream 404 sets run.streamGone, and status 404 marks run failed instead of completed (B2, B3)', async () => {
    let changedCount = 0;
    const onRunChanged = () => { changedCount++; };
    runs.addEventListener(RUN_CHANGED, onRunChanged);

    try {
      const run = makeRun('sess-b3', 'run-b3');

      globalThis.fetch = async (input) => {
        const url = new URL(input);
        if (url.pathname.includes('/events')) {
          return new Response(JSON.stringify({ error: 'run stream pruned' }), { status: 404, statusText: 'Not Found' });
        }
        if (url.pathname.includes('/run/status') || url.pathname.includes('/v1/runs/')) {
          return new Response(JSON.stringify({ error: 'run record gone' }), { status: 404, statusText: 'Not Found' });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      };

      const ctrl = new AbortController();
      await runs.follow(run, ctrl.signal);

      assert.equal(run.streamGone, true, 'SSE 404 must mark streamGone');
      assert.equal(run.status, 'failed', 'run status 404 must mark status as failed, never completed');
      assert.match(run.error, /无法确认这次运行的结果/, 'without a matching sidecar record the outcome is reported as unknown');
      assert.ok(changedCount > 0, 'RUN_CHANGED must be dispatched');
    } finally {
      runs.removeEventListener(RUN_CHANGED, onRunChanged);
    }
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
