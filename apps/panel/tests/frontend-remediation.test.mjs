import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import path from 'node:path';
import { runBrowser } from './fixtures/browser-case.mjs';
import { installSsrDom, ssrViteOptions } from './helpers/ssr-dom.mjs';

const stub = path.join(import.meta.dirname, 'fixtures/dompurify-node-stub.mjs');
const restoreSsrDom = installSsrDom({ matchMedia: true, timers: false });
const server = await createServer(ssrViteOptions({ cacheName: 'frontend-remediation', stub }));

const message = (id, role = 'user', content = `synthetic-${id}`, timestamp = id) => ({ id, role, content, timestamp });
const toolMessage = (id, callId, timestamp = 101, text = '') => ({
  ...message(id, 'assistant', text, timestamp),
  tool_calls: [{ id: callId, function: { name: 'terminal', arguments: '{}' } }],
});
const textOf = (html) => html.replace(/<[^>]+>/g, ' ');

try {
  const { withoutInFlight, visibleTurnsBesideLive, liveTextBesideHistory, buildTurns } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const { historyReloadMs, HISTORY_SYNC_MS } = await server.ssrLoadModule('/src/lib/sync-poll.ts');
  const { HistoryTurn } = await server.ssrLoadModule('/src/react/pages/chat/turns.tsx');

  await test('FE-01 history-first parallel open reprojects the discovered remote run without a second GET', () => {
    const rows = [message(100, 'user', 'task', 100), toolMessage(101, 'same-call')];
    const live = { remote: true, startedAt: 100000, steps: [{ kind: 'tool', id: 'same-call', status: 'running' }], thinking: '', text: '' };
    const turns = visibleTurnsBesideLive(rows, live, { localActive: true, ownsHistory: false });
    const tools = turns.flatMap((turn) => turn.role === 'assistant' ? turn.steps.filter((step) => step.kind === 'tool') : []);
    assert.deepEqual(tools.map((step) => step.id), []);
  });

  await test('C3 history that arrives before task discovery is neither failed nor marked complete', () => {
    const rows = [message(90, 'user', 'task', 90), toolMessage(91, 'pending-call', 91), message(100, 'user', 'later', 100), toolMessage(101, 'open-call', 101)];
    const before = buildTurns(rows, undefined, { inFlight: true });
    const tools = before.flatMap((turn) => turn.role === 'assistant' ? turn.steps.filter((step) => step.kind === 'tool').map((step) => [step.id, step.status]) : []);
    assert.deepEqual(tools, [['pending-call', 'failed'], ['open-call', 'running']]);
  });

  await test('FE-02 degraded running history advances at a bounded interval even with an unchanged status signature', () => {
    assert.equal(historyReloadMs(true), 3000);
    assert.equal(historyReloadMs(false), 30000);
    assert.equal(HISTORY_SYNC_MS, 3000);
  });

  await test('FE-03 an old offset page cannot append into a manual or automatic latest-page replacement', async () => {
    await runBrowser('../react-gate-browser.mjs', 'history-late-earlier');
  });

  await test('FE-05 remote discovery without a current input preserves provably old answers and unknown-time history', () => {
    const unknown = { id: 3, role: 'assistant', content: 'undated answer' };
    const rows = [message(1, 'user', 'old task', 90), message(2, 'assistant', 'old answer', 91), unknown, toolMessage(4, 'current-call', 101)];
    assert.deepEqual(withoutInFlight(rows, 100).map((row) => row.id), [1, 2, 3]);
    const kept = visibleTurnsBesideLive(rows, { remote: true, startedAt: 100000, steps: [], thinking: '', text: '' }, { localActive: true, ownsHistory: false });
    assert.ok(kept.some((turn) => turn.role === 'assistant' && turn.text.includes('old answer')));
    assert.ok(kept.some((turn) => turn.role === 'assistant' && turn.text.includes('undated answer')));
  });

  await test('FE-06 steering does not turn unfinished current tools into failed persisted results', () => {
    const rows = [
      message(100, 'user', 'task', 100),
      toolMessage(101, 'replayed'),
      toolMessage(102, 'history-only', 102),
      { ...message(103, 'user', 'synthetic steer', 103), display_kind: 'steer', content: '[OUT-OF-BAND USER MESSAGE]\nsynthetic steer\n[/OUT-OF-BAND USER MESSAGE]' },
    ];
    const turns = buildTurns(rows, undefined, { inFlight: true });
    const tools = turns.flatMap((turn) => turn.role === 'assistant' ? turn.steps.filter((step) => step.kind === 'tool').map((step) => [step.id, step.status]) : []);
    assert.deepEqual(tools, [['replayed', 'running'], ['history-only', 'running']]);
  });

  await test('FE-06 the unified running process never displays completed timing or a second idle process', () => {
    const turn = buildTurns([message(100, 'user', 'task', 100), toolMessage(101, 'current')], undefined, { inFlight: true }).find((item) => item.role === 'assistant');
    const html = textOf(renderToStaticMarkup(React.createElement(HistoryTurn, {
      turn, pending: true, outcome: '', editDisabled: '', editing: false, messageKey: '',
      onEditStart() {}, onEditEnd() {}, onEditSend: async () => {},
    })));
    assert.equal(html.includes('已完成'), false);
  });

  await test('B2 streaming body is shown until the history row ends with it, including a truncated tail', () => {
    assert.equal(liveTextBesideHistory('fresh answer', 'earlier commentary'), 'fresh answer');
    assert.equal(liveTextBesideHistory('fresh answer and more', 'earlier commentary'), 'fresh answer and more');
    assert.ok(liveTextBesideHistory('fresh answer and more', 'earlier commentary').startsWith('fresh answer'));
    assert.equal(liveTextBesideHistory('fresh answer and more', 'beginning lost in replay, fresh answer and more'), '');
  });

  await test('B2 local run boundary does not depend on the browser clock', () => {
    const rows = [message(90, 'user', 'old task', 90), message(91, 'assistant', 'old answer', 91), message(100, 'user', 'current task', 100), toolMessage(101, 'call-a', 101)];
    for (const skew of [-3600, 3600]) {
      const live = { remote: false, startedAt: (100 + skew) * 1000, steps: [{ kind: 'tool', status: 'running' }], thinking: '', text: '' };
      const turns = visibleTurnsBesideLive(rows, live, { localActive: true, ownsHistory: false });
      assert.equal(turns.filter((turn) => turn.role === 'user' && turn.text === 'current task').length, 0, `skew ${skew}`);
      assert.ok(turns.some((turn) => turn.role === 'assistant' && turn.text === 'old answer'), `skew ${skew}`);
    }
  });

  await test('visible document with hidden chat does not write live or open a frame', async () => {
    await runBrowser('../react-gate-browser.mjs', 'snapshot-hidden');
  });

  await test('a frame queued before hide does not land after the chat is hidden', async () => {
    await runBrowser('../react-gate-browser.mjs', 'snapshot-hidden');
  });

  await test('visible chat still updates live on RUN_CHANGED', async () => {
    await runBrowser('../react-gate-browser.mjs', 'snapshot-coalesce');
  });

  await test('preservation: late old-session pagination and main-load failures cannot corrupt the new view', async () => {
    await runBrowser('../react-gate-browser.mjs', 'history-switch');
  });

  await test('FE-04 failed terminal history load retains visible temporary content and manual retry safely clears it', async () => {
    await runBrowser('../react-gate-browser.mjs', 'history-failure');
  });
} finally {
  await server.close();
  restoreSsrDom();
}
