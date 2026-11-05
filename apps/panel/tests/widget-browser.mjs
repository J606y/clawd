import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "widget-"));
const probeHits = [];
const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  cacheDir: path.join(owned, "vite"),
  plugins: [{
    name: "widget-probe",
    configureServer(dev) {
      dev.middlewares.use((req, res, next) => {
        const url = req.url || "";
        if (url.includes("widget-external-probe")) {
          probeHits.push(url);
          res.statusCode = 204;
          res.end();
          return;
        }
        next();
      });
    },
  }],
  server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
  logLevel: "error",
});
let browser;
let socket;
let failure;
const pending = new Map();
const attached = [];
let sequence = 0;

try {
  await server.listen();
  const port = server.httpServer.address().port;
  browser = spawn("/usr/bin/chromium", [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Chromium startup timeout")), 30000);
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
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Target.attachedToTarget") attached.push(message.params);
    const job = pending.get(message.id);
    if (!job) return;
    pending.delete(message.id);
    clearTimeout(job.timer);
    if (message.error) job.reject(new Error(JSON.stringify(message.error)));
    else job.resolve(message.result);
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
  await call("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false, flatten: true }, sessionId);
  const evaluate = async (expression, targetSession = sessionId) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, targetSession);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/widget-fixture.html` }, sessionId);
  let ready = null;
  for (let n = 0; n < 80; n++) {
    ready = await evaluate(`(() => ({ ready: window.fixtureReady === true, error: window.fixtureError || "" }))()`);
    if (ready?.ready || ready?.error) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready?.error || "", "", "fixture import/setup failed before the widget assertion");
  assert.equal(ready?.ready, true, "fixture did not mount");

  const report = await evaluate(`(() => {
    const card = (id) => document.querySelector('.turn.assistant [data-step="' + id + '"]');
    const complete = card("complete");
    const dirty = card("dirty");
    const partial = card("partial");
    const pendingCard = card("pending");
    const missing = card("missing");
    const over = card("over");
    const html = card("html");
    const frame = html?.querySelector("iframe");
    return {
      complete: {
        inMessage: !!complete,
        circle: complete?.querySelector("#widget-dot")?.tagName || "",
        fold: !!complete?.closest(".fold"),
      },
      dirty: {
        svg: !!dirty?.querySelector("svg"),
        rect: !!dirty?.querySelector("#widget-rect"),
        script: !!dirty?.querySelector("script"),
        onload: !!dirty?.querySelector("[onload]"),
        onclick: !!dirty?.querySelector("[onclick]"),
        external: dirty?.innerHTML.includes("example.com") === true,
        xss: window.__xss === 1,
      },
      partial: {
        skeleton: !!partial?.querySelector(".widget-skeleton"),
        svg: !!partial?.querySelector("svg"),
        script: !!partial?.querySelector("script"),
        leaked: partial?.innerHTML.includes("<svg") === true || partial?.textContent.includes("<svg") === true,
      },
      pending: {
        skeleton: !!pendingCard?.querySelector(".widget-skeleton"),
        svg: !!pendingCard?.querySelector("svg"),
      },
      missing: {
        skeleton: !!missing?.querySelector(".widget-skeleton"),
        svg: !!missing?.querySelector("svg"),
      },
      over: {
        alert: over?.querySelector("[role='alert']")?.textContent || "",
        svg: !!over?.querySelector("svg"),
        iframe: !!over?.querySelector("iframe"),
      },
      html: {
        frame: !!frame,
        sandbox: frame?.getAttribute("sandbox") || "",
        src: frame?.getAttribute("src"),
        srcdoc: frame?.getAttribute("srcdoc") || "",
        height: frame ? frame.getBoundingClientRect().height : 0,
      },
    };
  })()`);
  console.log(`SVG_REPORT ${JSON.stringify({ complete: report.complete, dirty: report.dirty, partial: report.partial, pending: report.pending, missing: report.missing, over: report.over })}`);
  assert.equal(report.complete.inMessage, true, "complete svg must appear in the assistant message");
  assert.equal(report.complete.circle, "circle", "sanitized svg circle must remain");
  assert.equal(report.complete.fold, false, "widget must not be hidden inside the collapsed tool fold");
  assert.equal(report.dirty.svg, true, "dirty svg element must remain after sanitize");
  assert.equal(report.dirty.rect, true, "safe svg shape must remain");
  assert.equal(report.dirty.script, false, "script must be stripped");
  assert.equal(report.dirty.onload, false, "onload must be stripped");
  assert.equal(report.dirty.onclick, false, "onclick must be stripped");
  assert.equal(report.dirty.external, false, "svg must not keep an external network URL");
  assert.equal(report.dirty.xss, false, "svg script/event must not execute");
  assert.equal(report.partial.skeleton, true, "incomplete svg args must show a skeleton");
  assert.equal(report.partial.svg, false, "incomplete svg must not be inserted");
  assert.equal(report.partial.script, false, "incomplete svg must not insert a script");
  assert.equal(report.partial.leaked, false, "half svg source must not appear in the message");
  assert.equal(report.pending.skeleton, true, "null args must show a skeleton");
  assert.equal(report.pending.svg, false, "null args must not render svg");
  assert.equal(report.missing.skeleton, true, "missing widget_code must show a skeleton");
  assert.equal(report.missing.svg, false, "missing widget_code must not render svg");
  assert.match(report.over.alert, /100000/, "over-limit widget must show an error");
  assert.equal(report.over.svg, false, "over-limit widget must not render svg");
  assert.equal(report.over.iframe, false, "over-limit widget must not render html");
  console.log("PASS svg widget");

  console.log(`HTML_FRAME ${JSON.stringify(report.html)}`);
  assert.equal(report.html.frame, true, "html widget must render an iframe in the message");
  assert.equal(report.html.sandbox.includes("allow-same-origin"), false, "iframe sandbox must not include allow-same-origin");
  assert.equal(report.html.sandbox, "allow-scripts", "iframe sandbox must be only allow-scripts");
  assert.equal(report.html.src, null, "html widget must use srcdoc, not src");
  const cspAt = report.html.srcdoc.indexOf("Content-Security-Policy");
  const csp = cspAt < 0 ? "" : report.html.srcdoc.slice(cspAt, cspAt + 600);
  assert.match(csp, /https:\/\/cdnjs\.cloudflare\.com/, "csp must allow the pinned cdnjs host");
  assert.equal(/cdn\.jsdelivr\.net|unpkg\.com|esm\.sh/.test(csp), false, "csp must not allow an arbitrary cdn");
  assert.equal(/(?:default-src|script-src|style-src|img-src|connect-src|font-src)[^;]*\*/.test(csp), false, "csp must not use a wildcard source");

  const frameSession = async () => {
    for (let n = 0; n < 40; n++) {
      const found = attached.find((item) => String(item.targetInfo?.url || "").includes("srcdoc"));
      if (found?.sessionId) return found.sessionId;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error(`srcdoc frame session missing: ${JSON.stringify(attached.map((item) => item.targetInfo))}`);
  };
  const frameId = await frameSession();
  const frameEval = (expression) => evaluate(expression, frameId);
  let grown = false;
  let before = report.html.height;
  for (let n = 0; n < 40; n++) {
    before = await evaluate(`document.querySelector('[data-step="html"] iframe')?.getBoundingClientRect().height || 0`);
    if (before >= 160) {
      grown = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(grown, true, `height message must grow the parent iframe, saw ${before}`);
  assert.equal(probeHits.some((url) => url.includes("widget-external-probe")), false, "html widget must not fetch external or same-origin probe");
  await frameEval(`document.getElementById("grow").style.height = "360px"`);
  let taller = false;
  let after = before;
  for (let n = 0; n < 40; n++) {
    after = await evaluate(`document.querySelector('[data-step="html"] iframe')?.getBoundingClientRect().height || 0`);
    if (after >= before + 80) {
      taller = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(taller, true, `later height message must change parent height from ${before} to ${after}`);

  const beforeSend = await evaluate(`window.__sendLog.length`);
  assert.equal(beforeSend, 0, "mounting the widget must not send");
  await frameEval(`sendPrompt("来自小组件")`);
  const sent = await evaluate(`window.__sendLog`);
  const fetches = await evaluate(`window.__fetches`);
  assert.equal(sent.length, 1, "sendPrompt must use the existing send path once");
  assert.equal(sent[0].text, "来自小组件");
  assert.equal(sent[0].sessionId, "widget-session");
  assert.equal(sent[0].model, "widget-model");
  assert.equal(sent[0].provider, "widget-provider");
  assert.equal(fetches.some((item) => item.includes("v1/runs") || item.includes("widget-send")), false, "sendPrompt must not invent a second send request");

  const themeOf = async () => frameEval(`(() => {
    const cs = getComputedStyle(document.documentElement);
    return {
      text: cs.getPropertyValue("--text-primary").trim(),
      surface: cs.getPropertyValue("--surface-1").trim(),
      border: cs.getPropertyValue("--border").trim(),
    };
  })()`);
  const parentTheme = async () => evaluate(`(() => {
    const cs = getComputedStyle(document.documentElement);
    return {
      text: cs.getPropertyValue("--text").trim(),
      surface: cs.getPropertyValue("--bg-elevated").trim(),
      border: cs.getPropertyValue("--border").trim(),
    };
  })()`);
  const light = await themeOf();
  const parentLight = await parentTheme();
  assert.equal(light.text, parentLight.text, "light --text-primary must map the panel --text");
  assert.equal(light.surface, parentLight.surface, "light --surface-1 must map the panel surface");
  assert.equal(light.border, parentLight.border, "light --border must map the panel --border");
  await evaluate(`window.__setTheme("dark")`);
  let darkMatched = false;
  let dark = light;
  let parentDark = parentLight;
  for (let n = 0; n < 40; n++) {
    dark = await themeOf();
    parentDark = await parentTheme();
    if (dark.text && dark.text === parentDark.text && dark.text !== parentLight.text && dark.surface === parentDark.surface && dark.border === parentDark.border) {
      darkMatched = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(darkMatched, true, `theme switch must update iframe variables ${JSON.stringify({ dark, parentDark, parentLight })}`);
  console.log("PASS html widget");
  await call("Target.disposeBrowserContext", { browserContextId });
} catch (error) {
  failure = error;
} finally {
  socket?.close();
  for (const job of pending.values()) clearTimeout(job.timer);
  if (browser) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => (browser.exitCode !== null ? resolve() : browser.once("exit", resolve)));
  }
  await server.close();
  await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
if (failure) {
  console.error(failure instanceof Error ? failure.stack || failure.message : failure);
  process.exit(1);
}
