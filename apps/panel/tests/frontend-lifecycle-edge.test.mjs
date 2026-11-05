import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createServer } from 'vite';

const root = new URL('../', import.meta.url);
const pageSource = fs.readFileSync(new URL('../src/react/pages/chat/chat-page.tsx', import.meta.url), 'utf8');

function loadMerge() {
  const start = pageSource.indexOf('function mergeExpandedWindow');
  if (start < 0) throw new Error('mergeExpandedWindow missing from chat-page.tsx');
  let depth = 0;
  let seen = false;
  let end = start;
  for (let index = start; index < pageSource.length; index += 1) {
    const char = pageSource[index];
    if (char === '{') {
      depth += 1;
      seen = true;
    } else if (char === '}') {
      depth -= 1;
      if (seen && depth === 0) {
        end = index + 1;
        break;
      }
    }
  }
  const fn = pageSource.slice(start, end)
    .replace(/: Message\[\]/g, '')
    .replace(/: number/g, '')
    .replace(/: Message/g, '')
    .replace(/<number>/g, '');
  return new Function(`${fn}\nreturn mergeExpandedWindow;`)();
}

const mergeExpandedWindow = loadMerge();

function row(id, extra = {}) {
  return { id, role: id % 2 ? 'user' : 'assistant', content: `row ${id}`, timestamp: id, ...extra };
}

function sequence(ids, extraFor = () => ({})) {
  return ids.map((id) => row(id, extraFor(id)));
}

function idsOf(messages) {
  return messages.map((message) => message.id);
}

test('expanded window keeps the loaded row before a shifted 500-row page', () => {
  const previous = sequence(Array.from({ length: 501 }, (_, index) => index + 1));
  const incoming = sequence(Array.from({ length: 500 }, (_, index) => index + 3));
  const merged = mergeExpandedWindow(previous, incoming, 500);
  assert.equal(merged.some((message) => message.id === 2), true, 'id 2 sits before the incoming oldest boundary and must remain');
  assert.equal(merged.filter((message) => message.id === 2).length, 1);
  assert.equal(merged.at(-1).id, 502);
  assert.equal(merged[0].id, 1);
});

test('800 consecutive appends never open a hole behind the 500 cap', () => {
  let previous = sequence(Array.from({ length: 800 }, (_, index) => index + 1));
  for (let next = 801; next <= 1600; next += 1) {
    const incoming = sequence(Array.from({ length: 500 }, (_, index) => next - 499 + index));
    previous = mergeExpandedWindow(previous, incoming, 500);
    assert.equal(previous[0].id, 1, `append ${next} dropped the oldest loaded row`);
    assert.equal(previous.some((message) => message.id === 2), true, `append ${next} dropped id 2`);
    assert.equal(previous.at(-1).id, next, `append ${next} lost the new tail`);
    assert.equal(previous.length, next, `append ${next} changed length to ${previous.length}`);
    if (previous[1]?.id !== 2 || previous[next - 500]?.id !== next - 499) {
      assert.fail(`append ${next} opened a hole at ${previous[next - 500]?.id}, expected ${next - 499}`);
    }
  }
});

test('multi-line growth keeps every loaded row before the new oldest boundary', () => {
  const previous = sequence(Array.from({ length: 501 }, (_, index) => index + 1));
  const incoming = sequence(Array.from({ length: 500 }, (_, index) => index + 5));
  const merged = mergeExpandedWindow(previous, incoming, 500);
  assert.deepEqual(idsOf(merged).slice(0, 4), [1, 2, 3, 4]);
  assert.equal(merged.at(-1).id, 504);
});

test('overlap uses incoming content and does not retain a deleted overlap row', () => {
  const previous = sequence(Array.from({ length: 501 }, (_, index) => index + 1));
  previous[9] = row(10, { content: 'OLD_OVERLAP' });
  const incomingIds = Array.from({ length: 500 }, (_, index) => index + 3).filter((id) => id !== 4);
  incomingIds.push(503);
  const incoming = incomingIds.map((id) => row(id, id === 10 ? { content: 'NEW_OVERLAP' } : {}));
  const merged = mergeExpandedWindow(previous, incoming, 500);
  assert.equal(merged.find((message) => message.id === 10).content, 'NEW_OVERLAP');
  assert.equal(merged.some((message) => message.id === 4), false, 'deleted overlap id 4 must not be kept forever');
  assert.equal(merged.filter((message) => message.id === 3).length, 1, 'pagination boundary is not duplicated');
  assert.equal(merged.some((message) => message.id === 2), true);
});

