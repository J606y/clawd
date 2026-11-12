#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdir, writeFile, readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SCRIPT_DIR, '..');

function printHelp() {
  console.log(`Usage: node scripts/test-gates.mjs [options]

Options:
  --target=(node|python|browser|all|verify)  Target test gate category (default: all)
  --plan=<path>                             Custom JSON plan file (array of { name, cmd, args, timeoutMs, cwd, env })
  --output-dir=<path>                       Directory to write log files and gates-summary.json
  --continue-on-failure                     Do not exit immediately on gate failure
  --list                                    Print the gate plan without running commands
  --help                                    Show this help message
`);
}

function parseArgs(argv) {
  const options = {
    target: 'all',
    plan: null,
    outputDir: null,
    continueOnFailure: false,
    help: false,
    list: false,
  };

  for (const arg of argv) {
    if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (arg === '--list') {
      options.list = true;
    } else if (arg.startsWith('--target=')) {
      options.target = arg.slice('--target='.length);
    } else if (arg.startsWith('--plan=')) {
      options.plan = arg.slice('--plan='.length);
    } else if (arg.startsWith('--output-dir=')) {
      options.outputDir = arg.slice('--output-dir='.length);
    } else if (arg === '--continue-on-failure') {
      options.continueOnFailure = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }
  if (!['node', 'python', 'browser', 'all', 'verify'].includes(options.target)) {
    throw new Error(`Unknown target: ${options.target}`);
  }

  return options;
}

async function getStandardGates(target, repoRoot) {
  const gates = [];

  // Define node gates
  if (target === 'node' || target === 'all' || target === 'verify') {
    const testsDir = path.join(repoRoot, 'tests');
    const allFiles = await readdir(testsDir);
    const testFiles = allFiles
      .filter(f => f.endsWith('.test.mjs'))
      .sort()
      .map(f => path.join('tests', f));

    gates.push({
      name: 'node',
      cmd: process.execPath,
      args: ['--test', '--test-concurrency=1', ...testFiles],
      // 67 个 *.test.mjs（含浏览器包装器）在这台机器上超过 300s 被 runner 杀掉。
      // 这是进程总超时，不是产品性能阈值。
      timeoutMs: 600000,
    });
  }

  // Define python gates
  if (target === 'python' || target === 'all' || target === 'verify') {
    gates.push({
      name: 'python-tests',
      cmd: 'python3',
      args: ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test*.py'],
      timeoutMs: 300000,
    });
    gates.push({
      name: 'python-sidecar',
      cmd: 'python3',
      args: ['-B', '-m', 'unittest', 'discover', '-s', 'sidecar', '-p', 'test*.py'],
      timeoutMs: 180000,
    });
  }

  // Lit component browser gates were removed with the old framework.
  if (target === 'browser' || target === 'all' || target === 'verify') {
    const browserTests = [
      'tests/delegated-sidebar-browser.mjs',
      'tests/scroll-layout-phase3-browser.mjs',
      'tests/artifacts-browser.mjs',
      'tests/composer-sizing-browser.mjs',
      'tests/folders-browser.mjs',
      'tests/project-resources-browser.mjs',
      'tests/session-navigation-browser.mjs',
      'tests/mobile-nav-browser.mjs',
      'tests/image-preview-layer-browser.mjs',
      'tests/live-confirm-hit-browser.mjs',
      'tests/agent-origin-browser.mjs',
      'tests/subagent-card-size-browser.mjs',
      'tests/liquid-glass-browser.mjs',
      'scripts/test-react-admission-recovery-browser.mjs',
      'scripts/test-react-composer-upload-browser.mjs',
      'tests/scroll-jump-probe-browser.mjs',
      'tests/agent-close-browser.mjs',
      'tests/folder-offpage-browser.mjs',
      'tests/image-aspect-menu-browser.mjs',
      'tests/mcp-path-browser.mjs',
      'tests/message-menu-size-browser.mjs',
      'tests/subagent-fold-motion-browser.mjs',
      'tests/subagents-session-frame-browser.mjs',
      'tests/task-popup-mutex-browser.mjs',
      'tests/display-edge-browser.mjs',
      'tests/subagent-clock-browser.mjs',
      'tests/subagent-spin-browser.mjs',
      'tests/stream-perf-react-browser.mjs',
    ];
    for (const bt of browserTests) {
      const name = path.basename(bt, '.mjs');
      gates.push({
        name,
        cmd: process.execPath,
        args: [bt],
        timeoutMs: bt === 'tests/stream-perf-react-browser.mjs' ? 360000 : 120000,
      });
    }
  }

  // typecheck is part of the default npm test plan (target=all) and verify.
  // build stays verify-only. --list prints this plan and does not run tsc.
  if (target === 'all' || target === 'verify') {
    gates.unshift({
      name: 'typecheck',
      cmd: 'npm',
      args: ['run', 'typecheck'],
      timeoutMs: 120000,
    });
  }
  if (target === 'verify') {
    gates.push({
      name: 'build',
      cmd: 'npm',
      args: ['run', 'build'],
      timeoutMs: 180000,
    });
  }

  return gates;
}

function runCommandWithTimeout(cmd, args, { cwd, env, timeoutMs }) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let timer = null;

    const child = spawn(cmd, args, {
      cwd,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true, // create a new process group so we can kill subprocess trees cleanly
    });

    if (timeoutMs && timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        try {
          // Kill the process group
          process.kill(-child.pid, 'SIGKILL');
        } catch {
          try {
            child.kill('SIGKILL');
          } catch {
            // ignore
          }
        }
      }, timeoutMs);
    }

    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
    });

    child.stderr.on('data', chunk => {
      stderr += chunk.toString();
    });

    child.on('close', (code, signal) => {
      if (timer) clearTimeout(timer);
      resolve({
        exitCode: timedOut ? 124 : (code ?? (signal ? 128 : 0)),
        signal,
        timedOut,
        stdout,
        stderr,
      });
    });

    child.on('error', (err) => {
      if (timer) clearTimeout(timer);
      resolve({
        exitCode: 1,
        signal: null,
        timedOut: false,
        stdout,
        stderr: stderr + '\n' + err.message,
      });
    });
  });
}

