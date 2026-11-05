import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "agent-origin-"));
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

const near = (point, target, slack) => Math.abs(point.x - target.x) <= slack && Math.abs(point.y - target.y) <= slack;

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
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/agent-origin-fixture.html` }, sessionId);
  for (let n = 0; n < 80 && !await evaluate("window.ready === true"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate("window.ready === true"), true, "fixture must initialize");

  const opened = await evaluate("motion.open()");
  const button = await evaluate("motion.origin()");
  console.log(JSON.stringify({ opened, button }));
  assert.equal(opened.modal, true, "agent view must open with showModal");
  assert.equal(opened.position, "fixed");
  assert.ok(opened.sheetCenter, "traveling sheet must exist");
  assert.ok(near(opened.sheetCenter, { x: button.x + button.width / 2, y: button.y + button.height / 2 }, 2), `open must start at the button, got ${JSON.stringify(opened.sheetCenter)}`);

  const ended = await evaluate("motion.end()");
  const buttonCenter = { x: button.x + button.width / 2, y: button.y + button.height / 2 };
  assert.ok(!near(ended.sheetCenter, buttonCenter, 40), "open must grow away from the button");
  assert.ok(ended.sheet.x >= 12 && ended.sheet.y >= 20, "resting card keeps its inset");
  assert.equal(ended.radius, "14px");
  assert.equal(ended.dialogAnimation, "none");
  assert.notEqual(ended.dialogOverflow, "hidden");

  const left = await evaluate("motion.leave()");
  console.log(JSON.stringify({ left }));
  assert.ok(near(left.sheetCenter, buttonCenter, 2), `close must return to the button, got ${JSON.stringify(left.sheetCenter)}`);

  const again = await evaluate("motion.open()");
  assert.ok(near(again.sheetCenter, buttonCenter, 2), "reopen starts at the button again");

  const hit = await evaluate("motion.hit()");
  assert.equal(hit.closed, true, `margin click must close, hit ${hit.tag}`);
  const escaped = await evaluate("motion.escape()");
  assert.equal(escaped.closed, true, "escape must ask to close");
  assert.equal(escaped.stillModal, true, "escape must not destroy the dialog before the return animation");
  const cover = await evaluate("motion.cover()");
  assert.ok(!cover.includes("cover"), `top layer must cover the page, got ${cover.join(",")}`);

  await call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  const desktop = await evaluate("motion.desktop()");
  assert.equal(desktop.modal, false, "desktop keeps the side column, not the origin modal");
  assert.equal(desktop.side, "agent");
  console.log("PASS agent origin");
} finally {
  await stop();
}
