import test, { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

const admittedBrowserGates = [
  'artifacts-browser',
  'composer-sizing-browser',
  'folders-browser',
  'mobile-nav-browser',
  'live-confirm-hit-browser',
];

// Confirmed not the same headless node entry as existing gates.
const addedBrowserGates = [
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
];
const excludedBrowserGates = [
  'live-dock-browser',
  'stream-perf-browser',
];

function runProcess(cmd, args, options = {}) {
  return new Promise((resolve) => {
    const proc = spawn(cmd, args, { ...options, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', d => { stdout += d; });
    proc.stderr.on('data', d => { stderr += d; });
    proc.on('close', (code, signal) => {
      resolve({ code, signal, stdout, stderr });
    });
  });
}

describe('test-gates runner contract', () => {
  it('exposes help text and options', async () => {
    const res = await runProcess(process.execPath, ['scripts/test-gates.mjs', '--help']);
    assert.equal(res.code, 0);
    assert.match(res.stdout, /Usage: node scripts\/test-gates\.mjs/);
    assert.match(res.stdout, /--target=\(node\|python\|browser\|all\|verify\)/);
  });

  it('verify plan includes every browser gate as well as typecheck and build', async () => {
    const res = await runProcess(process.execPath, ['scripts/test-gates.mjs', '--target=verify', '--list']);
    assert.equal(res.code, 0, res.stderr);
    const names = JSON.parse(res.stdout).map(gate => gate.name);
    for (const name of ['typecheck', 'node', 'python-tests', 'python-sidecar',
      'delegated-sidebar-browser', 'scroll-layout-phase3-browser',
      ...admittedBrowserGates,
      ...addedBrowserGates,
      'build']) {
      assert.ok(names.includes(name), `verify omits ${name}`);
    }
    assert.equal(names[0], 'typecheck');
    assert.equal(names.at(-1), 'build');
    for (const name of excludedBrowserGates) {
      assert.equal(names.includes(name), false, `verify must not include ${name}`);
    }
  });

  it('all plan includes typecheck and admitted headless browser gates without executing tsc', async () => {
    const res = await runProcess(process.execPath, ['scripts/test-gates.mjs', '--target=all', '--list']);
    assert.equal(res.code, 0, res.stderr);
    assert.doesNotMatch(res.stdout, /\[test-gates\] Running/);
    assert.doesNotMatch(res.stdout + res.stderr, /\btsc\b|error TS/);
    const gates = JSON.parse(res.stdout);
    const names = gates.map(gate => gate.name);
    assert.equal(names[0], 'typecheck');
    assert.equal(gates[0].cmd, 'npm');
    assert.deepEqual(gates[0].args, ['run', 'typecheck']);
    assert.equal(names.includes('build'), false);
    for (const name of ['node', 'python-tests', 'python-sidecar', ...admittedBrowserGates, ...addedBrowserGates]) {
      assert.ok(names.includes(name), `all omits ${name}`);
    }
    for (const name of excludedBrowserGates) {
      assert.equal(names.includes(name), false, `all must not include ${name}`);
    }
  });

  it('rejects unknown targets instead of reporting an empty successful gate', async () => {
    const tempDir = await mkdtemp(join('/root/hermes-workspace', 'gates-test-invalid-'));
    try {
      const res = await runProcess(process.execPath, [
        'scripts/test-gates.mjs', '--target=verfiy', `--output-dir=${tempDir}`,
      ]);
      assert.notEqual(res.code, 0);
      assert.match(res.stderr, /Unknown target/);
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it('fails and propagates exit code when a child command fails, stops further execution by default', async () => {
    const tempDir = await mkdtemp(join('/root/hermes-workspace', 'gates-test-fail-'));
    try {
      const planFile = join(tempDir, 'gates-plan.json');
      await writeFile(planFile, JSON.stringify([
        { name: 'step1', cmd: process.execPath, args: ['-e', 'console.log("step1_ok")'] },
        { name: 'step2_fail', cmd: process.execPath, args: ['-e', 'console.error("step2_failed"); process.exit(42)'] },
        { name: 'step3_should_not_run', cmd: process.execPath, args: ['-e', 'console.log("step3_ran")'] },
      ]));

      const res = await runProcess(process.execPath, [
        'scripts/test-gates.mjs',
        `--plan=${planFile}`,
        `--output-dir=${tempDir}`
      ]);

      assert.equal(res.code, 42);
      assert.match(res.stdout, /step1/);
      assert.match(res.stderr + res.stdout, /step2_failed/);
      assert.doesNotMatch(res.stdout, /step3_ran/);

      // Verify report output
      const report = JSON.parse(await readFile(join(tempDir, 'gates-summary.json'), 'utf8'));
      assert.equal(report.success, false);
      assert.equal(report.failedGate, 'step2_fail');
      assert.equal(report.results.length, 2);
      assert.equal(report.results[0].status, 'passed');
      assert.equal(report.results[1].status, 'failed');
      assert.equal(report.results[1].exitCode, 42);
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it('enforces execution order and preserves logs per-gate without output interleaving', async () => {
    const tempDir = await mkdtemp(join('/root/hermes-workspace', 'gates-test-order-'));
    try {
      const planFile = join(tempDir, 'gates-plan.json');
      await writeFile(planFile, JSON.stringify([
        { name: 'gateA', cmd: process.execPath, args: ['-e', 'console.log("LOG_FROM_A")'] },
        { name: 'gateB', cmd: process.execPath, args: ['-e', 'console.log("LOG_FROM_B")'] },
      ]));

      const res = await runProcess(process.execPath, [
        'scripts/test-gates.mjs',
        `--plan=${planFile}`,
        `--output-dir=${tempDir}`
      ]);

      assert.equal(res.code, 0);

      // Verify individual log files exist and attribute output correctly
      const logA = await readFile(join(tempDir, 'gateA.log'), 'utf8');
      const logB = await readFile(join(tempDir, 'gateB.log'), 'utf8');

      assert.match(logA, /LOG_FROM_A/);
      assert.doesNotMatch(logA, /LOG_FROM_B/);
      assert.match(logB, /LOG_FROM_B/);
      assert.doesNotMatch(logB, /LOG_FROM_A/);

      const report = JSON.parse(await readFile(join(tempDir, 'gates-summary.json'), 'utf8'));
      assert.equal(report.success, true);
      assert.deepEqual(report.results.map(r => r.name), ['gateA', 'gateB']);
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it('handles child timeout properly, killing process and reporting timeout status', async () => {
    const tempDir = await mkdtemp(join('/root/hermes-workspace', 'gates-test-timeout-'));
    try {
      const planFile = join(tempDir, 'gates-plan.json');
      // Sleep for 5 seconds with a 1-second timeout
      await writeFile(planFile, JSON.stringify([
        { name: 'slow_gate', cmd: process.execPath, args: ['-e', 'setTimeout(() => {}, 5000)'], timeoutMs: 800 },
      ]));

      const res = await runProcess(process.execPath, [
        'scripts/test-gates.mjs',
        `--plan=${planFile}`,
        `--output-dir=${tempDir}`
      ]);

      assert.notEqual(res.code, 0);
      const report = JSON.parse(await readFile(join(tempDir, 'gates-summary.json'), 'utf8'));
      assert.equal(report.success, false);
      assert.equal(report.results[0].status, 'timeout');
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it('configures isolated test environments without writing to ~/.hermes or system /tmp', async () => {
    const tempDir = await mkdtemp(join('/root/hermes-workspace', 'gates-test-env-'));
    try {
      const planFile = join(tempDir, 'gates-plan.json');
      // Check environment variables passed to child
      await writeFile(planFile, JSON.stringify([
        { name: 'env_check', cmd: process.execPath, args: ['-e', 'console.log(JSON.stringify({ tmp: process.env.TMPDIR, home: process.env.HERMES_HOME, out: process.env.PANEL_TEST_OUTPUT }))'] },
      ]));

      const res = await runProcess(process.execPath, [
        'scripts/test-gates.mjs',
        `--plan=${planFile}`,
        `--output-dir=${tempDir}`
      ]);

      assert.equal(res.code, 0);
      const log = await readFile(join(tempDir, 'env_check.log'), 'utf8');
      const envData = JSON.parse(log.trim());
      assert.ok(envData.tmp, 'TMPDIR is set');
      assert.ok(!envData.tmp.startsWith('/tmp'), 'TMPDIR must not use system /tmp');
      assert.ok(!envData.tmp.includes('/.hermes'), 'TMPDIR must not use ~/.hermes');
      assert.equal(envData.out, tempDir);
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });
});
