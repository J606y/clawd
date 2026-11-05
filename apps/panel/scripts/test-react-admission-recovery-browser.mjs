import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "admission-recovery-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
let failure = null;

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  for (const waiter of pending.values()) clearTimeout(waiter.timer);
  pending.clear();
  if (browser && browser.exitCode === null) {
    const exited = new Promise((resolve) => browser.once("exit", resolve));
    browser.kill("SIGTERM");
    const escalation = setTimeout(() => { if (browser.exitCode === null) browser.kill("SIGKILL"); }, 5000);
    try { await exited; } finally { clearTimeout(escalation); }
  }
  try {
    if (server) await server.close();
  } finally {
    await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
};

try {
  server = await createServer({
    configFile: path.join(root, "vite.config.ts"),
    root,
    cacheDir: path.join(owned, "vite"),
    server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false, watch: null },
    logLevel: "error",
  });
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn("/usr/bin/chromium", [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output}`)), 15000);
    browser.stderr.on("data", (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    browser.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Chromium exited ${code}: ${output}`));
    });
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
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  const width = Number(process.env.HP_TEST_VIEWPORT_WIDTH || 1280);
  assert.ok(Number.isInteger(width) && width >= 320 && width <= 2560, "invalid fixture viewport");
  await call("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: width < 600 }, sessionId);
  console.log(`viewport ${width}x844 Chromium (not physical iOS)`);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const snapshot = () => evaluate(`(() => {
    const body = document.body;
    const box = document.querySelector('textarea[aria-label="消息"]');
    const stop = document.querySelector('button[aria-label="停止"]');
    const send = document.querySelector('button[aria-label="发送"]');
    return {
      ready: window.fixtureReady === true,
      errorText: Boolean(body && (body.textContent || "").includes("创建结果待核验：尚未确认运行标识，已保留请求标识；不会重复创建或停止未知任务。")),
      fixtureError: window.fixtureError || "",
      runState: window.__runState ? window.__runState() : null,
      hook: window.__hook || null,
      ui: window.__uiState?.() || null,
      preparing: Boolean(document.querySelector('.att.busy')),
      uploads: window.__uploads?.length || 0,
      attachments: [...document.querySelectorAll('.att:not(.busy)')].map((node) => node.getAttribute('title')),
      sameBox: !window.__pinnedUploadBox || window.__pinnedUploadBox === box,
      changes: window.__changes || 0,
      notice: document.querySelectorAll(".notice").length,
      stop: Boolean(stop) && !stop.disabled,
      send: Boolean(send) && !send.disabled,
      disabled: Boolean(box?.disabled),
      value: box ? box.value : null,
      placeholder: box ? box.placeholder : "",
      generation: window.__generation || 0,
      requests: window.__requests || [],
      href: location.href,
    };
  })()`);
  const waitFor = async (fn, message) => {
    let last = null;
    for (let n = 0; n < 160; n += 1) {
      try {
        last = await snapshot();
        if (fn(last)) return last;
      } catch (error) {
        last = { evaluateError: String(error) };
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`${message}: ${JSON.stringify(last)}`);
  };

  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/react-admission-recovery-fixture.html` }, sessionId);
  const locked = await waitFor((page) => page.ready && page.errorText && page.stop, "resume must show the screenshot lock");
  assert.equal(locked.disabled, false, "the field itself is not disabled; the wait control is the lock");
  assert.equal(locked.send, false, "unabandoned admission must not offer a normal send");
  assert.equal(locked.requests.filter((item) => item.method === "POST" && item.url.includes("/v1/runs")).length, 0);
  assert.equal(locked.requests.filter((item) => item.url.includes("/stop")).length, 0);
  const seen = locked.generation;

  await evaluate(`document.querySelector('button[aria-label="停止"]').click()`);
  const abandoned = await waitFor((page) => !page.stop && page.send && page.value === "尚未入库的原话", "explicit abandon must restore the unsent text for editing");
  assert.equal(abandoned.disabled, false);
  assert.equal(abandoned.errorText, true, "abandon must not hide the uncertainty");
  assert.equal(abandoned.placeholder.includes("正在启动"), false);
  assert.equal(abandoned.requests.filter((item) => item.url.includes("/stop")).length, 0, "abandon must not stop an unknown run");
  assert.equal(abandoned.requests.filter((item) => item.method === "POST" && item.url.includes("/v1/runs") && !item.url.includes("/stop")).length, 0, "abandon must not create");

  await call("Page.reload", { ignoreCache: true }, sessionId);
  const returned = await waitFor((page) => page.ready && page.generation !== seen && !page.stop && page.value === "尚未入库的原话", "refresh after abandon must stay editable");
  assert.equal(returned.errorText, true, "refresh must keep the uncertainty visible");
  assert.equal(returned.send, true);
  assert.equal(returned.disabled, false);
  assert.equal(returned.requests.filter((item) => item.url.includes("/stop")).length, 0);
  assert.equal(returned.requests.filter((item) => item.key === "old-request-01-20" && item.method === "POST").length, 0);

  await evaluate(`(() => {
    const box = document.querySelector('textarea[aria-label="消息"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    setter.call(box, "linux xray follow-up");
    box.dispatchEvent(new Event("input", { bubbles: true }));
  })()`);
  await evaluate(`document.querySelector('button[aria-label="发送"]').click()`);
  const sent = await waitFor((page) => page.requests.some((item) => item.method === "POST" && item.url.includes("/v1/runs") && !item.url.includes("/stop")), "a new message may create once");
  const posts = sent.requests.filter((item) => item.method === "POST" && item.url.includes("/v1/runs") && !item.url.includes("/stop"));
  assert.equal(posts.length, 1);
  assert.notEqual(posts[0].key, "old-request-01-20");
  assert.equal(sent.requests.filter((item) => item.url.includes("/stop")).length, 0);
  console.log(JSON.stringify({ generation: sent.generation, posts: posts.length, stopRequests: 0, restored: true }));

  await evaluate("localStorage.clear()");
  await call("Page.reload", { ignoreCache: true }, sessionId);
  await waitFor((page) => page.ready && page.generation !== sent.generation && page.stop, "fresh unknown admission must lock before the switch race");
  await evaluate(`(() => {
    document.querySelector('button[aria-label="停止"]').click();
    window.__selectSession("sess-other");
  })()`);
  const switched = await waitFor((page) => page.ui?.sessionId === "sess-other" && !page.stop, "switch must reach the other session");
  assert.equal(switched.ui.value, "", "old stop must not restore text into a different session");
  assert.equal(switched.value, "", "the other session's textarea must stay empty");
  console.log("PASS stop/session-switch ownership");

  await evaluate(`(() => {
    window.__pinnedUploadBox = document.querySelector('textarea[aria-label="消息"]');
    window.__pickUpload = (name) => {
      const input = document.querySelector('input[type="file"]');
      const transfer = new DataTransfer();
      transfer.items.add(new File(['%PDF'], name, { type: 'application/pdf' }));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    window.__pickUpload('old.pdf');
  })()`);
  await waitFor((page) => page.uploads === 1 && page.preparing, "old ChatPage upload must be pending");
  await evaluate(`window.__selectSession("sess-upload-new")`);
  const uploadSwitched = await waitFor((page) => page.ui?.sessionId === "sess-upload-new" && !page.preparing, "ChatPage must clear the previous session's preparing state");
  assert.equal(uploadSwitched.sameBox, true, "production Composer must not remount on session switch");
  await evaluate(`window.__pickUpload('new.pdf')`);
  await waitFor((page) => page.uploads === 2 && page.preparing, "new ChatPage upload must be pending");
  await evaluate(`(() => {
    const box = document.querySelector('textarea[aria-label="消息"]');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(box, '新会话正在输入');
    box.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor((page) => page.ui?.value === '新会话正在输入', "new draft must be committed before old completion");
  await evaluate(`window.releaseUpload(0, 'old.pdf'); new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
  const oldFinished = await snapshot();
  assert.equal(oldFinished.preparing, true, "old upload must not clear new ChatPage preparing");
  assert.equal(oldFinished.ui.value, '新会话正在输入');
  assert.equal(oldFinished.attachments.includes('old.pdf'), false);
  assert.equal(oldFinished.sameBox, true);
  await evaluate(`window.releaseUpload(1, 'new.pdf')`);
  const uploaded = await waitFor((page) => !page.preparing && page.attachments.includes('new.pdf'), "new ChatPage upload must finish normally");
  assert.equal(uploaded.value, '新会话正在输入');
  assert.equal(uploaded.attachments.includes('old.pdf'), false);
  assert.equal(uploaded.requests.filter((item) => item.method === 'POST' && item.url.includes('/v1/runs')).length, 0, 'uploads must not create model tasks');
  console.log("PASS real ChatPage upload/session-switch ownership");
} catch (error) {
  failure = error;
} finally {
  try {
    await stop();
  } catch (error) {
    failure = failure ? new AggregateError([failure, error], "test and cleanup failed") : error;
  }
}
if (failure) {
  console.error(failure);
  process.exit(1);
}
