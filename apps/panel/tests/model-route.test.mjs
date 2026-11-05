import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const keys = ['document', 'window', 'fetch', 'navigator', 'localStorage'];
const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const storage = new Map();
const browser = new EventTarget();
Object.assign(browser, { setTimeout, clearTimeout, localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) } });
const doc = new EventTarget();
Object.assign(doc, { baseURI: 'https://panel.example.test/', visibilityState: 'visible' });
for (const [key, value] of Object.entries({ document: doc, window: browser, localStorage: browser.localStorage, navigator: { onLine: true } })) {
  Object.defineProperty(globalThis, key, { configurable: true, value });
}
const cacheDir = await mkdtemp(join(tmpdir(), 'panel-model-route-test-'));
const server = await createServer({ configFile: false, cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true }, appType: 'custom' });

async function finished(runs, RUN_FINISHED, sid) {
  await new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); runs.removeEventListener(RUN_FINISHED, on); };
    const on = e => { if (e.detail === sid) { cleanup(); resolve(); } };
    const timer = setTimeout(() => {
      cleanup();
      const run = runs.get(sid);
      reject(new Error(`Synthetic route did not finish: ${sid}; status=${run?.status}; unverified=${run?.unverified}; error=${run?.error}`));
    }, 2000);
    runs.addEventListener(RUN_FINISHED, on);
  });
}

// providerOf 带缓存：每个用例把时钟往后拨一大截，让缓存过期、重新取模型列表。
const realNow = Date.now;
let skew = 0;
Date.now = () => realNow() + skew;
const expireProviderCache = () => { skew += 24 * 3600 * 1000; };

