// 子代理分页边界。本次只准备，不运行。不是验收通过。
// 运行前需要能加载 TypeScript 的测试器，并先有 document.baseURI。不要指向生产 home。

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const globals = Object.fromEntries(["document", "window", "fetch"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
Object.defineProperty(globalThis, "document", { configurable: true, value: { baseURI: "https://panel.example.test/" } });
Object.defineProperty(globalThis, "window", { configurable: true, value: browser });

const {
  collectSubagentPages,
  sidecar,
  SUBAGENT_MAX_ATTEMPTS,
  SUBAGENT_MAX_CHILDREN,
  SUBAGENT_SERVER_MAX,
} = await import("../src/react/lib/sidecar.ts");

function row(id, extra = {}) {
  return {
    id,
    parentId: "parent-1",
    parentTitle: null,
    title: id,
    model: null,
    startedAt: 1,
    endedAt: 10,
    endReason: "agent_close",
    status: "done",
    messages: 1,
    toolCalls: 0,
    apiCalls: 0,
    tokens: 0,
    lastActivityAt: 10,
    lastTool: null,
    snippet: "",
    executionStatus: "returned",
    ...extra,
  };
}

function page({ total, limit, offset, rows, running = 0, stalled = 0, now = 100, profileKey }) {
  const view = {
    data: rows.map((item) => (typeof item === "string" ? row(item) : item)),
    running,
    stalled,
    total,
    now,
    limit,
    offset,
  };
  if (profileKey !== undefined) {
    view.profileKey = profileKey;
    view.data = view.data.map((item) => ({ ...item, profileKey }));
  }
  return view;
}

function ids(view) {
  return view.data.map((item) => item.id);
}

test("source: useSubagents reuses the collector and does not add a poll", async () => {
  const queries = await readFile(path.join(root, "src/react/lib/queries.ts"), "utf8");
  const sidecarText = await readFile(path.join(root, "src/react/lib/sidecar.ts"), "utf8");
  const chat = await readFile(path.join(root, "src/react/pages/chat/chat-page.tsx"), "utf8");
  const dock = await readFile(path.join(root, "src/react/docks/agent-dock.tsx"), "utf8");
  assert.match(queries, /queryFn: \(\{ signal \}\) => sidecar\.collectSubagents\(sessionId, limit, signal\)/);
  assert.match(queries, /refetchInterval,/);
  assert.equal(queries.includes("setInterval"), false);
  assert.match(queries, /export function useModelsAdmin/);
  assert.match(queries, /export function useMcpServers/);
  assert.match(queries, /export function useSettings/);
  assert.match(sidecarText, /api\/runtime\/subagents/);
  assert.match(sidecarText, /params\.set\("offset", String\(offset\)\)/);
  assert.match(sidecarText, /models: \(\) => request<ModelsView>/);
  assert.match(sidecarText, /mcp: \(\) => request/);
  assert.match(sidecarText, /settings: \(\) => request<SettingsView>/);
  assert.match(chat, /useSubagents\(\s*sessionId,\s*50,/);
  assert.match(dock, /sidecar\.subagents\(sessionId, 30\)/);
});

test("51st child is included and returned does not become completed", async () => {
  const calls = [];
  const rows = Array.from({ length: 51 }, (_, index) => row(`child-${index + 1}`, { profileKey: "alpha" }));
  const view = await collectSubagentPages(async (offset, limit) => {
    calls.push([offset, limit]);
    return page({
      total: 51,
      limit,
      offset,
      rows: rows.slice(offset, offset + limit),
      running: 1,
      stalled: 0,
      now: 20 + offset,
      profileKey: "alpha",
    });
  }, 50);
  assert.deepEqual(calls, [[0, 50], [50, 50]]);
  assert.equal(view.data.length, 51);
  assert.equal(view.data[50].id, "child-51");
  assert.equal(view.data[50].executionStatus, "returned");
  assert.equal(view.data[50].status, "done");
  assert.equal(view.profileKey, "alpha");
  assert.equal(view.total, 51);
  assert.equal(view.running, 1);
  assert.equal(view.now, 70);
  assert.equal(view.limit, 50);
  assert.equal(view.offset, 0);
  assert.equal(new Set(ids(view)).size, 51);
});

test("a full first page is not followed by an extra request", async () => {
  const calls = [];
  const view = await collectSubagentPages(async (offset, limit) => {
    calls.push(offset);
    return page({ total: 50, limit, offset, rows: Array.from({ length: 50 }, (_, index) => `id-${index}`), profileKey: "alpha" });
  }, 50);
  assert.deepEqual(calls, [0]);
  assert.equal(view.data.length, 50);
});

test("server-proven default is kept and an omitted profile is not invented", async () => {
  const proven = await collectSubagentPages(async (offset, limit) => page({
    total: 1,
    limit,
    offset,
    rows: ["only"],
    profileKey: "default",
  }), 50);
  assert.equal(proven.profileKey, "default");
  assert.equal(proven.data[0].profileKey, "default");
  const omitted = await collectSubagentPages(async (offset, limit) => page({
    total: 1,
    limit,
    offset,
    rows: [row("only")],
  }), 50);
  assert.equal("profileKey" in omitted, false);
  assert.equal("profileKey" in omitted.data[0], false);
  assert.equal(omitted.data[0].executionStatus, "returned");
  assert.equal(omitted.data[0].status, "done");
});

test("disagreeing or partial profile proof is stripped so projection cannot recover a key", async () => {
  let offsetSeen = -1;
  const view = await collectSubagentPages(async (offset, limit) => {
    offsetSeen = offset;
    const key = offset === 0 ? "alpha" : "beta";
    return page({ total: 2, limit, offset, rows: [row(`id-${offset}`, { profileKey: key })], profileKey: key });
  }, 1);
  assert.equal(offsetSeen, 1);
  assert.deepEqual(ids(view), ["id-0", "id-1"]);
  assert.equal("profileKey" in view, false);
  assert.equal(view.data.every((item) => !("profileKey" in item)), true);
  assert.equal(view.data[1].executionStatus, "returned");
  assert.equal(view.data[1].status, "done");
});

test("empty complete snapshot is success, not a failure wipe", async () => {
  const calls = [];
  const view = await collectSubagentPages(async (offset, limit) => {
    calls.push(offset);
    return page({ total: 0, limit, offset, rows: [], running: 0, profileKey: "alpha" });
  }, 12);
  assert.deepEqual(calls, [0]);
  assert.deepEqual(view.data, []);
  assert.equal(view.total, 0);
  assert.equal(view.profileKey, "alpha");
});

test("total change restarts the walk and does not publish the first partial page", async () => {
  let raced = false;
  const calls = [];
  const view = await collectSubagentPages(async (offset, limit) => {
    calls.push([offset, limit]);
    if (!raced) {
      if (offset === 0) return page({ total: 2, limit, offset, rows: [row("stale")], profileKey: "alpha" });
      raced = true;
      return page({ total: 9, limit, offset, rows: [row("shifted")], profileKey: "alpha" });
    }
    if (offset === 0) return page({ total: 2, limit, offset, rows: [row("a")], running: 1, now: 5, profileKey: "alpha" });
    return page({ total: 2, limit, offset, rows: [row("b")], running: 1, now: 6, profileKey: "alpha" });
  }, 1);
  assert.deepEqual(ids(view), ["a", "b"]);
  assert.equal(view.total, 2);
  assert.equal(ids(view).includes("stale"), false);
  assert.equal(calls.length, 4);
});

test("duplicate page and empty page stay bounded and reject instead of returning a partial list", async () => {
  const duplicateCalls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    duplicateCalls.push(offset);
    return page({ total: 2, limit, offset, rows: [row("same")], profileKey: "alpha" });
  }, 1), (error) => error.code === "subagents_incomplete" && !Array.isArray(error.data));
  assert.equal(duplicateCalls.length, SUBAGENT_MAX_ATTEMPTS * 2);

  const emptyCalls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    emptyCalls.push(offset);
    if (offset === 0) return page({ total: 2, limit, offset, rows: [row("only")], profileKey: "alpha" });
    return page({ total: 2, limit, offset, rows: [], profileKey: "alpha" });
  }, 1), (error) => error.code === "subagents_incomplete");
  assert.equal(emptyCalls.length, SUBAGENT_MAX_ATTEMPTS * 2);
});

