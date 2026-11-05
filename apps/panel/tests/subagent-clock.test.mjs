import assert from "node:assert/strict";
import test from "node:test";
import { clockAt, holdClock } from "../src/react/lib/subagent-clock.ts";

const sample = (now, toolCalls = 1) => ({ now, toolCalls });

test("running clock advances one second per client second and ignores a stale sample", () => {
  let held = holdClock(null, "sess", sample(1000), 1000);
  const series = [0, 1, 2, 3].map((step) => clockAt(held, 1000 + step) - 990);
  assert.deepEqual(series, [10, 11, 12, 13]);
  held = holdClock(held, "sess", sample(1001, 8), 1003);
  assert.equal(held.view.toolCalls, 1);
  assert.equal(clockAt(held, 1003) - 990, 13);
  held = holdClock(held, "sess", sample(1003, 4), 1003);
  assert.equal(held.view.toolCalls, 4);
  assert.equal(clockAt(held, 1004) - 990, 14);
});

test("an older server timestamp cannot replace a newer snapshot or restart a finished run", () => {
  let held = holdClock(null, "sess", { now: 1100, status: "done", endedAt: 1012 }, 1100);
  held = holdClock(held, "sess", { now: 1099, status: "running", endedAt: null }, 1104);
  assert.equal(held.view.status, "done");
  assert.equal(held.view.endedAt, 1012);
  assert.equal(clockAt(held, 1104), 1104);
});

test("scope change and a new run do not inherit the previous anchor", () => {
  const left = holdClock(null, "a", sample(2000), 2000);
  const right = holdClock(left, "b", sample(2001), 2005);
  assert.equal(clockAt(right, 2005), 2001);
  const next = holdClock(right, "b", { now: 2006, id: "new" }, 2006);
  assert.equal(next.view.id, "new");
  assert.equal(clockAt(next, 2006), 2006);
});

test("the same view keeps extrapolating, and a backward client clock is not clamped", () => {
  const held = holdClock(null, "sess", sample(3000), 3000);
  const again = holdClock(held, "sess", held.view, 3002);
  assert.equal(again, held);
  assert.equal(clockAt(again, 2997), 2997);
});

test("a sample within the local round trip is accepted and does not jump two seconds", () => {
  const held = holdClock(null, "sess", sample(4000), 4000);
  const next = holdClock(held, "sess", sample(4004.05, 3), 4004.1);
  assert.equal(next.view.toolCalls, 3);
  assert.ok(Math.abs(clockAt(next, 4005.1) - 3990 - 15.05) < 1e-9);
});
