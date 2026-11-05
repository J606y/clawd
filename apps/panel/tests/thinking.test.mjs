import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'fetch', 'localStorage', 'navigator'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
browser.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const document = new EventTarget();
document.baseURI = 'https://panel.example.test/';
document.visibilityState = 'visible';
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: browser.localStorage });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });

function makeRun(sessionId = 'sess', extra = {}) {
  return {
    sessionId, runId: `run-${sessionId}`, status: 'running', userText: 'synthetic task',
    attachments: [], steers: [], thinking: '', steps: [], text: '', approval: null,
    error: '', stopping: false, startedAt: 100000, lastEventAt: 100000, model: '', provider: '', ...extra,
  };
}

try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { splitLeakedThinking, buildTurns } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const { splitSteerTimeline } = await server.ssrLoadModule('/src/lib/steer-timeline.ts');
  const leakedText = splitLeakedThinking;

  await test('unframed prose, quoted examples and incomplete markers remain byte-for-byte intact', () => {
    const cases = [
      '',
      '先用一句话说明结果。部署成功，服务运行正常。',
      '思考过程是解决问题的重要环节。这里讲的是学习方法。',
      'Example prompt: Remember: before calling a tool, describe the action. This is documentation.',
      'THINKING: a guide to clearer writing.',
      'THINKING:\nLet me inspect the result.\n\n答案：部署成功。',
      'Need to inspect logs.$]',
      '说明。$]后续说明。',
      '示例代码：\n```text\nhello.$]world\n```\n以上为原样样例。',
      '```text\nTHINKING:\nexample\n$]\n```',
      '> THINKING:\n> quoted thought\n> $]\n正文',
      'THINKING:\nexample with no ending',
    ];
    for (const text of cases) {
      assert.deepEqual(splitLeakedThinking(text), { reasoning: '', text }, text);
    }
  });

  await test('complete legacy frames preserve the answer and code/quote ambiguity is left untouched', () => {
    assert.deepEqual(splitLeakedThinking('THINKING:\nSynthetic thought\n$]\n\n  Answer.\n'), {
      reasoning: 'Synthetic thought', text: '\n  Answer.\n',
    });
    assert.deepEqual(splitLeakedThinking('THINKING:\r\nSynthetic thought\r\n$]\r\nAnswer.'), {
      reasoning: 'Synthetic thought', text: 'Answer.',
    });
    assert.deepEqual(splitLeakedThinking('THINKING:\nSynthetic thought\n$]'), { reasoning: 'Synthetic thought', text: '' });
    for (const text of [
      'THINKING:\n```text\n$]\n```\nAnswer.',
      'THINKING:\n~~~text\n$]\n~~~\\nAnswer.',
      'THINKING:\n`multiline\n$]\nexample`\nAnswer.',
      'THINKING:\n> quoted\n$]\nAnswer.',
      'THINKING:\n<pre>example\n$]\n</pre>',
      'THINKING:\nTHINKING:\nnested\n$]\nAnswer.',
    ]) assert.deepEqual(splitLeakedThinking(text), { reasoning: '', text });
  });

  await test('tool start preserves raw note offsets and extracted content cannot be overwritten by standard thinking', () => {
    const raw = 'THINKING:\nSynthetic thought\n$]\nAnswer.';
    const run = makeRun('raw', { text: raw, steers: [{ text: 'steer', status: 'sent', files: [], uploads: [], anchor: { step: 0, offset: raw.length } }] });
    runs.apply(run, 'tool.started', { tool_name: 'terminal' });
    assert.equal(run.steps[0].text, raw, 'tool note must keep original bytes for the captured steer offset');
    assert.equal(run.thinking, '', 'inferred reasoning must not share the transport-owned thinking buffer');
    runs.apply(run, 'tool.progress', { tool_name: '_thinking', delta: 'Standard thought' });
    assert.equal(run.thinking, 'Standard thought');
    assert.equal(run.steps[0].text, raw);
    assert.equal(run.steers[0].anchor.offset, raw.length);
  });

  await test('rendered commentary and pre-steer text use the same split as the live tail', () => {
    const raw = 'THINKING:\nSynthetic thought\n$]\nAnswer.';
    const run = makeRun('sess', { text: raw });
    runs.apply(run, 'assistant.commentary', { text: raw, already_streamed: true });
    runs.apply(run, 'tool.started', { tool_name: 'terminal' });
    const note = run.steps.find((step) => step.kind === 'note');
    assert.ok(note);
    assert.deepEqual(leakedText(note.text), { reasoning: 'Synthetic thought', text: 'Answer.' });
    assert.equal(splitLeakedThinking(note.text).text, 'Answer.');
    const steered = makeRun('sess', { text: raw + 'Tail.', steers: [{ text: 'steer', status: 'sent', files: [], uploads: [], anchor: { step: 0, offset: raw.length } }] });
    const parts = splitSteerTimeline(steered).filter((part) => part.kind === 'assistant');
    assert.deepEqual(parts.map((part) => leakedText(part.text).text), ['Answer.', 'Tail.']);
    assert.equal(leakedText(parts[0].text).reasoning, 'Synthetic thought');
  });

  await test('history preserves images and standard reasoning and does not render a framed commentary twice', () => {
    const raw = 'THINKING:\nSynthetic thought\n$]\nAnswer.';
    const messages = [{ id: 1, role: 'user', content: 'Question' }, {
      id: 2, role: 'assistant', reasoning: 'Standard thought',
      content: [{ type: 'text', text: raw }, { type: 'image_url', image_url: { url: 'data:image/png;base64,AA==' } }],
      tool_calls: [{ id: 'c', function: { name: 'terminal', arguments: '{}' } }],
    }];
    const notes = new Map([[2, [raw]]]);
    const [user, turn] = buildTurns(messages, notes, { inFlight: true });
    assert.equal(user.text, 'Question');
    assert.equal(turn.reasoning, 'Standard thought\n\nSynthetic thought');
    assert.equal(turn.steps.filter((step) => step.kind === 'note').length, 1);
    assert.equal(turn.steps[0].text, 'Answer.');
    assert.deepEqual(turn.steps[0].images, ['data:image/png;base64,AA==']);
    assert.equal(turn.steps[1].status, 'running');
    assert.equal(turn.reasoning.split('Synthetic thought').length - 1, 1);
    assert.equal(turn.steps[0].text.includes('Synthetic thought'), false);
    assert.equal(messages[1].content[0].text, raw, 'history source remains immutable');
  });

  await test('every streamed prefix preserves uncertain text and converges to history without hiding the answer', () => {
    for (const raw of ['THINKING:\nSynthetic thought\n$]\nAnswer.', 'THINKING:\nNo closing delimiter.\nAnswer.']) {
      const run = makeRun();
      for (const char of raw) {
        runs.apply(run, 'assistant.delta', { delta: char });
        const split = splitLeakedThinking(run.text);
        if (!split.reasoning) assert.equal(split.text, run.text);
        assert.deepEqual(leakedText(run.text).text.trim() ? [leakedText(run.text).text] : [], split.text.trim() ? [split.text] : []);
      }
      const final = buildTurns([{ role: 'assistant', content: raw }])[0];
      assert.equal(final.text, splitLeakedThinking(run.text).text);
      assert.ok(final.text.includes('Answer.'));
    }
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
