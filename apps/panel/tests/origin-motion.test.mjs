import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({ configFile: false, server: { middlewareMode: true }, appType: "custom" });
const { readAgent } = await server.ssrLoadModule("/src/react/shell/shell-events.ts");
const { reduceDock, EMPTY_DOCK } = await server.ssrLoadModule("/src/react/shell/dock-state.ts");
const { sheetFromOrigin, pickOrigin } = await server.ssrLoadModule("/src/react/shell/origin-motion.ts");

const origin = { x: 280, y: 700, width: 36, height: 20 };

test("view event keeps a finite button rect and drops a useless one", () => {
  const kept = readAgent(new CustomEvent("hp-view-subagent", {
    detail: { id: "agent-1", parentId: "parent-1", title: "ignored", origin },
  }));
  assert.deepEqual(kept, { id: "agent-1", parentId: "parent-1", origin });

  const dropped = readAgent(new CustomEvent("hp-view-subagent", {
    detail: { id: "agent-2", origin: { x: 1, y: 2, width: 0, height: 18 } },
  }));
  assert.equal(dropped.id, "agent-2");
  assert.equal(dropped.parentId, "");
  assert.equal(dropped.origin, null);

  assert.equal(readAgent(new CustomEvent("hp-view-subagent", { detail: { id: "" } })), null);
});

test("sheet motion centers on the button and stays visibly smaller than the card", () => {
  const resting = { x: 16, y: 24, width: 358, height: 692 };
  const button = { x: 300, y: 700, width: 48, height: 32 };
  const motion = sheetFromOrigin(resting, button);
  assert.ok(motion);
  assert.equal(motion.scale, 48 / 358);
  assert.equal(resting.x + resting.width / 2 + motion.x, button.x + button.width / 2);
  assert.equal(resting.y + resting.height / 2 + motion.y, button.y + button.height / 2);
  const tiny = sheetFromOrigin(resting, { x: 1, y: 2, width: 8, height: 8 });
  assert.equal(tiny.scale, 0.12);
  const wide = sheetFromOrigin(resting, { x: 0, y: 0, width: 300, height: 40 });
  assert.equal(wide.scale, 0.4);
  assert.equal(sheetFromOrigin(resting, { x: 0, y: 0, width: 0, height: 20 }), null);
  assert.deepEqual(pickOrigin([], button), button);
  assert.equal(pickOrigin([{ x: 9, y: 9, width: 10, height: 10 }], null).x, 9);
});
test("closing an agent dock keeps the button rect for the return trip", () => {
  const opened = reduceDock(EMPTY_DOCK, {
    kind: "agent", sessionId: "parent-1", agentId: "agent-1", key: "agent-1", origin,
  }, false);
  const leaving = reduceDock(opened, null, false);
  assert.equal(leaving.current, null);
  assert.deepEqual(leaving.leaving.origin, origin);
  assert.equal(leaving.pending, null);
});

try {
  await server.close();
} catch {
  // middleware server has no listen socket
}
