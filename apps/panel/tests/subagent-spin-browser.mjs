import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "subagent-spin-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
};

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
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let n = 0; n < 80; n += 1) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`wait failed: ${expression}; text=${await evaluate("document.body.innerText")}`);
  };

  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/subagent-spin-fixture.html` }, sessionId);
  await wait("!!document.querySelector('.bar > .spin') && document.body.innerText.includes('1 个进行中')");
  const style = await evaluate(`(() => {
    const spin = document.querySelector(".bar > .spin");
    const text = document.querySelector(".bar > span:not(.spin)");
    const spinStyle = getComputedStyle(spin);
    const textStyle = getComputedStyle(text);
    const bar = spin.closest(".bar").getBoundingClientRect();
    return {
      overflow: spinStyle.overflow,
      name: spinStyle.animationName,
      duration: spinStyle.animationDuration,
      timing: spinStyle.animationTimingFunction,
      iteration: spinStyle.animationIterationCount,
      textOverflow: textStyle.overflow,
      barHeight: bar.height,
      chev: getComputedStyle(document.querySelector(".chev")).transitionProperty,
    };
  })()`);
  assert.equal(style.overflow, "visible", "status circle must not inherit the label's overflow clipping");
  assert.equal(style.name, "hp-spin");
  assert.equal(style.duration, "0.9s");
  assert.equal(style.timing, "linear");
  assert.equal(style.iteration, "infinite");
  assert.equal(style.textOverflow, "hidden");
  assert.ok(style.barHeight <= 34, `bar height changed: ${style.barHeight}`);
  assert.match(style.chev, /transform/);
  console.log("PASS status circle keeps a linear infinite spin and does not clip");

  await evaluate("new Promise((resolve) => setTimeout(resolve, 180))");
  const restarted = await evaluate(`(() => {
    const spin = document.querySelector(".bar > .spin");
    const before = spin.getAnimations().find((item) => item.animationName === "hp-spin")?.currentTime ?? -1;
    document.querySelector(".slot-agents").dispatchEvent(new TransitionEvent("transitionend", { propertyName: "opacity", bubbles: true }));
    const afterSlot = spin.getAnimations().find((item) => item.animationName === "hp-spin")?.currentTime ?? -1;
    document.dispatchEvent(new Event("visibilitychange"));
    const afterVisible = spin.getAnimations().find((item) => item.animationName === "hp-spin")?.currentTime ?? -1;
    return { before, afterSlot, afterVisible };
  })()`);
  assert.ok(restarted.before > 80, `animation did not advance: ${restarted.before}`);
  assert.ok(restarted.afterSlot < 50, `slot fade did not restart the circle: ${JSON.stringify(restarted)}`);
  assert.ok(restarted.afterVisible < 50, `foreground return did not restart the circle: ${JSON.stringify(restarted)}`);
  console.log("PASS circle restarts after the bar fade and foreground return");
} finally {
  await stop();
}
