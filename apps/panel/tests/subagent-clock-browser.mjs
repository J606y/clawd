import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "subagent-clock-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
const failures = [];
let sessionSeq = 0;

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
};

const secondsOf = (text) => {
  const matched = /(\d+)\s*秒/.exec(text || "");
  return matched ? Number(matched[1]) : null;
};

const agent = (patch) => ({
  id: "agent-1",
  parentId: "sess-a",
  parentTitle: null,
  title: "合成子任务",
  model: null,
  startedAt: 1_700_000_000,
  endedAt: null,
  endReason: null,
  status: "running",
  messages: 1,
  toolCalls: 1,
  apiCalls: 1,
  tokens: 1,
  lastActivityAt: 1_700_000_000,
  lastTool: null,
  snippet: "",
  ...patch,
});

const view = (now, items, running = items.filter((item) => item.status === "running").length) => ({
  data: items,
  running,
  now,
});

try {
  server = await createServer({
    configFile: path.join(root, "vite.config.ts"),
    root,
    cacheDir: path.join(owned, "vite"),
    server: { host: "127.0.0.1", port: 0, hmr: false, watch: null },
    logLevel: "error",
  });
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn("/usr/bin/chromium", [
    "--headless", "--no-sandbox", "--disable-dev-shm-usage",
    "--remote-debugging-port=0", `--user-data-dir=${path.join(owned, "profile")}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Chromium startup timeout")), 15000);
    browser.stderr.on("data", (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    browser.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Chromium exited ${code}`)); });
  });
  socket = new WebSocket(ws);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timeout`)); }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || JSON.stringify(result.exceptionDetails));
    }
    return result.result.value;
  };
  await call("Page.enable", {}, sessionId);
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/subagent-clock-fixture.html` }, sessionId);
  for (let n = 0; n < 50; n += 1) {
    if (await evaluate("Boolean(window.__ready)")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (n === 49) throw new Error("fixture did not become ready");
  }

  const check = (name, ok, detail) => {
    console.log(`${ok ? "PASS" : "FAIL"} ${name}: ${detail}`);
    if (!ok) failures.push(name);
  };
  const flush = () => evaluate("window.__clock.flush()");
  const advance = (ms) => evaluate(`window.__clock.advance(${ms})`);
  const localTimers = (timers) => timers.filter((ms) => ms === 1000);

  const mountElapsed = async (elapsed, patch = {}, session = `sess-${++sessionSeq}`) => {
    const nowMs = await evaluate("window.__clock.now()");
    const now = nowMs / 1000;
    const body = view(now, [agent({
      parentId: session,
      startedAt: now - elapsed,
      lastActivityAt: now,
      ...patch,
    })]);
    await evaluate(`window.__clock.push(${JSON.stringify(session)}, ${JSON.stringify(body)})`);
    await evaluate(`window.__clock.setSession(${JSON.stringify(session)})`);
    await evaluate("window.__clock.mount()");
    let read = null;
    for (let n = 0; n < 20; n += 1) {
      read = await flush();
      if (secondsOf(read.text) !== null) break;
    }
    return { session, now, read };
  };

  const first = await mountElapsed(10);
  const series = [secondsOf(first.read.text)];
  let read = first.read;
  for (let n = 0; n < 3; n += 1) {
    await advance(1000);
    read = await flush();
    series.push(secondsOf(read.text));
  }
  const deltas = series.slice(1).map((value, index) => value - series[index]);
  console.log(`running-series=${JSON.stringify(series)} deltas=${JSON.stringify(deltas)} timers=${JSON.stringify(read.timers)}`);
  check("running advances one second per tick", series.join(",") === "10,11,12,13" && deltas.every((delta) => delta === 1), `series=${series.join(",")}`);
  check("one local timer while running", localTimers(read.timers).length === 1, `timers=${read.timers.join(",")}`);

  const staleNow = first.now + 1;
  await evaluate(`window.__clock.inject(${JSON.stringify(first.session)}, ${JSON.stringify(view(staleNow, [agent({
    parentId: first.session,
    startedAt: first.now - 10,
    toolCalls: 8,
  })]))})`);
  const afterStale = await flush();
  console.log(`stale-inject now=${staleNow} text=${afterStale.text}`);
  check("older poll does not rewind or replace the card", afterStale.text === read.text, `${read.text} -> ${afterStale.text}`);

  const caughtUp = first.now + 3;
  await evaluate(`window.__clock.inject(${JSON.stringify(first.session)}, ${JSON.stringify(view(caughtUp, [agent({
    parentId: first.session,
    startedAt: first.now - 10,
    toolCalls: 4,
  })]))})`);
  const atPoll = await flush();
  await advance(1000);
  const afterPoll = await flush();
  console.log(`fresh-poll at=${atPoll.text} after=${afterPoll.text}`);
  check("newer poll stays on the server instant then advances one second", secondsOf(atPoll.text) === 13 && secondsOf(afterPoll.text) === 14 && atPoll.text.includes("工具调用 4"), `${atPoll.text} -> ${afterPoll.text}`);

  const doneNow = first.now + 4;
  await evaluate(`window.__clock.inject(${JSON.stringify(first.session)}, ${JSON.stringify(view(doneNow, [agent({
    parentId: first.session,
    startedAt: first.now - 10,
    status: "done",
    endedAt: first.now + 2,
    endReason: "agent_close",
    toolCalls: 4,
  })], 0))})`);
  const doneRead = await flush();
  await advance(3000);
  const doneLater = await flush();
  console.log(`done text=${doneRead.text} later=${doneLater.text} timers=${doneLater.timers.join(",")}`);
  check("completion freezes at endedAt", doneRead.text.includes("用时 12 秒") && doneLater.text.includes("用时 12 秒"), `${doneRead.text} -> ${doneLater.text}`);
  check("completion stops the local timer", localTimers(doneLater.timers).length === 0, `timers=${doneLater.timers.join(",")}`);

  await evaluate(`window.__clock.inject(${JSON.stringify(first.session)}, ${JSON.stringify(view(doneNow - 1, [agent({
    parentId: first.session,
    startedAt: first.now - 10,
    status: "running",
    endedAt: null,
    toolCalls: 5,
  })]))})`);
  await advance(2000);
  const staleRunning = await flush();
  console.log(`stale-running-after-done text=${staleRunning.text} timers=${staleRunning.timers.join(",")}`);
  check("older running snapshot cannot restart a finished clock", staleRunning.text.includes("用时 12 秒") && localTimers(staleRunning.timers).length === 0, staleRunning.text);

  const same = await mountElapsed(10, { id: "agent-same", toolCalls: 2 });
  await advance(2000);
  const beforeStatus = await flush();
  const statusNow = same.now + 2;
  await evaluate(`window.__clock.inject(${JSON.stringify(same.session)}, ${JSON.stringify(view(statusNow, [agent({
    id: "agent-same",
    parentId: same.session,
    startedAt: same.now - 10,
    toolCalls: 9,
  })]))})`);
  const statusRead = await flush();
  await advance(1000);
  const statusLater = await flush();
  console.log(`same-id before=${beforeStatus.text} at=${statusRead.text} later=${statusLater.text}`);
  check("same id status update keeps the start and advances one second", secondsOf(beforeStatus.text) === 12 && secondsOf(statusRead.text) === 12 && secondsOf(statusLater.text) === 13 && statusRead.text.includes("工具调用 9"), `${beforeStatus.text} -> ${statusRead.text} -> ${statusLater.text}`);

  const left = await mountElapsed(20, { id: "agent-left" });
  await advance(1000);
  const rightNowMs = await evaluate("window.__clock.now()");
  const rightNow = rightNowMs / 1000;
  const rightSession = `sess-${++sessionSeq}`;
  await evaluate(`window.__clock.push(${JSON.stringify(rightSession)}, ${JSON.stringify(view(rightNow, [agent({
    id: "agent-right",
    parentId: rightSession,
    startedAt: rightNow - 3,
    lastActivityAt: rightNow,
  })]))})`);
  await evaluate(`window.__clock.setSession(${JSON.stringify(rightSession)})`);
  let switched = null;
  for (let n = 0; n < 20; n += 1) {
    switched = await flush();
    if (switched.text.includes("秒")) break;
  }
  console.log(`session-switch from=${left.session} text=${switched.text}`);
  check("session switch isolates the clock", secondsOf(switched.text) === 3, switched.text);

  const newRunNow = rightNow + 1;
  await evaluate("window.__clock.advance(1000)");
  await evaluate(`window.__clock.inject(${JSON.stringify(rightSession)}, ${JSON.stringify(view(newRunNow, [agent({
    id: "agent-new",
    parentId: rightSession,
    startedAt: newRunNow - 1,
    lastActivityAt: newRunNow,
  })]))})`);
  const newRun = await flush();
  console.log(`new-run text=${newRun.text}`);
  check("new run does not inherit the previous elapsed", secondsOf(newRun.text) === 1, newRun.text);

  const background = await mountElapsed(6, { id: "agent-bg" });
  await advance(1000);
  const beforeHide = await flush();
  await evaluate("window.__clock.setVisible(false)");
  await advance(5000);
  const hidden = await flush();
  await evaluate("window.__clock.setVisible(true)");
  const returned = await flush();
  console.log(`background before=${beforeHide.text} hidden=${hidden.text} returned=${returned.text}`);
  check("background return catches up by the hidden wall time", secondsOf(returned.text) === secondsOf(beforeHide.text) + 5, `${beforeHide.text} -> ${returned.text}`);
  check("background return keeps a single local timer", localTimers(returned.timers).length === 1, returned.timers.join(","));

  const wall = await mountElapsed(8, { id: "agent-wall" });
  await advance(2000);
  const beforeJump = await flush();
  await evaluate("window.__clock.set(window.__clock.now() - 3000)");
  await evaluate("window.__clock.setVisible(false)");
  await evaluate("window.__clock.setVisible(true)");
  const afterJump = await flush();
  console.log(`wall-clock before=${beforeJump.text} after=${afterJump.text}`);
  check("backward wall clock is shown, not clamped", secondsOf(afterJump.text) === secondsOf(beforeJump.text) - 3, `${beforeJump.text} -> ${afterJump.text}`);

  await evaluate("window.__clock.unmount()");
  const afterUnmount = await flush();
  console.log(`unmount timers=${afterUnmount.timers.join(",") || "none"}`);
  check("unmount clears the local timer", localTimers(afterUnmount.timers).length === 0, afterUnmount.timers.join(",") || "none");

  const errors = await evaluate("window.__errors");
  check("no page errors", errors.length === 0, errors.join(" | ") || "none");

  if (failures.length) {
    console.error(`FAILED ${failures.join(", ")}`);
    process.exitCode = 1;
  } else {
    console.log("ALL PASSED");
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  await stop();
  if (process.exitCode) process.exit(process.exitCode);
}
