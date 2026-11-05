import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'fetch'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
Object.defineProperty(globalThis, 'document', { configurable: true, value: { baseURI: 'https://panel.example.test/' } });
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });

try {
  const { buildTurns, withoutInFlight, visibleTurnsBesideLive } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const { api, ApiError } = await server.ssrLoadModule('/src/lib/api.ts');

  test('withoutInFlight drops whole in-flight batch when start is outside current page (long turn)', () => {
    // 构造一个超长回合：最近 200 行全部是助手调工具和工具返回，没有 user 消息作为 turn start
    const longMessages = [];
    for (let i = 1; i <= 100; i++) {
      longMessages.push({
        id: i * 2 - 1,
        role: 'assistant',
        content: '',
        tool_calls: [{ id: `call_${i}`, function: { name: 'terminal', arguments: '{"command":"ls"}' } }],
        timestamp: 1000 + i,
      });
      longMessages.push({
        id: i * 2,
        role: 'tool',
        tool_call_id: `call_${i}`,
        content: 'output',
        timestamp: 1000 + i + 0.1,
      });
    }
    // 最后一条是进行中的未结助手工具调用
    longMessages.push({
      id: 201,
      role: 'assistant',
      content: '',
      tool_calls: [{ id: 'call_last', function: { name: 'terminal', arguments: '{"command":"cat"}' } }],
      timestamp: 1200,
    });

    const trimmed = withoutInFlight(longMessages);
    assert.deepEqual(trimmed, [], '超长回合在当前页没有起点时应返回空，由实时流完整承载，避免历史与实时重复显示');
  });

  test('buildTurns preserves running status in last turn when inFlight is true (remote running)', () => {
    const messages = [
      { id: 1, role: 'user', content: 'hello', timestamp: 100 },
      {
        id: 2,
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'call_1', function: { name: 'terminal', arguments: '{"command":"sleep"}' } }],
        timestamp: 101,
      },
    ];

    // 常规历史：未结步骤被标记为 failed（中断）
    const completedHistory = buildTurns(messages);
    const completedAssistant = completedHistory.find((t) => t.role === 'assistant');
    assert.ok(completedAssistant);
    assert.equal(completedAssistant.steps[0].status, 'failed', '常规历史中无结果步骤标为 failed');

    // 正在另一端执行中：未结步骤保持 running，不误报失败
    const runningHistory = buildTurns(messages, undefined, { inFlight: true });
    const runningAssistant = runningHistory.find((t) => t.role === 'assistant');
    assert.ok(runningAssistant);
    assert.equal(runningAssistant.steps[0].status, 'running', '任务在另一端执行时未结步骤应保持 running');
  });

  test('projectHistory fails interrupted previous tools after stripping the local in-flight turn', () => {
    const messages = [
      { id: 1, role: 'user', content: 'u1', timestamp: 90 },
      { id: 2, role: 'assistant', content: '', timestamp: 91,
        tool_calls: [{ id: 'c1', function: { name: 'terminal', arguments: '{}' } }] },
      { id: 3, role: 'user', content: 'u2', timestamp: 100 },
    ];
    const turns = visibleTurnsBesideLive(messages, { steps: [{ kind: 'tool', status: 'running' }], text: '', thinking: '' }, { localActive: true, ownsHistory: false });
    assert.equal(turns.filter((turn) => turn.role === 'user').length, 1, '本轮应已裁掉');
    const assistant = turns.find((turn) => turn.role === 'assistant');
    assert.ok(assistant);
    assert.deepEqual(assistant.steps.map((step) => [step.id, step.status]), [['c1', 'failed']]);
  });

  test('projectHistory preserves last tool running when serverBusy and no live run', () => {
    const messages = [
      { id: 1, role: 'user', content: 'u1', timestamp: 100 },
      { id: 2, role: 'assistant', content: '', timestamp: 101,
        tool_calls: [{ id: 'c1', function: { name: 'terminal', arguments: '{}' } }] },
    ];
    const assistant = visibleTurnsBesideLive(messages, null, { localActive: false, ownsHistory: false, serverBusy: true }).find((turn) => turn.role === 'assistant');
    assert.ok(assistant);
    assert.deepEqual(assistant.steps.map((step) => [step.id, step.status]), [['c1', 'running']]);
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
