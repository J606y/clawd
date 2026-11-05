import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
const previousDocument = globalThis.document;
globalThis.document = { baseURI: 'http://fixture/' };
const previousWindow = globalThis.window;
const previousCustomEvent = globalThis.CustomEvent;
const storage = new Map();
globalThis.window = {
  localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.CustomEvent ??= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail; } };
try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { withoutInFlight, buildTurns, AUTO_CONTINUE_TEXT } = await server.ssrLoadModule('/src/lib/transcript.ts');
  // Isolate SSE lifetime; exercise the real localStorage resume and restoration paths.
  runs.consume = async () => {};
  const human = content => ({ role: 'user', content });
  const steer = content => ({ role: 'user', display_kind: 'steer', content: `[OUT-OF-BAND USER MESSAGE — a direct message from the user]\n${content}\n[/OUT-OF-BAND USER MESSAGE]` });
  const calling = { role: 'assistant', content: '', tool_calls: [{ id: 'synthetic-call', function: { name: 'terminal', arguments: '{}' } }] };
  const old = [human('旧任务'), steer('旧插话'), { role: 'assistant', content: '旧回复' }];
  let next = 0;
  function resume(remote = false) {
    const sid = `restore-${++next}`;
    storage.set('hp_active_runs', JSON.stringify({ [sid]: { runId: `run-${next}`, userText: '当前任务', startedAt: 100000, remote } }));
    assert.deepEqual(runs.resumeAll(), [sid]);
    return [sid, runs.get(sid)];
  }

  await test('local steering freezes its visual boundary before acceptance and retains it on failure', async () => {
    const { api } = await server.ssrLoadModule('/src/lib/api.ts');
    const original = api.steer;
    try {
      const [sid, run] = resume();
      run.steps.push({ kind: 'note', text: '先前说明' });
      run.text = '已显示正文';
      let release;
      api.steer = () => new Promise(resolve => { release = resolve; });
      const sending = runs.steer(sid, '插在这里');
      assert.deepEqual(run.steers[0].anchor, { step: 1, offset: 5 });
      assert.equal(run.steers[0].status, 'sending');
      run.steps.push({ kind: 'note', text: run.text });
      run.text = '之后正文';
      release({});
      assert.equal(await sending, true);
      assert.deepEqual(run.steers[0].anchor, { step: 1, offset: 5 });
      api.steer = async () => { throw new Error('synthetic rejection'); };
      assert.equal(await runs.steer(sid, '失败也留在原位'), false);
      assert.equal(run.steers[1].status, 'failed');
      assert.deepEqual(run.steers[1].anchor, { step: 2, offset: 4 });
    } finally { api.steer = original; }
  });

  await test('refresh resume restores only current-turn steering with sent status and no wrapper', () => {
    const [sid, run] = resume();
    const messages = [...old, human('当前任务'), calling, steer('先保留数据')];
    runs.restoreSteers(sid, messages);
    assert.deepEqual(run.steers, [{ text: '先保留数据', files: [], uploads: [], status: 'sent' }]);
    assert.deepEqual(withoutInFlight(messages), old);
  });
  await test('restoration is idempotent and preserves existing local entries and status', () => {
    const [sid, run] = resume();
    const messages = [human('当前任务'), calling, steer('同一句'), steer('同一句')];
    runs.restoreSteers(sid, messages);
    const restored = run.steers;
    runs.restoreSteers(sid, messages);
    assert.equal(run.steers, restored);
    assert.equal(run.steers.length, 2, 'identical but distinct persisted steering rows are preserved');
    const [other, existing] = resume();
    const local = { text: '本地已有', files: [], uploads: [], status: 'sending' };
    existing.steers.push(local);
    runs.restoreSteers(other, messages);
    assert.deepEqual(existing.steers, [local]);
  });

  await test('multiple steering rows keep database order across notifications and scaffolding', () => {
    const [sid, run] = resume();
    runs.restoreSteers(sid, [...old, human('当前任务'), steer('第一条'), calling,
      { role: 'user', display_kind: 'async_delegation_complete', content: '后台完成' },
      { role: 'user', content: '[CONTEXT SUMMARY] synthetic context' },
      steer('第二条'), steer('第三条')]);
    assert.deepEqual(run.steers.map(entry => entry.text), ['第一条', '第二条', '第三条']);
  });

  await test('steering restores text attachments and uploaded image cards through transcript parsing', async () => {
    const { buildSteerText, parseUserText } = await server.ssrLoadModule('/src/lib/attachments.ts');
    const [sid, run] = resume();
    const body = buildSteerText('查看附件', [
      { id: 'txt', kind: 'text', name: 'notes.md', text: '附件正文\n```sh\ntrue\n```', bytes: 40 },
      { id: 'img', kind: 'file', name: 'image.png', absPath: '/root/hermes-uploads/synthetic/image.png', bytes: 2048, image: true },
    ]);
    const expected = parseUserText(body);
    runs.restoreSteers(sid, [human('当前任务'), calling, steer(body)]);
    assert.deepEqual(run.steers, [{ text: expected.text, files: expected.files, uploads: expected.uploads, status: 'sent' }]);
    assert.equal(run.steers[0].files.length, 1);
    assert.equal(run.steers[0].uploads.length, 1);
    assert.equal(run.steers[0].text, '查看附件');
  });

  await test('remote resume leaves steering in historical user rows, never duplicates into live state', () => {
    const [sid, run] = resume(true);
    const messages = [...old, { ...human('当前任务'), timestamp: 101 }, calling, { ...steer('远端插话'), timestamp: 102 }];
    runs.restoreSteers(sid, messages);
    assert.deepEqual(run.steers, []);
    const visible = buildTurns(withoutInFlight(messages, run.startedAt / 1000));
    assert.equal(visible.filter(turn => turn.steer && turn.text === '远端插话').length, 1);
    assert.ok(visible.some(turn => turn.text === '当前任务'));
  });

  await test('automatic continuation is the current turn boundary', () => {
    const [sid, run] = resume();
    runs.restoreSteers(sid, [human('任务'), steer('上一轮插话'), { role: 'assistant', content: '等待后台' },
      human(AUTO_CONTINUE_TEXT), calling, steer('续跑插话')]);
    assert.deepEqual(run.steers.map(entry => entry.text), ['续跑插话']);
  });

  await test('missing boundary can be retried after loading earlier history; completed runs are untouched', () => {
    const [sid, run] = resume();
    runs.restoreSteers(sid, [calling, steer('插话')]);
    assert.deepEqual(run.steers, []);
    runs.restoreSteers(sid, [human('当前任务'), calling, steer('插话')]);
    assert.equal(run.steers.length, 1);
    const [doneId, done] = resume();
    done.status = 'completed';
    runs.restoreSteers(doneId, [human('任务'), calling, steer('不恢复')]);
    assert.deepEqual(done.steers, []);
  });

  await test('same-page started runs do not restore steering from history', () => {
    const sid = `started-${++next}`;
    runs.start(sid, '当前任务', [], '当前任务', '', '', { run_id: 'accepted', status: 'running' });
    runs.restoreSteers(sid, [human('当前任务'), calling, steer('历史插话')]);
    assert.deepEqual(runs.get(sid).steers, []);
  });
} finally {
  globalThis.document = previousDocument;
  globalThis.window = previousWindow;
  globalThis.CustomEvent = previousCustomEvent;
  await server.close();
}
