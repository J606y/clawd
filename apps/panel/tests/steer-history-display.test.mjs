// 插话后实时库还没有 steps/thinking/text 时，已落盘的思考和工具步仍要留在可见历史里。
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { buildTurns, withoutInFlight, historyBesideLive, visibleTurnsBesideLive } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const steer = (text, id, timestamp) => ({
    id,
    role: 'user',
    display_kind: 'steer',
    timestamp,
    content: `[OUT-OF-BAND USER MESSAGE — a direct message from the user]\n${text}\n[/OUT-OF-BAND USER MESSAGE]`,
  });
  const call = (id, name) => ({ id, function: { name, arguments: '{}' } });
  const messages = [
    { id: 10, role: 'user', content: '上一轮任务', timestamp: 10 },
    steer('插一句', 11, 11),
    { id: 12, role: 'assistant', content: '上一轮回复', timestamp: 12 },
    { id: 1, role: 'user', content: '原来的任务', timestamp: 100 },
    {
      id: 2,
      role: 'assistant',
      content: '',
      reasoning: '插话前的思考',
      timestamp: 101,
      tool_calls: [call('c1', 'search_files')],
    },
    { id: 3, role: 'tool', tool_call_id: 'c1', content: '{"output":"found"}', timestamp: 102 },
    steer('插一句', 4, 103),
    {
      id: 5,
      role: 'assistant',
      content: '',
      reasoning: '插话后的思考',
      timestamp: 104,
      tool_calls: [call('c2', 'read_file'), call('c3', 'terminal')],
    },
    { id: 6, role: 'tool', tool_call_id: 'c2', content: '{"output":"file"}', timestamp: 105 },
  ];
  const emptyLive = { steps: [], thinking: '', text: '', steers: [{ text: '插一句', status: 'sent' }] };
  const withSteps = { steps: [{ kind: 'tool', id: 'live', name: 'terminal' }], thinking: '', text: '', steers: [{ text: '插一句', status: 'sent' }] };
  const active = { localActive: true, ownsHistory: false };
  const toolNames = (turns) => turns.flatMap((turn) => turn.role === 'assistant' ? turn.steps.filter((step) => step.kind === 'tool').map((step) => step.name) : []);
  const steerCount = (turns, text) => turns.filter((turn) => turn.role === 'user' && turn.steer && turn.text === text).length;

  test('empty live projection keeps persisted thoughts and tool steps around a steer', () => {
    const view = historyBesideLive(messages, emptyLive, active);
    assert.equal(view.stripped, false);
    const built = buildTurns(view.messages, undefined, { inFlight: !view.stripped && active.localActive });
    assert.ok(built.some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话前的思考')));
    assert.ok(built.some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话后的思考')));
    assert.deepEqual(toolNames(built), ['search_files', 'read_file', 'terminal']);

    const visible = visibleTurnsBesideLive(messages, emptyLive, { ...active, drawnSteers: emptyLive.steers });
    assert.ok(visible.some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话前的思考')));
    assert.ok(visible.some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话后的思考')));
    assert.deepEqual(toolNames(visible), ['search_files', 'read_file', 'terminal']);
    assert.equal(visible.find((turn) => turn.role === 'assistant' && turn.steps.some((step) => step.name === 'terminal')).steps.find((step) => step.name === 'terminal').status, 'running');
    assert.equal(steerCount(visible, '插一句'), 2, '空投影时插话留在历史原位，不能抽到尾栏下面');
    const before = visible.findIndex((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话前的思考'));
    const after = visible.findIndex((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话后的思考'));
    const steerAt = visible.findIndex((turn, index) => index > before && turn.role === 'user' && turn.steer);
    assert.ok(steerAt > before && steerAt < after, '插话必须夹在前后思考之间');
  });

  test('live steps still drop the in-flight history instead of drawing it twice', () => {
    const view = historyBesideLive(messages, withSteps, active);
    assert.equal(view.stripped, true);
    assert.deepEqual(view.messages, withoutInFlight(messages));
    const visible = visibleTurnsBesideLive(messages, withSteps, { ...active, drawnSteers: withSteps.steers });
    assert.deepEqual(toolNames(visible), []);
    assert.equal(steerCount(visible, '插一句'), 1, '本轮已裁掉后，不能把上一轮同文插话当成尾栏重复气泡删掉');
    assert.equal(visible.some((turn) => turn.role === 'assistant' && turn.text === '上一轮回复'), true);
  });

  test('thinking or text in the live projection still owns the in-flight history', () => {
    for (const live of [
      { steps: [], thinking: '实时思考', text: '' },
      { steps: [], thinking: '', text: '实时正文' },
    ]) {
      const view = historyBesideLive(messages, live, active);
      assert.equal(view.stripped, true);
      assert.deepEqual(view.messages, withoutInFlight(messages));
    }
  });

  test('blank live text does not count as a projection, and a quiet tail keeps the history steer', () => {
    const blank = { steps: [], thinking: ' \n', text: '  ', steers: [{ text: '插一句' }] };
    const view = historyBesideLive(messages, blank, active);
    assert.equal(view.stripped, false);
    assert.ok(buildTurns(view.messages).some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话后的思考')));
    const quiet = visibleTurnsBesideLive(messages, emptyLive, { ...active, drawnSteers: [] });
    assert.equal(steerCount(quiet, '插一句'), 2, '尾栏不画插话时，历史里的两条都要留下');
  });

  test('an unpersisted live steer does not consume an earlier same-text bubble', () => {
    const pendingOnly = messages.filter((row) => row.id !== 4);
    const visible = visibleTurnsBesideLive(pendingOnly, emptyLive, { ...active, drawnSteers: emptyLive.steers });
    assert.equal(steerCount(visible, '插一句'), 1, '本轮插话还没入库时，不能把上一轮同文插话删掉');
    assert.ok(visible.some((turn) => turn.role === 'assistant' && turn.reasoning.includes('插话后的思考')));
    assert.deepEqual(toolNames(visible), ['search_files', 'read_file', 'terminal']);
  });
} finally {
  await server.close();
}
