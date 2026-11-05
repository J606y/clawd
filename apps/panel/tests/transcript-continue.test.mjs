// 压缩脚手架、插话、后台子任务完成后的自动续跑：transcript.ts 的识别与判断。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { buildTurns, withoutInFlight, continuationState, AUTO_CONTINUE_TEXT, CLAWD_CONTINUATION_KIND, CLAWD_CONTINUATION_MARK } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const NOW = 1_800_000_000;
  const human = (content, timestamp, id) => ({ id, role: 'user', content, timestamp });
  const reply = (content, timestamp, id) => ({ id, role: 'assistant', content, timestamp });
  const delegate = (timestamp, deleg) => [
    { role: 'assistant', content: '', timestamp, tool_calls: [{ id: `d${timestamp}`, function: { name: 'delegate_task', arguments: '{}' } }] },
    { role: 'tool', tool_call_id: `d${timestamp}`, timestamp, content: JSON.stringify({ status: 'dispatched', mode: 'background', count: 1, delegation_id: deleg }) },
  ];
  const done = (timestamp, deleg, id) => ({
    id, role: 'user', display_kind: 'async_delegation_complete', timestamp,
    content: `[ASYNC DELEGATION BATCH COMPLETE — ${deleg}]\nA background fan-out unit you dispatched earlier has finished.`,
  });
  const cont = (timestamp, id) => human(AUTO_CONTINUE_TEXT, timestamp, id);

  test('compacted assistant prior context is a notice, never reply text or reasoning', () => {
    const scaffold = { id: 77808, role: 'assistant', compacted: 1, timestamp: 3,
      content: '[PRIOR CONTEXT — for reference only; not a new message]\n合成的压缩摘要', reasoning: '仅供模型的思考' };
    const turns = buildTurns([human('原话', 1), reply('原回复', 2), scaffold, reply('后续回复', 4)]);
    assert.deepEqual(turns.map((t) => t.role), ['user', 'assistant', 'notice', 'assistant']);
    assert.equal(turns[2].title, '上下文已压缩');
    assert.equal(turns[2].detail, scaffold.content);
    assert.deepEqual(turns.filter((t) => t.role === 'assistant').map((t) => t.text), ['原回复', '后续回复']);
    assert.ok(turns.filter((t) => t.role === 'assistant').every((t) => !t.reasoning));
  });

  test('user scaffold rows without display_kind are notices, not user bubbles or task starts', () => {
    const task = human('[Your active task list was preserved across context compression]\n- [>] steer-fix', 3);
    const turns = buildTurns([human('原话', 1), reply('好', 2), task, reply('继续', 4)]);
    assert.deepEqual(turns.map((t) => t.role), ['user', 'assistant', 'notice', 'assistant']);
    assert.equal(turns[2].title, '任务列表已恢复');
    // 脚手架不是人写的：进行中裁剪仍从真正的原话起。
    assert.deepEqual(withoutInFlight([human('原话', 1), task, { role: 'assistant', content: '', timestamp: 4, tool_calls: [{ id: 'x', function: { name: 'terminal', arguments: '{}' } }] }]), []);
  });

  test('scaffold appended after the user sentence leaves the bubble and becomes a notice', () => {
    const row = human('gpt接着做\n\n[Your active task list was preserved across context compression]\n- [>] task-popup. 合成项\n\n[Skills pruned during compression — reload before acting on these tasks]\nThe task list above crosses the boundary.', 3, 9);
    const turns = buildTurns([row]);
    assert.deepEqual(turns.map((t) => t.role), ['user', 'notice']);
    assert.equal(turns[0].text, 'gpt接着做');
    assert.equal(turns[0].message, row);
    assert.equal(turns[1].title, '任务列表已恢复');
    assert.ok(String(turns[1].detail).startsWith('[Your active task list'));
    assert.ok(!turns[0].text.includes('Skills pruned'));
  });

  test('a scaffold phrase inside a sentence stays in the user bubble', () => {
    const row = human('不是系统行：[Your active task list was preserved across context compression] 只是引用', 3, 9);
    const turns = buildTurns([row]);
    assert.equal(turns.length, 1);
    assert.equal(turns[0].role, 'user');
    assert.equal(turns[0].text, row.content);
  });

  test('proven queue input keeps the user sentence when a scaffold is appended', () => {
    const row = { id: 9, role: 'user', display_kind: 'internal_notification', input_origin: 'api_queue', timestamp: 3,
      content: '我的原话\n\n[Your active task list was preserved across context compression]\n- [ ] item' };
    const turns = buildTurns([row]);
    assert.deepEqual(turns.map((t) => t.role), ['user', 'notice']);
    assert.equal(turns[0].text, '我的原话');
    assert.equal(turns[0].message, undefined);
    assert.equal(turns[1].title, '任务列表已恢复');
  });

  test('kind-less machine rows are notices, not the user bubble', () => {
    assert.equal(buildTurns([human('[ASYNC DELEGATION BATCH COMPLETE — deleg_x]\nfinished', 3, 9)])[0].role, 'notice');
    const skills = buildTurns([human('[Skills pruned during compression — reload before acting on these tasks]', 4, 10)])[0];
    assert.equal(skills.role, 'notice');
    assert.equal(skills.title, '技能已裁剪');
  });

  test('steer rows render as non-editable user bubbles with only the user words', () => {
    const steer = { role: 'user', display_kind: 'steer', timestamp: 3,
      content: '[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; not tool output and not a new delivery when replayed from conversation history] 修复了就提交吧 [/OUT-OF-BAND USER MESSAGE]' };
    const turns = buildTurns([human('做个功能', 1), reply('', 2), steer, reply('好的，提交了', 4)]);
    const s = turns.find((t) => t.role === 'user' && t.steer);
    assert.ok(s, 'steer becomes a user turn');
    assert.equal(s.text, '修复了就提交吧');
    assert.equal(s.message, undefined, 'no edit entry for steers');
    assert.ok(!turns.some((t) => t.role === 'notice'), 'steer is not a system notice');
    // 插话不是任务起点：进行中裁剪从原话起，而不是从插话起。
    const inflight = [human('做个功能', 1), steer, { role: 'assistant', content: '', timestamp: 4, tool_calls: [{ id: 'y', function: { name: 'terminal', arguments: '{}' } }] }];
    assert.deepEqual(withoutInFlight(inflight), []);
  });

  test('completion after the final reply triggers continuation keyed by the notice row id', () => {
    const msgs = [human('查一下', NOW - 100, 1), ...delegate(NOW - 90, 'deleg_aa11'), reply('已派子代理核对，等结果', NOW - 80, 4), done(NOW - 10, 'deleg_aa11', 5)];
    const st = continuationState(msgs);
    assert.equal(st.trigger?.id, 5);
    assert.equal(st.limited, false);
  });

  test('no continuation while the delegation is still out', () => {
    const msgs = [human('查一下', NOW - 100, 1), ...delegate(NOW - 90, 'deleg_bb22'), reply('等结果', NOW - 80, 4)];
    assert.deepEqual(continuationState(msgs), { trigger: null, limited: false });
  });

  test('a human message after the completion owns the next turn: no auto continuation', () => {
    const msgs = [human('查一下', NOW - 100, 1), ...delegate(NOW - 90, 'deleg_cc33'), reply('等结果', NOW - 80, 4), done(NOW - 30, 'deleg_cc33', 5), human('先别管它', NOW - 10, 6)];
    assert.equal(continuationState(msgs).trigger, null);
  });

  test('completion already followed by an assistant reply does not trigger again', () => {
    const msgs = [human('查一下', NOW - 100, 1), ...delegate(NOW - 90, 'deleg_dd44'), reply('等', NOW - 80, 4), done(NOW - 50, 'deleg_dd44', 5), cont(NOW - 49, 6), reply('结果已用上，完成', NOW - 20, 7)];
    assert.equal(continuationState(msgs).trigger, null);
  });

  test('five successful continuations are not a display cap', () => {
    const msgs = [human('大任务', NOW - 1000, 1)];
    let id = 2;
    for (let i = 0; i < 5; i++) {
      msgs.push(...delegate(NOW - 900 + i * 100, `deleg_e${i}`), reply('等', NOW - 890 + i * 100, id++), done(NOW - 880 + i * 100, `deleg_e${i}`, id++), cont(NOW - 879 + i * 100, id++));
    }
    msgs.push(reply('又派了一个', NOW - 300, id++), done(NOW - 200, 'deleg_last', id++));
    const fifth = continuationState(msgs);
    assert.equal(fifth.limited, false, '成功次数不是展示门槛');
    assert.equal(fifth.trigger?.id, id - 1);
    msgs.push(human('继续', NOW - 100, id++));
    const owned = continuationState(msgs);
    assert.equal(owned.trigger, null, '人话占有下一轮，不是次数解锁');
    assert.equal(owned.limited, false);
    msgs.push(reply('好，又派了', NOW - 90, id++), done(NOW - 50, 'deleg_x', id++));
    const after = continuationState(msgs);
    assert.equal(after.limited, false);
    assert.ok(after.trigger, '之后的完成通知仍可展示，不靠把成功次数门槛调大或清掉');
  });

  test('core continuation kind and new mark are notices; another kind is not', () => {
    const byKind = { id: 4, role: 'user', display_kind: CLAWD_CONTINUATION_KIND, timestamp: 4, content: '无前缀也是续跑' };
    const byMark = human(`${CLAWD_CONTINUATION_MARK} 继续`, 5, 5);
    const other = { id: 6, role: 'user', display_kind: 'process_complete', timestamp: 6, content: `${CLAWD_CONTINUATION_MARK} 其他 kind` };
    const turns = buildTurns([byKind, byMark, other]);
    assert.equal(turns[0].title, '子任务已完成，自动继续');
    assert.equal(turns[1].title, '子任务已完成，自动继续');
    assert.equal(turns[2].title, '后台命令已结束');
    assert.notEqual(turns[2].title, '子任务已完成，自动继续');
  });

  test('a failed completion stays a failure notice and is not a success-count lock', () => {
    const failed = {
      id: 9, role: 'user', display_kind: 'async_delegation_complete', timestamp: 3,
      display_metadata: { failed_count: 1 },
      content: '[ASYNC DELEGATION BATCH COMPLETE — deleg_fail FAILED]\nA background fan-out unit you dispatched earlier has finished.',
    };
    const turns = buildTurns([human('查一下', 1, 1), reply('等结果', 2, 2), failed]);
    const notice = turns.find((t) => t.failed);
    assert.equal(notice?.title, '后台子任务失败');
    assert.equal(notice?.failed, true);
    assert.equal(continuationState([human('查一下', 1, 1), reply('等结果', 2, 2), failed]).limited, false);
  });

  test('dropping the success cap does not drop cancel, approval, or failure display', () => {
    const page = readFileSync(new URL('../src/react/pages/chat/chat-page.tsx', import.meta.url), 'utf8');
    const tail = readFileSync(new URL('../src/react/pages/chat/live-tail.tsx', import.meta.url), 'utf8');
    assert.match(page, /setNotice\("已停止"\)/);
    assert.match(page, /setNotice\(run\.error \|\| "执行失败"\)/);
    assert.match(page, /waiting_for_approval/);
    assert.match(page, /respondApproval/);
    assert.match(tail, /<ApprovalCard/);
    assert.match(tail, /onApprove/);
    assert.doesNotMatch(page, /已连续自动继续/);
    assert.doesNotMatch(tail, /已连续自动继续/);
  });

  test('continuation rows render as a notice and are the run start for in-flight trimming', () => {
    const turns = buildTurns([human('查一下', 1, 1), reply('等结果', 2, 2), done(3, 'deleg_ff', 3), cont(4, 4), reply('接着做完了', 5, 5)]);
    assert.deepEqual(turns.map((t) => t.role), ['user', 'assistant', 'notice', 'notice', 'assistant']);
    assert.equal(turns[3].title, '子任务已完成，自动继续');
    // 续跑进行中：只裁掉续跑这一轮，之前已完成的回合要保留。
    const inflight = [human('查一下', 1, 1), reply('等结果', 2, 2), done(3, 'deleg_ff', 3), cont(4, 4), { role: 'assistant', content: '', timestamp: 5, tool_calls: [{ id: 'z', function: { name: 'terminal', arguments: '{}' } }] }];
    assert.deepEqual(withoutInFlight(inflight).map((m) => m.id), [1, 2, 3]);
  });

  test('ambiguous legacy samples remain visible instead of guessing a reasoning boundary', () => {
    const samples = [
      'THINKING:\nSynthetic analysis.\nRemember: before calling a tool, describe the action.\nFinal answer.',
      'Synthetic analysis.\n记住：简洁说明。$]正式回答。',
    ];
    for (const content of samples) {
      const turns = buildTurns([human('Question', 1, 1), reply(content, 2, 2)]);
      assert.equal(turns[1].text, content);
      assert.equal(turns[1].reasoning, '');
    }
  });
} finally {
  await server.close();
}