async function main() {
  const options = parseArgs(process.argv.slice(2));

  if (options.help) {
    printHelp();
    process.exit(0);
  }

  if (options.list) {
    const gates = options.plan
      ? JSON.parse(await readFile(path.resolve(process.cwd(), options.plan), 'utf8'))
      : await getStandardGates(options.target, REPO_ROOT);
    console.log(JSON.stringify(gates));
    return;
  }

  // Setup output and workspace directory
  const outputDir = options.outputDir
    ? path.resolve(process.cwd(), options.outputDir)
    : (process.env.PANEL_TEST_OUTPUT
      ? path.resolve(process.env.PANEL_TEST_OUTPUT)
      : path.join('/root/hermes-workspace', 'panel-gates-output'));

  await mkdir(outputDir, { recursive: true });

  // Use a short workspace temp directory for Unix domain sockets (max 104-108 chars on Linux)
  // Never use ~/.hermes or system /tmp directly.
  const shortTmpDir = path.join('/root/hermes-workspace', 'p-tmp');
  await mkdir(shortTmpDir, { recursive: true });

  const isolatedHome = path.join('/root/hermes-workspace', 'p-home');
  await mkdir(isolatedHome, { recursive: true });

  // Determine gate list
  let gates = [];
  if (options.plan) {
    const planContent = await readFile(path.resolve(process.cwd(), options.plan), 'utf8');
    gates = JSON.parse(planContent);
  } else {
    gates = await getStandardGates(options.target, REPO_ROOT);
  }

  console.log(`[test-gates] Running ${gates.length} gates for target='${options.target}' in ${REPO_ROOT}`);
  console.log(`[test-gates] Output dir: ${outputDir}`);
  console.log(`[test-gates] Short TMPDIR: ${shortTmpDir}`);

  const summary = {
    target: options.target,
    success: true,
    failedGate: null,
    total: gates.length,
    results: [],
  };

  let firstFailureExitCode = 0;

  for (const gate of gates) {
    const gateName = gate.name;
    const gateCmd = gate.cmd;
    const gateArgs = gate.args || [];
    const gateTimeout = gate.timeoutMs || 300000;
    const gateCwd = gate.cwd ? path.resolve(REPO_ROOT, gate.cwd) : REPO_ROOT;

    const baseEnv = {
      ...process.env,
      TMPDIR: shortTmpDir,
      HERMES_HOME: isolatedHome,
      PANEL_TEST_OUTPUT: outputDir,
      PYTHONPATH: REPO_ROOT,
      PYTHONDONTWRITEBYTECODE: '1',
      ...(gate.env || {}),
    };

    console.log(`[test-gates] Starting gate: ${gateName} (${gateCmd} ${gateArgs.slice(0, 3).join(' ')}...)`);
    const startTime = Date.now();

    const outcome = await runCommandWithTimeout(gateCmd, gateArgs, {
      cwd: gateCwd,
      env: baseEnv,
      timeoutMs: gateTimeout,
    });

    const durationMs = Date.now() - startTime;
    const isPassed = !outcome.timedOut && outcome.exitCode === 0;
    const status = outcome.timedOut ? 'timeout' : (isPassed ? 'passed' : 'failed');

    const logFile = path.join(outputDir, `${gateName}.log`);
    const fullLog = (outcome.stdout || '') + (outcome.stderr || '');
    await writeFile(logFile, fullLog, 'utf8');

    const resultRecord = {
      name: gateName,
      status,
      exitCode: outcome.exitCode,
      durationMs,
      logFile,
    };
    summary.results.push(resultRecord);

    if (isPassed) {
      console.log(`[test-gates] PASS: ${gateName} (${durationMs}ms)`);
    } else {
      console.error(`[test-gates] FAIL: ${gateName} status=${status} exitCode=${outcome.exitCode} (${durationMs}ms)`);
      if (outcome.stderr) {
        console.error(outcome.stderr.slice(-500));
      }
      summary.success = false;
      if (!summary.failedGate) {
        summary.failedGate = gateName;
        firstFailureExitCode = outcome.exitCode || 1;
      }
      if (!options.continueOnFailure) {
        break;
      }
    }
  }

  const summaryFile = path.join(outputDir, 'gates-summary.json');
  await writeFile(summaryFile, JSON.stringify(summary, null, 2), 'utf8');

  if (!summary.success) {
    console.error(`[test-gates] Gates execution finished with failure at gate: ${summary.failedGate}`);
    process.exit(firstFailureExitCode || 1);
  } else {
    console.log(`[test-gates] All ${summary.results.length} gates passed successfully.`);
    process.exit(0);
  }
}

main().catch(err => {
  console.error('[test-gates] Unexpected runner error:', err);
  process.exit(1);
});
