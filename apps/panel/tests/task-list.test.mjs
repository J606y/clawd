// 历史消息里的 todo_list 折叠成一份就地更新的清单。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { foldTaskList, isTodoStep, taskProgress, tasksForSession } = await server.ssrLoadModule('/src/lib/task-list.ts');

  const call = (id, name, args) => ({
    role: 'assistant',
    content: '',
    tool_calls: [{ id, function: { name, arguments: typeof args === 'string' ? args : JSON.stringify(args) } }],
  });
  const tool = (id, content) => ({ role: 'tool', tool_call_id: id, content });
  const todo = (id, content, status, extra) => ({ id, content, status, ...extra });

  test('整表替换会丢掉旧 id', () => {
    const items = foldTaskList([
      call('a', 'todo_list', { todos: [todo('1', '旧', 'pending'), todo('2', '留', 'pending')] }),
      call('b', 'todo_list', { merge: false, todos: [todo('2', '留完', 'completed'), todo('3', '新', 'pending')] }),
    ]);
    assert.deepEqual(items.map((item) => item.id), ['2', '3']);
    assert.equal(items[0].content, '留完');
  });

  test('同一数组重复 id 保留最后一次并排到最后出现的位置', () => {
    const items = foldTaskList([
      call('a', 'todo_list', {
        todos: [todo('1', '旧', 'pending'), todo('2', '二', 'pending'), todo('1', '新', 'completed')],
      }),
    ]);
    assert.deepEqual(items.map((item) => [item.id, item.content, item.status]), [['2', '二', 'pending'], ['1', '新', 'completed']]);
  });

  test('merge 改状态并追加、保留原顺序', () => {
    const items = foldTaskList([
      call('a', 'todo_list', { todos: [todo('1', '一', 'pending'), todo('2', '二', 'pending')] }),
      call('b', 'todo_list', {
        merge: true,
        todos: [todo('2', '二完成', 'completed'), todo('3', '三', 'in_progress')],
      }),
    ]);
    assert.deepEqual(items.map((item) => item.id), ['1', '2', '3']);
    assert.equal(items[0].status, 'pending');
    assert.equal(items[1].status, 'completed');
    assert.equal(items[1].content, '二完成');
    assert.equal(items[2].content, '三');
  });

  test('无 todos 不清空', () => {
    const items = foldTaskList([
      call('a', 'todo_list', { todos: [todo('1', '留', 'pending')] }),
      call('b', 'todo_list', { merge: true }),
      call('c', 'todo_list', {}),
      call('d', 'todo_list', { todos: null }),
      call('e', 'todo_list', { todos: 'not-array', merge: false }),
    ]);
    assert.deepEqual(items.map((item) => item.id), ['1']);
    assert.equal(foldTaskList([call('z', 'todo_list', {})]), null);
  });

  test('失败结果不入账', () => {
    assert.equal(foldTaskList([
      call('bad', 'todo_list', { todos: [todo('1', '坏', 'pending')] }),
      tool('bad', JSON.stringify({ error: 'nope' })),
    ]), null);
    assert.equal(foldTaskList([
      tool('bad', JSON.stringify({ success: false })),
      call('bad', 'todo_list', { todos: [todo('1', '坏', 'pending')] }),
    ]), null);
    const kept = foldTaskList([
      call('ok', 'todo_list', { todos: [todo('1', '好', 'pending')] }),
      tool('ok', 'plain text is not a failure'),
      call('code', 'todo_list', { merge: true, todos: [todo('2', '码', 'pending')] }),
      tool('code', JSON.stringify({ exit_code: 2, error: null })),
      call('flag', 'todo_list', { merge: true, todos: [todo('3', '旗', 'pending')] }),
      tool('flag', JSON.stringify({ success: false, exit_code: 0 })),
      call('zero', 'todo_list', { merge: true, todos: [todo('4', '零', 'completed')] }),
      tool('zero', JSON.stringify({ output: 'ok', exit_code: 0, error: null, success: true })),
    ]);
    assert.deepEqual(kept.map((item) => item.id), ['1', '4']);
    assert.equal(foldTaskList([
      call('parts', 'todo_list', { todos: [todo('9', '段', 'pending')] }),
      tool('parts', [{ type: 'text', text: JSON.stringify({ error: 'x' }) }]),
    ]), null);
  });

  test('没有 tool 消息时仍入账', () => {
    const items = foldTaskList([
      call('live', 'todo_list', { todos: [todo('1', '进行中', 'in_progress')] }),
    ]);
    assert.equal(items.length, 1);
    assert.equal(items[0].status, 'in_progress');
    assert.equal(items[0].content, '进行中');
  });

  test('functions.todo_list', () => {
    const items = foldTaskList([
      call('f', 'functions.todo_list', { todos: [todo('1', '前缀', 'pending')] }),
    ]);
    assert.equal(items[0].content, '前缀');
    assert.equal(isTodoStep({ kind: 'tool', name: 'functions.todo_list', args: null }), true);
  });

  test('tool_call 包装', () => {
    const objectArgs = foldTaskList([
      call('w', 'tool_call', {
        calls: [{ name: 'todo_list', arguments: { todos: [todo('1', '包', 'in_progress')], merge: false } }],
      }),
    ]);
    assert.equal(objectArgs[0].content, '包');
    assert.equal(objectArgs[0].status, 'in_progress');
    const stringArgs = foldTaskList([
      call('s', 'functions.tool_call', {
        calls: [{
          name: 'functions.todo_list',
          arguments: JSON.stringify({ todos: [todo('2', '串', 'pending')] }),
        }],
      }),
    ]);
    assert.equal(stringArgs[0].id, '2');
    const parallel = foldTaskList([
      call('p', 'parallel', {
        tool_uses: [{ recipient_name: 'todo_list', parameters: { todos: [todo('3', '并', 'pending')] } }],
      }),
    ]);
    assert.equal(parallel[0].content, '并');
    assert.equal(foldTaskList([
      call('failwrap', 'tool_call', {
        calls: [{ name: 'todo_list', arguments: { todos: [todo('8', '败', 'pending')] } }],
      }),
      tool('failwrap', JSON.stringify({ error: true })),
    ]), null);
  });

  test('非法 status 跳过', () => {
    const items = foldTaskList([
      call('a', 'todo_list', {
        todos: [
          todo('1', '好', 'pending'),
          todo('2', '坏', 'done'),
          todo('', '空id', 'pending'),
          todo('3', '', 'pending'),
          { content: '无id', status: 'pending' },
          { id: '4', content: '无状态' },
          todo('5', '取消', 'cancelled'),
        ],
      }),
    ]);
    assert.deepEqual(items.map((item) => item.id), ['1', '5']);
  });

  test('merge 时缺省 parent 不清掉已有 parent', () => {
    const items = foldTaskList([
      call('a', 'todo_list', {
        todos: [todo('1', '父', 'pending'), todo('2', '子', 'pending', { parent: '1' })],
      }),
      call('b', 'todo_list', {
        merge: true,
        todos: [todo('2', '子改', 'completed'), todo('1', '父改', 'in_progress', { parent: 'root' }), todo('3', '新子', 'pending', { parent: '' })],
      }),
    ]);
    assert.equal(items[1].parent, '1');
    assert.equal(items[1].content, '子改');
    assert.equal(items[1].status, 'completed');
    assert.equal(items[0].parent, 'root');
    assert.equal(items[2].parent, undefined);
    assert.deepEqual(items.map((item) => item.id), ['1', '2', '3']);
    const cleared = foldTaskList([
      call('a', 'todo_list', { todos: [todo('2', '子', 'pending', { parent: '1' })] }),
      call('b', 'todo_list', { merge: true, todos: [{ id: '2', content: '清', status: 'pending', parent: '' }] }),
    ]);
    assert.equal(cleared[0].parent, undefined);
    assert.equal('parent' in cleared[0], false);
  });

  test('从未入账返回 null，空数组替换返回空数组', () => {
    assert.equal(foldTaskList([]), null);
    assert.equal(foldTaskList([{ role: 'user', content: 'hi' }]), null);
    assert.deepEqual(foldTaskList([
      call('a', 'todo_list', { todos: [todo('1', '一', 'pending')] }),
      call('b', 'todo_list', { todos: [] }),
    ]), []);
  });

  test('cancelled 不计入 done/open', () => {
    assert.deepEqual(taskProgress([
      { id: '1', content: '完', status: 'completed' },
      { id: '2', content: '做', status: 'in_progress' },
      { id: '3', content: '等', status: 'pending' },
      { id: '4', content: '消', status: 'cancelled' },
    ]), { done: 1, open: 3 });
    assert.deepEqual(taskProgress([{ id: '4', content: '消', status: 'cancelled' }]), { done: 0, open: 0 });
  });

  test('isTodoStep 对 todo_list 为真、对 terminal 为假、对只包 todo_list 的 tool_call 为真、对混合批次为假', () => {
    assert.equal(isTodoStep({ kind: 'tool', name: 'todo_list', args: { todos: [] } }), true);
    assert.equal(isTodoStep({ kind: 'tool', name: 'terminal', args: { command: 'ls' } }), false);
    assert.equal(isTodoStep({ kind: 'note', text: '旁白' }), false);
    assert.equal(isTodoStep({
      kind: 'tool',
      name: 'tool_call',
      args: { calls: [{ name: 'todo_list', arguments: { todos: [] } }] },
    }), true);
    assert.equal(isTodoStep({
      kind: 'tool',
      name: 'tool_call',
      args: { calls: [{ name: 'todo_list', arguments: {} }, { name: 'terminal', arguments: {} }] },
    }), false);
    assert.equal(isTodoStep({ kind: 'tool', name: 'tool_call', args: null }), false);
    assert.equal(isTodoStep({
      kind: 'tool',
      name: 'parallel',
      args: { tool_uses: [{ recipient_name: 'functions.todo_list', parameters: {} }] },
    }), true);
    assert.equal(isTodoStep({
      kind: 'tool',
      name: 'parallel',
      args: { tool_uses: [{ recipient_name: 'todo_list' }, { recipient_name: 'terminal' }] },
    }), false);
  });

  test('只给出当前会话的清单，别的会话或空会话是空的', () => {
    const messages = [call('a', 'todo_list', { todos: [todo('1', '当前', 'pending')] })];
    assert.deepEqual(tasksForSession('s1', 's1', messages).map((item) => item.id), ['1']);
    assert.deepEqual(tasksForSession('s1', 's2', messages), []);
    assert.deepEqual(tasksForSession('s1', '', messages), []);
  });

  test('两份 task-list.ts 字节相同', () => {
    const left = readFileSync(new URL('../src/lib/task-list.ts', import.meta.url));
    const right = readFileSync(new URL('../src/react/lib/task-list.ts', import.meta.url));
    assert.deepEqual(left, right);
  });
} finally {
  await server.close();
}
