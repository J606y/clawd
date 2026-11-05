// 终态身份回归。走 RunStore 的 follow / settleLostExecutor / 409 / stop / reconcile，
// 不测独立 helper，不 import Python 产品。本文件在编写时未运行。
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

const cacheDir = await mkdtemp(join(scratch, 'runs-terminal-identity-'));
const server = await createServer({
  configFile: false,
  root: new URL('..', import.meta.url).pathname,
  cacheDir,
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom',
});
const tick = () => new Promise((resolve) => setImmediate(resolve));
const encoder = new TextEncoder();
const UNCONFIRMED = /无法确认这次运行的终态/;
const TERMINALS = ['completed', 'cancelled', 'failed', 'interrupted'];
const NAMES = ['no-id', 'run-only', 'session-only', 'empty-run', 'wrong-run', 'wrong-session', 'old-run', 'match'];
let seq = 0;

function identityBody(name, sid, runId, status) {
  const error = status === 'failed' ? '具体失败' : status === 'interrupted' ? '网关中断' : undefined;
  const extra = error ? { error } : {};
  const base = { status, ...extra };
  if (name === 'no-id') return base;
  if (name === 'run-only') return { ...base, run_id: runId };
  if (name === 'session-only') return { ...base, session_id: sid };
  if (name === 'empty-run') return { ...base, run_id: '', session_id: sid };
  if (name === 'wrong-run') return { ...base, run_id: 'run-foreign', session_id: sid };
  if (name === 'wrong-session') return { ...base, run_id: runId, session_id: 'other-session' };
  if (name === 'old-run') return { ...base, run_id: `old-${sid}`, session_id: sid };
  return { ...base, run_id: runId, session_id: sid };
}

function expectTerminal(status) {
  if (status === 'interrupted') return { status: 'failed', error: '网关中断' };
  if (status === 'failed') return { status: 'failed', error: '具体失败' };
  return { status, error: '' };
}

function holdStream() {
  let controller;
  let opened = false;
  globalThis.fetch = async (_input, init) => {
    const body = new ReadableStream({
      start(ctrl) {
        controller = ctrl;
        opened = true;
        init?.signal?.addEventListener('abort', () => {
          try { ctrl.error(new DOMException('Aborted', 'AbortError')); } catch { /* already closed */ }
        }, { once: true });
      },
    });
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
  };
  return {
    get opened() { return opened; },
    send(data) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
    },
    close() {
      try { controller?.close(); } catch { /* already closed */ }
    },
  };
}

async function waitFor(pred, label) {
  for (let n = 0; n < 50 && !pred(); n += 1) await tick();
  assert.equal(Boolean(pred()), true, label);
}

function stored(sid) {
  return JSON.parse(storage.get('hp_active_runs') ?? '{}')[sid];
}

