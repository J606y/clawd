import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const evidence = "/root/hermes-workspace/agent-close-evidence";
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "agent-close-"));
await mkdir(evidence, { recursive: true });
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
let sessionId = "";
const results = [];

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 5000);
      browser.once("exit", () => { clearTimeout(timer); resolve(); });
    });
  }
  if (server) await server.close();
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      await rm(owned, { recursive: true, force: true });
      return;
    } catch (error) {
      if (!error || error.code !== "ENOTEMPTY" || attempt === 7) throw error;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
};

const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timeout`)); }, 30000);
  pending.set(id, { resolve, reject, timer });
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});

const evaluate = async (expression) => {
  const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || JSON.stringify(result.exceptionDetails));
  }
  return result.result.value;
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const load = async (width, height, mobile, reduced = false) => {
  await call("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
  await call("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: reduced ? "reduce" : "no-preference" }],
  });
  await call("Page.navigate", { url: `http://127.0.0.1:${server.httpServer.address().port}/tests/agent-close-fixture.html` });
  for (let n = 0; n < 120 && !await evaluate("window.ready === true"); n += 1) await sleep(100);
  assert.equal(await evaluate("window.ready === true"), true, "fixture must initialize");
  if (width <= 768) await evaluate("window.agentClose.prepare()");
};

const samples = () => evaluate("window.agentClose.samples()");
const shot = async (name) => {
  const image = await call("Page.captureScreenshot", { format: "png" });
  const file = path.join(evidence, name);
  await writeFile(file, Buffer.from(image.data, "base64"));
  return file;
};

