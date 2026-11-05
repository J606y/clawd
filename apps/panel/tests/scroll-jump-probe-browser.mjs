import { createServer } from "vite";
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile, readFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const evidenceDir = "/root/hermes-workspace/chat-jump-evidence";
const evidenceName = process.env.HP_EVIDENCE_NAME || "scroll-jump-probe.json";
const wheelCount = Number(process.env.HP_WHEELS || 1);
const caseFilter = process.env.HP_PROBE_CASES || "";
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "scroll-jump-probe-"));
const portWanted = Number(process.env.HP_PROBE_PORT || 0);
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const report = {
  baseCommit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
  sourceRoot: root,
  messageListSha256: createHash("sha256").update(await readFile(path.join(root, "src/react/pages/chat/message-list.tsx"))).digest("hex"),
  browser: "chromium-headless",
  physicalDevice: false,
  touch: "CDP touch emulation, not physical iOS",
  cases: [],
  harnessError: "",
};

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  for (const waiter of pending.values()) clearTimeout(waiter.timer);
  pending.clear();
  if (browser && browser.exitCode === null) {
    const exited = new Promise((resolve) => browser.once("exit", resolve));
    browser.kill("SIGTERM");
    const escalation = setTimeout(() => { if (browser.exitCode === null) browser.kill("SIGKILL"); }, 4000);
    try { await exited; } finally { clearTimeout(escalation); }
  }
  try { if (server) await server.close(); } finally {
    await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
};

function callFactory(sock) {
  return (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, 20000);
    pending.set(id, { resolve, reject, timer });
    sock.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}

