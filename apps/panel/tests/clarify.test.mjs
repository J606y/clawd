import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';

const owned = await mkdtemp(join(tmpdir(), 'clarify-'));
const server = await createServer({
  configFile: false,
  root: new URL('../', import.meta.url).pathname,
  cacheDir: join(owned, 'vite'),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom',
});
const calls = [];
globalThis.document = { baseURI: 'http://fixture/' };
globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => {} },
  setTimeout, clearTimeout, addEventListener: () => {}, removeEventListener: () => {},
};
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), body: init?.body });
  const path = new URL(String(url)).pathname;
  if (path.endsWith('/clarify') && calls.filter((c) => c.url.includes('/clarify')).length === 1) {
    return new Response(JSON.stringify({ error: { message: 'already resolved' } }), { status: 409 });
  }
  if (path.endsWith('/run_clarify')) {
    return new Response(JSON.stringify({ status: 'running', run_id: 'run_clarify', session_id: 'sess' }), { status: 200 });
  }
  return new Response(JSON.stringify({ ok: true }), { status: 200 });
};

try {
  const { parseClarifyEvent, clarifyAnswer, clarifyAnswers, presentChoice } = await server.ssrLoadModule('/src/lib/clarify.ts');
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');

  await test('strips the presentation label and keeps a custom answer ahead of a pick', () => {
    assert.deepEqual(presentChoice('轻松愉快 (Recommended)'), { label: '轻松愉快', recommended: true });
    const question = { requestId: 'p1', question: '现在的心情是？', choices: [presentChoice('轻松愉快 (Recommended)'), presentChoice('有点累')], multiSelect: false };
    assert.equal(clarifyAnswer(question, [0], ''), '轻松愉快');
    assert.equal(clarifyAnswer(question, [0], '  说不上来  '), '说不上来');
    assert.equal(clarifyAnswer(question, [], ''), null);
  });

  await test('encodes multi-select as a JSON array and rejects an oversized answer', () => {
    const question = { requestId: 'p1', question: '选', choices: [presentChoice('甲'), presentChoice('乙')], multiSelect: true };
    assert.equal(clarifyAnswer(question, [1, 0, 1], '丙'), '["乙","甲","丙"]');
    assert.equal(clarifyAnswer({ ...question, choices: [], multiSelect: false }, [], 'x'.repeat(16385)), null);
  });

  await test('parses one gateway question and a batch with distinct request ids', () => {
    const single = parseClarifyEvent({
      event: 'clarify.request', prompt_id: 'c1', execution_generation: 3,
      question: '今天想聊点什么？', choices: ['学点英语 (Recommended)', '随便闲聊'], multi_select: false,
    });
    assert.equal(single.questions.length, 1);
    assert.equal(single.questions[0].choices[0].label, '学点英语');
    assert.equal(single.generation, 3);
    assert.equal(parseClarifyEvent({ prompt_id: 'c1', question: '缺代数' }), null);
    const batch = parseClarifyEvent({
      prompt_id: 'ignored', execution_generation: 4,
      questions: [
        { prompt_id: 'a', question: '第一题', choices: ['甲'] },
        { request_id: 'b', question: '第二题', multi_select: true, choices: ['乙', '丙'] },
      ],
    });
    assert.deepEqual(batch.questions.map((q) => q.requestId), ['a', 'b']);
    assert.equal(parseClarifyEvent({
      prompt_id: 'only', execution_generation: 1, question: '只有总题',
      questions: [{ qid: 'q0', question: '没有请求标识' }, { qid: 'q1', question: '也没有' }],
    }).questions[0].requestId, 'only');
  });

  await test('run events open and settle the sheet without repeating the same prompt', () => {
    const run = {
      sessionId: 'sess', runId: 'run_clarify', model: '', provider: '', userText: '', attachments: [], steers: [],
      status: 'running', thinking: '', steps: [], text: '', approval: null, clarify: null, error: '', stopping: false,
      startedAt: 1, lastEventAt: 1,
    };
    runs.runs.set('sess', run);
    const event = { event: 'clarify.request', prompt_id: 'c1', execution_generation: 2, question: '现在的心情是？', choices: ['轻松愉快'] };
    runs.applyRunEvent(run, event);
    const first = run.clarify;
    runs.applyRunEvent(run, event);
    assert.equal(run.clarify, first);
    runs.applyRunEvent(run, { event: 'clarify.settled', prompt_id: 'c1' });
    assert.equal(run.clarify, null);
    runs.applyRunEvent(run, event);
    runs.applyRunEvent(run, { event: 'run.completed' });
    assert.equal(run.clarify, null);
  });

  await test('submit posts the exact clarify body and a 409 removes the sheet', async () => {
    const run = runs.runs.get('sess');
    run.status = 'running';
    run.clarify = parseClarifyEvent({ prompt_id: 'c9', execution_generation: 7, question: '今天想聊点什么？', choices: ['学点英语'] });
    const answers = clarifyAnswers(run.clarify, [[0]], ['']);
    await runs.respondClarify('sess', answers);
    const posted = calls.find((c) => c.url.includes('/clarify'));
    assert.deepEqual(JSON.parse(posted.body), { request_id: 'c9', execution_generation: 7, answer: '学点英语' });
    assert.equal(run.clarify, null);
    assert.equal(calls.some((c) => c.url.endsWith('/run_clarify')), true);
  });
} finally {
  await server.close();
  await rm(owned, { recursive: true, force: true });
}
