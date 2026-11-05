import test from 'node:test';
import { runBrowser } from './fixtures/browser-case.mjs';

const slow = 180000;

test('FE-04 a queued degradation frame cannot abort terminal reconciliation or retain a completed live view', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'settle-join', slow);
});

test('preservation: discovery-first open still makes one history GET and no admission or stop request', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'one-history-get', slow);
});

test('FE-06 an unpersisted local task input stays visible during degradation and disappears only when history owns it', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'input-once', slow);
});

test('B2 degraded process comes only from history: no duplicated tools, notes or thinking from a partial replay', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'degraded-process', slow);
});

test('preservation: FE04-R1 genuinely new run discovery remains possible after settle', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'new-run-after-settle', slow);
});

test('preservation: pagination uses an independent controller and reload preserves the expanded range up to the page cap', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'page-controller', slow);
});

test('FE-04 terminal reconciliation excludes stale running discovery until its history commits', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'stale-poll', slow);
});

test('FE-04 discovery responses already in flight cannot replace a run after terminal settle starts', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'inflight-discovery', slow);
});

test('FE04-R1 discovery-after-settle-sidecar cannot revive the finished run', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'late-revive', slow);
});

test('FE04-R1 discovery-after-settle-status cannot revive the finished run', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'late-revive-status', slow);
});

test('healthy active SSE does not call sessionRun or runStatus', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'healthy-sse', slow);
});

test('expanded window above the 500 row cap is kept on refresh', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'over-500', slow);
});

test('same-session refresh generation does not let a late shorter page overwrite a newer refresh', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'generation', slow);
});

test('expanded window above 500 keeps the row before a shifted latest page', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'window-shift', slow);
});

test('discovery that started with no run does not apply success, failure, sessionExecution, or runStatus to a run that joined', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'discovery-join', slow);
});

test('a finish waiting on history does not clear or re-prompt a replacement that is terminal or locallyReleased', async () => {
  await runBrowser('../frontend-lifecycle-browser.mjs', 'finish-replace', slow);
});