async function runCase(call, sessionId, evaluate, width, height, variant) {
  const token = `${width}-${variant}-${Date.now()}`;
  const mobile = width < 600;
  await call("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile,
  }, sessionId);
  if (mobile) await call("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 }, sessionId);
  await call("Page.navigate", {
    url: `http://127.0.0.1:${server.httpServer.address().port}/tests/scroll-jump-probe-fixture.html?case=${variant}&token=${token}`,
  }, sessionId);
  const metricsExpr = `(() => {
    const el = document.querySelector(".hp-chat .scroll");
    const box = el ? el.getBoundingClientRect() : null;
    const groups = [...document.querySelectorAll(".hp-chat .tool-steps .summary")];
    const thoughts = [...document.querySelectorAll(".hp-chat details.reasoning summary")];
    const visible = (node) => {
      if (!node || !box) return null;
      const r = node.getBoundingClientRect();
      return { top: Math.round(r.top - box.top), inView: r.bottom > box.top + 1 && r.top < box.bottom - 1, expanded: node.getAttribute("aria-expanded"), open: node.parentElement?.hasAttribute("open") || null };
    };
    return {
      ready: window.fixtureReady === true,
      token: window.__token || "",
      variant: window.__variant || "",
      error: window.fixtureError || "",
      fetches: (window.__fetches || []).length,
      paths: [...new Set((window.__fetches || []).map((item) => item.path))],
      scrolls: (window.__scrolls || []).length,
      sets: (window.__scrollSets || []).length,
      lastSet: (window.__scrollSets || []).at(-1) || null,
      lastScroll: (window.__scrolls || []).at(-1) || null,
      hasScroller: Boolean(el),
      scrollTop: el ? el.scrollTop : null,
      scrollHeight: el ? el.scrollHeight : null,
      clientHeight: el ? el.clientHeight : null,
      gap: el ? el.scrollHeight - el.scrollTop - el.clientHeight : null,
      jump: Boolean(document.querySelector(".hp-chat .jump")),
      groups: groups.length,
      thoughts: thoughts.length,
      turns: document.querySelectorAll(".hp-chat .past").length,
      lastGroup: visible(groups.at(-1)),
      lastThought: visible(thoughts.at(-1)),
      scrollerTop: box ? box.top : null,
      scrollerLeft: box ? box.left : null,
      scrollerWidth: box ? box.width : null,
      anchor: getComputedStyle(el || document.body).overflowAnchor,
    };
  })()`;
  const read = () => evaluate(metricsExpr);
  let last = null;
  let stable = 0;
  for (let n = 0; n < 160; n += 1) {
    try {
      last = await read();
      const overflow = last.scrollHeight > last.clientHeight + 2;
      const settled = last.ready && last.token === token && !last.error && last.turns > 0 && last.clientHeight > 200 && last.clientHeight < height - 40 && (!overflow || last.gap <= 2);
      if (settled && last.fetches >= 2) {
        stable += 1;
        if (stable >= 4) break;
      } else stable = 0;
    } catch (error) {
      last = { evaluateError: String(error) };
      stable = 0;
    }
    await delay(100);
  }
  if (!last?.turns) throw new Error(`fixture did not mount ${variant}@${width}: ${JSON.stringify(last)}`);
  const point = await evaluate(`(() => {
    const el = document.querySelector(".hp-chat .scroll");
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + Math.min(120, r.height / 2)) };
  })()`);
  const beforeWheel = await read();
  const setCount = beforeWheel.sets;
  const scrollCount = beforeWheel.scrolls;
  await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: point.x, y: point.y, deltaX: 0, deltaY: -140, modifiers: 0 }, sessionId);
  await delay(80);
  let afterWheel = await read();
  let input = "cdp-mouseWheel";
  if (afterWheel.scrollTop === beforeWheel.scrollTop) {
    await call("Input.synthesizeScrollGesture", {
      x: point.x, y: point.y, yDistance: 180, speed: 600, gestureSourceType: "mouse",
    }, sessionId);
    await delay(120);
    afterWheel = await read();
    input = "synthesizeScrollGesture-yDistance-180";
  }
  const moved = afterWheel.scrollTop < beforeWheel.scrollTop - 2;
  const detached = afterWheel.jump === true;
  let afterSecond = null;
  if (wheelCount >= 2 && moved) {
    await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: point.x, y: point.y, deltaX: 0, deltaY: -140, modifiers: 0 }, sessionId);
    await delay(80);
    afterSecond = await read();
  }
  let afterLate = null;
  if (moved) {
    await evaluate("window.__releaseLate && window.__releaseLate()");
    await delay(250);
    afterLate = await read();
  }
  const targetExpr = (selector) => `(() => {
    const el = document.querySelector(".hp-chat .scroll");
    const box = el.getBoundingClientRect();
    const nodes = [...document.querySelectorAll(${JSON.stringify(selector)})];
    const node = nodes.find((item) => {
      const r = item.getBoundingClientRect();
      return r.bottom > box.top + 8 && r.top < box.bottom - 8;
    }) || nodes.at(-1);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: Math.round(r.left + Math.min(24, r.width / 2)), y: Math.round(r.top + r.height / 2), top: Math.round(r.top - box.top) };
  })()`;
  const clickAndMeasure = async (selector, label) => {
    const target = await evaluate(targetExpr(selector));
    if (!target) return { label, missing: true };
    const before = await read();
    await call("Input.dispatchMouseEvent", { type: "mouseMoved", x: target.x, y: target.y }, sessionId);
    await call("Input.dispatchMouseEvent", { type: "mousePressed", x: target.x, y: target.y, button: "left", clickCount: 1 }, sessionId);
    await call("Input.dispatchMouseEvent", { type: "mouseReleased", x: target.x, y: target.y, button: "left", clickCount: 1 }, sessionId);
    await delay(50);
    const mid = await read();
    await delay(360);
    const after = await read();
    return { label, beforeTop: before.scrollTop, beforeGap: before.gap, beforeJump: before.jump, headingBefore: target.top, midGap: mid.gap, afterGap: after.gap, afterTop: after.scrollTop, afterJump: after.jump, heading: label === "tool" ? after.lastGroup : after.lastThought };
  };
  let returned = null;
  if (afterLate?.jump) {
    await clickAndMeasure(".hp-chat .jump", "return");
    returned = await read();
    await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: point.x, y: point.y, deltaX: 0, deltaY: -140, modifiers: 0 }, sessionId);
    await delay(250);
  }
  const toolClick = variant === "plugin" ? await clickAndMeasure(".hp-chat .tool-steps .summary", "tool") : null;
  const thoughtClick = variant === "plugin" ? await clickAndMeasure(".hp-chat details.reasoning summary", "thought") : null;
  let touch = null;
  if (mobile && beforeWheel.scrollHeight > beforeWheel.clientHeight + 2) {
    const startTop = (await read()).scrollTop;
    await call("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: point.x, y: point.y + 80, id: 1 }] }, sessionId);
    for (let step = 1; step <= 6; step += 1) {
      await call("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: point.x, y: point.y + 80 - step * 28, id: 1 }] }, sessionId);
      await delay(16);
    }
    await call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }, sessionId);
    await delay(80);
    const afterTouch = await read();
    touch = { startTop, afterTop: afterTouch.scrollTop, jump: afterTouch.jump, gap: afterTouch.gap, note: "Chromium CDP touch, not a physical phone" };
  }
  const jumpedAfterSwallowedScroll = Boolean(moved && !detached && afterLate && afterLate.gap <= 2 && afterLate.scrollTop > afterWheel.scrollTop + 40);
  const expandJumped = [toolClick, thoughtClick].some((item) => item && !item.missing && item.beforeJump && item.afterGap <= 2 && item.beforeGap > 40);
  return {
    width, height, mobile, variant, input, point,
    returned: returned ? { gap: returned.gap, jump: returned.jump } : null,
    bounded: beforeWheel.clientHeight > 200 && beforeWheel.clientHeight < height - 40,
    overflow: beforeWheel.scrollHeight > beforeWheel.clientHeight + 2,
    anchor: beforeWheel.anchor,
    beforeWheel: { top: beforeWheel.scrollTop, gap: beforeWheel.gap, jump: beforeWheel.jump, h: beforeWheel.scrollHeight, c: beforeWheel.clientHeight, turns: beforeWheel.turns, groups: beforeWheel.groups, thoughts: beforeWheel.thoughts, fetches: beforeWheel.fetches, sets: setCount, scrolls: scrollCount, lastSet: beforeWheel.lastSet },
    afterWheel: { top: afterWheel.scrollTop, gap: afterWheel.gap, jump: afterWheel.jump, sets: afterWheel.sets, scrolls: afterWheel.scrolls },
    moved, detached,
    afterSecond: afterSecond ? { top: afterSecond.scrollTop, gap: afterSecond.gap, jump: afterSecond.jump } : null,
    afterLate: afterLate ? { top: afterLate.scrollTop, gap: afterLate.gap, jump: afterLate.jump } : null,
    toolClick, thoughtClick, touch,
    jumpedAfterSwallowedScroll, expandJumped,
    error: last.error || "",
  };
}

