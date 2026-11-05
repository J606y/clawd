// 任务清单执行投影。本文件在这次改动里不运行，不能当成已通过。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { foldTaskList, projectTaskExecution, taskIndicator, tasksForSession } = await server.ssrLoadModule('/src/lib/task-list.ts');
  const call = (id, args) => ({
    role: 'assistant',
    content: '',
    tool_calls: [{ id, function: { name: 'todo_list', arguments: JSON.stringify(args) } }],
  });
  const scope = { sessionId: 'sess-parent', profileKey: 'default' };
  const exec = (extra) => ({ sessionId: 'sess-parent', profileKey: 'default', ...extra });

  test('折叠保留显式关联，丢掉伪造 execution，缺省不投影', () => {
    const items = foldTaskList([
      call('a', {
        todos: [
          { id: 'parent', content: '调度', status: 'in_progress' },
          { id: 'a', content: '子任务', status: 'pending', delegation_id: 'deleg_a', child_id: 'child_a', execution: 'running' },
          { id: 'bare', content: '未关联', status: 'pending' },
        ],
      }),
    ]);
    assert.equal(items[1].delegations[0].delegation_id, 'deleg_a');
    assert.equal(items[1].delegations[0].child_id, 'child_a');
    assert.equal(items[1].execution, undefined);
    assert.equal(items[1].status, 'pending');
    assert.equal(items[2].delegations, undefined);
    assert.deepEqual(tasksForSession('sess-parent', 'sess-parent', [
      call('a', { todos: [{ id: 'a', content: '子任务', status: 'pending' }] }),
    ]).map((item) => item.status), ['pending']);
    assert.deepEqual(tasksForSession('sess-parent', 'other', [
      call('a', { todos: [{ id: 'a', content: '子任务', status: 'pending', delegation_id: 'deleg_a' }] }),
    ]), []);
  });

  test('两个 child 各自进行中，兄弟和未关联项不变', () => {
    const folded = foldTaskList([
      call('a', {
        todos: [
          { id: 'parent', content: '调度', status: 'in_progress' },
          { id: 'a', content: '甲', status: 'pending', delegation_id: 'deleg_a' },
          { id: 'b', content: '乙', status: 'pending', delegation_id: 'deleg_b' },
          { id: 'bare', content: '未关联', status: 'pending' },
        ],
      }),
    ]);
    const items = projectTaskExecution(folded, [
      exec({ delegationId: 'deleg_a', status: 'running' }),
      exec({ delegationId: 'deleg_b', executionStatus: 'running' }),
      exec({ delegationId: 'deleg_a', status: 'running', sessionId: 'other' }),
    ], scope);
    assert.equal(items[1].execution, 'running');
    assert.equal(items[1].status, 'pending');
    assert.equal(items[2].execution, 'running');
    assert.equal(items[0].execution, undefined);
    assert.equal(items[0].status, 'in_progress');
    assert.equal(items[3].execution, undefined);
    assert.equal(taskIndicator(items[1]).label, '进行中');
    assert.equal(taskIndicator(items[1]).markStatus, 'in_progress');
    assert.equal(taskIndicator(items[3]).label, '待办');
    assert.equal(taskIndicator(items[3]).tag, null);
  });

  test('共同一项要全部返回才待验收，失败不伪完成', () => {
    const item = {
      id: 'shared',
      content: '共同',
      status: 'pending',
      delegations: [{ delegation_id: 'deleg_a' }, { delegation_id: 'deleg_b' }],
    };
    const running = projectTaskExecution([item], [
      exec({ delegationId: 'deleg_a', status: 'running' }),
      exec({ delegationId: 'deleg_b', status: 'returned' }),
    ], scope);
    assert.equal(running[0].execution, 'running');
    assert.equal(running[0].status, 'pending');
    const failed = projectTaskExecution([item], [
      exec({ delegationId: 'deleg_a', status: 'failed' }),
      exec({ delegationId: 'deleg_b', status: 'returned' }),
    ], scope);
    assert.equal(failed[0].execution, 'failed');
    assert.equal(failed[0].status, 'pending');
    assert.equal(taskIndicator(failed[0]).label, '失败');
    assert.notEqual(taskIndicator(failed[0]).markStatus, 'completed');
    const ready = projectTaskExecution([item], [
      exec({ delegation_id: 'deleg_a', status: 'returned' }),
      exec({ delegation_id: 'deleg_b', status: 'returned' }),
    ], scope);
    assert.equal(ready[0].execution, 'awaiting_acceptance');
    assert.equal(ready[0].status, 'pending');
    assert.equal(taskIndicator(ready[0]).label, '待验收');
    assert.equal(taskIndicator(ready[0]).tag, '待验收');
    assert.equal(taskIndicator(ready[0]).markStatus, 'pending');
  });

  test('同 id 被两项占用时都不标进行中；空 scope 和别的 profile 不串', () => {
    const items = [
      { id: 'a', content: '甲', status: 'pending', delegations: [{ delegation_id: 'deleg_same' }] },
      { id: 'b', content: '乙', status: 'pending', delegations: [{ delegation_id: 'deleg_same' }] },
    ];
    const ambiguous = projectTaskExecution(items, [exec({ delegationId: 'deleg_same', status: 'running' })], scope);
    assert.equal(ambiguous[0].execution, 'unknown');
    assert.equal(ambiguous[1].execution, 'unknown');
    assert.equal(taskIndicator(ambiguous[0]).label, '状态不明');
    const otherProfile = projectTaskExecution(items, [
      exec({ delegationId: 'deleg_same', status: 'running', profileKey: 'other' }),
    ], scope);
    assert.equal(otherProfile[0].execution, 'unknown');
    const unloaded = tasksForSession('sess-parent', 'sess-parent', [
      call('a', { todos: [{ id: 'a', content: '甲', status: 'pending', delegation_id: 'deleg_same' }] }),
    ]);
    assert.equal(unloaded[0].execution, undefined);
    assert.equal(taskIndicator({ id: 'done', content: '已验', status: 'completed', execution: 'running' }).label, '已完成');
    assert.equal(taskIndicator({ id: 'cancel', content: '消', status: 'cancelled' }).tag, '已取消');
  });

  test('两份 task-list.ts 仍然字节相同', () => {
    const left = readFileSync(new URL('../src/lib/task-list.ts', import.meta.url));
    const right = readFileSync(new URL('../src/react/lib/task-list.ts', import.meta.url));
    assert.deepEqual(left, right);
  });
} finally {
  await server.close();
}