test("mid-walk failure rejects immediately and does not resolve an empty or partial view", async () => {
  const calls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    calls.push(offset);
    if (offset === 0) return page({ total: 2, limit, offset, rows: [row("kept")], profileKey: "alpha" });
    throw new Error("page-2-down");
  }, 1), (error) => error.message === "page-2-down" && !Array.isArray(error.data));
  assert.deepEqual(calls, [0, 1]);
});

test("invalid limit does not fetch, and a limit above the server max is clamped", async () => {
  let calls = 0;
  const fetchPage = async () => {
    calls += 1;
    throw new Error("should not fetch");
  };
  for (const limit of [0, -1, 1.5, Number.NaN]) {
    await assert.rejects(() => collectSubagentPages(fetchPage, limit), (error) => error.code === "subagents_invalid");
  }
  assert.equal(calls, 0);
  const seen = [];
  const view = await collectSubagentPages(async (offset, limit) => {
    seen.push([offset, limit]);
    const rows = Array.from({ length: Math.min(limit, 60 - offset) }, (_, index) => `n-${offset + index}`);
    return page({ total: 60, limit, offset, rows, profileKey: "alpha" });
  }, 80);
  assert.equal(SUBAGENT_SERVER_MAX, 50);
  assert.deepEqual(seen, [[0, 50], [50, 50]]);
  assert.equal(view.data.length, 60);
  assert.equal(view.data[50].id, "n-50");
});