const lingerAfterFinish = (rows) => {
  const finished = rows.find((row) => row.anims.some((anim) => anim.state === "finished" && anim.duration === 280));
  if (!finished) return { finished: null, late: rows.filter((row) => row.sheet && row.t > 400) };
  return {
    finished,
    late: rows.filter((row) => row.t > finished.t + 32 && row.sheet && (row.opacity ?? 1) > 0.05),
  };
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
  browser = spawn("/usr/bin/chromium", [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows", "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output.slice(-400)}`)), 15000);
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
    if (message.method === "Runtime.exceptionThrown") console.error("BROWSER", JSON.stringify(message.params.exceptionDetails).slice(0, 2400));
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") console.error("BROWSER", message.params.args.map((arg) => arg.description || arg.value || "").join(" ").slice(0, 2400));
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  sessionId = (await call("Target.attachToTarget", { targetId, flatten: true })).sessionId;
  await call("Page.enable");
  await call("Runtime.enable");

  const run = async (name, fn) => {
    try {
      const detail = await fn();
      results.push({ name, ok: true, detail });
      console.log(`PASS ${name}`);
    } catch (error) {
      results.push({ name, ok: false, error: error instanceof Error ? error.message : String(error) });
      console.log(`FAIL ${name}: ${error instanceof Error ? error.message : error}`);
    }
  };

  await run("mobile close leaves no opaque thumbnail", async () => {
    await load(390, 844, true);
    const opened = await evaluate("window.agentClose.open('agent-a')");
    await evaluate("window.agentClose.close()");
    await sleep(150);
    const mid = await shot("close-mid.png");
    await sleep(190);
    const settled = await shot("close-settled.png");
    await sleep(710);
    const after = await shot("close-after.png");
    const rows = await samples();
    const motion = rows.filter((row) => row.anims.some((anim) => anim.duration === 280));
    assert.ok(motion.length > 0, "close must play the 280ms return animation");
    assert.ok(motion.every((row) => row.anims.every((anim) => anim.duration === 280 || anim.duration === 0)), "must not change the return duration");
    const widths = rows.filter((row) => row.sheet).map((row) => row.w);
    assert.ok(widths.some((width) => width > 200) && widths.some((width) => width < 90), `must shrink toward the button, widths ${widths.slice(0, 6).join(",")}`);
    const running = rows.filter((row) => row.anims.some((anim) => anim.state === "running"));
    assert.ok(running.length > 0 && running.every((row) => row.opacity === 1), "in-flight return stays opaque; do not replace it with a fade");
    const { finished, late } = lingerAfterFinish(rows);
    assert.ok(finished, "return animation must reach finished");
    assert.equal(late.length, 0, `thumbnail still mounted ${late[0] ? late[0].t - finished.t : "?"}ms after animation finished: ${JSON.stringify(late[0])}`);
    const gone = rows.find((row) => !row.sheet);
    assert.ok(gone && gone.t < 500, `sheet still present at end, last t=${rows.at(-1)?.t}`);
    const button = opened.origin;
    const buttonCenter = { x: button.x + button.width / 2, y: button.y + button.height / 2 };
    const small = [...rows].reverse().find((row) => row.sheet && row.w > 0 && row.w < 100);
    assert.ok(small, "must reach the button-sized frame before unmount");
    const smallCenter = { x: small.x + small.w / 2, y: small.y + small.h / 2 };
    const openCenter = { x: opened.open.x + opened.open.w / 2, y: opened.open.y + opened.open.h / 2 };
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    assert.ok(dist(smallCenter, buttonCenter) + 8 < dist(openCenter, buttonCenter), "close must travel toward the view button");
    return { mid, settled, after, finishedAt: finished.t, goneAt: gone.t, small };
  });

  await run("repeat close does not extend the residue", async () => {
    await load(390, 844, true);
    await evaluate("window.agentClose.open('agent-a')");
    await evaluate("window.agentClose.close()");
    await sleep(30);
    await evaluate("window.agentClose.repeatClose()");
    await sleep(700);
    const rows = await samples();
    const { finished, late } = lingerAfterFinish(rows);
    assert.ok(finished, "repeat close must still finish the return animation");
    assert.equal(late.length, 0, `repeat close left a thumbnail: ${JSON.stringify(late[0])}`);
    assert.ok(rows.some((row) => !row.sheet && row.t < 500), "repeat close must not restart a long wait");
  });

  await run("fast reopen of B is not closed by the old callback", async () => {
    await load(390, 844, true);
    await evaluate("window.agentClose.open('agent-a')");
    await evaluate("window.agentClose.close()");
    await sleep(40);
    await evaluate("window.agentClose.dispatchView('agent-b')");
    await sleep(1200);
    const now = await evaluate("window.agentClose.now()");
    assert.equal(now.sheet, true, "new panel must still be mounted 1200ms later");
    assert.equal(now.modal, true, "new panel must stay modal");
    assert.equal(now.title, "合成子代理B", `expected B, got ${now.title}`);
    assert.equal(now.leave, false, "new panel must not be stuck leaving");
  });

  await run("fast reopen of the same agent is not closed by the old callback", async () => {
    await load(390, 844, true);
    await evaluate("window.agentClose.open('agent-a')");
    await evaluate("window.agentClose.close()");
    await sleep(40);
    await evaluate("window.agentClose.dispatchView('agent-a')");
    await sleep(1200);
    const now = await evaluate("window.agentClose.now()");
    assert.equal(now.sheet || now.kind === "agent", true, "same agent must remain open");
    assert.equal(now.title, "合成子代理A", `expected A, got ${now.title}`);
    assert.equal(now.leave, false, "reopened agent must not be leaving");
  });

  await run("reduced motion unmounts without a scaled hold", async () => {
    await load(390, 844, true, true);
    await evaluate("window.agentClose.open('agent-a')");
    await evaluate("window.agentClose.close()");
    await sleep(400);
    const rows = await samples();
    const scaled = rows.filter((row) => row.sheet && row.w > 0 && row.w < 120 && (row.opacity ?? 1) > 0.5);
    assert.equal(scaled.length, 0, `reduced motion left a thumbnail: ${JSON.stringify(scaled[0])}`);
    assert.ok(rows.some((row) => !row.sheet && row.t < 80), `reduced motion must unmount immediately, rows ${rows.slice(0, 4).map((row) => row.t).join(",")}`);
  });

  await run("desktop agent close keeps the side column", async () => {
    await load(1280, 800, false);
    await evaluate("window.agentClose.dispatchView('agent-a')");
    await sleep(400);
    const opened = await evaluate("window.agentClose.now()");
    assert.equal(opened.sheet, false, "desktop must not use the origin modal");
    assert.equal(opened.kind, "agent");
    await evaluate("window.agentClose.close()");
    await sleep(800);
    const rows = await samples();
    const scaled = rows.filter((row) => row.sheet);
    assert.equal(scaled.length, 0, "desktop close must not scale a modal sheet");
    const gone = rows.find((row) => !row.mounted);
    assert.ok(gone && gone.t >= 280 && gone.t <= 450, `desktop leave should follow the 350ms column, gone at ${gone?.t}`);
  });

  await run("live dock close still follows the column timer", async () => {
    await load(1280, 800, false);
    await evaluate("window.agentClose.dispatch('hp-live-open', { scope: 'global', token: 'live-probe' })");
    await sleep(200);
    const opened = await evaluate("window.agentClose.now()");
    assert.equal(opened.kind, "live", `live dock did not open: ${JSON.stringify(opened)}`);
    await evaluate("window.agentClose.watch()");
    await evaluate("window.agentClose.dispatch('hp-live-close', null)");
    await sleep(800);
    const rows = await samples();
    const gone = rows.find((row) => !row.mounted);
    assert.ok(gone && gone.t >= 280 && gone.t <= 450, `live leave regressed, gone at ${gone?.t}`);
  });

  await run("task dock close still follows the column timer", async () => {
    await load(1280, 800, false);
    await evaluate("window.agentClose.dispatch('hp-live-open', { scope: 'task', sessionId: 'parent-probe', token: 'task-probe', active: false })");
    await sleep(200);
    const opened = await evaluate("window.agentClose.now()");
    assert.equal(opened.kind, "task", `task dock did not open: ${JSON.stringify(opened)}`);
    await evaluate("window.agentClose.watch()");
    await evaluate("window.agentClose.dispatch('hp-task-live-close', null)");
    await sleep(800);
    const rows = await samples();
    const gone = rows.find((row) => !row.mounted);
    assert.ok(gone && gone.t >= 280 && gone.t <= 450, `task leave regressed, gone at ${gone?.t}`);
  });

  await writeFile(path.join(evidence, "agent-close-browser.json"), JSON.stringify(results, null, 2));
  const failed = results.filter((item) => !item.ok);
  console.log(JSON.stringify(results.map(({ name, ok, error }) => ({ name, ok, error })), null, 2));
  if (failed.length) process.exitCode = 1;
} finally {
  await stop();
}
