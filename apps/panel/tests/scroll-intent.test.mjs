import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
try {
  const { disclosureLeavesBottom } = await server.ssrLoadModule("/src/react/pages/chat/scroll-intent.ts");
  const bar = {
    matches: (selector) => selector.includes("button[aria-expanded]"),
    closest: (selector) => selector === ".subagents" ? {} : null,
  };
  const summary = { matches: (selector) => selector.startsWith("summary"), closest: () => null };

  test("subagent and task expand do not surface the jump-to-bottom control", () => {
    assert.equal(disclosureLeavesBottom([bar]), false);
    const taskBar = {
      matches: (selector) => selector.includes("button[aria-expanded]"),
      closest: (selector) => selector === ".task-list" ? {} : null,
    };
    assert.equal(disclosureLeavesBottom([taskBar]), false);
  });

  test("opening a message disclosure still leaves the bottom", () => {
    assert.equal(disclosureLeavesBottom([summary]), true);
  });

  const composerPopup = (marker) => ({
    matches: (selector) => selector.includes("button[aria-expanded]"),
    closest: (selector) => selector === marker ? {} : null,
  });

  test("model, effort and context popovers do not surface the jump-to-bottom control", () => {
    assert.equal(disclosureLeavesBottom([composerPopup(".model-picker")]), false);
    assert.equal(disclosureLeavesBottom([composerPopup(".effort-picker")]), false);
    assert.equal(disclosureLeavesBottom([composerPopup(".context-ring")]), false);
  });

  test("a message-list expanded button still leaves the bottom", () => {
    const row = {
      matches: (selector) => selector.includes("button[aria-expanded]"),
      closest: () => null,
    };
    assert.equal(disclosureLeavesBottom([row]), true);
  });
} finally {
  await server.close();
}
