import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "card-size-"));
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

const same = (cards, listWidth, scrollWidth, name) => {
  assert.equal(cards.length, 2, `${name} must show both cards`);
  assert.ok(Math.abs(cards[0].width - cards[1].width) <= 1, `${name} widths follow content: ${cards.map((c) => c.width).join(",")}`);
  assert.ok(Math.abs(cards[0].height - cards[1].height) <= 1, `${name} heights follow content: ${cards.map((c) => c.height).join(",")}`);
  assert.ok(Math.abs(cards[0].width - listWidth) <= 1, `${name} card ${cards[0].width} is not the list width ${listWidth}`);
  assert.ok(scrollWidth <= listWidth + 1, `${name} content widened the list to ${scrollWidth}`);
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
  await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/subagent-card-size-fixture.html` }, sessionId);
  for (let n = 0; n < 80 && !await evaluate("window.ready === true"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate("window.ready === true"), true, "fixture must initialize");
  const measured = await evaluate("motion.measure()");
  console.log(JSON.stringify(measured));
  same(measured.chat, measured.chatList, measured.chatScroll, "chat");
  same(measured.activity, measured.activityList, measured.activityScroll, "activity");
  console.log("PASS card size");
} finally {
  await stop();
}
