import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

const root = new URL('..', import.meta.url);

// Same positive set as tests/test-gates.test.mjs. 20591b7 removed the Lit p4/p6
// gates and tests/p5-status-animation.test.mjs; do not require them back.
const requiredVerifyGates = [
  'typecheck',
  'node',
  'python-tests',
  'python-sidecar',
  'delegated-sidebar-browser',
  'scroll-layout-phase3-browser',
  'artifacts-browser',
  'composer-sizing-browser',
  'folders-browser',
  'mobile-nav-browser',
  'live-confirm-hit-browser',
  'scroll-jump-probe-browser',
  'agent-close-browser',
  'folder-offpage-browser',
  'image-aspect-menu-browser',
  'mcp-path-browser',
  'message-menu-size-browser',
  'subagent-fold-motion-browser',
  'subagents-session-frame-browser',
  'task-popup-mutex-browser',
  'display-edge-browser',
  'subagent-clock-browser',
  'stream-perf-react-browser',
  'build',
];
const removedLitGates = [
  'p4-live-browser',
  'p4-parent-acceptance-browser',
  'p6-terminal-browser',
  'p6-settle-handover-browser',
];

test('unified verify retains the current plan and the F01 node regression', () => {
  const gates = JSON.parse(execFileSync(process.execPath, ['scripts/test-gates.mjs', '--target=verify', '--list'], { cwd: root, encoding: 'utf8' }));
  const names = gates.map((gate) => gate.name);
  assert.equal(names[0], 'typecheck');
  assert.equal(names.at(-1), 'build');
  for (const name of requiredVerifyGates) assert.ok(names.includes(name), `missing verify gate: ${name}`);
  for (const name of removedLitGates) assert.equal(names.includes(name), false, `Lit gate must stay out of the plan: ${name}`);
  const node = gates.find((gate) => gate.name === 'node');
  assert.ok(node, 'missing node gate');
  assert.ok(node.args.includes('tests/run-idempotency-recovery.test.mjs'), 'missing automatic node gate: tests/run-idempotency-recovery.test.mjs');
  assert.equal(node.args.includes('tests/p5-status-animation.test.mjs'), false, 'deleted Lit animation test must not be required');
});