try {
  const { runs, RUN_FINISHED } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { api } = await server.ssrLoadModule('/src/lib/api.ts');
  const { setEffort } = await server.ssrLoadModule('/src/lib/effort.ts');
  const locks = [];
  const readModelOptions = api.modelOptions;
  await test('Flash model options deduplicate configured tiers and normalize the default', async () => {
    globalThis.fetch = async () => new Response(JSON.stringify({ provider: 'sub2api-gemini', model: 'gemini-3.8-flash-high', providers: [
      { slug: 'sub2api-gemini', name: 'Gemini', authenticated: true, models: ['gemini-3.8-flash-high', 'gemini-3.8-flash-low', 'gemini-3.8-flash', 'gemini-4-argon'] },
      { slug: 'other', name: 'Other', authenticated: true, models: ['gemini-3.8-flash-high'] },
    ] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    const options = await readModelOptions();
    assert.deepEqual(options.current, { provider: 'sub2api-gemini', model: 'gemini-3.8-flash' });
    assert.deepEqual(options.choices.map(c => [c.model, c.provider]), [
      ['gemini-3.8-flash', 'sub2api-gemini'], ['gemini-4-argon', 'sub2api-gemini'], ['gemini-3.8-flash-high', 'other'],
    ]);
  });
  await test('default Flash configured as high obeys all three effort settings', async () => {
    api.modelOptions = async () => ({ current: { model: 'gemini-3.8-flash-high', provider: 'sub2api-gemini' }, choices: [
      { model: 'gemini-3.8-flash-high', provider: 'sub2api-gemini', providerName: 'Gemini' },
    ] });
    for (const effort of ['low', 'medium', 'high']) {
      const fields = await api.runModelFields('', '', effort);
      assert.equal(fields.model, `gemini-3.8-flash-${effort}`);
      assert.equal(fields.model_options.reasoning_effort, effort);
      assert.equal(fields.require_model_lock, true);
    }
  });
  await test('Flash restored low resolves through its configured high entry without guessing providers', async () => {
    expireProviderCache();
    api.modelOptions = async () => ({ current: { model: '', provider: '' }, choices: [
      { model: 'gemini-3.8-flash-high', provider: 'sub2api-gemini', providerName: 'Gemini' },
    ] });
    assert.equal(await api.providerOf('gemini-3.8-flash-low'), 'sub2api-gemini');
    expireProviderCache();
    api.modelOptions = async () => ({ current: { model: '', provider: '' }, choices: [
      { model: 'gemini-3.8-flash-high', provider: 'sub2api-gemini', providerName: 'Gemini' },
      { model: 'gemini-3.8-flash-low', provider: 'other', providerName: 'Other' },
    ] });
    assert.equal(await api.providerOf('gemini-3.8-flash-low'), '', 'ambiguous routes must remain blocked');
  });
  await test('other Gemini defaults clamp effort without overriding the global route', async () => {
    api.modelOptions = async () => ({ current: { model: 'Gemini-next', provider: 'other' }, choices: [] });
    assert.deepEqual(await api.runModelFields('', '', 'ultra'), { model_options: { reasoning_effort: 'high' } });
  });
  api.lockModel = async (...args) => { locks.push(args); };
  globalThis.fetch = async input => {
    const url = new URL(input instanceof URL || typeof input === 'string' ? String(input) : input.url);
    assert.equal(url.origin, 'https://panel.example.test');
    assert.match(url.pathname, /\/v1\/runs\/synthetic-route\/events$/);
    return new Response('data: {"seq":1,"event":"run.completed"}\n\n', { status: 200,
      headers: { 'Content-Type': 'text/event-stream' } });
  };
  const argonOptions = { current: { model: 'gemini-4-argon', provider: 'sub2api-gemini' }, choices: [
    { model: 'gemini-4-argon', provider: 'sub2api-gemini', providerName: 'Gemini' },
  ] };

  for (const [name, model, provider, effort, expected] of [
    ['new session default', '', '', 'low', 'low'],
    ['restored low session switches to medium', 'gemini-4-argon-low', '', 'medium', 'medium'],
    ['high switches to low', 'gemini-4-argon-high', 'sub2api-gemini', 'low', 'low'],
    ['xhigh', 'gemini-4-argon', 'sub2api-gemini', 'xhigh', 'high'],
    ['max', 'gemini-4-argon', 'sub2api-gemini', 'max', 'high'],
    ['ultra', 'gemini-4-argon', 'sub2api-gemini', 'ultra', 'high'],
  ]) {
    await test(`Argon route: ${name}`, async () => {
      expireProviderCache();
      const sid = `argon-${name}`;
      let body, lockAtAdmission;
      api.modelOptions = async () => argonOptions;
      api.startRun = async b => {
        lockAtAdmission = locks.at(-1);
        body = b; return { run_id: 'synthetic-route', status: 'running' };
      };
      setEffort(effort, sid);
      const done = finished(runs, RUN_FINISHED, sid);
      runs.start(sid, 'hello', [], 'hello', model, provider);
      await done;
      assert.ok(body, 'must resolve the restored model provider and submit');
      assert.deepEqual(lockAtAdmission, [sid, `gemini-4-argon-${expected}`, 'sub2api-gemini'], 'the actual variant must be locked before starting');
      assert.equal(body.model, `gemini-4-argon-${expected}`);
      assert.equal(body.provider, 'sub2api-gemini');
      assert.equal(body.require_model_lock, true);
      assert.equal(body.model_options.reasoning_effort, expected);
      runs.clear(sid);
    });
  }


  await test('Argon low sends the low variant with a per-turn model lock', async () => {
    const sid = 'argon-low';
    let body;
    api.modelOptions = async () => ({ current: { model: 'gemini-4-argon', provider: 'sub2api-gemini' }, choices: [
      { model: 'gemini-4-argon', provider: 'sub2api-gemini', providerName: 'Gemini' },
    ] });
    api.startRun = async b => { body = b; return { run_id: 'synthetic-route', status: 'running' }; };
    setEffort('low', sid);
    const done = finished(runs, RUN_FINISHED, sid);
    runs.start(sid, 'hello', [], 'hello', 'gemini-4-argon', 'sub2api-gemini');
    await done;
    assert.equal(body.model, 'gemini-4-argon-low');
    assert.equal(body.provider, 'sub2api-gemini');
    assert.equal(body.require_model_lock, true);
    assert.equal(body.model_options.reasoning_effort, 'low');
    runs.clear(sid);
  });

  await test('Argon lock refusal prevents admission and preserves retryable unsent state', async () => {
    const sid = 'argon-lock-refused';
    let starts = 0;
    api.lockModel = async () => { throw new Error('lock refused'); };
    api.startRun = async () => { starts++; throw new Error('must not start'); };
    const done = finished(runs, RUN_FINISHED, sid);
    runs.start(sid, 'unsent text', [], 'unsent text', 'gemini-4-argon', 'sub2api-gemini');
    await done;
    assert.equal(starts, 0);
    assert.equal(runs.get(sid).status, 'failed');
    assert.equal(runs.get(sid).runId, '');
    assert.equal(runs.get(sid).userText, 'unsent text');
    runs.clear(sid);
    api.lockModel = async (...args) => { locks.push(args); };
  });

  for (const [name, options] of [
    ['model list unavailable', async () => { throw new Error('offline'); }],
    ['same model under two providers', async () => ({ current: { provider: '', model: '' }, choices: [
      { provider: 'a', providerName: 'A', model: 'dup-model' }, { provider: 'b', providerName: 'B', model: 'dup-model' }] })],
  ]) {
    await test(`locked model whose provider cannot be resolved is not sent (${name})`, async () => {
      expireProviderCache();
      const sid = `route-${name}`;
      let started = 0;
      api.modelOptions = options;
      api.startRun = async () => { started++; return { run_id: 'never', status: 'started' }; };
      const done = finished(runs, RUN_FINISHED, sid);
      runs.start(sid, 'hello', [], 'hello', 'dup-model', '');
      await done;
      const run = runs.get(sid);
      assert.equal(started, 0, 'must not fall back to the global default model');
      assert.equal(run.status, 'failed');
      assert.equal(run.runId, '', 'no run id, so the chat view restores the text to the composer');
      assert.match(run.error, /dup-model/);
      runs.clear(sid);
    });
  }

  await test('resolved provider is sent together with the model', async () => {
    expireProviderCache();
    const sid = 'route-ok';
    let body;
    api.modelOptions = async () => ({ current: { provider: '', model: '' }, choices: [{ provider: 'cpa', providerName: 'CPA', model: 'solo-model' }] });
    api.startRun = async b => { body = b; return { run_id: 'synthetic-route', status: 'running' }; };
    const done = finished(runs, RUN_FINISHED, sid);
    runs.start(sid, 'hello', [], 'hello', 'solo-model', '');
    await done;
    assert.equal(body.model, 'solo-model');
    assert.equal(body.provider, 'cpa');
    runs.clear(sid);
  });
} finally {
  Date.now = realNow;
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
  for (const [key, descriptor] of Object.entries(previous)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
