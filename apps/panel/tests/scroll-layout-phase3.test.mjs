import test from 'node:test';
import assert from 'node:assert/strict';
import { runBrowser } from './fixtures/browser-case.mjs';

test('binary search vs linear search parity', async () => {
  await runBrowser('../react-gate-browser.mjs', 'scroll-parity');
});

test('1000 items binary search rect read count is logarithmic', async () => {
  await runBrowser('../react-gate-browser.mjs', 'scroll-rects');
});
