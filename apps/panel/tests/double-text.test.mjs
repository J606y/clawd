import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "double-text-"));
const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  cacheDir: path.join(owned, "vite"),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: "custom",
  logLevel: "error",
});

try {
  globalThis.document = { baseURI: "http://fixture/" };
  globalThis.window = {
    localStorage: { getItem: () => null, setItem: () => {} },
    setTimeout,
    clearTimeout,
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const { liveTextBesideHistory } = await server.ssrLoadModule("/src/lib/transcript.ts");
  const { runs, RUN_CHANGED } = await server.ssrLoadModule("/src/lib/runs.ts");

  test("history that already ends with the live reply suppresses the second copy", () => {
    assert.equal(liveTextBesideHistory("同一段回复", "前文\n\n同一段回复"), "");
    assert.equal(liveTextBesideHistory("  同一段回复  ", "同一段回复"), "");
    assert.equal(liveTextBesideHistory("还在长", "还在"), "还在长");
    assert.equal(liveTextBesideHistory("", "已有回复"), "");
  });

  test("clearing a finished run notifies listeners so the stale snapshot can drop", () => {
    const sid = "double-text";
    runs.runs.set(sid, { sessionId: sid, status: "completed", text: "同一段回复" });
    const seen = [];
    const onChange = (event) => { if (event.detail === sid) seen.push(event.detail); };
    runs.addEventListener(RUN_CHANGED, onChange);
    try {
      runs.clear(sid);
      assert.equal(runs.get(sid), undefined);
      assert.deepEqual(seen, [sid]);
      runs.clear(sid);
      assert.deepEqual(seen, [sid], "clearing an absent run must not notify again");
    } finally {
      runs.removeEventListener(RUN_CHANGED, onChange);
      runs.runs.delete(sid);
    }
  });

  test("clear does not drop a run that is still active", () => {
    const sid = "still-running";
    runs.runs.set(sid, { sessionId: sid, status: "running", text: "还在生成" });
    const seen = [];
    const onChange = (event) => { if (event.detail === sid) seen.push(event.detail); };
    runs.addEventListener(RUN_CHANGED, onChange);
    try {
      runs.clear(sid);
      assert.equal(runs.get(sid)?.status, "running");
      assert.deepEqual(seen, []);
    } finally {
      runs.removeEventListener(RUN_CHANGED, onChange);
      runs.runs.delete(sid);
    }
  });
} finally {
  await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
}
