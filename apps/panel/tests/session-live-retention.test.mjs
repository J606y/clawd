import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';

const cacheDir = await mkdtemp(join(process.cwd(), '.retention-test-'));
const server = await createServer({
  configFile: false,
  cacheDir,
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true },
  appType: 'custom',
});

const summary = (id, overrides = {}) => ({
  id,
  source: 'api_server',
  title: `Title ${id}`,
  model: null,
  started_at: 1000,
  last_active: 1000,
  message_count: 1,
  preview: null,
  pinned: false,
  archived: false,
  ...overrides,
});

function createMockStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, val) => { store.set(key, String(val)); },
    removeItem: (key) => { store.delete(key); },
    clear: () => { store.clear(); },
  };
}

try {
  const {
    LIVE_RETENTION_MS,
    LiveRetentionTracker,
  } = await server.ssrLoadModule('/src/lib/session-live-retention.ts');

  await test('LIVE_RETENTION_MS is 10 minutes (600,000 ms)', () => {
    assert.equal(LIVE_RETENTION_MS, 600_000);
  });

  await test('transitions active session to completed retention when not seen in next active snapshot', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    // Snapshot 1 at t=1,000,000 with active s1
    const out1 = tracker.reconcile([summary('s1')]);
    assert.deepEqual(out1.map(s => s.id), ['s1']);
    assert.equal(tracker.getEntry('s1')?.status, 'active');

    // Snapshot 2 at t=1,005,000: s1 is no longer in active response
    currentTime = 1_005_000;
    const out2 = tracker.reconcile([]);
    assert.deepEqual(out2.map(s => s.id), ['s1'], 's1 is retained in live list');
    const entry = tracker.getEntry('s1');
    assert.equal(entry?.status, 'completed');
    assert.equal(entry?.endedAt, 1_005_000);
    assert.equal(entry?.deadline, 1_005_000 + 600_000);
  });

  await test('exact deadline boundary: retained at deadline - 1ms (599.999s) and removed at deadline (600s)', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    tracker.reconcile([summary('s1')]);

    // Ends at 1_005_000. Deadline should be 1_605_000
    currentTime = 1_005_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.deadline, 1_605_000);

    // At 1_604_999 (599.999 seconds after ending) -> still retained
    currentTime = 1_604_999;
    const outBefore = tracker.reconcile([]);
    assert.deepEqual(outBefore.map(s => s.id), ['s1']);

    // At 1_605_000 (exact 600.000 seconds) -> expired and removed
    currentTime = 1_605_000;
    const outExact = tracker.reconcile([]);
    assert.deepEqual(outExact.map(s => s.id), []);
    assert.equal(tracker.getEntry('s1'), undefined);
  });

  await test('repeated polling does NOT extend the completion deadline', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    tracker.reconcile([summary('s1')]);

    // First inactive snapshot
    currentTime = 1_005_000;
    tracker.reconcile([]);
    const fixedDeadline = 1_005_000 + 600_000;
    assert.equal(tracker.getEntry('s1')?.deadline, fixedDeadline);

    // Multiple subsequent polls while inactive
    currentTime = 1_010_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.deadline, fixedDeadline, 'poll at +5s did not extend deadline');

    currentTime = 1_050_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.deadline, fixedDeadline, 'poll at +45s did not extend deadline');

    currentTime = 1_604_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.deadline, fixedDeadline);
  });

  await test('resuming execution clears completed deadline and restores active status', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    tracker.reconcile([summary('s1')]);
    currentTime = 1_005_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.status, 'completed');

    // Resumed execution at t=1_020_000
    currentTime = 1_020_000;
    tracker.reconcile([summary('s1', { last_active: 1_020_000 })]);
    assert.equal(tracker.getEntry('s1')?.status, 'active');
    assert.equal(tracker.getEntry('s1')?.endedAt, undefined);
    assert.equal(tracker.getEntry('s1')?.deadline, undefined);

    // Ends again at t=1_050_000 -> fresh deadline based on new endedAt
    currentTime = 1_050_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.status, 'completed');
    assert.equal(tracker.getEntry('s1')?.endedAt, 1_050_000);
    assert.equal(tracker.getEntry('s1')?.deadline, 1_050_000 + 600_000);
  });

  await test('persists completed deadlines to storage and restores them upon reload', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker1 = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    tracker1.reconcile([summary('s1')]);
    currentTime = 1_005_000;
    tracker1.reconcile([]);

    const savedRaw = storage.getItem('hp_live_retention_v1');
    assert.ok(savedRaw);

    // Simulate new instance / page reload at t=1_010_000
    currentTime = 1_010_000;
    const tracker2 = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    // Query returns []
    const outReload = tracker2.reconcile([]);
    assert.deepEqual(outReload.map(s => s.id), ['s1']);
    const entry = tracker2.getEntry('s1');
    assert.equal(entry?.status, 'completed');
    assert.equal(entry?.deadline, 1_005_000 + 600_000, 'retained unchanged original deadline across reload');
  });

  await test('on reload, previously active entry not seen again uses last confirmed active time as fallback deadline to prevent resurrecting after long offline interval', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker1 = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    // s1 was active at t=1_000_000 with last_active=1_000_000
    tracker1.reconcile([summary('s1', { last_active: 1_000_000 })]);

    // Now simulate user closed tab/browser and comes back 2 hours later at t=8_200_000
    currentTime = 8_200_000;
    const tracker2 = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    // Query returns [] (s1 has long finished)
    // tracker2 must NOT give s1 a fresh 10-minute lease from currentTime (8_200_000).
    // It must fallback to last confirmed active time (1_000_000 + 600_000 = 1_600_000 < 8_200_000)
    // and discard it!
    const out = tracker2.reconcile([]);
    assert.deepEqual(out.map(s => s.id), [], 'stale active entry from hours ago must not be resurrected');
  });

  await test('storage failures (e.g. quota exceeded or disabled storage) degrade gracefully without throwing', () => {
    const failingStorage = {
      getItem: () => { throw new Error('QuotaExceededError'); },
      setItem: () => { throw new Error('QuotaExceededError'); },
      removeItem: () => { throw new Error('QuotaExceededError'); },
      clear: () => {},
    };

    const tracker = new LiveRetentionTracker({
      now: () => 1_000_000,
      storage: failingStorage,
    });

    assert.doesNotThrow(() => {
      tracker.reconcile([summary('s1')]);
      tracker.reconcile([]);
    });
  });

  await test('handles deletion: explicit markDeleted removes from retention and prevents resurrection', () => {
    let currentTime = 1_000_000;
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => currentTime,
      storage,
    });

    tracker.reconcile([summary('s1')]);
    currentTime = 1_005_000;
    tracker.reconcile([]);
    assert.equal(tracker.getEntry('s1')?.status, 'completed');

    // User deletes session
    tracker.markDeleted('s1');
    const out = tracker.reconcile([]);
    assert.deepEqual(out, []);
    assert.equal(tracker.getEntry('s1'), undefined);
  });

  await test('subagents are excluded from active snapshots and persisted completed retention', () => {
    const child = summary('child', { source: 'subagent' });
    const storage = createMockStorage({ hp_live_retention_v1: JSON.stringify([
      { session: child, status: 'completed', endedAt: 995_000, deadline: 1_595_000, lastConfirmedActiveAt: 990_000 },
    ]) });
    const tracker = new LiveRetentionTracker({ storage, now: () => 1_000_000 });
    assert.deepEqual(tracker.reconcile([child, summary('parent')]).map(s => s.id), ['parent']);
    assert.ok(!storage.getItem('hp_live_retention_v1').includes('child'), 'old child cache is purged');
  });

  await test('storage access itself may be denied without preventing tracker construction', () => {
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
    Object.defineProperty(globalThis, 'window', { configurable: true, value: {
      get localStorage() { throw new Error('SecurityError'); },
    } });
    try {
      assert.doesNotThrow(() => new LiveRetentionTracker().reconcile([summary('s1')]));
    } finally {
      if (previous) Object.defineProperty(globalThis, 'window', previous);
      else delete globalThis.window;
    }
  });

  await test('cache capacity never hides conversations still executing', () => {
    const tracker = new LiveRetentionTracker({ storage: null, now: () => 1_000_000, maxEntries: 2 });
    const active = ['s1', 's2', 's3'].map(id => summary(id));
    assert.deepEqual(tracker.reconcile(active).map(s => s.id), ['s1', 's2', 's3']);
  });

  await test('cache bounded: evicts oldest expired/stale entries if count exceeds bound', () => {
    const storage = createMockStorage();
    const tracker = new LiveRetentionTracker({
      now: () => 1_000_000,
      storage,
      maxEntries: 5,
    });

    for (let i = 1; i <= 10; i++) {
      tracker.reconcile([summary(`s${i}`)]);
    }
    // The cache size should not exceed maxEntries
    assert.ok(tracker.size <= 5);
  });
} finally {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
}
