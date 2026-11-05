import assert from 'node:assert/strict';
import path from 'node:path';
import { withPage, waitFor } from './fixtures/cdp-harness.mjs';

const root = path.resolve(import.meta.dirname, '..');
const name = process.argv[2];
const red = process.argv.includes('--red');
if (!name) throw new Error('missing case');

function rows(n, start = 1) {
  return Array.from({ length: n }, (_, index) => {
    const id = start + index;
    return { id, role: id % 2 ? 'user' : 'assistant', content: `row ${id}`, timestamp: id };
  });
}

const finalPage = {
  data: [
    { id: 100, role: 'user', content: 'task', timestamp: 100 },
    { id: 101, role: 'assistant', content: 'synthetic final', timestamp: 102 },
  ],
  pagination: { returned: 2 },
};

async function open(gate, run, extraInit = '') {
  await withPage(root, '/tests/fixtures/react-gate.html', async (page) => {
    await waitFor(page.evaluate, 'window.fixtureReady === true', 'fixture ready', 20000);
    await waitFor(page.evaluate, 'document.body.innerText.length > 0', 'chat painted', 15000);
    await run(page);
  }, `${extraInit};window.__gate = ${JSON.stringify(gate)};`);
}

async function read(page, expression) {
  return page.evaluate(`(() => { ${expression} })()`);
}

function runningState(runId, sessionId, created) {
  return {
    busy: true,
    acquired_at: created,
    run: { run_id: runId, session_id: sessionId, status: 'running', created_at: created },
  };
}

