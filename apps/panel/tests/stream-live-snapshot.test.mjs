import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import path from 'node:path';
import { runBrowser } from './fixtures/browser-case.mjs';
import { installSsrDom, ssrViteOptions } from './helpers/ssr-dom.mjs';

const stub = path.join(import.meta.dirname, 'fixtures/dompurify-node-stub.mjs');
const restoreSsrDom = installSsrDom({ matchMedia: true, timers: true });
const server = await createServer(ssrViteOptions({ cacheName: 'stream-live', stub }));

/** 对齐旧 displayed()：只取助手正文文本节点，不含 Thought，也不把标签换成空格。 */
function elementText(html, className) {
  const at = html.indexOf(`class="${className}"`);
  if (at < 0) return '';
  const start = html.lastIndexOf('<', at);
  const tag = /^<([a-z0-9]+)/i.exec(html.slice(start))?.[1];
  if (!tag) return '';
  const re = new RegExp(`</?${tag}\\b[^>]*>`, 'gi');
  re.lastIndex = start;
  let depth = 0;
  let end = html.length;
  let match;
  while ((match = re.exec(html))) {
    if (match[0].startsWith('</')) depth -= 1;
    else if (!match[0].endsWith('/>')) depth += 1;
    if (depth === 0) {
      end = match.index + match[0].length;
      break;
    }
  }
  return html.slice(start, end).replace(/<[^>]+>/g, '');
}

try {
  const { LiveTail } = await server.ssrLoadModule('/src/react/pages/chat/live-tail.tsx');
  const render = (run) => renderToStaticMarkup(React.createElement(LiveTail, { run, userInHistory: true, pace: false, onApprove() {} }));

  await test('coalesced source bursts render in full without self-scheduling display-only frames', async () => {
    await runBrowser('../react-gate-browser.mjs', 'snapshot-coalesce');
  });

  await test('loaded visible snapshots stay monotonic across the next source update', async () => {
    await runBrowser('../react-gate-browser.mjs', 'snapshot-monotonic');
  });

  await test('complete reasoning boundaries stay out of public source-rendered body', () => {
    const original = 'THINKING:\n' + 'SYNTHETIC_PRIVATE_REASONING '.repeat(40) + '\n$]\nPublic synthetic answer';
    const run = {
      sessionId: 'classified-source', runId: 'run-classified-source', status: 'running', userText: '', attachments: [], steers: [],
      thinking: '', steps: [], text: original, approval: null, clarify: null, error: '', stopping: false,
      startedAt: 100000, lastEventAt: 100000, model: '', provider: '', unverified: false, remote: false,
    };
    const html = render(run);
    const body = elementText(html, 'assistant-body');
    assert.equal(body.includes('SYNTHETIC_PRIVATE_REASONING'), false, 'public body must not include leaked reasoning');
    assert.match(body, /Public synthetic answer/);
    assert.equal(run.text, original, 'Transport-owned source remains immutable');
    assert.match(elementText(html, 'reasoning'), /SYNTHETIC_PRIVATE_REASONING/, 'leaked reasoning stays in Thought');
  });
} finally {
  await server.close();
  restoreSsrDom();
}
