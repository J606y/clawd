import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const owned = await mkdtemp(path.join(process.env.TMPDIR || '/root/hermes-workspace', 'md-incremental-test-'));
const fixture = {
  name: 'md-incremental-fixture',
  transform(code, id) {
    if (!id.replaceAll('\\', '/').endsWith('/src/lib/markdown.ts')) return null;
    const needle = 'tokens = marked.lexer(src, marked.defaults);';
    if (!code.includes(needle)) return null;
    return code.replace(needle, 'globalThis.__fullLex && globalThis.__fullLex.push(src); tokens = marked.lexer(src, marked.defaults);');
  },
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url !== '/md-incremental-fixture') return next();
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><html><meta charset="utf-8"><body><script type="module">
        import * as md from '/src/lib/markdown.ts';
        window.md = md;
        window.__fullLex = [];
        window.ready = true;
      </script></body></html>`);
    });
  }
};

const server = await createServer({
  configFile: false,
  cacheDir: path.join(owned, 'vite'),
  plugins: [fixture],
  logLevel: 'error',
  server: {
    host: '127.0.0.1',
    port: 0,
    hmr: false,
    watch: null
  }
});

let browser, socket;
let sequence = 0;
const pending = new Map();

try {
  await server.listen();
  browser = spawn('/usr/bin/chromium', [
    '--headless',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--remote-debugging-port=0',
    `--user-data-dir=${path.join(owned, 'profile')}`,
    'about:blank'
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  const ws = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Chromium startup timeout')), 15000);
    browser.stderr.on('data', chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.once('error', e => { clearTimeout(timer); reject(e); });
  });

  socket = new WebSocket(ws);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  socket.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    const p = pending.get(m.id);
    if (p) {
      pending.delete(m.id);
      clearTimeout(p.timer);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  });

  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(method + ' timeout'));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });

  const disallowedRequests = [];
  const allowedOrigin = `http://127.0.0.1:${server.httpServer.address().port}`;

  await call('Fetch.enable', {
    patterns: [{ urlPattern: '*' }]
  }, sessionId);

  socket.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    if (m.method === 'Fetch.requestPaused' && m.params) {
      const { requestId, request } = m.params;
      const reqUrl = request.url;
      if (reqUrl.startsWith(allowedOrigin) || reqUrl === 'about:blank') {
        call('Fetch.continueRequest', { requestId }, sessionId).catch(() => {});
      } else {
        disallowedRequests.push(reqUrl);
        call('Fetch.failRequest', { requestId, errorReason: 'AccessDenied' }, sessionId).catch(() => {});
      }
    }
  });

  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  await call('Page.navigate', { url: `http://127.0.0.1:${server.httpServer.address().port}/md-incremental-fixture` }, sessionId);
  for (let n = 0; n < 100; n++) {
    if (await evaluate('window.ready')) break;
    await new Promise(r => setTimeout(r, 50));
  }

  console.log('1. Verify export and default lexer fallback');
  const exportCheck = await evaluate(`(() => {
    const s = typeof window.md.createMarkdownStreamState === 'function';
    const blocks = window.md.renderMarkdownBlocks('Hello world\\n\\nSecond paragraph');
    return { s, count: blocks.length, first: blocks[0].raw, space: blocks[1].raw, second: blocks[2].raw };
  })()`);
  assert.equal(exportCheck.s, true);
  assert.equal(exportCheck.count, 3);
  assert.equal(exportCheck.first, 'Hello world');
  assert.equal(exportCheck.space, '\n\n');
  assert.equal(exportCheck.second, 'Second paragraph');
  console.log('PASS export and default lexer fallback');

  console.log('2. Running incremental vs full lexer corpus dual-verification with deterministic PRNG...');
  const dualCheckResult = await evaluate(`(() => {
    const CORPUS_CASES = [
      {
        name: 'paragraphs and breaks',
        text: 'First line\\nSecond line in same para\\n\\nSecond paragraph\\n\\nThird paragraph',
      },
      {
        name: 'atx headings',
        text: '# Heading 1\\n\\n## Heading 2\\n\\nContent under heading',
      },
      {
        name: 'setext headings',
        text: 'Heading One\\n===\\n\\nHeading Two\\n---\\n\\nParagraph text',
      },
      {
        name: 'gfm tables',
        text: '| Col A | Col B |\\n|---|---|\\n| 1 | 2 |\\n| 3 | 4 |\\n\\nAfter table paragraph',
      },
      {
        name: 'lists and continuations',
        text: '- item 1\\n- item 2\\n\\n  nested continuation\\n- item 3\\n\\n1. ordered 1\\n2. ordered 2\\n\\nAfter list',
      },
      {
        name: 'blockquotes',
        text: '> Line 1\\n> Line 2\\n\\n> Nested quote\\n\\nAfter quote',
      },
      {
        name: 'fenced code blocks',
        text: '\`\`\`typescript\\nconst x: number = 42;\\nconsole.log(x);\\n\`\`\`\\n\\n~~~bash\\necho \"hello\"\\n~~~\\n\\nAfter code',
      },
      {
        name: 'mixed unclosed and closed code blocks',
        text: 'Lead paragraph\\n\\n\`\`\`python\\ndef test():\\n    return True\\n\`\`\`\\n\\n\`\`\`javascript\\nconst stream = true;\\n',
      },
      {
        name: 'reference links definition at end',
        text: 'Here is a [link][ref] to something.\\n\\n[ref]: https://example.com/test\\n\\nTrailing paragraph',
      },
      {
        name: 'raw html block',
        text: 'Leading text\\n\\n<details><summary>Details</summary>Body content</details>\\n\\nTrailing text',
      },
      {
        name: 'cjk mixed with emoji and symbols',
        text: '### 🚀 架构设计\\n\\n这是包含**中英混排**与 \`code\` 的段落。\\n\\n- 第一项：测试 emoji 🎉\\n- 第二项：保证 HTML 实体转义安全 & 准确\\n\\n> 引用块：Hermes Panel 流式测试 100% 对拍。',
      },
      {
        name: 'long prefix 4+ tokens before new quote list and html',
        text: 'Paragraph one established with ample text.\\n\\nParagraph two is also solid.\\n\\nParagraph three completes the initial stable base.\\n\\nParagraph four ensures more than 4 tokens ahead.\\n\\n> Here is an appended blockquote after 4 stable tokens\\n\\n- Item A in new list\\n- Item B in new list\\n\\n<div class=\"raw-block\">Safe HTML insert</div>\\n\\nTrailing concluding paragraph.',
      },
    ];

    function createPrng(seed = 123456789) {
      let s = seed >>> 0;
      return () => {
        s = (Math.imul(1664525, s) + 1013904223) >>> 0;
        return s / 4294967296;
      };
    }

    // Strip spans from code blocks for semantic equivalence comparison
    function normalizeSemanticHtml(html) {
      return html
        .replace(/<span\\b[^>]*>/g, '')
        .replace(/<\\/span>/g, '')
        .replace(/&#39;/g, \"'\")
        .replace(/&#34;/g, '\"');
    }

    const errors = [];
    for (const item of CORPUS_CASES) {
      const prng = createPrng(42);
      const state = window.md.createMarkdownStreamState();
      let previous = [];
      let currentSrc = '';
      let idx = 0;

      while (idx < item.text.length) {
        const chunkSize = 1 + Math.floor(prng() * 9);
        const nextIdx = Math.min(item.text.length, idx + chunkSize);
        currentSrc = item.text.slice(0, nextIdx);
        idx = nextIdx;

        // Incremental render
        const incrementalBlocks = window.md.renderMarkdownBlocks(currentSrc, previous, state);
        // Full lexer render (no state)
        const fullBlocks = window.md.renderMarkdownBlocks(currentSrc, []);

        if (incrementalBlocks.length !== fullBlocks.length) {
          errors.push('Block count mismatch in ' + item.name + ' at len=' + currentSrc.length + ': ' + incrementalBlocks.length + ' vs ' + fullBlocks.length);
          break;
        }

        let rawMismatch = false;
        for (let b = 0; b < fullBlocks.length; b++) {
          if (incrementalBlocks[b].raw !== fullBlocks[b].raw) {
            errors.push('Block raw mismatch in ' + item.name + ' at block ' + b + ' len=' + currentSrc.length);
            rawMismatch = true;
            break;
          }
        }
        if (rawMismatch) break;

        const incRaw = incrementalBlocks.map(b => b.raw).join('');
        const fullRaw = fullBlocks.map(b => b.raw).join('');
        if (incRaw !== fullRaw) {
          errors.push('Combined raw mismatch between incremental and full in ' + item.name + ' at len=' + currentSrc.length);
          break;
        }

        // Compare semantic HTML
        const incHtml = normalizeSemanticHtml(incrementalBlocks.map(b => b.html).join(''));
        const fullHtml = normalizeSemanticHtml(fullBlocks.map(b => b.html).join(''));
        if (incHtml !== fullHtml) {
          errors.push('HTML mismatch in ' + item.name + ' at len=' + currentSrc.length + ':\\nINC: ' + incHtml + '\\nFULL: ' + fullHtml);
          break;
        }

        previous = incrementalBlocks;
      }
    }

    return { ok: errors.length === 0, errors };
  })()`);

  if (!dualCheckResult.ok) {
    console.error('Dual check failures:', dualCheckResult.errors);
  }
  assert.equal(dualCheckResult.ok, true, 'Corpus dual check must have 0 errors');
  console.log('PASS Corpus dual check with deterministic random slicing');

  console.log('3. Verify block object reference preservation');
  const refCheck = await evaluate(`(() => {
    const state = window.md.createMarkdownStreamState();
    let src1 = 'Paragraph 1\\n\\nParagraph 2\\n\\nParagraph 3';
    let b1 = window.md.renderMarkdownBlocks(src1, [], state);

    let src2 = src1 + ' extended';
    let b2 = window.md.renderMarkdownBlocks(src2, b1, state);

    return {
      p1Same: b2[0] === b1[0],
      spaceSame: b2[1] === b1[1],
      p2Same: b2[2] === b1[2],
      p3Changed: b2[4] !== b1[4]
    };
  })()`);
  assert.deepEqual(refCheck, { p1Same: true, spaceSame: true, p2Same: true, p3Changed: true });
  console.log('PASS block object reference preservation');

  console.log('4. Verify conservative fallbacks (ref def, html, non-prefix)');
  const fallbackCheck = await evaluate(`(() => {
    const state1 = window.md.createMarkdownStreamState();
    const src1 = 'Paragraph 1\\n\\n[example]\\n\\n';
    const b1 = window.md.renderMarkdownBlocks(src1, [], state1);
    const src2 = src1 + '[example]: https://hermes.agent\\n\\nMore text';
    const b2 = window.md.renderMarkdownBlocks(src2, b1, state1);
    const f2 = window.md.renderMarkdownBlocks(src2, []);
    const refDefOk = b2.length === f2.length && b2.map(b => b.raw).join('') === src2;

    const state2 = window.md.createMarkdownStreamState();
    const srcHtml1 = 'Paragraph 1\\n\\n';
    const bh1 = window.md.renderMarkdownBlocks(srcHtml1, [], state2);
    const srcHtml2 = srcHtml1 + '<div>raw HTML</div>\\n\\nTrailing';
    const bh2 = window.md.renderMarkdownBlocks(srcHtml2, bh1, state2);
    const fh2 = window.md.renderMarkdownBlocks(srcHtml2, []);
    const htmlOk = bh2.length === fh2.length && bh2[0].html === fh2[0].html;

    const state3 = window.md.createMarkdownStreamState();
    const srcPre1 = 'Paragraph 1\\n\\nParagraph 2';
    const bp1 = window.md.renderMarkdownBlocks(srcPre1, [], state3);
    const srcPre2 = 'Prepended\\n\\n' + srcPre1;
    const bp2 = window.md.renderMarkdownBlocks(srcPre2, bp1, state3);
    const fp2 = window.md.renderMarkdownBlocks(srcPre2, []);
    const nonPrefixOk = bp2.length === fp2.length && bp2.map(b => b.raw).join('') === srcPre2;

    return { refDefOk, htmlOk, nonPrefixOk };
  })()`);
  assert.deepEqual(fallbackCheck, { refDefOk: true, htmlOk: true, nonPrefixOk: true });
  console.log('PASS conservative fallback verification');

  console.log('5. Single-segment pure append must not full-lex the seen prefix');
  const appendCheck = await evaluate(`(() => {
    const prefix = 'Hello';
    const next = 'Hello world';
    const state = window.md.createMarkdownStreamState();
    window.__fullLex.length = 0;
    const firstBlocks = window.md.renderMarkdownBlocks(prefix, [], state);
    const first = window.__fullLex.slice();
    window.__fullLex.length = 0;
    const second = window.md.renderMarkdownBlocks(next, firstBlocks, state);
    const during = window.__fullLex.slice();
    window.__fullLex.length = 0;
    const thirdSrc = next + ' again';
    const third = window.md.renderMarkdownBlocks(thirdSrc, second, state);
    const duringThird = window.__fullLex.slice();
    const html = second.map((block) => block.html).join('');
    const once = window.md.renderMarkdownBlocks(next, []);
    const onceHtml = once.map((block) => block.html).join('');
    const fullHtml = window.md.renderMarkdown(next, false);
    const thirdHtml = third.map((block) => block.html).join('');
    const thirdOnce = window.md.renderMarkdownBlocks(thirdSrc, []).map((block) => block.html).join('');
    const unsafe = html.includes('<script') || html.includes('<img') || html.includes('onerror') || html.includes('javascript:');
    return { first, during, duringThird, html, onceHtml, fullHtml, unsafe, raw: second.map((block) => block.raw).join(''), thirdHtml, thirdOnce };
  })()`);
  assert.ok(appendCheck.first.includes('Hello'), 'spy must observe the initial full lex: ' + JSON.stringify(appendCheck.first));
  assert.equal(
    appendCheck.during.some((src) => src.startsWith('Hello')),
    false,
    'second append must not full-lex the complete prefix: ' + JSON.stringify(appendCheck.during)
  );
  assert.equal(appendCheck.html, appendCheck.onceHtml);
  assert.equal(appendCheck.html, appendCheck.fullHtml);
  assert.equal(appendCheck.unsafe, false);
  assert.equal(appendCheck.raw, 'Hello world');
  assert.equal(appendCheck.duringThird.some((src) => src.startsWith('Hello world')), false, 'later append must not full-lex the grown prefix: ' + JSON.stringify(appendCheck.duringThird));
  assert.equal(appendCheck.thirdHtml, appendCheck.thirdOnce);
  console.log('PASS single-segment pure append skips full prefix lexer');

  assert.equal(disallowedRequests.length, 0, `Forbidden external requests detected: ${JSON.stringify(disallowedRequests)}`);

} finally {
  for (const [, p] of pending) {
    clearTimeout(p.timer);
    p.reject(new Error('Teardown cleanup'));
  }
  pending.clear();
  if (socket) socket.close();
  if (browser) {
    if (browser.exitCode === null && !browser.killed) {
      browser.kill('SIGKILL');
      await Promise.race([
        new Promise(r => browser.once('exit', r)),
        new Promise(r => setTimeout(r, 2000))
      ]);
    }
  }
  await server.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