test('sort contract keeps an earlier high id and does not treat id magnitude as order', () => {
  const previous = [row(7, { timestamp: 100, content: 'early-high-id' }), row(2, { timestamp: 2 })];
  for (let id = 8; id <= 506; id += 1) previous.push(row(id, { timestamp: id }));
  const incoming = previous.slice(2).concat([row(507)]);
  incoming[4] = row(12, { content: 'authoritative' });
  const deleted = incoming.findIndex((message) => message.id === 11);
  incoming.splice(deleted, 1);
  const merged = mergeExpandedWindow(previous, incoming, 500);
  assert.equal(merged.some((message) => message.id === 7 && message.content === 'early-high-id'), true);
  assert.equal(merged.some((message) => message.id === 2), true);
  assert.equal(merged.some((message) => message.id === 11), false);
  assert.equal(merged.find((message) => message.id === 12).content, 'authoritative');
});

test('a new oldest boundary absent from the loaded window keeps only strictly earlier timestamps', () => {
  const previous = sequence(Array.from({ length: 501 }, (_, index) => index + 1));
  previous[9] = row(10, { timestamp: undefined, content: 'no-timestamp-overlap' });
  const rest = sequence(Array.from({ length: 499 }, (_, index) => index + 3)).filter((message) => message.id !== 4 && message.id !== 10);
  const incoming = [row(900, { timestamp: 2.5, content: 'inserted-boundary' }), ...rest];
  const merged = mergeExpandedWindow(previous, incoming, 500);
  assert.deepEqual(idsOf(merged).slice(0, 3), [1, 2, 900]);
  assert.equal(merged.some((message) => message.id === 4), false);
  assert.equal(merged.some((message) => message.content === 'no-timestamp-overlap'), false);
  assert.equal(merged.filter((message) => message.id === 900).length, 1);
});

test('a page that already covers the loaded window still replaces it', () => {
  const previous = sequence([1, 2, 3]);
  const incoming = sequence([2, 3, 4]);
  assert.deepEqual(idsOf(mergeExpandedWindow(previous, incoming, 3)), [2, 3, 4]);
});

function installBrowserGlobals() {
  const storage = new Map();
  globalThis.document = {
    baseURI: 'http://fixture/',
    visibilityState: 'visible',
    addEventListener() {},
    removeEventListener() {},
  };
  globalThis.window = {
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  globalThis.CustomEvent ??= class extends Event {
    constructor(type, options) {
      super(type);
      this.detail = options?.detail;
    }
  };
}

async function waitUntil(predicate, label) {
  const started = Date.now();
  while (Date.now() - started < 4000) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`timed out waiting for ${label}`);
}

async function withRuns(loader) {
  installBrowserGlobals();
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    cacheDir: '/root/.hermes/cache/scratch/lifecycle-edge-fix/vite-runs',
  });
  try {
    await loader(server);
  } finally {
    await server.close();
  }
}

function sseResponse(frames) {
  const body = frames.join('');
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

async function feed(runs, frames, id) {
  const original = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes('/events')) return sseResponse(frames);
    if (url.includes('/v1/runs/')) return Response.json({ status: 'completed', run_id: `run-${id}`, session_id: id, created_at: 10 });
    return Response.json({ busy: false, run: null });
  };
  try {
    runs.observe(id, `run-${id}`, 10_000);
    await waitUntil(() => runs.get(id)?.status === 'completed', `${id} follow completion`);
    return runs.get(id);
  } finally {
    globalThis.fetch = original;
    runs.clear(id);
  }
}

test('bad JSON does not mark streamGone healthy; a valid frame does', async () => {
  await withRuns(async (server) => {
    for (const path of ['/src/react/lib/runs.ts', '/src/lib/runs.ts']) {
      const { runs } = await server.ssrLoadModule(path);
      const tag = path.includes('/react/') ? 'react' : 'lib';
      const bad = await feed(runs, ['data: {not-json}\n\n'], `${tag}-bad`);
      assert.notEqual(bad.streamGone, false, `${path} treated a bad data frame as a healthy stream`);
      const comment = await feed(runs, [': keep-alive\n\n'], `${tag}-comment`);
      assert.notEqual(comment.streamGone, false, `${path} treated a comment frame as data`);
      const valid = await feed(runs, ['data: {"seq":1}\n\n'], `${tag}-valid`);
      assert.equal(valid.streamGone, false, `${path} did not mark a parsed frame healthy`);
      const mixed = await feed(runs, ['data: {not-json}\n\n', 'data: {"seq":2}\n\n'], `${tag}-mixed`);
      assert.equal(mixed.streamGone, false, `${path} did not recover health after a later valid frame`);
    }
  });
});
