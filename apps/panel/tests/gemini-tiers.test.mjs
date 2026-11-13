import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true }, appType: 'custom' });
try {
  const { modelEffortFields } = await server.ssrLoadModule('/src/lib/model-effort.ts');
  await test('Hydrogen switches through verified four-tier variants, Ultra sends xhigh', () => {
    for (const tier of ['low', 'medium', 'high', 'xhigh']) {
      assert.deepEqual(modelEffortFields('gemini-4-hydrogen-xhigh', 'sub2api-gemini', tier), {
        model: `gemini-4-hydrogen-${tier}`, provider: 'sub2api-gemini', require_model_lock: true,
        model_options: { reasoning_effort: tier },
      });
    }
    assert.deepEqual(modelEffortFields('gemini-4-hydrogen', 'sub2api-gemini', 'ultra'), {
      model: 'gemini-4-hydrogen-xhigh', provider: 'sub2api-gemini', require_model_lock: true,
      model_options: { reasoning_effort: 'xhigh' },
    });
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
  await test('Hydrogen is the Gemini exception: xhigh/max/ultra stay xhigh', () => {
    for (const effort of ['xhigh', 'max', 'ultra']) {
      assert.equal(modelEffortFields('gemini-4-hydrogen', 'sub2api-gemini', effort).model_options.reasoning_effort, 'xhigh', effort);
      assert.equal(modelEffortFields('gemini-4-hydrogen-xhigh', 'sub2api-gemini', effort).model, 'gemini-4-hydrogen-xhigh', effort);
    }
    for (const effort of ['low', 'medium', 'high']) {
      assert.equal(modelEffortFields('gemini-4-hydrogen', 'sub2api-gemini', effort).model_options.reasoning_effort, effort, effort);
    }
  });
  await test('Hydrogen restored variants resolve to the configured family entry', async () => {
    const { canonicalModel } = await server.ssrLoadModule('/src/lib/model-effort.ts');
    const choices = [{ model: 'gemini-4-hydrogen-high', provider: 'sub2api-gemini', providerName: 'Gemini' }];
    for (const model of ['gemini-4-hydrogen', 'gemini-4-hydrogen-low', 'gemini-4-hydrogen-medium', 'gemini-4-hydrogen-high', 'gemini-4-hydrogen-xhigh']) {
      assert.equal(canonicalModel(model, choices, 'sub2api-gemini'), 'gemini-4-hydrogen');
    }
    assert.equal(canonicalModel('gemini-4-hydrogen-low', choices, 'other-provider'), 'gemini-4-hydrogen-low');
    assert.equal(canonicalModel('gemini-4-hydrogen-tiered', choices, 'sub2api-gemini'), 'gemini-4-hydrogen-tiered');
  });
} finally { await server.close(); }
