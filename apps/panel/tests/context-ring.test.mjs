import test from 'node:test';
import assert from 'node:assert/strict';
import {contextRingLevel,contextRingStroke} from '../src/lib/context-ring.ts';

test('context ring stays blue through 60, yellow after 60, red after 90, and closes at 100',()=>{
  assert.equal(contextRingLevel(0),'ok');
  assert.equal(contextRingLevel(37),'ok');
  assert.equal(contextRingLevel(60),'ok');
  assert.equal(contextRingLevel(61),'warn');
  assert.equal(contextRingLevel(90),'warn');
  assert.equal(contextRingLevel(91),'danger');
  assert.equal(contextRingLevel(100),'full');
  assert.equal(contextRingStroke('ok'),'var(--accent, #0a84ff)');
  assert.equal(contextRingStroke('warn'),'var(--warning, #ff9f0a)');
  assert.equal(contextRingStroke('danger'),'var(--danger, #ff453a)');
  assert.equal(contextRingStroke('full'),'var(--danger, #ff453a)');
});
