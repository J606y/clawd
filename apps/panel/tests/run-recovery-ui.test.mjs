import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'vite';

const globals = Object.fromEntries(['document', 'window', 'localStorage', 'navigator', 'fetch'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
browser.addEventListener = () => {};
browser.removeEventListener = () => {};
const document = new EventTarget();
document.baseURI = 'https://panel.example.test/';
document.visibilityState = 'visible';
Object.defineProperty(globalThis, 'window', { configurable: true, value: browser });
Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem: () => {}, removeItem: () => {} } });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: true } });

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });

function runBrowser(script, name) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [new URL(script, import.meta.url).pathname, name], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (chunk) => { out += chunk; });
    child.stderr.on('data', (chunk) => { out += chunk; });
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error(`browser case timed out\n${out}`)); }, 180000);
    child.on('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(out || `browser case exited ${code}`));
    });
  });
}

function makeRun(sessionId, extra = {}) {
  return {
    sessionId, runId: `run-${sessionId}`, status: 'running', userText: 'synthetic task',
    attachments: [], steers: [], thinking: '', steps: [], text: '', approval: null,
    error: '', stopping: false, startedAt: 100000, lastEventAt: 100000, model: '', provider: '', ...extra,
  };
}

try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');

  await test('visible active run reconciles an exact interrupted orphan without clicking stop', async () => {
    const sessionId = 'orphan_visible';
    const run = makeRun(sessionId);
    runs.runs.set(sessionId, run);
    await runs.reconcile(sessionId, {
      busy: false,
      run: { run_id: run.runId, session_id: sessionId, status: 'interrupted', created_at: 100, updated_at: 101 },
    });
    assert.equal(run.status, 'failed');
    assert.equal(runs.isActive(sessionId), false);
  });

  await test('CLI lease exposes deliberate two-step stop rather than pretending API cancellation', async () => {
    await runBrowser('run-recovery-ui-browser.mjs', 'two-step');
  });

  await test('late CLI stop response does not clear another session busy state', async () => {
    await runBrowser('run-recovery-ui-browser.mjs', 'late-stop');
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
