import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'fetch', 'localStorage', 'navigator'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
const doc = new EventTarget();
doc.baseURI = 'https://panel.example.test/';
doc.visibilityState = 'visible';
Object.defineProperty(globalThis, 'document', { configurable: true, value: doc });
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem: () => {}, removeItem: () => {} } });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });

try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { loadCommentary, invalidateCommentary } = await server.ssrLoadModule('/src/lib/commentary.ts');

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

  await test('parallel tool calls with same name match by call_id correctly out of order (C1)', () => {
    const run = makeRun('sess-c1', 'run-c1');
    // 两个并行的同名工具 read_file
    runs.apply(run, 'tool.started', { tool_name: 'read_file', preview: 'file1.txt', call_id: 'call_1' });
    runs.apply(run, 'tool.started', { tool_name: 'read_file', preview: 'file2.txt', call_id: 'call_2' });

    assert.equal(run.steps.length, 2);
    assert.equal(run.steps[0].id, 'call_1');
    assert.equal(run.steps[1].id, 'call_2');
    assert.equal(run.steps[0].status, 'running');
    assert.equal(run.steps[1].status, 'running');

    // 第二个工具先完成
    runs.apply(run, 'tool.completed', { tool_name: 'read_file', preview: 'content2', call_id: 'call_2' });
    assert.equal(run.steps[0].status, 'running', 'first tool must still be running');
    assert.equal(run.steps[1].status, 'done', 'second tool must be done');
    assert.equal(run.steps[1].output, 'content2');

    // 第一个工具后完成
    runs.apply(run, 'tool.completed', { tool_name: 'read_file', preview: 'content1', call_id: 'call_1' });
    assert.equal(run.steps[0].status, 'done');
    assert.equal(run.steps[0].output, 'content1');
  });

  await test('without call ids (the real gateway wire) completions pair with starts in call order', () => {
    // Hermes 并行执行同一批工具时按调用顺序发出开始与完成事件；完成事件的 preview 是结果摘要。
    const run = makeRun('sess-c1-wire', 'run-c1-wire');
    runs.apply(run, 'tool.started', { tool_name: 'terminal', preview: 'ls a' });
    runs.apply(run, 'tool.started', { tool_name: 'terminal', preview: 'ls b' });
    // 第一个的结果摘要恰好等于第二个的参数摘要：不能因此配到第二个上。
    runs.apply(run, 'tool.completed', { tool_name: 'terminal', preview: 'ls b' });
    assert.deepEqual(run.steps.map(s => [s.preview, s.status, s.output]), [['ls a', 'done', 'ls b'], ['ls b', 'running', null]]);
    runs.apply(run, 'tool.completed', { tool_name: 'terminal', preview: 'b-out' });
    assert.deepEqual(run.steps.map(s => [s.preview, s.status, s.output]), [['ls a', 'done', 'ls b'], ['ls b', 'done', 'b-out']]);
  });

  await test('streamGone status poll survives transient network errors without failing run (C2)', async () => {
    const run = makeRun('sess-c2', 'run-c2');
    let statusCalls = 0;

    globalThis.fetch = async (input) => {
      const url = new URL(input);
      if (url.pathname.includes('/events')) {
        return new Response(JSON.stringify({ error: 'pruned' }), { status: 404 });
      }
      if (url.pathname.includes('/run/status') || url.pathname.includes('/v1/runs/')) {
        statusCalls++;
        // 前两次发生网络异常（502）
        if (statusCalls <= 2) {
          queueMicrotask(() => browser.dispatchEvent(new Event('online')));
          return new Response(JSON.stringify({ error: 'bad gateway' }), { status: 502, statusText: 'Bad Gateway' });
        }
        // 第三次正常返回已完成
        return new Response(JSON.stringify({ status: 'completed', run_id: 'run-c2', session_id: 'sess-c2' }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    };

    const ctrl = new AbortController();
    await runs.follow(run, ctrl.signal);

    assert.equal(run.status, 'completed', 'run should survive transient 502 and complete when server status finishes');
    assert.ok(statusCalls >= 3, 'should have retried status poll through transient errors');
  });

  await test('recent message without commentary is not permanently poisoned in none cache (B5)', async () => {
    invalidateCommentary();
    let commentaryCalls = 0;

    globalThis.fetch = async () => {
      commentaryCalls++;
      // 返回空旁白
      return new Response(JSON.stringify({ items: {} }), { status: 200 });
    };

    const recentMsg = {
      id: 8888,
      role: 'assistant',
      content: 'hello',
      reasoning: 'some thought',
      timestamp: Math.floor(Date.now() / 1000) - 5 // 5秒前刚生成
    };

    // 第一次查
    await loadCommentary([recentMsg]);
    assert.equal(commentaryCalls, 1);

    // 第二次查：因为不超过 60s，不应被永久 none 屏蔽，仍会向 sidecar 发起查询
    await loadCommentary([recentMsg]);
    assert.equal(commentaryCalls, 2, 'recent message should be queryable again rather than poisoned in none');
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
