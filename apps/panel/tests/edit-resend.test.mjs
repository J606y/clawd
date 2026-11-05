import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { editBoundary, resendEdited } = await server.ssrLoadModule('/src/lib/message-edit.ts');
  const { buildTurns } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const target = { role: 'user', content: 'typo', timestamp: 2 };
  const messages = [{ role: 'user', content: 'first', timestamp: 1 }, target, { role: 'assistant', content: 'obsolete', timestamp: 3 }];
  test('rendered user turns retain exact raw message identity for editing', () => {
    const turns = buildTurns(messages);
    assert.equal(turns[1].message, target);
    assert.equal(turns[2].message, undefined);
  });
  test('boundary uses raw message position, excludes suffix from new run', () => {
    assert.deepEqual(editBoundary(messages, target), { fromEnd: 2, expectedContent: 'typo', expectedTimestamp: 2 });
    assert.throws(() => editBoundary(messages, messages[2]), /用户/);
  });
  test('changed text starts independent session with explicit current model and effort', async () => {
    const calls = [];
    const attempt = { requestId: 'id', startAttempted: false };
    const services = {
      branch: async (body) => { calls.push(['branch', body]); return { id: 'copy', model: 'm' }; },
      lock: async (...args) => calls.push(['lock', ...args]),
      start: async (body) => { calls.push(['start', body]); return { run_id: 'r', status: 'running' }; },
    };
    const result = await resendEdited({ sessionId: 'original', boundary: editBoundary(messages, target), originalText: 'typo', text: 'fixed', model: 'm', provider: 'p', effort: 'ultra' }, attempt, services);
    assert.equal(result.session.id, 'copy');
    assert.deepEqual(calls[2][1], { session_id: 'copy', input: 'fixed', model: 'm', provider: 'p', model_options: { reasoning_effort: 'ultra' } });
    assert.equal(calls.some(c => c[0] === 'delete'), false);
  });
  await test('Argon edited resend uses the same low variant and lock as normal send', async () => {
    let body, locked;
    await resendEdited({ sessionId: 'original', boundary: editBoundary(messages, target), originalText: 'typo', text: 'fixed', model: 'gemini-4-argon', provider: 'sub2api-gemini', effort: 'low' }, { requestId: 'argon-edit', startAttempted: false }, {
      branch: async () => ({ id: 'argon-copy' }), lock: async (...args) => { locked = args; },
      start: async payload => {
        assert.deepEqual(locked, ['argon-copy', 'gemini-4-argon-low', 'sub2api-gemini']);
        body = payload; return { run_id: 'r', status: 'running' };
      },
    });
    assert.equal(body.model, 'gemini-4-argon-low');
    assert.equal(body.require_model_lock, true);
    assert.equal(body.model_options.reasoning_effort, 'low');
  });
  test('attachment and wrapped steer messages cannot be silently edited as plain text', () => {
    for (const content of ['typo\n\n[附件已上传：/root/hermes-uploads/a.pdf（1 KB）]', 'typo\n[screenshot]', '[OUT-OF-BAND USER MESSAGE — note]\ntypo\n[/OUT-OF-BAND USER MESSAGE]']) {
      const message = { role: 'user', content, timestamp: 2 };
      assert.throws(() => editBoundary([message], message), /附件|插话/);
    }
  });
  test('blank and unchanged content do not call any API', async () => {
    for (const text of [' ', 'typo']) {
      const attempt = { requestId: 'id', startAttempted: false };
      await assert.rejects(() => resendEdited({ text, originalText: 'typo', model: 'm', provider: 'p' }, attempt, {}), /为空|没有改动/);
      assert.equal(attempt.startAttempted, false);
    }
  });
  test('branch refusal keeps original untouched and does not start a run', async () => {
    const attempt = { requestId: 'id', startAttempted: false };
    await assert.rejects(() => resendEdited({ sessionId: 'original', boundary: editBoundary(messages, target), text: 'fixed', originalText: 'typo', model: 'm', provider: 'p', effort: 'high' }, attempt, { branch: async () => { throw new Error('refused'); } }), /refused/);
    assert.equal(attempt.session, undefined);
    assert.equal(target.content, 'typo');
  });
  test('ambiguous start failure keeps the copy and refuses duplicate send', async () => {
    const attempt = { requestId: 'id', startAttempted: false };
    let branchCalls = 0, startCalls = 0;
    const services = { branch: async () => { branchCalls++; return { id: 'copy' }; }, lock: async () => {}, start: async () => { startCalls++; throw new Error('network'); } };
    const input = { sessionId: 'original', boundary: editBoundary(messages, target), text: 'fixed', originalText: 'typo', model: 'm', provider: 'p', effort: 'max' };
    await assert.rejects(() => resendEdited(input, attempt, services), /network/);
    await assert.rejects(() => resendEdited(input, attempt, services), /避免重复发送/);
    assert.equal(attempt.session.id, 'copy');
    assert.equal(branchCalls, 1);
    assert.equal(startCalls, 1);
  });
} finally { await server.close(); }
