import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "fold-motion-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
const traceEvents = [];

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
    if (message.method === "Tracing.dataCollected") traceEvents.push(...message.params.value);
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
  await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
  await call("Emulation.setCPUThrottlingRate", { rate: 4 }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/subagent-fold-motion-fixture.html` }, sessionId);
  for (let n = 0; n < 80 && !await evaluate("window.ready === true"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const contracts = await evaluate("motion.contracts()");
  assert.equal(contracts.path, "M2.5 6.5 5 4l2.5 2.5");
  assert.equal(contracts.chevDuration, "0.28s");
  assert.equal(contracts.chevProperty, "transform");
  assert.equal(contracts.framePosition, "absolute");
  assert.notEqual(contracts.frameBottom, "auto");
  await call("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline"], recordMode: "recordAsMuchAsPossible" } });
  const played = await evaluate("motion.play()");
  await call("Tracing.end");
  await new Promise((resolve) => setTimeout(resolve, 300));
  const layouts = traceEvents.filter((event) => event.name === "Layout" && event.ph === "X");
  console.log(JSON.stringify({ contracts, samples: played.samples, layouts: layouts.length, foldRows: played.foldRows }));
  assert.equal(played.cardsNow, 12);
  assert.ok(Math.abs(played.belowDelta) < 1, `page jumped ${played.belowDelta}`);
  assert.ok(played.samples.some((angle) => angle > 20 && angle < 160), `chevron did not interpolate: ${played.samples.join(",")}`);
} finally {
  await stop();
}
