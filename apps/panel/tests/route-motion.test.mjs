import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const server = await createServer({
  root,
  configFile: false,
  logLevel: 'silent',
  server: { middlewareMode: true },
  appType: 'custom',
});
try {
  const {
    latchRouteMotion,
    routeMotion,
    resetRouteMotion,
    activeMotion,
    reducedMotion,
    installRouteMotion,
    NAV_MS,
    POP_MS,
  } = await server.ssrLoadModule('/src/lib/route-motion.ts');

  test('route motion latch contract', () => {
    resetRouteMotion();
    latchRouteMotion('#/files', 'push');
    assert.equal(routeMotion('#/files'), 'push', 'same hash returns push');
    assert.equal(routeMotion('#/files'), 'push', 'a second read does not consume the latch');
    assert.equal(routeMotion('#/activity'), 'none', 'a different hash is none');
    assert.equal(routeMotion('#/files/extra'), 'none', 'hash match is exact, not a prefix');
    assert.equal(routeMotion(''), 'none', 'empty hash does not match a latched route');
    assert.equal(routeMotion('#/files'), 'push', 'a mismatch read does not consume the latch');

    latchRouteMotion('#/files', 'pop');
    assert.equal(routeMotion('#/files'), 'pop', 'pop overwrites push');
    assert.equal(routeMotion('#/files'), 'pop', 'pop is not consumed by a read');

    resetRouteMotion();
    assert.equal(routeMotion('#/files'), 'none', 'reset clears the latch');

    assert.equal(activeMotion('push', true), 'none');
    assert.equal(activeMotion('pop', false), 'pop');
    assert.equal(NAV_MS, 350);
    assert.equal(POP_MS, 280);
    assert.equal(typeof reducedMotion, 'function');
    assert.equal(typeof installRouteMotion, 'function');
  });
} finally {
  await server.close();
}
