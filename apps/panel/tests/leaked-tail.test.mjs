import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(tmpdir(), "leaked-tail-"));
const previous = {
  document: globalThis.document,
  window: globalThis.window,
  fetch: globalThis.fetch,
};
globalThis.fetch = () => { throw new Error("unexpected network"); };
globalThis.document = { baseURI: "https://panel.example.test/" };
globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  setTimeout, clearTimeout, addEventListener: () => {}, removeEventListener: () => {},
  dispatchEvent: () => true,
};
const server = await createServer({
  root,
  configFile: path.join(root, "vite.config.ts"),
  cacheDir: path.join(owned, "vite"),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: "custom",
});

function assertReleased(view, raw, label) {
  assert.equal(view.held, false, label);
  assert.equal(view.text, raw, label);
  assert.equal(view.reasoning, "", label);
}

function assertHeld(view, label) {
  assert.equal(view.held, true, label);
  assert.equal(view.text, "", label);
  assert.equal(view.reasoning, "", label);
}

try {
  const { projectLeakedTail, leakedTailSettled } = await server.ssrLoadModule("/src/react/pages/chat/leaked-tail.ts");
  const { splitLeakedThinking } = await server.ssrLoadModule("/src/react/lib/transcript.ts");

  await test("marker character boundaries stay withheld until they diverge or the run ends", () => {
    const marker = "THINKING:";
    for (let i = 1; i <= marker.length; i += 1) {
      const prefix = marker.slice(0, i);
      assertHeld(projectLeakedTail(prefix, false), prefix);
      assertReleased(projectLeakedTail(prefix, true), prefix, prefix + " ended");
      const last = prefix.at(-1);
      const diverged = prefix.slice(0, -1) + (last === "A" ? "B" : "A");
      if (!marker.startsWith(diverged)) assertReleased(projectLeakedTail(diverged, false), diverged, diverged);
    }
    for (const boundary of ["THINKING: ", "THINKING:\t", "THINKING:\r", "THINKING:\n", "THINKING:\r\n", "THINKING: \n", "THINKING:\t\r\n"]) {
      assertHeld(projectLeakedTail(boundary, false), JSON.stringify(boundary));
    }
    assertReleased(projectLeakedTail("THINKING:\rx", false), "THINKING:\rx", "cr without lf");
    assertReleased(projectLeakedTail("THINKING: a guide to clearer writing.", false), "THINKING: a guide to clearer writing.", "guide");
    assertReleased(projectLeakedTail("The public sentence stays visible.", false), "The public sentence stays visible.", "leading The");
    assertReleased(projectLeakedTail("Prefix stays. THINKING:\nnot a protocol", false), "Prefix stays. THINKING:\nnot a protocol", "middle");
  });

  await test("open content and close-marker splits stay held only while a legal frame is still possible", () => {
    const stem = "THINKING:\nSynthetic thought";
    for (const partial of [stem, stem + "\n", stem + "\r", stem + "\r\n", stem + "\n$", stem + "\r\n$", stem + "\n$]x", stem + "\n$]more"]) {
      assertHeld(projectLeakedTail(partial, false), JSON.stringify(partial));
      const ended = projectLeakedTail(partial, true);
      assert.equal(ended.held, false, JSON.stringify(partial));
      assert.equal(ended.text, partial, JSON.stringify(partial));
      assert.equal(ended.reasoning, "", JSON.stringify(partial));
    }
    for (const closed of [stem + "\n$]", stem + "\n$] ", stem + "\n$]\n", stem + "\r\n$]\r\n", stem + "\n$]\nAnswer."]) {
      const split = splitLeakedThinking(closed);
      const view = projectLeakedTail(closed, false);
      assert.equal(view.held, false, JSON.stringify(closed));
      assert.equal(view.reasoning, split.reasoning, JSON.stringify(closed));
      assert.equal(view.text, split.text, JSON.stringify(closed));
      assert.equal(view.reasoning, "Synthetic thought");
      assert.doesNotMatch(view.text, /Synthetic thought/);
    }
  });

  await test("invalid regex branches release immediately and valid frames keep the existing split", () => {
    const rejected = [
      "THINKING:\ncode `tick`",
      "THINKING:\n~~~",
      "THINKING:\n  ~~~fence",
      "THINKING:\n>",
      "THINKING:\n  > quoted",
      "THINKING:\nTHINKING:",
      "THINKING:\nthinking:",
      "THINKING:\n<pre>",
      "THINKING:\n</div>",
      "THINKING:\n<P",
      "THINKING:\ncode `tick` stays\n$]\nEDGE_TICK_BODY",
      "THINKING:\n> quoted\n$]\nAnswer.",
      "THINKING:\n~~~\n$]\nAnswer.",
      "THINKING:\nTHINKING:\nnested\n$]\nAnswer.",
      "THINKING:\n<pre>example\n$]\n</pre>",
      "```text\nTHINKING:\nexample\n$]\n```",
      "> THINKING:\n> quoted thought\n> $]\n正文",
      "THINKING: a guide to clearer writing.",
      "",
    ];
    for (const text of rejected) {
      const split = splitLeakedThinking(text);
      const view = projectLeakedTail(text, false);
      assert.deepEqual(split, { reasoning: "", text }, text);
      assert.equal(view.held, false, text);
      assert.equal(view.text, text, text);
      assert.equal(view.reasoning, "", text);
    }
    for (const still of ["THINKING:\n~~", "THINKING:\n<", "THINKING:\n</", "THINKING:\nTHINKIN", "THINKING:\n~"]) {
      assertHeld(projectLeakedTail(still, false), still);
    }
    const accepted = [
      "THINKING:\nSynthetic thought\n$]\n\n  Answer.\n",
      "THINKING:\r\nSynthetic thought\r\n$]\r\nAnswer.",
      "THINKING:\nSynthetic thought\n$]",
      "THINKING: \nSynthetic thought\n$]  \nAnswer.",
    ];
    for (const text of accepted) {
      const split = splitLeakedThinking(text);
      for (const ended of [false, true]) {
        const view = projectLeakedTail(text, ended);
        assert.equal(view.held, false, text);
        assert.equal(view.reasoning, split.reasoning, text);
        assert.equal(view.text, split.text, text);
      }
      assert.ok(split.reasoning.includes("Synthetic thought"), text);
    }
  });

  await test("terminal release, approval and pace do not change final classification or swallow open prose", () => {
    const open = "THINKING:\nEDGE_UNCLOSED_BODY never closes";
    assertHeld(projectLeakedTail(open, false), "running");
    for (const status of ["completed", "failed", "cancelled"]) {
      assert.equal(leakedTailSettled(status, false), true, status);
      assertReleased(projectLeakedTail(open, true), open, status);
    }
    for (const status of ["starting", "running", "paused", "waiting_for_approval", "queued", "stopping"]) {
      assert.equal(leakedTailSettled(status, false), false, status);
    }
    assert.equal(leakedTailSettled("running", true), true);
    assertReleased(projectLeakedTail(open, leakedTailSettled("running", true)), open, "stopping");
    assertHeld(projectLeakedTail(open, leakedTailSettled("running", false)), "approval still running");
    const emptyClose = "THINKING:\n   \n$]";
    assert.deepEqual(splitLeakedThinking(emptyClose), { reasoning: "", text: emptyClose });
    assertHeld(projectLeakedTail(emptyClose, false), "unstable empty close");
    assertReleased(projectLeakedTail(emptyClose, true), emptyClose, "ended empty close");
    assertReleased(projectLeakedTail(emptyClose + "\nanswer", false), emptyClose + "\nanswer", "stable empty close");
  });

  await test("displayed text never shrinks across marker splits and calls do not retain each other", () => {
    const samples = [
      "THINKING:\nSynthetic thought\n$]\nAnswer.",
      "THINKING: a guide to clearer writing.",
      "The public sentence stays visible.",
      "Prefix stays. THINKING:\nnot a protocol\n$]\nEDGE_MIDDLE",
      "THINKING:\ncode `tick` stays\n$]\nEDGE_TICK_BODY",
      "THINKING:\nEDGE_UNCLOSED_BODY never closes",
      "T",
      "THIN",
      "THINKING:\n",
    ];
    for (const raw of samples) {
      let shown = "";
      let acc = "";
      for (const ch of raw) {
        acc += ch;
        const next = projectLeakedTail(acc, false).text;
        assert.ok(next.startsWith(shown), JSON.stringify({ acc, shown, next }));
        shown = next;
      }
      const ended = projectLeakedTail(raw, true).text;
      assert.ok(ended.startsWith(shown), raw);
    }
    const huge = "T".repeat(20000);
    assertReleased(projectLeakedTail(huge, false), huge, "long non-prefix");
    const again = projectLeakedTail("THINKING:\nsecret\n$]\nanswer", false);
    assert.equal(again.reasoning, "secret");
    assert.equal(again.text, "answer");
    assert.equal(again.held, false);
    assert.equal(projectLeakedTail("TH", false).text, "");
  });
} finally {
  await server.close();
  await rm(owned, { recursive: true, force: true });
  globalThis.document = previous.document;
  globalThis.window = previous.window;
  globalThis.fetch = previous.fetch;
}
