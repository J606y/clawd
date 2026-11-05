import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';

const scratch = process.env.TMPDIR || '/root/.hermes/cache/scratch';
const keys = ['document', 'window', 'fetch', 'navigator', 'localStorage'];
const previous = Object.fromEntries(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const storage = new Map();
const browser = new EventTarget();
Object.assign(browser, {
  setTimeout,
  clearTimeout,
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
});
const doc = new EventTarget();
Object.assign(doc, { baseURI: 'https://panel.example.test/', visibilityState: 'visible' });
for (const [key, value] of Object.entries({
  document: doc,
  window: browser,
  localStorage: browser.localStorage,
  navigator: { onLine: true },
})) {
  Object.defineProperty(globalThis, key, { configurable: true, value });
}

const cacheDir = await mkdtemp(join(scratch, 'approval-contract-'));
const server = await createServer({
  configFile: false,
  root: new URL('..', import.meta.url).pathname,
  cacheDir,
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom',
});
const tick = () => new Promise((resolve) => setImmediate(resolve));
const roots = ['/src/lib', '/src/react/lib'];
const loaded = [];
for (const root of roots) {
  const runsMod = await server.ssrLoadModule(`${root}/runs.ts`);
  const apiMod = await server.ssrLoadModule(`${root}/api.ts`);
  loaded.push({ root, runs: runsMod.runs, api: apiMod.api, ApiError: apiMod.ApiError });
}

function exactBody(requestId, generation, choice) {
  return JSON.stringify({ request_id: requestId, execution_generation: generation, choice });
}

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

try {
  for (const mod of loaded) {
    const label = mod.root;

    await test(`${label} complete identity posts exactly three approval fields`, async () => {
      const captured = [];
      globalThis.fetch = async (input, init) => {
        captured.push({ url: String(input), method: init?.method, body: init?.body ?? '' });
        return jsonResponse(200, {});
      };
      await mod.api.approve('run_synthetic', 'once', 'prompt-1', 4);
      await mod.api.approve('run_boundary', 'deny', 'y'.repeat(256), 0);
      assert.deepEqual(captured.map((item) => item.body), [
        exactBody('prompt-1', 4, 'once'),
        exactBody('y'.repeat(256), 0, 'deny'),
      ]);
      for (const item of captured) {
        assert.equal(item.method, 'POST');
        assert.match(item.url, /\/v1\/runs\/[^/]+\/approval$/);
        assert.deepEqual(Object.keys(JSON.parse(item.body)), ['request_id', 'execution_generation', 'choice']);
      }
    });

    await test(`${label} incomplete identity does not post a one- or two-field approval body`, async () => {
      const cases = [
        ['request-only', 'prompt-1', undefined],
        ['neither', undefined, undefined],
        ['long-id', 'x'.repeat(257), 4],
        ['float-generation', 'prompt-1', 4.5],
        ['empty-id', '', 4],
      ];
      const failures = [];
      for (const [name, requestId, generation] of cases) {
        const captured = [];
        globalThis.fetch = async (input, init) => {
          captured.push({ url: String(input), body: init?.body ?? '' });
          return jsonResponse(200, {});
        };
        let rejected = false;
        try {
          await mod.api.approve('run_synthetic', 'once', requestId, generation);
        } catch (error) {
          rejected = error instanceof mod.ApiError && error.status === 0;
        }
        const posted = captured.filter((item) => String(item.url).includes('/approval'));
        if (posted.length !== 0 || !rejected) {
          failures.push(`${name}: posts=${posted.length} body=${posted[0]?.body ?? ''} rejected=${rejected}`);
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${label} missing generation is not submitted unless one approval control recovers it`, async () => {
      const sid = `recover-${label.replace(/\W/g, '')}`;
      const posts = [];
      const statusReads = [];
      const nativeStatus = mod.api.runStatus;
      let stream;
      mod.api.runStatus = async (runId) => {
        statusReads.push(runId);
        return {
          run_id: runId,
          session_id: sid,
          status: 'waiting_for_approval',
          execution_generation: 9,
          pending_controls: [{ kind: 'approval', prompt_id: 'event-id', execution_generation: 9, command: 'other' }],
        };
      };
      globalThis.fetch = async (input, init) => {
        const url = String(input);
        if (url.includes('/approval')) {
          posts.push(init?.body ?? '');
          return jsonResponse(200, {});
        }
        if (url.includes('/events')) {
          const body = new ReadableStream({
            start(controller) {
              stream = controller;
              controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                event: 'approval.request',
                request_id: 'event-id',
                command: 'shown-command',
                choices: ['once', 'deny'],
              })}\n\n`));
            },
          });
          return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
        }
        return jsonResponse(200, { status: 'running' });
      };
      mod.runs.observe(sid, `run-${sid}`, Date.now());
      const run = mod.runs.get(sid);
      try {
        for (let n = 0; n < 40 && !run.approval; n += 1) await tick();
        assert.equal(run.approval?.requestId, 'event-id');
        assert.equal(run.approval?.executionGeneration, undefined);
        const before = statusReads.length;
        await mod.runs.respondApproval(sid, 'once');
        assert.ok(statusReads.length > before, 'a present request id without an integer generation must consult runStatus');
        assert.deepEqual(posts, [exactBody('event-id', 9, 'once')]);
        assert.equal(run.approval, null);
      } finally {
        mod.runs.ctrls.get(sid)?.abort();
        try { stream?.close(); } catch { /* already closed */ }
        await tick();
        mod.runs.clear(sid);
        mod.api.runStatus = nativeStatus;
      }
    });

    await test(`${label} zero or multiple approval controls are not guessed into a generation`, async () => {
      const scenarios = [
        { name: 'zero', controls: [], statusGeneration: 9 },
        { name: 'clarify-only', controls: [{ kind: 'clarify', prompt_id: 'q1', execution_generation: 9 }], statusGeneration: 9 },
        {
          name: 'two-approvals',
          controls: [
            { kind: 'approval', prompt_id: 'left', execution_generation: 3 },
            { kind: 'approval', prompt_id: 'right', execution_generation: 4 },
          ],
          statusGeneration: 4,
        },
      ];
      const failures = [];
      for (const scenario of scenarios) {
        const sid = `guess-${scenario.name}-${label.replace(/\W/g, '')}`;
        const posts = [];
        const nativeStatus = mod.api.runStatus;
        let stream;
        mod.api.runStatus = async () => ({
          status: 'waiting_for_approval',
          execution_generation: scenario.statusGeneration,
          pending_controls: scenario.controls,
        });
        globalThis.fetch = async (input, init) => {
          const url = String(input);
          if (url.includes('/approval')) {
            posts.push(init?.body ?? '');
            return jsonResponse(200, {});
          }
          if (url.includes('/events')) {
            const body = new ReadableStream({
              start(controller) {
                stream = controller;
                controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                  event: 'approval.request',
                  request_id: 'event-id',
                  choices: ['once', 'deny'],
                })}\n\n`));
              },
            });
            return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
          }
          return jsonResponse(200, { status: 'running' });
        };
        mod.runs.observe(sid, `run-${sid}`, Date.now());
        const run = mod.runs.get(sid);
        try {
          for (let n = 0; n < 40 && !run.approval; n += 1) await tick();
          await mod.runs.respondApproval(sid, 'once');
          if (posts.length !== 0 || run.approval == null || run.approval.pending !== false || !String(run.error).includes('未提交')) {
            failures.push(`${scenario.name}: posts=${JSON.stringify(posts)} approval=${JSON.stringify(run.approval)} error=${run.error}`);
          }
        } finally {
          mod.runs.ctrls.get(sid)?.abort();
          try { stream?.close(); } catch { /* already closed */ }
          await tick();
          mod.runs.clear(sid);
          mod.api.runStatus = nativeStatus;
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${label} a choice outside the offered set is not submitted and choices are not widened`, async () => {
      const sid = `choice-${label.replace(/\W/g, '')}`;
      const posts = [];
      let stream;
      globalThis.fetch = async (input, init) => {
        const url = String(input);
        if (url.includes('/approval')) {
          posts.push(init?.body ?? '');
          return jsonResponse(200, {});
        }
        if (url.includes('/events')) {
          const body = new ReadableStream({
            start(controller) {
              stream = controller;
              controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                event: 'approval.request',
                prompt_id: 'prompt-node',
                execution_generation: 4,
                command: 'synthetic',
              })}\n\n`));
            },
          });
          return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
        }
        return jsonResponse(200, { status: 'running' });
      };
      mod.runs.observe(sid, `run-${sid}`, Date.now());
      const run = mod.runs.get(sid);
      try {
        for (let n = 0; n < 40 && !run.approval; n += 1) await tick();
        assert.deepEqual(run.approval.choices, ['once', 'deny']);
        await mod.runs.respondApproval(sid, 'session');
        await mod.runs.respondApproval(sid, 'always');
        assert.deepEqual(posts, []);
        assert.equal(run.approval.pending, false);
        assert.equal(run.approval.requestId, 'prompt-node');
      } finally {
        mod.runs.ctrls.get(sid)?.abort();
        try { stream?.close(); } catch { /* already closed */ }
        await tick();
        mod.runs.clear(sid);
      }
    });

    await test(`${label} prompt_id is submitted when an old request_id is also present`, async () => {
      const sid = `identity-${label.replace(/\W/g, '')}`;
      const posts = [];
      let stream;
      globalThis.fetch = async (input, init) => {
        const url = String(input);
        if (url.includes('/approval')) {
          posts.push(init?.body ?? '');
          return jsonResponse(200, {});
        }
        if (url.includes('/events')) {
          const body = new ReadableStream({
            start(controller) {
              stream = controller;
              controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                event: 'approval.request',
                request_id: 'legacy-id',
                prompt_id: 'prompt-node',
                execution_generation: 4,
                choices: ['once', 'deny'],
              })}\n\n`));
            },
          });
          return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
        }
        return jsonResponse(200, { status: 'running' });
      };
      mod.runs.observe(sid, `run-${sid}`, Date.now());
      const run = mod.runs.get(sid);
      try {
        for (let n = 0; n < 40 && !run.approval; n += 1) await tick();
        assert.equal(run.approval.requestId, 'prompt-node');
        await mod.runs.respondApproval(sid, 'once');
        assert.deepEqual(posts, [exactBody('prompt-node', 4, 'once')]);
      } finally {
        mod.runs.ctrls.get(sid)?.abort();
        try { stream?.close(); } catch { /* already closed */ }
        await tick();
        mod.runs.clear(sid);
      }
    });

    await test(`${label} success clears the approval failure text and leaves an unrelated error`, async () => {
      const sid = `clear-${label.replace(/\W/g, '')}`;
      const unrelated = `keep-${label.replace(/\W/g, '')}`;
      let attempts = 0;
      let stream;
      let other;
      globalThis.fetch = async (input, init) => {
        const url = String(input);
        if (url.includes('/approval')) {
          attempts += 1;
          if (url.includes(`/run-${sid}/`) && attempts === 1) {
            return jsonResponse(400, { error: { message: 'invalid_params', code: 'invalid_params' } });
          }
          assert.equal(init?.body, exactBody('prompt-synthetic', 4, 'once'));
          return jsonResponse(200, {});
        }
        if (url.includes('/events')) {
          const body = new ReadableStream({
            start(controller) {
              if (url.includes(`/run-${sid}/`)) stream = controller;
              else other = controller;
              controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                event: 'approval.request',
                prompt_id: 'prompt-synthetic',
                execution_generation: 4,
                choices: ['once', 'deny'],
              })}\n\n`));
            },
          });
          return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
        }
        return jsonResponse(200, { status: 'running' });
      };
      mod.runs.observe(sid, `run-${sid}`, Date.now());
      mod.runs.observe(unrelated, `run-${unrelated}`, Date.now());
      const run = mod.runs.get(sid);
      const otherRun = mod.runs.get(unrelated);
      try {
        for (let n = 0; n < 40 && (!run.approval || !otherRun.approval); n += 1) await tick();
        await mod.runs.respondApproval(sid, 'once');
        assert.equal(run.error, '审批提交失败：invalid_params');
        assert.equal(run.approval?.pending, false);
        await mod.runs.respondApproval(sid, 'once');
        assert.equal(run.approval, null);
        assert.equal(run.error, '', 'a successful retry must clear the approval submission failure');
        otherRun.error = '停止失败：请重试';
        await mod.runs.respondApproval(unrelated, 'once');
        assert.equal(otherRun.approval, null);
        assert.equal(otherRun.error, '停止失败：请重试');
      } finally {
        for (const id of [sid, unrelated]) mod.runs.ctrls.get(id)?.abort();
        try { stream?.close(); } catch { /* already closed */ }
        try { other?.close(); } catch { /* already closed */ }
        await tick();
        mod.runs.clear(sid);
        mod.runs.clear(unrelated);
      }
    });

    await test(`${label} 409 matched identity settles, anonymous terminal stays unconfirmed, 404 still fails`, async () => {
      const nativeStatus = mod.api.runStatus;
      const cases = [
        {
          name: 'conflict-completed',
          expectStatus: 'completed',
          expectError: '',
        },
        {
          name: 'conflict-anonymous',
          status: async () => ({ status: 'completed' }),
          expectStatus: 'running',
          expectError: /无法确认这次运行的终态/,
        },
        {
          name: 'conflict-missing',
          status: async () => { throw new mod.ApiError('server', 404, 'not found'); },
          expectStatus: 'failed',
          expectError: /运行记录已不存在/,
        },
      ];
      for (const scenario of cases) {
        const sid = `old-${scenario.name}-${label.replace(/\W/g, '')}`;
        let stream;
        mod.api.runStatus = scenario.name === 'conflict-completed'
          ? async () => ({ status: 'completed', run_id: `run-${sid}`, session_id: sid, created_at: 1, updated_at: 2 })
          : scenario.status;
        globalThis.fetch = async (input) => {
          const url = String(input);
          if (url.includes('/approval')) return jsonResponse(409, { error: { message: 'stale_generation', code: 'stale_generation' } });
          if (url.includes('/events')) {
            const body = new ReadableStream({
              start(controller) {
                stream = controller;
                controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
                  event: 'approval.request',
                  prompt_id: 'prompt-node',
                  execution_generation: 4,
                  choices: ['once', 'deny'],
                })}\n\n`));
              },
            });
            return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
          }
          return jsonResponse(200, { status: 'running' });
        };
        mod.runs.observe(sid, `run-${sid}`, Date.now());
        const run = mod.runs.get(sid);
        try {
          for (let n = 0; n < 40 && !run.approval; n += 1) await tick();
          run.error = '停止失败：请重试';
          await mod.runs.respondApproval(sid, 'once');
          await tick();
          assert.equal(run.approval, null, scenario.name);
          assert.equal(run.status, scenario.expectStatus, scenario.name);
          if (scenario.expectError instanceof RegExp) assert.match(run.error, scenario.expectError, scenario.name);
          else assert.equal(run.error, scenario.expectError, scenario.name);
        } finally {
          mod.runs.ctrls.get(sid)?.abort();
          try { stream?.close(); } catch { /* already closed */ }
          await tick();
          mod.runs.clear(sid);
          mod.api.runStatus = nativeStatus;
        }
      }
    });
  }
} finally {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
  for (const [key, descriptor] of Object.entries(previous)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
