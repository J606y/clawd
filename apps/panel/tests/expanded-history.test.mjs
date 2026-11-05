import test from 'node:test';
import { runBrowser } from './fixtures/browser-case.mjs';

test('refresh pages an expanded window, refreshes records and keeps hasMore for the entire window', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-within-cap');
});

test('failure on the second page keeps the entire previous window and notes unchanged', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-failure');
});

test('session switch aborts expanded refresh before the second page may publish', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-switch');
});

test('a superseding refresh aborts its predecessor and a late page cannot overwrite it', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-switch');
});

test('a growing tail may extend the refresh budget to keep the oldest loaded source', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-within-cap');
});

test('duplicate offset pages fail atomically rather than publishing a corrupted window', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-failure');
});

test('an empty page claiming more rows fails without discarding the reader window', async () => {
  await runBrowser('../react-gate-browser.mjs', 'history-failure');
});
