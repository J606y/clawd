import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "vite";
import {
  childId,
  childIds,
  childOnlyPage,
  compressionPage,
  legacyPage,
  listed,
  mixedPage,
  parentId,
  parentPage,
  resumedIds,
} from "./fixtures/session-identity-bug.mjs";

// Node 26 直接 import transcript.ts 会按 .js 说明符找 attachments.js 而失败。
// 这与泛型约束无关；与其它面板测试一样由 vite 解析别名后再跑原断言。
const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/tmp", "session-identity-"));
const previous = {
  document: globalThis.document,
  window: globalThis.window,
  fetch: globalThis.fetch,
};
globalThis.document = { baseURI: "https://panel.example.test/" };
globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  setTimeout,
  clearTimeout,
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};
globalThis.fetch = async () => new Response("{}", {
  status: 500,
  headers: { "Content-Type": "application/json" },
});
let server;
try {
  server = await createServer({
    root,
    configFile: path.join(root, "vite.config.ts"),
    cacheDir: path.join(owned, "vite"),
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: "custom",
  });
  const transcript = await server.ssrLoadModule("/src/react/lib/transcript.ts");
  const logic = await server.ssrLoadModule("/src/react/shell/session-logic.ts");
  const {
    canAnchorHistory,
    confirmedForeignSession,
    projectSessionHistory,
    subagentTranscriptTakeover,
  } = transcript;
  const { resumeMainSession, resumeSelectsSubagent } = logic;

  await test("a known child page does not replace the main session, and a mixed page keeps the parent row", () => {
    assert.equal(subagentTranscriptTakeover(parentId, childOnlyPage, childIds), true);
    assert.equal(projectSessionHistory(parentId, childOnlyPage, childIds).blocked, true);
    const mixed = projectSessionHistory(parentId, mixedPage, childIds);
    assert.equal(mixed.blocked, false);
    assert.deepEqual(mixed.messages.map((row) => row.session_id), [parentId]);
    assert.equal(mixed.messages[0], mixedPage[0]);
    assert.equal(subagentTranscriptTakeover(parentId, compressionPage, childIds), false);
    const compression = projectSessionHistory(parentId, compressionPage, childIds);
    assert.equal(compression.messages.length, 2);
    assert.equal(compression.messages[0], compressionPage[0]);
    assert.equal(compression.messages[1], compressionPage[1]);
    assert.equal(canAnchorHistory(parentId, parentPage, childIds), true);
    assert.equal(canAnchorHistory(parentId, childOnlyPage, new Set()), false);
    assert.equal(canAnchorHistory(parentId, legacyPage, childIds), true);
  });

  await test("refresh recovery selects the listed main session instead of a later subagent", () => {
    const chosen = resumeMainSession([parentId, childId], listed);
    assert.equal(chosen?.id, parentId);
    assert.equal(resumeMainSession([childId], listed), null);
    assert.equal(resumeMainSession(["off-page-main"], listed), null);
    assert.equal(resumeSelectsSubagent("subagent"), true);
    assert.equal(resumeSelectsSubagent("api_server"), false);
    assert.deepEqual(resumedIds.at(-1), childId);
  });

  await test("a late child list reveals a takeover, and a late status cannot retarget the live owner", () => {
    const before = projectSessionHistory(parentId, childOnlyPage, new Set());
    assert.equal(before.blocked, false);
    assert.equal(canAnchorHistory(parentId, before.messages, new Set()), false);
    assert.equal(subagentTranscriptTakeover(parentId, before.messages, childIds), true);
    assert.equal(confirmedForeignSession(parentId, undefined), false);
    assert.equal(confirmedForeignSession(parentId, ""), false);
    assert.equal(confirmedForeignSession(parentId, parentId), false);
    assert.equal(confirmedForeignSession(parentId, childId), true);
  });
} finally {
  globalThis.document = previous.document;
  globalThis.window = previous.window;
  globalThis.fetch = previous.fetch;
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
}
