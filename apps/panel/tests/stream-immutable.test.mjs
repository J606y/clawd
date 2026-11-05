import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';

const owned = await mkdtemp(join(tmpdir(), 'stream-immutable-'));
const server = await createServer({ configFile: false, root: new URL('../', import.meta.url).pathname,
  cacheDir: join(owned, 'vite'), server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
const previousDocument = globalThis.document;
const previousWindow = globalThis.window;
const previousFetch = globalThis.fetch;
let unexpectedRequests = 0;
globalThis.fetch = () => { unexpectedRequests++; throw new Error('Unexpected network in immutable-state fixture'); };
globalThis.document = { baseURI: 'http://fixture/' };
globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => {} },
  setTimeout, clearTimeout, addEventListener: () => {}, removeEventListener: () => {},
};

try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { segments, splitLeakedThinking } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const { api } = await server.ssrLoadModule('/src/lib/api.ts');
  function syntheticRun(sessionId) {
    const run = { sessionId, runId: `synthetic-${sessionId}`, model: '', provider: '', userText: 'hello',
      attachments: [], steers: [], status: 'running', thinking: '', steps: [], text: '', approval: null,
      error: '', stopping: false, startedAt: Date.now(), lastEventAt: Date.now() };
    runs.runs.set(sessionId, run);
    return run;
  }

  await test('1.2: segments caches result with WeakMap on steps reference', () => {
    const steps1 = [
      { kind: 'note', text: 'Step 1' },
      { kind: 'tool', id: 'call_1', name: 'bash', args: null, preview: null, status: 'done', output: 'ok' },
    ];
    const segsA = segments(steps1);
    assert.equal(segsA, segments(steps1));
    const segsC = segments([...steps1]);
    assert.notEqual(segsA, segsC);
    assert.deepEqual(segsA, segsC);
  });

  await test('1.4: splitLeakedThinking caches recent 8 results and preserves exact semantics', () => {
    const raw = 'Just normal assistant output.';
    const original = splitLeakedThinking(raw);
    assert.equal(original, splitLeakedThinking(raw));
    assert.deepEqual(original, { reasoning: '', text: raw });
    const thinking = 'THINKING:\nInner reasoning\n$]\nAnswer after thinking.';
    const parsed = splitLeakedThinking(thinking);
    assert.equal(parsed, splitLeakedThinking(thinking));
    assert.deepEqual(parsed, { reasoning: 'Inner reasoning', text: 'Answer after thinking.' });
    for (let i = 0; i < 8; i++) splitLeakedThinking(`distinct input ${i}`);
    const reloaded = splitLeakedThinking(raw);
    assert.notEqual(reloaded, original, 'Eviction must be exercised, not merely structural equality');
    assert.deepEqual(reloaded, { reasoning: '', text: raw });
  });

  await test('1.3: event application replaces steps and preserves old snapshots', () => {
    const run = syntheticRun('immutable-steps');
    const initialSteps = run.steps;
    runs.apply(run, 'assistant.commentary', { text: 'Note 1' });
    assert.notEqual(run.steps, initialSteps);
    assert.deepEqual(initialSteps, []);
    assert.deepEqual(run.steps, [{ kind: 'note', text: 'Note 1' }]);
    const noteStep = run.steps[0];
    const afterNote = run.steps;
    runs.apply(run, 'tool.started', { tool_name: 'test_tool', id: 'call_1', args: { foo: 'bar' } });
    assert.notEqual(run.steps, afterNote);
    assert.equal(afterNote.length, 1);
    assert.equal(run.steps.length, 2);
    const started = run.steps[1];
    assert.equal(started.status, 'running');
    const afterStart = run.steps;
    runs.apply(run, 'tool.completed', { id: 'call_1', preview: 'tool result preview' });
    assert.notEqual(run.steps, afterStart);
    assert.notEqual(run.steps[1], started);
    assert.equal(started.status, 'running', 'Previous snapshot must not change');
    assert.equal(started.output, null);
    assert.equal(run.steps[1].status, 'done');
    assert.equal(run.steps[1].output, 'tool result preview');
    assert.equal(run.steps[0], noteStep);
  });

  await test('1.3: steer sending and success replace arrays and items without mutating snapshots', async () => {
    const originalSteer = api.steer;
    const run = syntheticRun('immutable-steer');
    try {
      const initial = run.steers;
      let release;
      api.steer = () => new Promise(resolve => { release = resolve; });
      const operation = runs.steer(run.sessionId, 'steer command');
      assert.notEqual(run.steers, initial);
      assert.equal(initial.length, 0);
      const during = run.steers;
      const sending = during[0];
      assert.equal(sending.status, 'sending');
      release({});
      assert.equal(await operation, true);
      assert.notEqual(run.steers, during);
      assert.notEqual(run.steers[0], sending);
      assert.equal(run.steers[0].status, 'sent');
      assert.equal(sending.status, 'sending');
    } finally { api.steer = originalSteer; }
  });

  await test('1.3: approval pending replacement preserves prior approval snapshots', async () => {
    const originalApprove = api.approve;
    const run = syntheticRun('immutable-approval');
    try {
      // 完整身份才替换快照。缺代次不得在此零网络夹具里 POST，也不能当成已同意。
      runs.apply(run, 'approval.request', { command: 'synthetic approval', description: 'Synthetic operation',
        choices: ['once', 'deny'], request_id: 'req_1', execution_generation: 4 });
      const initial = run.approval;
      assert.ok(initial);
      assert.equal(initial.pending, false);
      assert.equal(initial.executionGeneration, 4);
      let release;
      const seen = [];
      api.approve = (runId, choice, requestId, generation) => new Promise(resolve => {
        seen.push({ runId, choice, requestId, generation });
        release = resolve;
      });
      const operation = runs.respondApproval(run.sessionId, 'once');
      assert.notEqual(run.approval, initial);
      assert.equal(run.approval.pending, true);
      assert.equal(initial.pending, false);
      assert.equal(initial.executionGeneration, 4);
      assert.notEqual(run.approval, null);
      assert.deepEqual(seen, [{ runId: run.runId, choice: 'once', requestId: 'req_1', generation: 4 }]);
      assert.equal(unexpectedRequests, 0);
      release({});
      await operation;
      assert.equal(run.approval, null);
    } finally { api.approve = originalApprove; }
  });

  await test('immutable-state fixtures issue no network requests', () => assert.equal(unexpectedRequests, 0));
} finally {
  await server.close();
  globalThis.document = previousDocument;
  globalThis.window = previousWindow;
  globalThis.fetch = previousFetch;
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