test("total above the child bound fails after one page and does not walk the excess", async () => {
  const calls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    calls.push(offset);
    return page({ total: SUBAGENT_MAX_CHILDREN + 1, limit, offset, rows: [row("a")], profileKey: "alpha" });
  }, 50), (error) => error.code === "subagents_incomplete" && /不能把部分结果当作全部/.test(error.message));
  assert.deepEqual(calls, [0]);
});

test("missing identity and a mismatched echoed offset are contract failures, not a deduped success", async () => {
  const missing = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    missing.push(offset);
    return page({ total: 1, limit, offset, rows: [{ ...row("x"), id: "" }] });
  }, 50), (error) => error.code === "subagents_unavailable");
  assert.deepEqual(missing, [0]);

  const echoed = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    echoed.push(offset);
    const view = page({ total: 2, limit, offset, rows: [row(offset === 0 ? "a" : "b")], profileKey: "alpha" });
    if (offset === 1) view.offset = 0;
    return view;
  }, 1), (error) => error.code === "subagents_unavailable");
  assert.deepEqual(echoed, [0, 1]);
});

test("same delegationId is not an identity, and a repeated child id is not published as complete", async () => {
  const view = await collectSubagentPages(async (offset, limit) => page({
    total: 2,
    limit,
    offset,
    rows: [
      row("child-a", { delegationId: "same-delegation" }),
      row("child-b", { delegationId: "same-delegation" }),
    ],
    profileKey: "alpha",
  }), 50);
  assert.deepEqual(ids(view), ["child-a", "child-b"]);
  const calls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    calls.push(offset);
    return page({ total: 2, limit, offset, rows: [row("same"), row("same")], profileKey: "alpha" });
  }, 50), (error) => error.code === "subagents_incomplete");
  assert.equal(calls.length, SUBAGENT_MAX_ATTEMPTS);
});

test("an aborted walk does not continue and does not resolve a partial page", async () => {
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  await assert.rejects(collectSubagentPages(async () => {
    calls += 1;
    throw new Error("fetched after abort");
  }, 50, controller.signal));
  assert.equal(calls, 0);

  const mid = new AbortController();
  const midCalls = [];
  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    midCalls.push(offset);
    mid.abort();
    return page({ total: 2, limit, offset, rows: [row("a")], profileKey: "alpha" });
  }, 1, mid.signal));
  assert.deepEqual(midCalls, [0]);
});

test("wire: collectSubagents sends parent, limit and offset and does not stop at 50", async () => {
  const urls = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(input);
    urls.push(url);
    assert.equal(init.credentials, "same-origin");
    assert.equal(url.pathname, "/panel-api/api/runtime/subagents");
    const offset = Number(url.searchParams.get("offset") || "0");
    const limit = Number(url.searchParams.get("limit"));
    assert.equal(url.searchParams.get("parent"), "parent 1");
    const start = offset;
    const rows = Array.from({ length: Math.min(limit, 51 - start) }, (_, index) => row(`wire-${start + index}`, { profileKey: "alpha" }));
    return new Response(JSON.stringify(page({
      total: 51,
      limit,
      offset,
      rows,
      running: 2,
      now: 30 + offset,
      profileKey: "alpha",
    })), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const view = await sidecar.collectSubagents("parent 1", 50);
  assert.equal(urls.length, 2);
  assert.equal(urls[0].searchParams.get("offset"), null);
  assert.equal(urls[0].searchParams.get("limit"), "50");
  assert.equal(urls[1].searchParams.get("offset"), "50");
  assert.equal(urls[1].searchParams.get("limit"), "50");
  assert.equal(view.data.length, 51);
  assert.equal(view.data[50].id, "wire-50");
  assert.equal(view.data[50].executionStatus, "returned");
  assert.equal(view.data[50].status, "done");
  assert.equal(view.profileKey, "alpha");
  assert.equal(view.running, 2);
  assert.equal(view.total, 51);
});

test("wire: a failed later page rejects and is not an empty success payload", async () => {
  let count = 0;
  globalThis.fetch = async (input) => {
    count += 1;
    const url = new URL(input);
    if (Number(url.searchParams.get("offset") || "0") === 0) {
      return new Response(JSON.stringify(page({
        total: 2,
        limit: 1,
        offset: 0,
        rows: [row("first")],
        profileKey: "alpha",
      })), { status: 200 });
    }
    return new Response(JSON.stringify({ error: { message: "子代理状态暂不可用", code: "subagents_unavailable" } }), { status: 503 });
  };
  await assert.rejects(() => sidecar.collectSubagents("parent-1", 1), (error) => {
    assert.equal(Array.isArray(error.data), false);
    assert.notEqual(error.data, []);
    return true;
  });
  assert.equal(count, 2);
});
