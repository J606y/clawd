import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import path from 'node:path';
import { installSsrDom, ssrViteOptions } from './helpers/ssr-dom.mjs';

const stub = path.join(import.meta.dirname, 'fixtures/dompurify-node-stub.mjs');
const restoreSsrDom = installSsrDom({ matchMedia: true, timers: false });
const server = await createServer(ssrViteOptions({ cacheName: 'paged-reasoning', stub }));

const saved = (id, timestamp, reasoning) => ({
  id, role: 'assistant', content: '', timestamp, reasoning,
  tool_calls: [{ id: 't' + id, function: { name: 'terminal', arguments: '{}' } }],
});
const visible = (html) => html.replace(/<[^>]+>/g, ' ');
const reasoningOf = (turns) => turns.filter((turn) => turn.role === 'assistant').map((turn) => turn.reasoning || '').join('|');

try {
  const { visibleTurnsBesideLive } = await server.ssrLoadModule('/src/lib/transcript.ts');
  const { LiveTail } = await server.ssrLoadModule('/src/react/pages/chat/live-tail.tsx');
  const renderLive = (run) => visible(renderToStaticMarkup(React.createElement(LiveTail, {
    run, userInHistory: true, pace: false, onApprove() {},
  })));

  await test('remote reasoning recovery uses only timestamp-proven current process when input is paged out', () => {
    const run = {
      sessionId: 'remote', runId: 'run-remote', status: 'running', userText: '', attachments: [], steers: [],
      thinking: '', steps: [], text: '', approval: null, clarify: null, error: '', stopping: false,
      startedAt: 100500, lastEventAt: 100500, model: '', provider: '', unverified: false, remote: true,
    };
    const msgs = [saved(1, 99, 'OLD'), saved(2, 100.4, 'BEFORE_CREATION'), saved(3, undefined, 'UNDATED'), saved(4, 101, 'CURRENT')];
    const turns = visibleTurnsBesideLive(msgs, run, { localActive: true, ownsHistory: false });
    const history = reasoningOf(turns);
    assert.match(history, /CURRENT/, 'saved current-process reasoning stays visible when the live projection has not taken ownership');
    assert.match(history, /OLD/);
    assert.match(history, /UNDATED/);
    const live = renderLive(run);
    for (const text of ['OLD', 'BEFORE_CREATION', 'UNDATED', 'CURRENT']) assert.equal(live.includes(text), false, text);
    assert.equal(run.thinking, '');
    const owned = { ...run, thinking: 'LIVE_ONLY' };
    const ownedTurns = visibleTurnsBesideLive(msgs, owned, { localActive: true, ownsHistory: false });
    const ownedHistory = reasoningOf(ownedTurns);
    assert.equal(ownedHistory.includes('CURRENT'), false, 'once live thinking owns the process, current-timestamp history is not drawn again');
    assert.match(ownedHistory, /OLD/);
    const ownedLive = renderLive(owned);
    assert.match(ownedLive, /LIVE_ONLY/);
    assert.equal(ownedLive.includes('CURRENT'), false);
  });

  await test('missing local task boundary and invalid remote clock never borrow arbitrary reasoning', () => {
    const local = {
      sessionId: 'local', runId: 'run-local', status: 'running', userText: 'synthetic task', attachments: [], steers: [],
      thinking: '', steps: [], text: '', approval: null, clarify: null, error: '', stopping: false,
      startedAt: 100000, lastEventAt: 100000, model: '', provider: '', unverified: false, remote: false,
    };
    const msgs = [
      { id: 1, role: 'user', content: 'OTHER_INPUT', timestamp: 1 },
      saved(2, 101, 'UNRELATED'),
      { id: 3, role: 'assistant', content: 'FINISHED', timestamp: 102 },
    ];
    assert.equal(renderLive(local).includes('UNRELATED'), false);
    assert.match(reasoningOf(visibleTurnsBesideLive(msgs, local, { localActive: true, ownsHistory: false })), /UNRELATED/);
    const remote = { ...local, remote: true, startedAt: Number.NaN };
    assert.equal(renderLive(remote).includes('UNRELATED'), false);
    assert.match(reasoningOf(visibleTurnsBesideLive(msgs, remote, { localActive: true, ownsHistory: false })), /UNRELATED/);
  });
} finally {
  await server.close();
  restoreSsrDom();
}
