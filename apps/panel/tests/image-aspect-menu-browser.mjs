import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "image-aspect-menu-"));
const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  cacheDir: path.join(owned, "vite"),
  server: { host: "127.0.0.1", port: 0, strictPort: false },
  logLevel: "error",
});
let browser;
let socket;
let failure;
const pending = new Map();
let sequence = 0;

const measure = `(() => {
  const page = document.querySelector(".image-page");
  const host = document.querySelector(".image-page .aspect");
  const trigger = host?.querySelector(".trigger");
  const menu = host?.querySelector('[role="menu"][aria-label="画面比例"]');
  const options = menu ? [...menu.querySelectorAll('[role="menuitemradio"]')] : [];
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  };
  const clipOf = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const chain = [];
    for (let node = el.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      const overflow = style.overflow + " " + style.overflowX + " " + style.overflowY;
      if (!/(hidden|clip|scroll|auto)/.test(overflow)) continue;
      const b = node.getBoundingClientRect();
      const clipped = r.top < b.top - 0.5 || r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.bottom > b.bottom + 0.5;
      if (clipped) chain.push({ tag: node.tagName, className: node.className, overflow, top: b.top, right: b.right, bottom: b.bottom, left: b.left });
    }
    return chain;
  };
  const hits = options.map((el) => {
    const r = el.getBoundingClientRect();
    const points = [
      [r.left + r.width / 2, r.top + r.height / 2],
      [r.left + 4, r.top + r.height / 2],
      [r.right - 4, r.top + r.height / 2],
    ];
    return {
      label: el.textContent.trim(),
      box: box(el),
      clip: clipOf(el),
      points: points.map(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return {
          x, y,
          inside: x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight,
          ok: !!hit && (hit === el || el.contains(hit)),
          text: hit ? hit.textContent.trim().slice(0, 24) : "",
        };
      }),
    };
  });
  return {
    ready: window.fixtureReady === true,
    error: window.fixtureError || "",
    vw: window.innerWidth,
    vh: window.innerHeight,
    page: box(page),
    host: box(host),
    trigger: trigger ? trigger.textContent.trim() : "",
    menu: box(menu),
    menuClip: clipOf(menu),
    labels: options.map((el) => el.querySelector("span")?.textContent || el.textContent.trim()),
    hits,
  };
})()`;

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

  const failures = [];
  for (const width of [390, 1280]) {
    const { browserContextId } = await call("Target.createBrowserContext");
    const { targetId } = await call("Target.createTarget", { url: "about:blank", browserContextId });
    const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
    const evaluate = async (expression) => {
      const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await call("Emulation.setDeviceMetricsOverride", {
      width,
      height: 844,
      deviceScaleFactor: 1,
      mobile: width < 500,
    }, sessionId);
    await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/image-aspect-menu-fixture.html` }, sessionId);
    let opened = false;
    let last = null;
    for (let n = 0; n < 120; n++) {
      last = await evaluate(`(() => ({ ready: window.fixtureReady === true, error: window.fixtureError || "", trigger: !!document.querySelector(".image-page .aspect .trigger") }))()`);
      if (last?.trigger) {
        opened = true;
        break;
      }
      if (last?.error) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.ok(opened, `${width} fixture did not mount image page: ${JSON.stringify(last)}`);
    await evaluate(`document.querySelector(".image-page .aspect .trigger").click()`);
    let menuReady = false;
    for (let n = 0; n < 40; n++) {
      menuReady = await evaluate(`!!document.querySelector('.image-page .aspect [role="menu"][aria-label="画面比例"]')`);
      if (menuReady) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.ok(menuReady, `${width} aspect menu did not open`);
    await evaluate(`(() => {
      const menu = document.querySelector('.image-page .aspect [role="menu"]');
      return Promise.all(menu.getAnimations({ subtree: true }).map((item) => item.finished)).then(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    })()`);
    const sample = await evaluate(measure);
    console.log(`SAMPLE ${width} ${JSON.stringify(sample)}`);
    const vw = sample.vw;
    const vh = sample.vh;
    const inView = (rect, insetX) => rect
      && rect.left >= insetX - 0.5
      && rect.top >= -0.5
      && rect.right <= vw - insetX + 0.5
      && rect.bottom <= vh + 0.5
      && rect.width > 0
      && rect.height > 0;
    try {
      assert.deepEqual(sample.labels, ["方形", "横向", "纵向"], `${width} aspect options`);
      assert.ok(sample.page && Math.abs(sample.page.width - vw) < 2 && Math.abs(sample.page.height - vh) < 2, `${width} image page must fill the viewport ${JSON.stringify(sample.page)}`);
      if (width === 390) {
        assert.ok(inView(sample.menu, 16), `${width} menu edges must stay inside the viewport with 16px horizontal inset ${JSON.stringify(sample.menu)}`);
        assert.equal(sample.menuClip?.length ?? 0, 0, `${width} menu clipped by overflow ancestor ${JSON.stringify(sample.menuClip)}`);
        for (const item of sample.hits) {
          assert.ok(inView(item.box, 0), `${width} option ${item.label} edges outside viewport ${JSON.stringify(item.box)}`);
          assert.equal(item.clip?.length ?? 0, 0, `${width} option ${item.label} clipped ${JSON.stringify(item.clip)}`);
          assert.ok(item.points.every((point) => point.inside && point.ok), `${width} option ${item.label} elementFromPoint missed ${JSON.stringify(item.points)}`);
        }
      } else {
        assert.ok(sample.menu && sample.host, `${width} missing menu geometry`);
        assert.ok(Math.abs(sample.menu.left - sample.host.left) < 1.5, `${width} desktop menu must stay left-aligned to the trigger ${JSON.stringify({ menu: sample.menu, host: sample.host })}`);
        assert.ok(Math.abs(sample.menu.bottom - (sample.host.top - 6)) < 1.5, `${width} desktop menu must stay 6px above the trigger ${JSON.stringify({ menu: sample.menu, host: sample.host })}`);
      }
      console.log(`PASS ${width}`);
    } catch (error) {
      failures.push(error instanceof Error ? error.message : String(error));
      console.log(`FAIL ${width} ${error instanceof Error ? error.message : error}`);
    }
    await call("Target.disposeBrowserContext", { browserContextId });
  }
  assert.equal(failures.length, 0, failures.join("\n"));
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