const cases = {
  async 'settle-join'() {
    for (const status of ['completed', 'cancelled', 'failed']) {
      const expectedNotice = status === 'completed' ? '' : status === 'cancelled' ? '已停止' : 'synthetic failure';
      await open({
        sessionId: 'sess',
        messages: { sess: finalPage.data },
        installRun: { status: 'running', streamGone: true, text: 'synthetic temporary final', userText: 'task', error: status === 'failed' ? 'synthetic failure' : '' },
      }, async (page) => {
        await waitFor(page.evaluate, 'document.body.innerText.includes("task")', 'history', 15000);
        await page.evaluate('new Promise((resolve) => setTimeout(resolve, 250))');
        await page.evaluate('window.__requests = []; window.__heldQueue = []; window.__holdMessages = true;');
        await page.evaluate(`window.__settle('sess', ${JSON.stringify(status)})`);
        await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'settle held', 15000);
        await page.evaluate(`window.dispatchEvent(new Event('focus')); window.__finish('sess');`);
        await page.evaluate('new Promise((resolve) => setTimeout(resolve, 350))');
        const mid = await read(page, `const msgs = window.__requests.filter((item) => item.url.includes('/messages') && !item.url.includes('origins'));
          return { held: window.__heldQueue.length, aborted: msgs.some((item) => item.aborted), urls: msgs.map((item) => item.url) };`);
        assert.equal(mid.held, 1, `${status}: terminal paths share one history request`);
        assert.equal(mid.aborted, false, `${status}: a queued sync must not abort settle`);
        await page.evaluate(`window.__heldQueue[0].resolve(200, ${JSON.stringify(finalPage)})`);
        await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
        const after = await read(page, `return {
          live: document.querySelector('.mount-row')?.innerText || '',
          notice: document.querySelector('.notice')?.innerText || '',
          run: window.__runState('sess'),
          finals: (document.body.textContent.match(/synthetic final/g) || []).length,
        };`);
        if (red) {
          assert.match(after.live, /synthetic temporary final/, 'red control: retaining the live view must fail this assertion');
        }
        assert.equal(after.run, null, `${status}: settled run is cleared`);
        assert.equal(after.live, '', `${status}: completed live view must not remain`);
        assert.equal(after.notice, expectedNotice, status);
        assert.equal(after.finals, 1, `${status}: stored final appears once`);
      });
    }
  },

  async 'one-history-get'() {
    const discovered = runningState('run-sess', 'sess', 100);
    await open({
      sessionId: 'sess',
      messages: {
        sess: [
          { id: 100, role: 'user', content: 'task', timestamp: 100 },
          { id: 101, role: 'assistant', content: 'note', timestamp: 101, tool_calls: [{ id: 'shared', function: { name: 'terminal', arguments: '{}' } }] },
        ],
      },
    }, async (page) => {
      await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'held history', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 400))');
      const before = await read(page, `return window.__requests.filter((item) => item.url.includes('/messages') && !item.url.includes('origins')).map((item) => item.url);`);
      assert.equal(before.length, 1, 'discovery-first open makes one history GET');
      await page.evaluate(`window.__holdMessages = false; window.__heldMessages.resolve(200, { data: window.__gate.messages.sess, pagination: { returned: 2 } });`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 600))');
      const after = await read(page, `return {
        gets: window.__requests.filter((item) => item.url.includes('/messages') && !item.url.includes('origins')).length,
        writes: window.__requests.filter((item) => item.method !== 'GET' && (item.url.includes('/v1/runs') || item.url.includes('/stop'))).map((item) => item.method + ' ' + item.url),
        tools: document.querySelectorAll('.tool-steps li').length,
        groups: document.querySelectorAll('.tool-steps').length,
      };`);
      if (red) assert.equal(after.gets, 2, 'red control: a second GET must fail the one-GET assertion');
      assert.equal(after.gets, 1, 'releasing discovery must not start another history GET');
      assert.deepEqual(after.writes, [], 'open must not admit or stop');
      assert.equal(after.groups, 1, 'history tool is not drawn twice');
      assert.equal(after.tools, 1, 'shared tool id appears once');
    }, `window.__holdMessages = true; window.__sessionRunBody = ${JSON.stringify(discovered)};`);
  },

  async 'input-once'() {
    await open({
      sessionId: 'sess',
      messages: { sess: [{ id: 90, role: 'user', content: 'old task', timestamp: 90 }, { id: 91, role: 'assistant', content: 'old answer', timestamp: 91 }] },
      installRun: { status: 'running', streamGone: true, userText: 'synthetic unsaved input', text: '' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("old answer")', 'history', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 300))');
      const unsaved = await read(page, `return {
        count: document.body.textContent.split('synthetic unsaved input').length - 1,
        title: document.querySelector('button.trigger')?.title || '',
      };`);
      assert.equal(unsaved.count, 1, 'stream loss does not imply the input has persisted');
      assert.match(unsaved.title, /停止当前回复/, 'an active run still blocks edit');
      await page.evaluate(`window.__gate.messages.sess = [{ id: 100, role: 'user', content: 'synthetic unsaved input', timestamp: 100 }]; window.__finish('sess');`);
      await waitFor(page.evaluate, `document.querySelectorAll('.message-text').length === 1 && document.body.textContent.includes('synthetic unsaved input')`, 'saved input', 15000);
      const saved = await read(page, `return {
        count: document.body.textContent.split('synthetic unsaved input').length - 1,
        editors: [...document.querySelectorAll('.message-text')].filter((node) => node.textContent === 'synthetic unsaved input').length,
        live: document.querySelector('.mount-row')?.textContent || '',
        title: document.querySelector('button.trigger')?.title || '',
      };`);
      if (red) assert.equal(saved.count, 2, 'red control: a duplicated input must fail');
      assert.equal(saved.editors, 1, 'once saved, the input is the history row');
      assert.equal(saved.count, 1, 'once saved, input belongs only to history');
      assert.equal(saved.live.includes('synthetic unsaved input'), false);
      assert.match(saved.title, /停止当前回复/);
    });
  },

  async 'degraded-process'() {
    const history = [
      { id: 100, role: 'user', content: 'task', timestamp: 100 },
      { id: 101, role: 'assistant', content: '', timestamp: 101, reasoning: 'synthetic thought', tool_calls: [{ id: 'call-a', function: { name: 'terminal', arguments: '{}' } }] },
      { id: 102, role: 'tool', content: 'full persisted result', timestamp: 102, tool_call_id: 'call-a' },
      { id: 103, role: 'assistant', content: '', timestamp: 103, tool_calls: [{ id: 'call-b', function: { name: 'terminal', arguments: '{}' } }] },
    ];
    for (const flag of ['streamGone', 'replayTruncated']) {
      await open({
        sessionId: 'sess',
        messages: { sess: history },
        installRun: {
          status: 'running',
          [flag]: true,
          text: 'live tail text',
          thinking: 'synthetic thought',
          steps: [
            { kind: 'note', text: 'synthetic note' },
            { kind: 'tool', id: 'live-1', name: 'terminal', status: 'done', output: 'live only output' },
            { kind: 'tool', id: 'live-2', name: 'terminal', status: 'running', input: 'synthetic next' },
          ],
        },
      }, async (page) => {
        await waitFor(page.evaluate, 'document.body.innerText.includes("task")', 'history', 15000);
        await page.evaluate('new Promise((resolve) => setTimeout(resolve, 400))');
        const seen = await read(page, `const text = document.body.textContent;
          return {
            thought: text.split('synthetic thought').length - 1,
            note: text.split('synthetic note').length - 1,
            result: text.split('full persisted result').length - 1,
            liveOnly: text.split('live only output').length - 1,
            groups: document.querySelectorAll('.tool-steps').length,
            tools: document.querySelectorAll('.tool-steps li').length,
            steps: window.__runState('sess')?.steps,
          };`);
        assert.equal(seen.liveOnly, 0, `${flag}: live replay tools are not drawn beside history`);
        assert.equal(seen.groups, 1, `${flag}: one process group`);
        assert.equal(seen.tools, 2, `${flag}: history tools only`);
        const dots = await read(page, `return [...document.querySelectorAll('.tool-steps li .row .dot')].map((node) => node.getAttribute('data-s'));`);
        assert.deepEqual(dots, ['done', 'running'], `${flag}: history statuses, not the live replay`);
        await page.evaluate(`document.querySelector('.tool-steps li .row')?.click()`);
        await page.evaluate('new Promise((resolve) => setTimeout(resolve, 50))');
        const opened = await read(page, `const text = document.body.textContent;
          return {
            result: text.split('full persisted result').length - 1,
            liveResult: (document.querySelector('.mount-row')?.textContent || '').includes('full persisted result'),
            thought: text.split('synthetic thought').length - 1,
            note: text.split('synthetic note').length - 1,
            liveOnly: text.split('live only output').length - 1,
            steps: window.__runState('sess')?.steps,
          };`);
        assert.equal(opened.liveResult, false, `${flag}: persisted output is not also drawn in the live tail`);
        assert.ok(opened.result >= 1, `${flag}: persisted output is in the history tool`);
        assert.equal(opened.thought, 1, `${flag}: thinking shown once`);
        assert.equal(opened.note, 1, `${flag}: note shown once`);
        assert.equal(opened.liveOnly, 0, `${flag}: opening history does not reveal the live replay`);
        assert.equal(opened.steps, 3, 'projection must not mutate RunStore');
      }, 'window.__commentary = { "101": ["synthetic note"] };');
    }
  },

  async 'new-run-after-settle'() {
    const discovered = runningState('new-run', 'sess', 110);
    await open({
      sessionId: 'sess',
      messages: { sess: finalPage.data },
      installRun: { runId: 'run-sess', status: 'completed', text: 'temp', userText: 'task' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("synthetic final")', 'history', 15000);
      await page.evaluate('window.__requests = [];');
      await page.evaluate(`window.__settle('sess', 'completed')`);
      await waitFor(page.evaluate, 'window.__runState("sess") === null', 'cleared', 15000);
      await page.evaluate(`window.__sessionRunBody = ${JSON.stringify(discovered)}; window.__runStatusBody = ${JSON.stringify({ status: 'running', run_id: 'new-run', session_id: 'sess', created_at: 110 })}; window.dispatchEvent(new Event('focus'));`);
      await waitFor(page.evaluate, 'window.__runState("sess")?.runId === "new-run"', 'new run', 15000);
      const after = await read(page, `return {
        run: window.__runState('sess'),
        writes: window.__requests.filter((item) => item.method !== 'GET' && (item.url.includes('/v1/runs') || item.url.includes('/stop'))).map((item) => item.method + ' ' + item.url),
      };`);
      assert.equal(after.run.runId, 'new-run');
      assert.equal(after.run.status, 'running');
      assert.deepEqual(after.writes, [], 'discovery must not admit or stop');
    });
  },

  async 'page-controller'() {
    await open({ sessionId: 'wide', messages: { wide: rows(600) } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 600")', 'latest', 15000);
      await page.evaluate(`document.querySelector('.earlier-btn')?.click()`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 401")', 'expanded', 15000);
      const signals = await read(page, `return window.__requests.filter((item) => item.url.includes('/messages') && !item.url.includes('origins')).map((item) => ({ url: item.url, signalId: item.signalId, aborted: item.aborted }));`);
      const earlier = signals.find((item) => item.url.includes('offset=200'));
      const mains = signals.filter((item) => item.url.includes('offset=0'));
      assert.ok(earlier, 'pagination issued its own request');
      assert.ok(mains.length >= 1);
      assert.equal(mains.some((item) => item.signalId === earlier.signalId), false, 'pagination uses an independent controller');
      assert.equal(mains.some((item) => item.aborted), false, 'loading earlier must not abort the latest-page controller');
      assert.equal(earlier.aborted, false);
      await page.evaluate('window.__requests = [];');
      await page.evaluate(`window.__finish('wide')`);
      await waitFor(page.evaluate, `window.__requests.some((item) => item.url.includes('offset=0') && /limit=(400|500)/.test(item.url))`, 'capped reload', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 400))');
      const kept = await read(page, `const text = document.body.textContent;
        return {
          sources: document.querySelectorAll('[data-history-source]').length,
          row401: text.includes('row 401'),
          row600: text.includes('row 600'),
          urls: window.__requests.filter((item) => item.url.includes('/messages') && item.url.includes('offset=0')).map((item) => item.url),
        };`);
      assert.equal(kept.sources, 400, 'reload keeps the expanded window that fits under the 500 cap');
      assert.equal(kept.row401, true);
      assert.equal(kept.row600, true);
      assert.ok(kept.urls.some((url) => /limit=(400|500)/.test(url)), kept.urls.join('\n'));
    });
  },

  async 'stale-poll'() {
    const stale = runningState('run-sess', 'sess', 100);
    await open({
      sessionId: 'sess',
      messages: { sess: [{ id: 100, role: 'user', content: 'task', timestamp: 100 }, { id: 101, role: 'assistant', content: 'known final', timestamp: 101 }] },
      installRun: { runId: 'run-sess', status: 'running', text: 'temp', userText: 'task' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("known final")', 'history', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 200))');
      await page.evaluate('window.__holdMessages = true; window.__heldQueue = [];');
      await page.evaluate(`window.__sessionRunBody = ${JSON.stringify(stale)}; window.__settle('sess', 'completed');`);
      await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'settle held', 15000);
      await page.evaluate(`window.dispatchEvent(new Event('focus'))`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 700))');
      const mid = await read(page, `return window.__runState('sess');`);
      assert.notEqual(mid?.remote, true, 'polling cannot replace a known terminal run while it settles');
      assert.notEqual(mid?.status, 'running', 'stale running discovery must not revive the settling run');
    });
  },

  async 'inflight-discovery'() {
    const stale = runningState('run-sess', 'sess', 100);
    await open({
      sessionId: 'sess',
      messages: { sess: [{ id: 100, role: 'user', content: 'task', timestamp: 100 }, { id: 101, role: 'assistant', content: 'known final', timestamp: 101 }] },
      installRun: { runId: 'run-sess', status: 'running', streamGone: true, text: 'temp', userText: 'task' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("known final")', 'history', 15000);
      await page.evaluate('window.__holdMessages = true;');
      await page.evaluate(`window.__settle('sess', 'completed')`);
      await waitFor(page.evaluate, 'Boolean(window.__heldMessages)', 'settle held', 15000);
      await page.evaluate(`window.__sessionRunBody = ${JSON.stringify(stale)}; window.__holdSessionRun = false; if (window.__heldSessionRun) window.__heldSessionRun.resolve(200, window.__sessionRunBody);`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 700))');
      const mid = await read(page, `return { run: window.__runState('sess'), live: document.querySelector('.mount-row')?.innerText || '' };`);
      assert.notEqual(mid.run?.remote, true, 'an in-flight discovery must not replace the run after settle starts');
      await page.evaluate(`window.__heldMessages.resolve(200, { data: [{ id: 100, role: 'user', content: 'task', timestamp: 100 }, { id: 101, role: 'assistant', content: 'known final', timestamp: 101 }], pagination: { returned: 2 } });`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
      const after = await read(page, `return window.__runState('sess');`);
      assert.equal(after, null, 'history commit must still clear the finished run');
    }, 'window.__holdSessionRun = true;');
  },

  async 'late-revive'() {
    const stale = runningState('run-sess', 'sess', 100);
    await open({
      sessionId: 'sess',
      messages: { sess: finalPage.data },
      installRun: { runId: 'run-sess', status: 'running', streamGone: true, text: 'temp', userText: 'task' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("synthetic final")', 'history', 15000);
      await page.evaluate(`window.__settle('sess', 'completed')`);
      await waitFor(page.evaluate, 'window.__runState("sess") === null', 'cleared', 15000);
      await page.evaluate(`window.__sessionRunBody = ${JSON.stringify(stale)}; window.__holdSessionRun = false; if (window.__heldSessionRun) window.__heldSessionRun.resolve(200, window.__sessionRunBody);`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 800))');
      const after = await read(page, `return { run: window.__runState('sess'), live: document.querySelector('.mount-row')?.innerText || '' };`);
      assert.equal(after.run, null, 'late discovery must not resurrect the cleared run');
      assert.equal(after.live, '', 'a revived live view must not return');
    }, 'window.__holdSessionRun = true;');
  },

  async 'late-revive-status'() {
    const stale = runningState('run-sess', 'sess', 100);
    await open({
      sessionId: 'sess',
      messages: { sess: finalPage.data },
      installRun: { runId: 'run-sess', status: 'running', text: 'temp', userText: 'task' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("synthetic final")', 'history', 15000);
      await waitFor(page.evaluate, 'Boolean(window.__heldRunStatus)', 'status held', 15000);
      await page.evaluate(`window.__settle('sess', 'completed')`);
      await waitFor(page.evaluate, 'window.__runState("sess") === null', 'cleared', 15000);
      await page.evaluate(`window.__holdRunStatus = false; window.__heldRunStatus.resolve(200, { status: 'running', run_id: 'run-sess', session_id: 'sess', created_at: 100 });`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 800))');
      const after = await read(page, `return window.__runState('sess');`);
      assert.equal(after, null, 'a late runStatus must not resurrect the cleared run');
    }, `window.__holdRunStatus = true; window.__sessionRunBody = ${JSON.stringify(stale)};`);
  },

  async 'healthy-sse'() {
    const state = runningState('run-healthy', 'healthy', 100);
    await open({
      sessionId: 'healthy',
      messages: { healthy: [{ id: 1, role: 'user', content: 'kept question', timestamp: 1 }, { id: 2, role: 'assistant', content: 'kept reply', timestamp: 2 }] },
      installRun: { runId: 'run-healthy', status: 'running', text: 'live tokens', userText: 'kept question', streamGone: false, replayTruncated: false },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("live tokens")', 'healthy live', 15000);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 3400))');
      const measured = await read(page, `const runs = window.__requests.filter((item) => item.url.includes('/sessions/run') && !item.url.includes('resolve'));
        const status = window.__requests.filter((item) => item.url.includes('/v1/runs/') && !item.url.includes('/events') && !item.url.includes('/stop'));
        const gaps = runs.slice(1).map((item, index) => item.at - runs[index].at);
        return { sessionRuns: runs.length, runStatus: status.length, gaps, run: window.__runState('healthy') };`);
      assert.equal(measured.run?.status, 'running');
      assert.equal(measured.run?.streamGone, false);
      assert.equal(measured.sessionRuns, 0, `healthy SSE must not request sessions/run; measured sessionRun=${measured.sessionRuns} gaps=${measured.gaps.join(',') || 'none'}ms runStatus=${measured.runStatus}`);
      assert.equal(measured.runStatus, 0, `healthy SSE must not request v1/runs/{id}; measured ${measured.runStatus} calls`);
    }, `window.__sessionRunBody = ${JSON.stringify(state)};`);
  },

  async 'over-500'() {
    await open({ sessionId: 'wide', messages: { wide: rows(800) } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 800")', 'latest', 15000);
      for (const marker of ['row 601', 'row 401', 'row 201']) {
        await page.evaluate(`document.querySelector('.earlier-btn')?.click()`);
        await waitFor(page.evaluate, `document.body.innerText.includes(${JSON.stringify(marker)})`, marker, 15000);
      }
      const expanded = await page.evaluate(`document.querySelectorAll('[data-history-source]').length`);
      assert.equal(expanded, 800);
      await page.evaluate('window.__requests = [];');
      await page.evaluate(`window.__finish('wide')`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 800))');
      const after = await read(page, `return {
        sources: document.querySelectorAll('[data-history-source]').length,
        row1: document.body.textContent.includes('row 1'),
        urls: window.__requests.filter((item) => item.url.includes('/messages') && item.url.includes('offset=0')).map((item) => item.url),
      };`);
      assert.equal(after.sources, 800, `refresh must not truncate an expanded window; measured ${after.sources}, requests ${after.urls.join(' | ')}`);
      assert.equal(after.row1, true, 'rows already loaded beyond the 500 cap must remain');
    });
  },

  async 'generation'() {
    await open({ sessionId: 'gen', messages: { gen: rows(400) } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 400")', 'latest', 15000);
      await page.evaluate('window.__holdOffsetZero = true; window.__heldQueue = []; window.__requests = [];');
      await page.evaluate(`window.__finish('gen')`);
      await waitFor(page.evaluate, 'window.__heldQueue.length >= 1', 'first refresh', 15000);
      await page.evaluate(`document.querySelector('.earlier-btn')?.click()`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 1")', 'expanded', 15000);
      await page.evaluate(`window.__finish('gen')`);
      await waitFor(page.evaluate, 'window.__heldQueue.length >= 2', 'second refresh', 15000);
      await page.evaluate(`(() => {
        const newer = window.__heldQueue.find((item) => item.url.includes('limit=400'));
        const older = window.__heldQueue.find((item) => item.url.includes('limit=200'));
        if (!newer || !older) throw new Error('missing generation ' + window.__heldQueue.map((item) => item.url).join(' | '));
        newer.resolve(200, { data: [{ id: 9, role: 'user', content: 'NEW_PAGE', timestamp: 9 }], pagination: { returned: 1 } });
        window.setTimeout(() => older.resolve(200, { data: [{ id: 8, role: 'user', content: 'STALE_PAGE', timestamp: 8 }], pagination: { returned: 1 } }), 150);
      })()`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 700))');
      const text = await page.evaluate('document.body.textContent');
      assert.equal(text.includes('STALE_PAGE'), false, 'a late shorter refresh must not overwrite the newer page');
      assert.equal(text.includes('NEW_PAGE'), true, 'the newer same-session refresh must remain');
    });
  },

  async 'window-shift'() {
    const messages = rows(501);
    messages[1].content = 'KEEP_ID_2';
    await open({ sessionId: 'wide', messages: { wide: messages } }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("row 501")', 'latest', 15000);
      for (let step = 0; step < 4 && !(await page.evaluate('document.body.textContent.includes("KEEP_ID_2")')); step += 1) {
        const before = await page.evaluate('document.querySelectorAll("[data-history-source]").length');
        await page.evaluate('document.querySelector(".earlier-btn")?.click()');
        await waitFor(page.evaluate, `document.querySelectorAll("[data-history-source]").length > ${before}`, `expanded past ${before}`, 15000);
      }
      const expanded = await page.evaluate('document.body.textContent.includes("KEEP_ID_2")');
      assert.equal(expanded, true, 'fixture must load id 2 before the refresh under test');
      await page.evaluate(`window.__gate.messages.wide.push({ id: 502, role: 'user', content: 'APPENDED_502', timestamp: 502 }); window.__requests = []; window.__finish('wide');`);
      await waitFor(page.evaluate, 'document.body.innerText.includes("APPENDED_502")', 'appended', 15000);
      const after = await read(page, `const text = document.body.textContent;
        return {
          keep: (text.match(/KEEP_ID_2/g) || []).length,
          appended: (text.match(/APPENDED_502/g) || []).length,
          sources: document.querySelectorAll('[data-history-source]').length,
        };`);
      assert.equal(after.keep, 1, 'id 2 must remain after the latest 500 window shifts to 3..502');
      assert.equal(after.appended, 1);
      assert.ok(after.sources > 500, `expanded rows must survive the shift; measured ${after.sources}`);
    });
  },

  async 'discovery-join'() {
    const fresh = { runId: 'run-new', status: 'running', userText: 'fresh task', text: 'live tokens', error: '' };
    await open({ sessionId: 'sess', messages: { sess: [{ id: 1, role: 'user', content: 'old', timestamp: 1 }] } }, async (page) => {
      await waitFor(page.evaluate, 'Boolean(window.__heldSessionRun)', 'sessionRun held', 15000);
      await page.evaluate(`window.__installRun('sess', ${JSON.stringify(fresh)})`);
      await page.evaluate(`window.__heldSessionRun.reject(new Error('synthetic sync failure'))`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 400))');
      const failed = await read(page, `return {
        run: window.__runState('sess'),
        sync: document.body.textContent.includes('任务状态同步暂不可用'),
      };`);
      assert.equal(failed.sync, false, 'a failed discovery that started with no run must not surface against the new run');
      assert.equal(failed.run?.status, 'running');
      assert.equal(failed.run?.userText, 'fresh task');
      assert.equal(failed.run?.error, '');
    }, 'window.__holdSessionRun = true;');

    await open({ sessionId: 'sess', messages: { sess: [{ id: 1, role: 'user', content: 'old', timestamp: 1 }] } }, async (page) => {
      await waitFor(page.evaluate, 'Boolean(window.__heldSessionRun)', 'sessionRun held', 15000);
      await page.evaluate(`window.__installRun('sess', ${JSON.stringify(fresh)})`);
      await page.evaluate(`window.__heldSessionRun.resolve(200, ${JSON.stringify({ busy: false, run: { run_id: 'run-new', session_id: 'sess', status: 'completed', created_at: 1, updated_at: 2 } })});`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
      const succeeded = await read(page, `return window.__runState('sess');`);
      assert.equal(succeeded?.status, 'running', 'stale sessionRun success must not finish the run that joined during the await');
      assert.equal(succeeded?.userText, 'fresh task');
      assert.equal(succeeded?.text, 'live tokens');
      assert.equal(succeeded?.runId, 'run-new');
    }, 'window.__holdSessionRun = true;');

    await open({ sessionId: 'sess', messages: { sess: [{ id: 1, role: 'user', content: 'old', timestamp: 1 }] } }, async (page) => {
      await waitFor(page.evaluate, 'Boolean(window.__heldSessionRun)', 'sessionRun held', 15000);
      await page.evaluate(`window.__executionBody = ${JSON.stringify({ busy: true, execution: { id: 'foreign-exec' } })};
        window.__runStatusBody = ${JSON.stringify({ status: 'running', run_id: 'run-foreign', session_id: 'sess', created_at: 10 })};
        window.__holdEvents = true;
        window.__installRun('sess', { status: 'starting', userText: 'fresh task', text: 'kept', runId: '', error: '', locallyReleased: true });`);
      await page.evaluate(`window.__heldSessionRun.resolve(200, ${JSON.stringify({ busy: true, run: { run_id: 'run-foreign', session_id: 'sess', status: 'running', created_at: 10 } })});`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
      const applied = await read(page, `return {
        run: window.__runState('sess'),
        stop: document.body.textContent.includes('停止独立任务'),
        sync: document.body.textContent.includes('任务状态同步暂不可用'),
      };`);
      assert.equal(applied.stop, false, 'sessionExecution from the stale discovery must not attach a foreign stop control');
      assert.equal(applied.sync, false, 'runStatus mismatch or failure must not mark the new run unsynced');
      assert.equal(applied.run?.userText, 'fresh task');
      assert.equal(applied.run?.text, 'kept');
      assert.equal(applied.run?.runId, '');
      assert.equal(applied.run?.status, 'starting');
      assert.equal(applied.run?.locallyReleased, true);
      assert.equal(applied.run?.remote, false);
    }, 'window.__holdSessionRun = true;');
  },

  async 'finish-replace'() {
    const history = [
      { id: 1, role: 'user', content: 'earlier task', timestamp: 1 },
      { id: 2, role: 'assistant', content: 'kept history', timestamp: 2 },
    ];
    await open({
      sessionId: 'sess',
      messages: { sess: history },
      installRun: { runId: 'run-A', status: 'running', userText: 'prompt A', text: 'temp A', error: '' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("kept history")', 'history', 15000);
      await page.evaluate(`window.__holdOffsetZero = true; window.__heldQueue = [];
        window.__patchRun('sess', { status: 'failed', runId: '', userText: 'prompt A', error: 'A failed prompt' });
        window.__finish('sess');`);
      await waitFor(page.evaluate, 'window.__heldQueue.some((item) => item.url.includes("offset=0"))', 'finish held', 15000);
      await page.evaluate(`window.__installRun('sess', { runId: 'run-B', status: 'failed', userText: 'prompt B', text: 'temp B', error: 'B owns the prompt' });
        const held = window.__heldQueue.find((item) => item.url.includes('offset=0'));
        held.resolve(200, { data: window.__gate.messages.sess, pagination: { returned: 1 } });`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
      const after = await read(page, `return {
        run: window.__runState('sess'),
        notice: document.body.textContent.includes('A failed prompt'),
        draft: window.__composerDraft().value,
      };`);
      assert.equal(after.run?.runId, 'run-B', 'old finish must not clear the replacement run');
      assert.equal(after.run?.userText, 'prompt B');
      assert.equal(after.notice, false, 'old finish must not publish A prompt');
      assert.equal(after.draft, '', 'old finish must not restore A draft');
    });

    await open({
      sessionId: 'sess',
      messages: { sess: history },
      installRun: { runId: 'run-A', status: 'running', userText: 'prompt A', text: 'temp A', error: '' },
    }, async (page) => {
      await waitFor(page.evaluate, 'document.body.innerText.includes("kept history")', 'history', 15000);
      await page.evaluate(`window.__holdOffsetZero = true; window.__heldQueue = [];
        window.__patchRun('sess', { status: 'cancelled', userText: 'prompt A', error: '' });
        window.__finish('sess');`);
      await waitFor(page.evaluate, 'window.__heldQueue.some((item) => item.url.includes("offset=0"))', 'finish held', 15000);
      await page.evaluate(`window.__installRun('sess', { runId: '', status: 'starting', userText: 'prompt B', text: 'temp B', error: '', locallyReleased: true });
        const held = window.__heldQueue.find((item) => item.url.includes('offset=0'));
        held.resolve(200, { data: window.__gate.messages.sess, pagination: { returned: 1 } });`);
      await page.evaluate('new Promise((resolve) => setTimeout(resolve, 500))');
      const after = await read(page, `return {
        run: window.__runState('sess'),
        notice: document.body.textContent.includes('已停止'),
        draft: window.__composerDraft().value,
      };`);
      assert.equal(after.run?.status, 'starting');
      assert.equal(after.run?.locallyReleased, true, 'old finish must not clear a locallyReleased replacement');
      assert.equal(after.run?.userText, 'prompt B');
      assert.equal(after.notice, false, 'old finish must not publish A stop notice over B');
      assert.equal(after.draft, '');
    });
  },
};

const run = cases[name];
if (!run) throw new Error(`unknown case ${name}`);
await run();
console.log(`ok ${name}`);
