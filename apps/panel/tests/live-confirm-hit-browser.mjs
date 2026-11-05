import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "live-confirm-hit-"));
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
    "--headless",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Chromium startup timeout")), 15000);
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
      reject(new Error(`Chromium exited ${code}`));
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
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  }, sessionId);
  await call("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/live-confirm-hit-fixture.html` }, sessionId);
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  }, sessionId);
  for (let n = 0; n < 80 && !await evaluate("window.ready === true"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate("window.ready === true"), true, "fixture must initialize");
  for (let n = 0; n < 20 && !await evaluate("!!document.querySelector('dialog.live-confirm')?.open"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }

  for (let n = 0; n < 30 && !await evaluate(`(() => {
    const dialog = document.querySelector("dialog.live-confirm");
    const card = dialog?.querySelector(".dialog");
    if (!dialog?.open || !card) return false;
    return dialog.getAnimations().every((anim) => anim.playState === "finished")
      && card.getAnimations().every((anim) => anim.playState === "finished");
  })()`); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 40));
  }

  const probe = await evaluate(`(() => {
    const dialog = document.querySelector("dialog.live-confirm");
    const danger = [...dialog.querySelectorAll("button")].find((button) => button.classList.contains("danger"));
    const cancel = [...dialog.querySelectorAll("button")].find((button) => button.textContent === "取消");
    const box = (node) => {
      const rect = node.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, w: rect.width, h: rect.height };
    };
    const hit = (node) => {
      const point = box(node);
      const found = document.elementFromPoint(point.x, point.y);
      return {
        point,
        tag: found?.tagName || "",
        text: found?.textContent || "",
        same: found === node || node.contains(found),
      };
    };
    const style = getComputedStyle(dialog);
    const card = dialog.querySelector(".dialog");
    return {
      modal: dialog.matches(":modal"),
      parent: dialog.parentElement === document.body,
      transform: style.transform,
      animationName: style.animationName,
      backdropFilter: style.backdropFilter || style.webkitBackdropFilter || "none",
      danger: hit(danger),
      cancel: hit(cancel),
      cardH: card.getBoundingClientRect().height,
      buttonH: danger.getBoundingClientRect().height,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      position: style.position,
    };
  })()`);
  console.log(JSON.stringify(probe));
  assert.equal(probe.modal, true, "confirm must be a modal top layer");
  assert.equal(probe.parent, true, "confirm must be portaled to body, outside the transformed dock");
  assert.equal(probe.transform, "none", "dialog itself must not keep a transform");
  assert.equal(probe.animationName, "none", "dialog itself must not run the shell sheet transform");
  assert.equal(probe.backdropFilter, "none", "blur must not sit on the element that contains the buttons");
  assert.equal(probe.danger.same, true, `danger hit ${probe.danger.tag} ${probe.danger.text}`);
  assert.equal(probe.cancel.same, true, `cancel hit ${probe.cancel.tag} ${probe.cancel.text}`);
  assert.equal(probe.position, "fixed");
  assert.equal(probe.innerWidth, 390);
  assert.equal(probe.innerHeight, 844);
  assert.ok(probe.danger.point.x > 0 && probe.danger.point.x < 390 && probe.danger.point.y > 0 && probe.danger.point.y < 844, `danger center offscreen ${JSON.stringify(probe.danger.point)}`);
  assert.ok(probe.buttonH >= 39 && probe.buttonH <= 41, `button height ${probe.buttonH}`);

  const tap = async (point) => {
    await call("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: point.x, y: point.y, radiusX: 1, radiusY: 1, force: 1, id: 1 }],
    }, sessionId);
    await call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }, sessionId);
  };
  await tap(probe.danger.point);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const afterDanger = await evaluate("window.hits.danger");
  assert.equal(afterDanger, 1, "touch on 停止任务并接管 must reach the button");
  assert.equal(await evaluate("document.querySelector('dialog.live-confirm')?.open === true"), true, "danger tap must not dismiss the confirm");

  await tap(probe.cancel.point);
  for (let n = 0; n < 20 && await evaluate("!!document.querySelector('dialog.live-confirm')?.open"); n += 1) {
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  const afterCancel = await evaluate("({ cancel: window.hits.cancel, open: !!document.querySelector('dialog.live-confirm')?.open })");
  assert.equal(afterCancel.cancel >= 1, true, "touch on 取消 must reach the button");
  assert.equal(afterCancel.open, false, "取消 must close the confirm");
  console.log("PASS live confirm hit");
} finally {
  await stop();
}
