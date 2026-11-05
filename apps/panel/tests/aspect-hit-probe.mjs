import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/hermes-workspace", "aspect-hit-"));
const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  server: { host: "127.0.0.1", port: 0 },
  logLevel: "error",
});
let browser;
let socket;
const pending = new Map();
let sequence = 0;
try {
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn("/usr/bin/chromium", ["--headless", "--no-sandbox", "--disable-dev-shm-usage", "--remote-debugging-port=0", `--user-data-dir=${path.join(owned, "profile")}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Chromium startup timeout")), 15000);
    browser.stderr.on("data", (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    browser.once("error", (error) => { clearTimeout(timer); reject(error); });
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
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/aspect-hit-fixture.html` }, sessionId);
  for (let n = 0; n < 80; n += 1) {
    if (await evaluate("window.ready === true && document.querySelectorAll('img').length >= 4")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  await evaluate("Promise.all([...document.images].map(img => img.decode().catch(() => {})))");
  const measured = await evaluate(`(() => {
    const box = (node) => {
      const rect = node.getBoundingClientRect();
      return { w: Math.round(rect.width), h: Math.round(rect.height), top: Math.round(rect.top), left: Math.round(rect.left) };
    };
    const probe = (selector) => {
      const img = document.querySelector(selector);
      const host = img?.closest("button, a");
      if (!img || !host) return { missing: selector };
      const rect = img.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return { img: box(img), host: box(host), hit: hit ? (hit.id || hit.tagName + "." + hit.className) : "none", covers: Boolean(hit && host.contains(hit)) };
    };
    return {
      wide: probe("#wide img"),
      tall: probe("#tall img"),
      assistantWide: probe("#assistant-wide img"),
      assistantTall: probe("#assistant-tall img"),
    };
  })()`);
  console.log(JSON.stringify(measured, null, 2));
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  await server.close();
  await rm(owned, { recursive: true, force: true });
}
