import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true }, appType: 'custom' });
try {
  const { modelEffortFields } = await server.ssrLoadModule('/src/lib/model-effort.ts');
  await test('Flash configured as high switches through verified three-tier variants', () => {
    for (const tier of ['low', 'medium', 'high']) {
      assert.deepEqual(modelEffortFields('gemini-3.8-flash-high', 'sub2api-gemini', tier), {
        model: `gemini-3.8-flash-${tier}`, provider: 'sub2api-gemini', require_model_lock: true,
        model_options: { reasoning_effort: tier },
      });
    }
  });
  await test('every Gemini family clamps legacy high efforts while other model identifiers remain untouched', () => {
    for (const model of ['gemini-4-argon', 'gemini-3.8-flash-high', 'Gemini-3.8-flash', 'google/gemini-2.5-pro', 'models/gemini-test']) {
      const provider = model === 'gemini-4-argon' ? 'sub2api-gemini' : 'other-provider';
      for (const effort of ['xhigh', 'max', 'ultra']) {
        assert.equal(modelEffortFields(model, provider, effort).model_options.reasoning_effort, 'high', model);
      }
    }
    for (const model of ['gpt-6.1-sol', 'claude-opus-5-5', 'not-gemini-test']) {
      assert.deepEqual(modelEffortFields(model, 'sub2api-gemini', 'ultra'), {
        model, provider: 'sub2api-gemini', model_options: { reasoning_effort: 'ultra' },
      });
    }
  });
  await test('Flash restored variants resolve to the configured family entry', async () => {
    const { canonicalModel } = await server.ssrLoadModule('/src/lib/model-effort.ts');
    const choices = [{ model: 'gemini-3.8-flash-high', provider: 'sub2api-gemini', providerName: 'Gemini' }];
    for (const model of ['gemini-3.8-flash', 'gemini-3.8-flash-low', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-high']) {
      assert.equal(canonicalModel(model, choices, 'sub2api-gemini'), 'gemini-3.8-flash');
    }
    assert.equal(canonicalModel('gemini-3.8-flash-low', choices, 'other-provider'), 'gemini-3.8-flash-low');
    assert.equal(canonicalModel('gemini-3.8-flash-tiered', choices, 'sub2api-gemini'), 'gemini-3.8-flash-tiered');
  });
} finally { await server.close(); }
