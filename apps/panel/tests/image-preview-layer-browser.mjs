import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/hermes-workspace", "image-preview-layer-"));
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

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  await server.close();
  await rm(owned, { recursive: true, force: true });
};

try {
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
  await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/image-preview-layer-fixture.html` }, sessionId);
  let ready = false;
  for (let n = 0; n < 80; n += 1) {
    if (await evaluate("window.ready === true && Boolean(document.querySelector('#user-shot .thumbnail'))")) {
      ready = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!ready) {
    const text = await evaluate("document.body.innerText.slice(0, 400)");
    throw new Error(`fixture did not mount: ${text}`);
  }

  const failures = [];
  const check = (ok, message) => {
    if (!ok) failures.push(message);
  };
  const caption = await evaluate(`(() => {
    const user = document.querySelector("#user-shot .thumbnail");
    const archived = document.querySelector("#archived-shot .thumbnail");
    const chips = [...document.querySelectorAll("#user-shot button, #archived-shot button")].filter((node) => !node.classList.contains("thumbnail")).map((node) => node.textContent);
    return {
      userText: user?.textContent ?? "",
      userLabel: user?.getAttribute("aria-label") ?? "",
      archivedText: archived?.textContent ?? "",
      archivedLabel: archived?.getAttribute("aria-label") ?? "",
      links: document.querySelectorAll("#user-shot a, #archived-shot .imgs a").length,
      chips,
      pdf: chips.some((text) => text.includes("说明.pdf")),
    };
  })()`);
  check(!caption.userText.includes("IMG_2465.jpeg"), `user thumbnail still shows filename: ${caption.userText}`);
  check(caption.userLabel === "查看 IMG_2465.jpeg", `user thumbnail lost accessible name: ${caption.userLabel}`);
  check(!caption.archivedText.includes("原图.png"), `archived thumbnail still shows filename: ${caption.archivedText}`);
  check(caption.archivedLabel === "查看 原图.png", `archived thumbnail lost accessible name: ${caption.archivedLabel}`);
  check(caption.links === 0, "thumbnail image rendered an anchor");
  check(!caption.chips.some((text) => text.includes("IMG_2465.jpeg") || text.includes("原图.png")), `path chip returned: ${caption.chips.join(" | ")}`);
  check(caption.pdf, "non-image file chip was removed");
  const aspect = await evaluate(`(() => {
    const measure = (selector) => {
      const node = document.querySelector(selector);
      const rect = node.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return { w: rect.width, h: rect.height, hit: Boolean(hit && node.contains(hit)) };
    };
    return { wide: measure("#wide-shot .thumbnail"), tall: measure("#tall-shot .thumbnail") };
  })()`);
  check(aspect.wide.w >= 44 && aspect.wide.h >= 44 && aspect.wide.hit, `wide image is not a 44px target: ${JSON.stringify(aspect.wide)}`);
  check(aspect.tall.w >= 44 && aspect.tall.h >= 44 && aspect.tall.hit, `tall image is not a 44px target: ${JSON.stringify(aspect.tall)}`);

  await evaluate("document.querySelector('#user-shot .thumbnail').click()");
  const userDialog = await evaluate(`(() => {
    const dialog = document.querySelector("#user-shot dialog");
    const status = document.getElementById("status");
    const point = status.getBoundingClientRect();
    const hit = document.elementsFromPoint(point.left + 4, point.top + 4)[0];
    return {
      open: Boolean(dialog?.open),
      modal: Boolean(dialog?.matches(":modal")),
      position: dialog ? getComputedStyle(dialog).position : "",
      hit: hit ? (hit.id || hit.tagName) : "",
    };
  })()`);
  check(userDialog.open, "user preview dialog did not open");
  check(userDialog.modal, `user preview is not the top layer: ${JSON.stringify(userDialog)}`);
  check(userDialog.position === "fixed", `user preview is not fixed: ${userDialog.position}`);
  check(userDialog.hit === "DIALOG", `status still paints over the preview: ${userDialog.hit}`);

  await evaluate("document.querySelector('#assistant-shot .artifact a').click()");
  const assistantDialog = await evaluate(`(() => {
    const dialog = document.querySelector("#assistant-shot dialog");
    return { open: Boolean(dialog?.open), modal: Boolean(dialog?.matches(":modal")), enlarge: document.querySelector("#assistant-shot .artifact")?.textContent.includes("放大查看") };
  })()`);
  check(assistantDialog.enlarge, "assistant enlarge link was removed");
  check(assistantDialog.open && assistantDialog.modal, `assistant preview is not modal: ${JSON.stringify(assistantDialog)}`);

  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  } else {
    await evaluate(`document.querySelector("#user-shot dialog").dispatchEvent(new MouseEvent("click", { clientX: 1, clientY: 1, bubbles: true }))`);
    const closed = await evaluate("!document.querySelector('#user-shot dialog')");
    check(closed, "backdrop click did not close the user preview");
    await evaluate(`document.querySelector("#assistant-shot dialog")?.dispatchEvent(new Event("cancel", { cancelable: true, bubbles: true }))`);
    let escaped = false;
    for (let n = 0; n < 20; n += 1) {
      escaped = await evaluate("!document.querySelector('#assistant-shot dialog')");
      if (escaped) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    check(escaped, "Escape did not close the assistant preview");
    if (failures.length) {
      console.error(failures.join("\n"));
      process.exitCode = 1;
    } else {
      console.log("PASS image preview is top layer and thumbnails have no filename link");
    }
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  await stop();
}