async function runAnchorChecks(call, sessionId, evaluate) {
  await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false }, sessionId);
  await call("Page.navigate", { url: `http://127.0.0.1:${server.httpServer.address().port}/tests/scroll-jump-anchor-fixture.html` }, sessionId);
  for (let i = 0; i < 100; i++) {
    if (await evaluate("Boolean(window.anchorProbe)")) break;
    await delay(50);
  }
  const read = () => evaluate("window.anchorProbe.read()");
  await delay(150);
  const initial = await read();
  assert.equal(initial.gap, 0, "initial content follows bottom");
  await evaluate("window.anchorProbe.grow()");
  await delay(150);
  const grown = await read();
  assert.ok(grown.height > initial.height, "positive control must grow content");
  assert.equal(grown.gap, 0, "ResizeObserver keeps bottom follow");
  assert.equal(grown.userScrolls, initial.userScrolls, "programmatic pin is not a reader gesture");
  const cases = [];
  for (const [offset, missing] of [[0, false], [0.5, false], [0, true], [40, false]]) {
    await evaluate("window.anchorProbe.pin()");
    await delay(100);
    await evaluate(`window.anchorProbe.hold(${offset}, ${missing})`);
    await delay(100);
    const before = await read();
    await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: 100, y: 100, deltaX: 0, deltaY: -100 }, sessionId);
    await delay(150);
    const after = await read();
    assert.ok(after.top < before.top - 2, "native reader wheel must move");
    assert.ok(after.userScrolls > before.userScrolls, "anchor correction must not swallow reader wheel");
    assert.equal(after.stick, false, "reader remains detached");
    await evaluate("window.anchorProbe.grow()");
    await delay(150);
    const later = await read();
    assert.equal(later.top, after.top, "detached reader stays put during growth");
    cases.push({ offset, missing, before, after, later });
  }
  return { initial, grown, cases };
}

try {
  assert.equal(await import("node:fs").then((fs) => fs.existsSync(path.join(root, "node_modules"))), true, "node_modules symlink missing");
  server = await createServer({
    configFile: path.join(root, "vite.config.ts"),
    root,
    cacheDir: path.join(owned, "vite"),
    server: { host: "127.0.0.1", port: portWanted, strictPort: portWanted !== 0, hmr: false, watch: null },
    logLevel: "error",
  });
  await server.listen();
  browser = spawn("/usr/bin/chromium", [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`Chromium startup timeout: ${output}`)), 15000);
    browser.stderr.on("data", (chunk) => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    browser.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Chromium exited ${code}: ${output}`)); });
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
  const call = callFactory(socket);
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const planned = caseFilter
    ? caseFilter.split(",").map((item) => item.split("x"))
    : [["390", "plugin"], ["390", "short"], ["1280", "plugin"], ["1280", "short"]];
  for (const [widthText, variant] of planned) {
    const width = Number(widthText);
    const height = width === 390 ? 844 : 800;
    report.cases.push(await runCase(call, sessionId, evaluate, width, height, variant));
  }
  report.anchorChecks = await runAnchorChecks(call, sessionId, evaluate);
} catch (error) {
  report.harnessError = String(error && error.stack || error);
} finally {
  await mkdir(evidenceDir, { recursive: true });
  const out = path.join(evidenceDir, evidenceName);
  await writeFile(out, JSON.stringify(report, null, 2));
  const reproduced = report.cases.some((item) => item.jumpedAfterSwallowedScroll || item.expandJumped);
  const failures = report.cases.filter((item) => !item.bounded || !item.overflow || !item.moved || !item.detached || !item.afterLate || Math.abs(item.afterLate.top - (item.afterSecond ?? item.afterWheel).top) > 2 || !item.afterLate.jump || !item.returned || item.returned.gap > 2 || item.returned.jump);
  console.log(JSON.stringify({ evidence: out, harnessError: report.harnessError, reproduced, failures: failures.map((item) => `${item.width}-${item.variant}`), cases: report.cases.map((item) => ({
    width: item.width, variant: item.variant, overflow: item.overflow, moved: item.moved, detached: item.detached, second: item.afterSecond,
    jumpedAfterSwallowedScroll: item.jumpedAfterSwallowedScroll, expandJumped: item.expandJumped, input: item.input,
    before: item.beforeWheel, after: item.afterWheel, late: item.afterLate, tool: item.toolClick, thought: item.thoughtClick, touch: item.touch,
  })) }, null, 2));
  await stop();
  if (report.harnessError) process.exit(1);
  process.exit(reproduced || failures.length || report.cases.length === 0 ? 2 : 0);
}