try {
  const loaded = [];
  for (const root of ['/src/lib', '/src/react/lib']) {
    const runsMod = await server.ssrLoadModule(`${root}/runs.ts`);
    const apiMod = await server.ssrLoadModule(`${root}/api.ts`);
    const sideMod = await server.ssrLoadModule(`${root}/sidecar.ts`);
    loaded.push({
      root,
      runs: runsMod.runs,
      RUN_FINISHED: runsMod.RUN_FINISHED,
      api: apiMod.api,
      ApiError: apiMod.ApiError,
      sidecar: sideMod.sidecar,
      native: {
        runStatus: apiMod.api.runStatus,
        approve: apiMod.api.approve,
        clarify: apiMod.api.clarify,
        stopRun: apiMod.api.stopRun,
        startRun: apiMod.api.startRun,
        runModelFields: apiMod.api.runModelFields,
        sessionRun: sideMod.sidecar.sessionRun,
      },
    });
  }

  function restore(mod) {
    mod.api.runStatus = mod.native.runStatus;
    mod.api.approve = mod.native.approve;
    mod.api.clarify = mod.native.clarify;
    mod.api.stopRun = mod.native.stopRun;
    mod.api.startRun = mod.native.startRun;
    mod.api.runModelFields = mod.native.runModelFields;
    mod.sidecar.sessionRun = mod.native.sessionRun;
  }

  async function release(mod, sid, stream) {
    mod.runs.ctrls.get(sid)?.abort();
    stream?.close();
    await tick();
    mod.runs.clear(sid);
    storage.delete('hp_active_runs');
    restore(mod);
  }

  function arm(mod, sid) {
    let finished = 0;
    const onFinish = (event) => { if (event.detail === sid) finished += 1; };
    mod.runs.addEventListener(mod.RUN_FINISHED, onFinish);
    return {
      get finished() { return finished; },
      stop() { mod.runs.removeEventListener(mod.RUN_FINISHED, onFinish); },
    };
  }

  function checkOutcome(run, settle, status, finishes, where) {
    if (settle) {
      const expected = expectTerminal(status);
      assert.equal(run.status, expected.status, where);
      assert.equal(run.error, expected.error, where);
      assert.equal(finishes, 1, where);
      assert.equal(stored(run.sessionId), undefined, where);
      return;
    }
    assert.equal(run.status, 'running', where);
    assert.notEqual(run.status, 'completed', where);
    assert.equal(finishes, 0, where);
    assert.ok(stored(run.sessionId), `${where} storage`);
  }

  function promptStream(event) {
    let stream;
    globalThis.fetch = async () => {
      const readable = new ReadableStream({
        start(ctrl) {
          stream = ctrl;
          ctrl.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        },
      });
      return new Response(readable, { headers: { 'Content-Type': 'text/event-stream' } });
    };
    return { close() { try { stream?.close(); } catch { /* already closed */ } } };
  }

  for (const mod of loaded) {
    await test(`${mod.root} follow status poll refuses untrusted terminals and settles exact identity`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `follow-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const settle = name === 'match';
          const stream = holdStream();
          const watch = arm(mod, sid);
          mod.sidecar.sessionRun = async () => ({ busy: true, run: null });
          mod.api.runStatus = async () => body;
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => stream.opened, sid);
            stream.close();
            await waitFor(() => settle ? run.status !== 'running' : run.followDropped === true, sid);
            if (!settle) assert.match(run.error, UNCONFIRMED, sid);
            checkOutcome(run, settle, status, watch.finished, `${mod.root} follow ${status} ${name}`);
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, stream);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} lost-executor 200 terminal requires both ids`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `lost-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const settle = name === 'match';
          const watch = arm(mod, sid);
          globalThis.fetch = async () => new Response(JSON.stringify({ error: { message: 'missing' } }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          });
          mod.api.runStatus = async () => body;
          mod.sidecar.sessionRun = async () => ({ busy: false, run: null });
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => settle ? run.status !== 'running' : run.error.length > 0, sid);
            if (!settle) assert.match(run.error, UNCONFIRMED, sid);
            checkOutcome(run, settle, status, watch.finished, `${mod.root} lost ${status} ${name}`);
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, null);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} approval 409 status settlement requires exact identity`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `appr-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const settle = name === 'match';
          const stream = promptStream({
            event: 'approval.request', prompt_id: 'prompt-node', execution_generation: 4, choices: ['once', 'deny'],
          });
          const watch = arm(mod, sid);
          mod.api.approve = async () => { throw new mod.ApiError('server', 409, 'stale', 'stale_generation'); };
          mod.api.runStatus = async () => body;
          mod.sidecar.sessionRun = async () => ({ busy: true, run: null });
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => run.approval, sid);
            run.error = '停止失败：请重试';
            await mod.runs.respondApproval(sid, 'once');
            await tick();
            assert.equal(run.approval, null, sid);
            if (!settle) assert.match(run.error, UNCONFIRMED, sid);
            checkOutcome(run, settle, status, watch.finished, `${mod.root} approval ${status} ${name}`);
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, stream);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} clarify 409 status settlement requires exact identity`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `clar-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const settle = name === 'match';
          const stream = promptStream({
            event: 'clarify.request', prompt_id: 'c1', execution_generation: 2, question: '现在的心情是？', choices: ['轻松愉快'],
          });
          const watch = arm(mod, sid);
          mod.api.clarify = async () => { throw new mod.ApiError('server', 409, 'stale', 'stale_generation'); };
          mod.api.runStatus = async () => body;
          mod.sidecar.sessionRun = async () => ({ busy: true, run: null });
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => run.clarify, sid);
            await mod.runs.respondClarify(sid, [{ requestId: run.clarify.questions[0].requestId, answer: '好' }]);
            await tick();
            assert.equal(run.clarify, null, sid);
            if (!settle) assert.match(run.error, UNCONFIRMED, sid);
            checkOutcome(run, settle, status, watch.finished, `${mod.root} clarify ${status} ${name}`);
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, stream);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} busy false stop does not settle an untrusted body`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `stop-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const settle = name === 'match';
          let calls = 0;
          const watch = arm(mod, sid);
          const opened = holdStream();
          mod.api.stopRun = async () => { calls += 1; return body; };
          mod.sidecar.sessionRun = async () => ({ busy: false, run: null });
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => opened.opened, sid);
            await mod.runs.stop(sid);
            assert.equal(calls, 1, sid);
            assert.equal(run.stopping, false, sid);
            if (!settle) assert.match(run.error, UNCONFIRMED, sid);
            checkOutcome(run, settle, status, watch.finished, `${mod.root} stop ${status} ${name}`);
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, opened);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} reconcile settles only the current run and session`, async () => {
      const failures = [];
      for (const status of TERMINALS) {
        for (const name of NAMES) {
          const sid = `rec-${status}-${name}-${++seq}`;
          const runId = `run-${sid}`;
          const body = identityBody(name, sid, runId, status);
          const record = name === 'no-id' ? null : {
            run_id: body.run_id,
            session_id: body.session_id,
            status,
            created_at: 1,
            updated_at: 2,
            ...(body.error ? { error: body.error } : {}),
          };
          const watch = arm(mod, sid);
          const opened = holdStream();
          try {
            mod.runs.observe(sid, runId, Date.now());
            const run = mod.runs.get(sid);
            await waitFor(() => opened.opened, sid);
            await mod.runs.reconcile(sid, { busy: false, run: record });
            await tick();
            if (name === 'match') {
              checkOutcome(run, true, status, watch.finished, `${mod.root} reconcile ${status} ${name}`);
            } else {
              assert.equal(run.status, 'running', sid);
              assert.equal(watch.finished, 0, sid);
              assert.equal(run.error, '', `${sid} reconcile mismatch stays quiet`);
              assert.ok(stored(sid), sid);
            }
          } catch (error) {
            failures.push(`${status}/${name}: ${error.message}`);
          } finally {
            watch.stop();
            await release(mod, sid, opened);
          }
        }
      }
      assert.deepEqual(failures, [], failures.join('\n'));
    });

    await test(`${mod.root} content events still accept missing ids; status poll does not`, async () => {
      const sid = `content-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.runStatus = async () => ({ status: 'completed' });
      mod.sidecar.sessionRun = async () => ({ busy: true, run: null });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        opened.send({ event: 'message.delta', delta: 'kept' });
        await waitFor(() => run.text === 'kept', sid);
        opened.send({ event: 'message.delta', delta: 'nope', run_id: 'run-foreign', session_id: sid });
        for (let n = 0; n < 5; n += 1) await tick();
        assert.equal(run.text, 'kept', 'wrong run_id must not append');
        opened.close();
        await waitFor(() => run.followDropped === true, sid);
        assert.equal(run.status, 'running');
        assert.match(run.error, UNCONFIRMED);
        assert.equal(watch.finished, 0);
        assert.equal(run.text, 'kept');
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} SSE terminal event may omit session_id and still finish`, async () => {
      const sid = `event-terminal-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        opened.send({ event: 'run.completed', run_id: runId });
        await waitFor(() => run.status === 'completed', sid);
        assert.equal(run.error, '');
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} unknown 404 is failed confirmation, not completed`, async () => {
      const sid = `missing-404-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.runStatus = async () => { throw new mod.ApiError('server', 404, 'not found'); };
      mod.sidecar.sessionRun = async () => ({ busy: false, run: null });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        opened.close();
        await waitFor(() => run.status === 'failed', sid);
        assert.match(run.error, /无法确认这次运行的结果/);
        assert.notEqual(run.status, 'completed');
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} 404 with the same sidecar run still settles that terminal`, async () => {
      const sid = `known-404-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.runStatus = async () => { throw new mod.ApiError('server', 404, 'not found'); };
      mod.sidecar.sessionRun = async () => ({
        busy: false,
        run: { run_id: runId, session_id: sid, status: 'cancelled', created_at: 1, updated_at: 2 },
      });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        opened.close();
        await waitFor(() => run.status === 'cancelled', sid);
        assert.equal(run.error, '');
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} approval 409 then 404 still fails the missing run`, async () => {
      const sid = `appr-404-${++seq}`;
      const runId = `run-${sid}`;
      const stream = promptStream({
        event: 'approval.request', prompt_id: 'prompt-node', execution_generation: 4, choices: ['once'],
      });
      const watch = arm(mod, sid);
      mod.api.approve = async () => { throw new mod.ApiError('server', 409, 'expired'); };
      mod.api.runStatus = async () => { throw new mod.ApiError('server', 404, 'not found'); };
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => run.approval, sid);
        await mod.runs.respondApproval(sid, 'once');
        await tick();
        assert.equal(run.status, 'failed');
        assert.match(run.error, /运行记录已不存在/);
        assert.notEqual(run.status, 'completed');
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, stream);
      }
    });

    await test(`${mod.root} busy true stop keeps the owner hold and does not loop`, async () => {
      const sid = `busy-true-${++seq}`;
      const runId = `run-${sid}`;
      let calls = 0;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.stopRun = async () => {
        calls += 1;
        return { status: 'cancelled', run_id: runId, session_id: sid };
      };
      mod.sidecar.sessionRun = async () => ({
        busy: true,
        run: { run_id: runId, session_id: sid, status: 'cancelled', created_at: 1, updated_at: 2 },
      });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        await mod.runs.stop(sid);
        assert.equal(calls, 1);
        assert.equal(run.status, 'running');
        assert.equal(run.stopping, true);
        assert.equal(run.error, '');
        assert.equal(watch.finished, 0);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} non-terminal stop ack without session_id is not an unconfirmed failure`, async () => {
      const sid = `stopping-ack-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.stopRun = async () => ({ run_id: runId, status: 'stopping' });
      mod.sidecar.sessionRun = async () => ({ busy: true, run: null });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        await mod.runs.stop(sid);
        assert.equal(run.status, 'running');
        assert.equal(run.stopping, true);
        assert.equal(run.error, '');
        assert.equal(watch.finished, 0);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} identity-less stop body cannot complete; matching sidecar terminal can`, async () => {
      const sid = `stop-sidecar-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.stopRun = async () => ({ status: 'completed' });
      mod.sidecar.sessionRun = async () => ({
        busy: false,
        run: { run_id: runId, session_id: sid, status: 'interrupted', created_at: 1, updated_at: 2 },
      });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        await mod.runs.stop(sid);
        assert.equal(run.status, 'failed');
        assert.match(run.error, /回合被中断/);
        assert.notEqual(run.status, 'completed');
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} unverified stop does not call stop or invent a terminal`, async () => {
      const sid = `unverified-${++seq}`;
      let calls = 0;
      const watch = arm(mod, sid);
      mod.api.runModelFields = async () => ({});
      mod.api.startRun = async () => { throw new Error('lost'); };
      mod.api.stopRun = async () => { calls += 1; return { status: 'cancelled' }; };
      try {
        mod.runs.start(sid, 'keep', [], 'keep');
        await waitFor(() => mod.runs.get(sid)?.unverified === true, sid);
        const run = mod.runs.get(sid);
        await mod.runs.stop(sid);
        assert.equal(calls, 0);
        assert.equal(run.status, 'starting');
        assert.equal(run.stopping, false);
        assert.equal(run.unverified, true);
        assert.equal(watch.finished, 0);
        assert.notEqual(run.status, 'completed');
      } finally {
        watch.stop();
        await release(mod, sid, null);
      }
    });

    await test(`${mod.root} stop 500 keeps the failure contract and does not finish`, async () => {
      const sid = `stop-500-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.stopRun = async () => { throw new mod.ApiError('server', 500, 'nope'); };
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        await assert.rejects(() => mod.runs.stop(sid), (error) => error instanceof mod.ApiError && error.status === 500);
        assert.equal(run.status, 'running');
        assert.equal(run.stopping, false);
        assert.match(run.error, /停止失败/);
        assert.equal(watch.finished, 0);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
      }
    });

    await test(`${mod.root} stop 409 run_not_active reconciles only the same run`, async () => {
      const sid = `stop-409-${++seq}`;
      const runId = `run-${sid}`;
      const watch = arm(mod, sid);
      const opened = holdStream();
      mod.api.stopRun = async () => { throw new mod.ApiError('server', 409, 'gone', 'run_not_active'); };
      mod.sidecar.sessionRun = async () => ({
        busy: false,
        run: { run_id: `old-${sid}`, session_id: sid, status: 'completed', created_at: 1, updated_at: 2 },
      });
      try {
        mod.runs.observe(sid, runId, Date.now());
        const run = mod.runs.get(sid);
        await waitFor(() => opened.opened, sid);
        await mod.runs.stop(sid);
        assert.equal(run.status, 'running');
        assert.equal(run.stopping, false);
        assert.match(run.error, /停止失败/);
        assert.equal(watch.finished, 0);
        mod.sidecar.sessionRun = async () => ({
          busy: false,
          run: { run_id: runId, session_id: sid, status: 'interrupted', created_at: 1, updated_at: 2 },
        });
        await mod.runs.stop(sid);
        assert.equal(run.status, 'failed');
        assert.match(run.error, /回合被中断/);
        assert.equal(watch.finished, 1);
      } finally {
        watch.stop();
        await release(mod, sid, opened);
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
