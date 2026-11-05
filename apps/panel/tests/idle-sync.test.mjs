import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cacheDir = await mkdtemp(join(tmpdir(), 'panel-idle-'));
const server = await createServer({
  configFile: false,
  cacheDir,
  server: { middlewareMode: true },
  appType: 'custom',
});

try {
  const { syncPollMs, historyReloadMs } = await server.ssrLoadModule('/src/lib/sync-poll.ts');
  await test('an open page keeps discovering remote tasks every 3 seconds', () => {
    assert.equal(syncPollMs(false), 3_000);
    assert.equal(syncPollMs(true), 3_000);
  });
  await test('idle history reload waits 30 seconds and an active run stays on 3 seconds', () => {
    assert.equal(historyReloadMs(false), 30_000);
    assert.equal(historyReloadMs(true), 3_000);
  });
} finally {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
}
