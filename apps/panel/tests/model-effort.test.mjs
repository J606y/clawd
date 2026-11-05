import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true }, appType: 'custom' });
try {
  const { modelEffortFields, canonicalModel } = await server.ssrLoadModule('/src/lib/model-effort.ts');
  const { GROK_EFFORT_LEVELS, EFFORT_LEVELS } = await server.ssrLoadModule('/src/lib/effort.ts');
  const choice = (model, provider = 'sub2api-gemini') => ({ model, provider, providerName: provider });
  const choices = [choice('gemini-4-argon')];
  for (const [model, provider, gemini] of [
    ['gpt-6.1-sol', 'sub2api-codex', false], ['claude-opus-5-5', 'sub2api-gemini', false],
    ['gemini-4-argon', 'other-provider', true], ['gemini-4-argon-preview', 'sub2api-gemini', true],
  ]) {
    test(`${provider}/${model} preserves its ID and applies the model's effort limit`, () => {
      for (const effort of ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']) {
        const expected = gemini && !['low', 'medium'].includes(effort) ? 'high' : effort;
        assert.deepEqual(modelEffortFields(model, provider, effort, choices), { model, provider, model_options: { reasoning_effort: expected } });
      }
    });
  }
  test('explicit configured Argon tiers stay pinned even when bare model also exists', () => {
    const pinned = [...choices, choice('gemini-4-argon-high')];
    assert.equal(canonicalModel('gemini-4-argon-high', pinned), 'gemini-4-argon-high');
    assert.deepEqual(modelEffortFields('gemini-4-argon-high', 'sub2api-gemini', 'low', pinned), {
      model: 'gemini-4-argon-high', provider: 'sub2api-gemini', model_options: { reasoning_effort: 'low' },
    });
  });
  test('Grok slider ceiling is xhigh, and max or ultra is not sent', () => {
    for (const model of ['grok-4.7', 'grok-4.20-multi-agent-0309', 'x-ai/grok-4.6']) {
      for (const effort of ['low', 'medium', 'high']) {
        assert.equal(modelEffortFields(model, 'sub2api-grok', effort).model_options.reasoning_effort, effort);
      }
      for (const effort of ['xhigh', 'max', 'ultra']) {
        assert.deepEqual(modelEffortFields(model, 'sub2api-grok', effort), {
          model, provider: 'sub2api-grok', model_options: { reasoning_effort: 'xhigh' },
        });
      }
    }
    assert.equal(modelEffortFields('gpt-6.1-sol', 'sub2api-codex', 'ultra').model_options.reasoning_effort, 'ultra');
    assert.deepEqual(GROK_EFFORT_LEVELS.map((l) => l.label), ['低', '中', '高', 'Ultra']);
    assert.deepEqual(GROK_EFFORT_LEVELS.map((l) => l.id), ['low', 'medium', 'high', 'ultra']);
    assert.equal(EFFORT_LEVELS.length, 6);
  });
  test('only recognized dynamic Argon variants normalize to an available bare model', () => {
    for (const tier of ['low', 'medium', 'high']) assert.equal(canonicalModel(`gemini-4-argon-${tier}`, choices), 'gemini-4-argon');
    assert.equal(canonicalModel('gemini-4-argon-high', []), 'gemini-4-argon-high');
    assert.equal(canonicalModel('gemini-4-argon-high', [choice('gemini-4-argon', 'other')]), 'gemini-4-argon-high');
    assert.equal(canonicalModel('gemini-3.8-flash-high', [choice('gemini-3.8-flash')]), 'gemini-3.8-flash');
    assert.equal(canonicalModel('gemini-4-argon-high', choices, 'other'), 'gemini-4-argon-high');
  });
} finally { await server.close(); }
