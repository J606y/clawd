import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import path from 'node:path';
import { installSsrDom, ssrViteOptions } from './helpers/ssr-dom.mjs';

const stub = path.join(import.meta.dirname, 'fixtures/dompurify-node-stub.mjs');
const restoreSsrDom = installSsrDom({ matchMedia: false, timers: true });
const server = await createServer(ssrViteOptions({ cacheName: 'live-status', stub }));

function makeRun(sessionId = 'sess', extra = {}) {
  return {
    sessionId, runId: `run-${sessionId}`, status: 'running', userText: '', attachments: [], steers: [], thinking: '', steps: [], text: '',
    approval: null, clarify: null, error: '', stopping: false, startedAt: 100000, lastEventAt: Date.now(), model: '', provider: '',
    unverified: false, remote: true, ...extra,
  };
}
const visible = (html) => html.replace(/<[^>]+>/g, '');

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

try {
  const { runs } = await server.ssrLoadModule('/src/lib/runs.ts');
  const { LiveTail } = await server.ssrLoadModule('/src/react/pages/chat/live-tail.tsx');
  const render = (run) => renderToStaticMarkup(React.createElement(LiveTail, { run, userInHistory: true, pace: false, onApprove() {} }));

  await test('whitespace before a tool call is neither a note nor leftover body', () => {
    const run = makeRun('sess');
    runs.apply(run, 'assistant.delta', { delta: '\n\n' });
    runs.apply(run, 'tool.started', { tool_name: 'read_file', preview: 'a.txt' });
    assert.equal(run.text, '');
    assert.deepEqual(run.steps.map((step) => step.kind), ['tool']);
  });

  await test('already-streamed commentary does not leave its leading newlines behind', () => {
    const run = makeRun('sess');
    runs.apply(run, 'assistant.delta', { delta: '\n\n先看一下文件' });
    runs.apply(run, 'assistant.commentary', { text: '先看一下文件', already_streamed: true });
    assert.equal(run.text, '');
  });

  await test('a whitespace-only body draws no caret and keeps the thinking status with elapsed time', () => {
    const run = makeRun('sess', { text: '\n\n' });
    runs.apply(run, 'tool.started', { tool_name: 'read_file', preview: 'a.txt' });
    runs.apply(run, 'tool.completed', { tool_name: 'read_file', preview: 'ok' });
    run.text = '\n';
    const html = render(run);
    const body = html.match(/<div class="assistant-body">([\s\S]*?)<\/div><div class="run-status-slot">/);
    assert.equal((body?.[1] ?? '').replace(/<[^>]+>/g, '').trim(), '', 'no empty blinking caret text');
    assert.match(html, /class="run-status"/, 'thinking status stays visible');
    assert.match(html, /class="spinner"/, 'whitespace is not treated as a streaming body');
  });

  await test('after some real text the status keeps showing elapsed time while streaming', () => {
    const run = makeRun('sess', { text: '我先读一下配置' });
    const html = render(run);
    assert.equal(visible(html.match(/<div class="assistant-body">([\s\S]*?)<\/div>/)?.[1] ?? '').replace(/\s/g, ''), '我先读一下配置');
    assert.match(html, /class="run-status"/, 'elapsed time does not vanish just because the model said something');
    assert.doesNotMatch(html, /class="spinner"/, 'the status component shows only elapsed time until the stream pauses');
  });

  await test('a running tool still owns the progress even when a note follows it', () => {
    const run = makeRun('sess');
    runs.apply(run, 'tool.started', { tool_name: 'terminal', preview: 'sleep 60' });
    runs.apply(run, 'assistant.commentary', { text: '等它跑完' });
    assert.equal(run.steps.at(-1).kind, 'note');
    const html = render(run);
    assert.match(html, /class="run-status"/, 'retry observer stays mounted');
    assert.match(html, /data-s="running"/, 'a running tool keeps the thinking status mounted');
  });

  await test('an approval prompt replaces the thinking status', () => {
    const run = makeRun('sess', { approval: { command: 'rm -rf build', description: '', choices: ['once', 'deny'], pending: false } });
    const html = render(run);
    assert.doesNotMatch(html, /class="run-status"/);
    assert.match(html, /class="approval"/);
  });

  await test('a finished turn whose background process still runs says so instead of only "completed"', async () => {
    await runBrowser('live-status-browser.mjs', 'background-line');
  });

  await test('a failed query keeps the last known list; a reply for a session that is not open is ignored', async () => {
    await runBrowser('live-status-browser.mjs', 'failed-query');
  });
} finally {
  await server.close();
  restoreSsrDom();
}
