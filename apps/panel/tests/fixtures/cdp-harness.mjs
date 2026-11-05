import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";

export async function withPage(root, htmlUrl, run, initScript = "") {
  const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "gatefix-node2-"));
  const server = await createServer({
    configFile: path.join(root, "vite.config.ts"),
    root,
    cacheDir: path.join(owned, "vite"),
    server: { host: "127.0.0.1", port: 0, hmr: false, watch: null },
    logLevel: "error",
  });
  let browser;
  let socket;
  const pending = new Map();
  let sequence = 0;
  const close = async () => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
    if (browser && browser.exitCode === null) {
      browser.kill("SIGTERM");
      await new Promise((resolve) => browser.once("exit", resolve));
    }
    await server.close();
    await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => undefined);
  };
  try {
    await server.listen();
    const port = server.httpServer.address().port;
    browser = spawn("/usr/bin/chromium", [
      "--headless",
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--remote-debugging-port=0",
      `--user-data-dir=${path.join(owned, "profile")}`,
      "about:blank",
    ], { stdio: ["ignore", "ignore", "pipe"] });
    const ws = await new Promise((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output.slice(-400)}`)), 15000);
      browser.stderr.on("data", (chunk) => {
        output += chunk;
        const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
        if (match) {
          clearTimeout(timer);
          resolve(match[1]);
        }
      });
      browser.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
    socket = new WebSocket(ws);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    const logs = [];
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.method === "Runtime.consoleAPICalled") {
        logs.push(message.params.args.map((arg) => arg.value ?? arg.description ?? "").join(" "));
      }
      if (message.method === "Runtime.exceptionThrown") {
        logs.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text || "exception");
      }
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      clearTimeout(waiter.timer);
      message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
    });
    const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timeout`));
      }, 20000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
    const { browserContextId } = await call("Target.createBrowserContext");
    const { targetId } = await call("Target.createTarget", { url: "about:blank", browserContextId });
    const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
    await call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evaluate = async (expression) => {
      const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
      if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "evaluate failed");
      }
      return result.result.value;
    };
    if (initScript) await call("Page.addScriptToEvaluateOnNewDocument", { source: initScript }, sessionId);
    await call("Runtime.enable", {}, sessionId);
    await call("Page.enable", {}, sessionId);
    await call("Page.navigate", { url: `http://127.0.0.1:${port}${htmlUrl}` }, sessionId);
    await waitFor(evaluate, "Boolean(document.querySelector('#root'))", "page root", 15000);
    return await run({ call, evaluate, sessionId, port, close, logs });
  } finally {
    await close();
  }
}

export async function waitFor(evaluate, expression, message = expression, timeoutMs = 8000) {
  const start = Date.now();
  let last = "";
  while (Date.now() - start < timeoutMs) {
    last = await evaluate(`(() => { try { return JSON.stringify({ ok: Boolean(${expression}), text: document.body?.innerText?.slice(0, 500) || "", err: window.fixtureError || "" }); } catch (error) { return JSON.stringify({ ok: false, text: String(error), err: "" }); } })()`);
    const parsed = JSON.parse(last);
    if (parsed.err) throw new Error(parsed.err);
    if (parsed.ok) return parsed;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timeout waiting for ${message}: ${last}`);
}
