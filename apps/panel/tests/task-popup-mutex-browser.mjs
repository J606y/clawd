import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const evidence = "/root/hermes-workspace/fullgate-evidence/panel-fold-motion/fold";
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "task-popup-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
const failures = [];
const cleanupErrors = [];

const stop = async () => {
  try {
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
    if (browser && browser.exitCode === null) {
      const exited = new Promise((resolve) => browser.once("exit", resolve));
      browser.kill("SIGTERM");
      await exited;
    }
    if (server) await server.close();
  } catch (error) {
    cleanupErrors.push(`stop ${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } catch (error) {
    cleanupErrors.push(`rm ${error instanceof Error ? error.message : String(error)}`);
  }
};

const check = (name, ok, detail) => {
  const line = `${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`;
  console.log(line);
  if (!ok) failures.push(line);
};

const bothOpen = (frames) => frames.some((frame) => frame.tasks === "true" && frame.agents === "true");

try {
  await mkdir(evidence, { recursive: true });
  console.log(`CANDIDATE ${root}`);
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
    message.error ? waiter.reject(new Error(`${waiter.label}: ${JSON.stringify(message.error)}`)) : waiter.resolve(message.result);
  });
  const call = (method, params = {}, sessionId, label = method) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${label} timeout`)); }, 20000);
    pending.set(id, { resolve, reject, timer, label });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  await call("Page.enable", {}, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId, `eval ${expression.slice(0, 80)}`);
    if (result.exceptionDetails) throw new Error(`${expression.slice(0, 120)} :: ${result.exceptionDetails.text || ""} ${result.exceptionDetails.exception?.description || ""}`);
    return result.result.value;
  };
  const openPage = async (width, height, mobile) => {
    await call("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile }, sessionId);
    const token = `${width}-${height}-${Date.now()}`;
    await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/task-popup-mutex-fixture.html?vp=${width}x${height}&token=${token}` }, sessionId);
    const ready = `window.ready === true && window.readyToken === ${JSON.stringify(token)}`;
    for (let n = 0; n < 80 && !await evaluate(ready); n += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!await evaluate(ready)) throw new Error(`fixture did not initialize at ${width}x${height}`);
  };
  const waitFor = async (expression, label) => {
    for (let n = 0; n < 40; n += 1) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error(`timeout: ${label}`);
  };
  const sampleLayout = async () => evaluate(`(() => {
    const input = document.querySelector('#input').getBoundingClientRect();
    const shell = document.querySelector('.panel-shell');
    const shellBox = shell && getComputedStyle(shell).display !== 'none' ? shell.getBoundingClientRect() : null;
    const composer = document.querySelector('#composer');
    return {
      input: input.top,
      shell: shellBox ? shellBox.height : null,
      panels: document.querySelector('#panels').getBoundingClientRect().height,
      pad: getComputedStyle(composer).paddingBottom,
      scroll: document.querySelector('#log').scrollTop,
    };
  })()`);
  const waitSettled = async (label) => {
    await evaluate(`document.fonts ? document.fonts.ready : true`);
    await evaluate(`Promise.all([...document.querySelectorAll('.composer-panels .fold')].flatMap((el) => el.getAnimations().filter((anim) => anim.playState === 'running').map((anim) => anim.finished.catch(() => {}))))`);
    const first = await sampleLayout();
    let previous = first;
    let stable = 0;
    for (let n = 0; n < 24; n += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      const sample = await sampleLayout();
      if (Math.abs(sample.input - previous.input) <= 0.05 && sample.shell === previous.shell && sample.panels === previous.panels && sample.scroll === previous.scroll) {
        stable += 1;
        if (stable >= 2) {
          console.log(`SETTLE ${label} ${JSON.stringify({ first, settled: sample, frames: n + 1 })}`);
          return sample;
        }
      } else stable = 0;
      previous = sample;
    }
    console.log(`SETTLE-TIMEOUT ${label} ${JSON.stringify({ first, last: previous })}`);
    return previous;
  };
  const measureGap = async () => evaluate(`(() => {
    const list = document.querySelector('.subagents .list');
    const bar = document.querySelector('.panel-shell .bar');
    if (!list || !bar) return { missing: true };
    const listBox = list.getBoundingClientRect();
    const barBox = bar.getBoundingClientRect();
    const cards = [...list.querySelectorAll('.card')].map((card) => {
      const r = card.getBoundingClientRect();
      return { title: card.querySelector('.title')?.textContent ?? '', top: r.top, bottom: r.bottom };
    });
    const visible = cards.filter((card) => card.bottom > listBox.top + 1 && card.top < listBox.bottom - 1);
    const lastVisible = visible[visible.length - 1] ?? null;
    const probeY = Math.min(listBox.bottom - 2, barBox.top - 1);
    const hit = document.elementFromPoint(listBox.left + 24, probeY);
    return {
      barTop: barBox.top,
      listBottom: listBox.bottom,
      listGap: barBox.top - listBox.bottom,
      last: cards[cards.length - 1] ?? null,
      lastVisible,
      visibleGap: lastVisible ? barBox.top - lastVisible.bottom : null,
      hit: hit ? String(hit.className || hit.tagName) : 'none',
      shellMax: getComputedStyle(document.querySelector('.panel-shell')).maxHeight,
    };
  })()`);
  const shot = async (name) => {
    const { data } = await call("Page.captureScreenshot", { format: "png" }, sessionId);
    const file = path.join(evidence, name);
    await writeFile(file, Buffer.from(data, "base64"));
    console.log(`SHOT ${file}`);
  };
  const pageSource = await (await fetch(`http://127.0.0.1:${port}/src/react/pages/chat/chat-page.tsx`)).text();
  check("chat-page calls ComposerPanels", pageSource.includes("ComposerPanels") && pageSource.includes("taskDockRef") && !pageSource.includes("<TaskList") && !pageSource.includes("<SubagentsBar") && !pageSource.includes("from \"./task-list\"") && !pageSource.includes("from \"./subagents-bar\""), pageSource.includes("ComposerPanels") ? "served" : "missing");

  await openPage(390, 844, true);
  await waitFor(`!!(document.querySelector("#panels .subagents .bar") || document.querySelector("#panels .task-list"))`, "panels");
  let snap = await evaluate("window.inspect()");
  console.log(`STATE390 ${JSON.stringify({ taskBar: snap.taskBar, taskExpanded: snap.taskExpanded, agentBar: snap.agentBar, agentExpanded: snap.agentExpanded, titles: snap.titles })}`);
  check("tasks default collapsed in the shared bar", snap.taskExpanded === "false" && snap.taskBar.includes("任务 1/3"), snap.taskBar || String(snap.taskExpanded));
  check("subagent request uses limit 50", (snap.requests ?? []).some((item) => item.session === "A" && item.limit === "50"), JSON.stringify(snap.requests));
  check("foreign running does not auto-open subagents", snap.agentExpanded === "false" && snap.titles.every((title) => title.startsWith("任务A-")), `${snap.agentExpanded} ${snap.titles.join(",")}`);
  check("task status nested and cancel survive", snap.items.length === 4 && snap.items[0].status === "completed" && snap.items[0].mark === "☑" && snap.items[0].strike.includes("line-through") && snap.items[1].status === "in_progress" && snap.items[1].mark === "◐" && snap.items[2].nested === true && snap.items[2].label.includes("待办") && snap.items[3].status === "cancelled" && snap.items[3].tag === "已取消", JSON.stringify(snap.items));
  const dockBefore = snap.dockHeight;
  const inputBefore = snap.input?.top;
  const scrollBefore = snap.scrollTop;
  await evaluate("window.harness.armFrames(); window.harness.measureDock();");
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const opened = await evaluate("window.inspect()");
  console.log(`TASKGEOM ${JSON.stringify({ frame: opened.taskFrame, input: opened.input, dock: opened.dockHeight, frames: opened.frames })}`);
  check("one click opens only the task popup", opened.taskExpanded === "true" && opened.agentExpanded === "false" && !bothOpen(opened.frames), JSON.stringify(opened.frames));
  check("task frame floats inside the 390 viewport and does not push input or scroll", opened.taskFrame && opened.taskFrame.top >= 0 && opened.taskFrame.bottom <= 844.5 && opened.taskFrame.left >= 0 && opened.taskFrame.right <= 390.5 && Math.abs((opened.input?.top ?? 0) - inputBefore) <= 1 && opened.scrollTop === scrollBefore && opened.jumped !== "1" && Math.abs(opened.dockHeight - dockBefore) <= 1, JSON.stringify({ frame: opened.taskFrame, input: opened.input?.top, scroll: opened.scrollTop, jumped: opened.jumped, dock: opened.dockHeight }));
  check("task bar is the hit target", opened.taskHit === true, String(opened.taskHit));
  await waitSettled("tasks shot");
  await shot("phone-tasks-open.png");

  await evaluate(`document.querySelector(".task-list .bar").click()`);
  const closed = await evaluate("window.inspect()");
  check("second click collapses tasks and keeps the fold inert", closed.taskExpanded === "false" && closed.taskInert === true && closed.agentExpanded === "false", `${closed.taskExpanded} inert=${closed.taskInert}`);
  const tab = await evaluate(`(() => {
    const view = document.querySelector(".task-list li");
    view?.focus();
    const focused = document.activeElement === view;
    document.querySelector(".task-list .bar").focus();
    return { focused, inert: document.querySelector(".task-list .fold").hasAttribute("inert") };
  })()`);
  check("collapsed task rows are not focusable", tab.focused === false && tab.inert === true, JSON.stringify(tab));

  await evaluate("window.harness.armFrames()");
  const rapid = [];
  const take = async () => rapid.push(await evaluate(`({ tasks: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") ?? "missing", agents: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") ?? "missing" })`));
  await take();
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await take();
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await take();
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await take();
  const burst = await evaluate(`(() => {
    const seen = [];
    const sample = () => {
      const tasks = document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") ?? "missing";
      const agents = document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") ?? "missing";
      seen.push({ tasks, agents });
    };
    document.querySelector(".task-list .bar").click();
    sample();
    document.querySelector(".subagents .bar").click();
    sample();
    document.querySelector(".task-list .bar").click();
    sample();
    return seen;
  })()`);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const burstPainted = await evaluate("window.harness.frames");
  console.log(`RAPID ${JSON.stringify({ rapid, burst, burstPainted })}`);
  check("task then subagent then task never opens both in one frame", rapid.some((frame) => frame.tasks === "true" && frame.agents !== "true") && rapid.some((frame) => frame.agents === "true" && frame.tasks !== "true") && rapid.at(-1).tasks === "true" && rapid.at(-1).agents !== "true" && !bothOpen(rapid) && !bothOpen(burst) && !bothOpen(burstPainted), JSON.stringify({ rapid, burst, burstPainted }));
  const afterRapid = await evaluate("window.inspect()");
  check("shared frame chrome matches between the two bars", afterRapid.taskStyle && afterRapid.agentStyle && afterRapid.taskStyle.borderTop === afterRapid.agentStyle.borderTop && afterRapid.taskStyle.radius === afterRapid.agentStyle.radius && afterRapid.taskStyle.position === "absolute" && afterRapid.agentStyle.position === "absolute" && afterRapid.taskStyle.bottom !== "auto" && afterRapid.agentStyle.bottom !== "auto", JSON.stringify({ task: afterRapid.taskStyle, agent: afterRapid.agentStyle }));
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const agentsOpen = await evaluate("window.inspect()");
  check("opening subagents closes tasks without jumping", agentsOpen.agentExpanded === "true" && agentsOpen.taskExpanded === "false" && agentsOpen.scrollTop === scrollBefore && agentsOpen.jumped !== "1" && Math.abs((agentsOpen.input?.top ?? 0) - inputBefore) <= 1, `${agentsOpen.agentBar} ${agentsOpen.taskExpanded}`);
  check("subagent frame stays inside 390", agentsOpen.agentFrame && agentsOpen.agentFrame.top >= 0 && agentsOpen.agentFrame.bottom <= 844.5 && agentsOpen.agentFrame.right <= 390.5, JSON.stringify(agentsOpen.agentFrame));
  const viewed = await evaluate(`(() => {
    let detail = null;
    const onView = (event) => { detail = event.detail; };
    window.addEventListener("hp-view-subagent", onView);
    document.querySelector(".subagents .view").click();
    window.removeEventListener("hp-view-subagent", onView);
    return detail ? { id: detail.id, parentId: detail.parentId, w: detail.origin?.width ?? 0 } : null;
  })()`);
  check("view event keeps id and origin", viewed?.id === "a1" && viewed.parentId === "A" && viewed.w > 0, JSON.stringify(viewed));
  await shot("phone-subagents-open.png");
  console.log(`GAP-EARLY ${JSON.stringify(await measureGap())}`);
  await waitSettled("subagents open");
  console.log(`GAP-REST ${JSON.stringify(await measureGap())}`);
  await shot("phone-subagents-open.png");

  await call("Page.bringToFront", {}, sessionId);
  await call("Emulation.setFocusEmulationEnabled", { enabled: true }, sessionId);
  await evaluate(`document.querySelector(".subagents .bar").click(); document.querySelector(".task-list .bar").focus();`);
  await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", text: "\r", unmodifiedText: "\r", windowsVirtualKeyCode: 13 }, sessionId);
  await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }, sessionId);
  const keyed = await evaluate("window.inspect()");
  check("keyboard opens the focused task bar", keyed.taskExpanded === "true" && keyed.agentExpanded === "false", `${keyed.taskExpanded} ${keyed.agentExpanded}`);

  await evaluate("window.harness.armFrames(); window.harness.setSession(\"R\")");
  await waitFor(`document.querySelector(".subagents .title")?.textContent === "进行中的R"`, "owned running cards");
  await new Promise((resolve) => setTimeout(resolve, 250));
  const arrived = await evaluate("window.inspect()");
  console.log(`ARRIVE-R ${JSON.stringify({ task: arrived.taskExpanded, agent: arrived.agentExpanded, titles: arrived.titles, frames: arrived.frames })}`);
  check("running data arrival leaves tasks and subagents closed", arrived.taskExpanded !== "true" && arrived.agentExpanded !== "true" && arrived.titles.includes("进行中的R"), `${arrived.taskExpanded} ${arrived.agentExpanded} ${arrived.titles.join(",")}`);
  const markIdlePoll = await evaluate("window.harness.requests.length");
  await evaluate("window.harness.armFrames(); window.harness.bump(\"R\"); window.harness.invalidate()");
  await waitFor(`window.harness.requests.length > ${markIdlePoll}`, "idle poll");
  await new Promise((resolve) => setTimeout(resolve, 200));
  const idlePolled = await evaluate("window.inspect()");
  check("poll does not open a closed task or subagent list", idlePolled.taskExpanded !== "true" && idlePolled.agentExpanded !== "true" && !idlePolled.frames.some((frame) => frame.tasks === "true" || frame.agents === "true"), JSON.stringify(idlePolled.frames));
  if (idlePolled.agentExpanded === "true") await evaluate(`document.querySelector(".subagents .bar").click()`);
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  const manualOpen = await evaluate("window.inspect()");
  check("user can still open a running subagent list", manualOpen.agentExpanded === "true" && manualOpen.taskExpanded !== "true", `${manualOpen.agentExpanded} ${manualOpen.taskExpanded}`);
  const markOpenPoll = await evaluate("window.harness.requests.length");
  await evaluate("window.harness.bump(\"R\"); window.harness.invalidate()");
  await waitFor(`window.harness.requests.length > ${markOpenPoll}`, "open poll");
  await new Promise((resolve) => setTimeout(resolve, 200));
  const openPolled = await evaluate("window.inspect()");
  check("poll does not close a manually open list", openPolled.agentExpanded === "true" && openPolled.taskExpanded !== "true", `${openPolled.agentExpanded} ${openPolled.taskExpanded}`);
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  const dismissed = await evaluate("window.inspect()");
  check("user can close a running list", dismissed.agentExpanded === "false" && dismissed.taskExpanded === "false", dismissed.agentExpanded);
  const markPoll = await evaluate("window.harness.requests.length");
  await evaluate("window.harness.armFrames(); window.harness.bump(\"R\"); window.harness.invalidate()");
  await waitFor(`window.harness.requests.length > ${markPoll}`, "poll refetch");
  await new Promise((resolve) => setTimeout(resolve, 80));
  const polled = await evaluate("window.inspect()");
  check("running poll does not steal the closed list back", polled.agentExpanded === "false" && polled.taskExpanded === "false" && !polled.frames.some((frame) => frame.agents === "true"), JSON.stringify(polled.frames));
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await evaluate("window.harness.setSession(\"B\")");
  await waitFor(`document.querySelector(".subagents .title")?.textContent === "任务B"`, "leave to B");
  await evaluate("window.harness.setSession(\"R\")");
  await waitFor(`document.querySelector(".subagents .title")?.textContent === "进行中的R"`, "reenter R");
  await new Promise((resolve) => setTimeout(resolve, 250));
  const reentered = await evaluate("window.inspect()");
  console.log(`REENTER-R ${JSON.stringify({ task: reentered.taskExpanded, agent: reentered.agentExpanded, titles: reentered.titles })}`);
  check("leaving and reentering the same running session stays closed", reentered.agentExpanded !== "true" && reentered.taskExpanded !== "true" && reentered.titles.includes("进行中的R") && !reentered.titles.includes("任务B"), `${reentered.agentExpanded} ${reentered.taskExpanded} ${reentered.titles.join(",")}`);
  if (reentered.agentExpanded === "true") await evaluate(`document.querySelector(".subagents .bar").click()`);
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await evaluate("window.harness.remount()");
  await waitFor(`document.querySelector(".subagents .title")?.textContent === "进行中的R"`, "remount R");
  await new Promise((resolve) => setTimeout(resolve, 250));
  const remounted = await evaluate("window.inspect()");
  console.log(`REMOUNT-R ${JSON.stringify({ task: remounted.taskExpanded, agent: remounted.agentExpanded, titles: remounted.titles })}`);
  check("remounting the same running session stays closed", remounted.agentExpanded !== "true" && remounted.taskExpanded !== "true" && remounted.titles.includes("进行中的R"), `${remounted.agentExpanded} ${remounted.taskExpanded}`);
  if (remounted.agentExpanded === "true") await evaluate(`document.querySelector(".subagents .bar").click()`);

  const tabFromTaskBar = async () => {
    await call("Page.bringToFront", {}, sessionId);
    await call("Emulation.setFocusEmulationEnabled", { enabled: true }, sessionId);
    await evaluate(`document.querySelector(".task-list .bar").focus()`);
    await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
    await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
    return evaluate(`(() => {
      const active = document.activeElement;
      const fold = document.querySelector(".subagents .fold");
      return {
        cls: active?.className ?? "",
        tag: active?.tagName ?? "",
        inFold: !!(fold && active && fold.contains(active)),
        view: active?.classList?.contains("view") === true,
        inert: fold ? fold.hasAttribute("inert") : null,
        samePanel: document.querySelector(".composer-panels") === window.__panelNode,
      };
    })()`);
  };
  await evaluate(`window.__panelNode = document.querySelector(".composer-panels"); window.__inertForged = false`);
  check("first entry leaves both lists closed", snap.taskExpanded === "false" && snap.agentExpanded === "false", `${snap.taskExpanded} ${snap.agentExpanded}`);
  const arrivedTasks = await evaluate(`(() => {
    window.harness.setTasks([{ id: "arrive", content: "同页新到的任务", status: "in_progress" }]);
    return {
      task: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded"),
      agent: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded"),
      text: document.querySelector(".task-list .text")?.textContent ?? "",
    };
  })()`);
  check("same-session task arrival does not auto-expand", arrivedTasks.task !== "true" && arrivedTasks.agent !== "true" && arrivedTasks.text.includes("同页新到的任务"), JSON.stringify(arrivedTasks));
  await evaluate(`window.harness.setTasks([{ id: "r-task", content: "跑着时不要抢开", status: "pending" }])`);
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await waitFor(`document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") === "true"`, "tab positive open");
  const tabOpen = await tabFromTaskBar();
  console.log(`TAB-OPEN ${JSON.stringify(tabOpen)}`);
  check("open list tab reaches 查看", tabOpen.view === true && tabOpen.inFold === true, JSON.stringify(tabOpen));
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await waitFor(`document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") !== "true"`, "tab close");
  const tabClosed = await tabFromTaskBar();
  console.log(`TAB-CLOSED ${JSON.stringify(tabClosed)}`);
  check("collapsed tab does not enter fold 查看", tabClosed.inFold === false && tabClosed.view === false && tabClosed.inert === true && tabClosed.samePanel === true, JSON.stringify(tabClosed));
  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await waitFor(`document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") === "true"`, "reopen before refresh");
  const markRefresh = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.bump("R"); window.harness.invalidate()`);
  await waitFor(`window.harness.requests.length > ${markRefresh}`, "same-page refresh");
  await new Promise((resolve) => setTimeout(resolve, 200));
  const refreshed = await evaluate(`({
    agent: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded"),
    task: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded"),
    hidden: document.querySelector("#chat-slot")?.hasAttribute("hidden") === true,
    title: document.querySelector(".subagents .title")?.textContent ?? "",
  })`);
  check("same-page refresh keeps a manual open", refreshed.agent === "true" && refreshed.task !== "true" && refreshed.hidden === false && refreshed.title === "进行中的R", JSON.stringify(refreshed));
  const slotHost = await evaluate(`({
    slot: !!document.querySelector(".composer-panels")?.closest(".chat-slot"),
    id: document.querySelector(".composer-panels")?.closest(".chat-slot")?.id ?? "",
  })`);
  check("panels sit inside the production chat-slot", slotHost.slot === true && slotHost.id === "chat-slot", JSON.stringify(slotHost));
  await evaluate(`document.querySelector("#chat-slot").hidden = true`);
  await new Promise((resolve) => setTimeout(resolve, 40));
  await evaluate(`document.querySelector("#chat-slot").hidden = false`);
  let slotReturned = null;
  for (let n = 0; n < 20; n += 1) {
    slotReturned = await evaluate(`({
      agent: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded"),
      task: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded"),
      title: document.querySelector(".subagents .title")?.textContent ?? "",
      hidden: document.querySelector("#chat-slot")?.hasAttribute("hidden") === true,
      samePanel: document.querySelector(".composer-panels") === window.__panelNode,
    })`);
    if (slotReturned.agent !== "true" && slotReturned.task !== "true") break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  console.log(`SLOT-RETURN ${JSON.stringify(slotReturned)}`);
  check("chat-slot hide then show collapses the same session", slotReturned.agent !== "true" && slotReturned.task !== "true" && slotReturned.title === "进行中的R" && slotReturned.hidden === false && slotReturned.samePanel === true, JSON.stringify(slotReturned));
  const lingering = await evaluate(`({
    agent: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded"),
    task: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded"),
  })`);
  if (lingering.agent === "true") await evaluate(`document.querySelector(".subagents .bar").click()`);
  if (lingering.task === "true") await evaluate(`document.querySelector(".task-list .bar").click()`);
  await evaluate(`document.querySelector("#chat-slot").hidden = false`);

  await evaluate(`document.querySelector(".task-list .bar").click()`);
  const markC = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.hold.add("C"); window.harness.armFrames(); window.harness.setSession("C")`);
  await waitFor(`window.harness.requests.slice(${markC}).some((item) => item.session === "C")`, "C inflight");
  const pendingC = await evaluate("window.inspect()");
  check("session switch drops the previous open panel before the next response", pendingC.taskExpanded !== "true" && pendingC.agentExpanded !== "true" && !pendingC.titles.includes("进行中的R") && !pendingC.taskBar.includes("跑着时"), `${pendingC.taskBar} ${pendingC.titles.join(",")}`);
  await evaluate(`window.harness.setSession("B"); window.harness.release("C")`);
  await new Promise((resolve) => setTimeout(resolve, 120));
  const lateC = await evaluate("window.inspect()");
  check("late C does not leak onto B or reopen a panel", !lateC.titles.includes("迟到C") && lateC.titles.includes("任务B") && lateC.agentExpanded === "false" && lateC.taskExpanded === "false" && !lateC.taskBar.includes("C 的任务"), `${lateC.titles.join(",")} ${lateC.taskBar} ${lateC.agentExpanded}`);

  const markD = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.hold.add("D"); window.harness.setSession("D")`);
  await waitFor(`window.harness.requests.slice(${markD}).some((item) => item.session === "D")`, "D inflight");
  const markEmpty = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.setSession(""); window.harness.release("D")`);
  await new Promise((resolve) => setTimeout(resolve, 150));
  const empty = await evaluate(`({ snap: window.inspect(), added: window.harness.requests.slice(${markEmpty}) })`);
  check("empty session keeps no task shell and ignores late D", empty.snap.taskCount === 0 && empty.snap.agentCount === 0 && !empty.snap.titles.includes("迟到D") && empty.added.every((item) => item.session !== ""), JSON.stringify({ titles: empty.snap.titles, added: empty.added, taskCount: empty.snap.taskCount }));

  await evaluate(`window.harness.setSession("A")`);
  await waitFor(`!!document.querySelector(".subagents .title")`, "A cards again");
  await openPage(1280, 800, false);
  await waitFor(`!!document.querySelector(".task-list .bar")`, "desktop task bar");
  await waitSettled("desktop collapsed");
  let desk = await evaluate("window.inspect()");
  const deskInput = desk.input?.top;
  const deskScroll = desk.scrollTop;
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await waitFor(`document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") === "true"`, "desktop task open");
  await waitSettled("desktop open");
  desk = await evaluate("window.inspect()");
  const deskParts = {
    expanded: desk.taskExpanded === "true",
    frameTopOk: desk.taskFrame.top >= 0,
    frameBottomOk: desk.taskFrame.bottom <= 800.5,
    frameRightOk: desk.taskFrame.right <= 1280.5,
    inputOk: Math.abs(desk.input.top - deskInput) <= 1,
    scrollOk: desk.scrollTop === deskScroll,
    jumpedOk: desk.jumped !== "1",
    deskInput,
    inputTop: desk.input?.top,
    inputDelta: Math.abs((desk.input?.top ?? NaN) - deskInput),
    deskScroll,
    scrollTop: desk.scrollTop,
    jumped: desk.jumped,
    frame: desk.taskFrame,
    paintFrames: desk.frames?.length ?? 0,
  };
  console.log(`DIAG desktop ${JSON.stringify(deskParts)}`);
  check("desktop task popup stays in view and does not push the input", desk.taskExpanded === "true" && desk.taskFrame.top >= 0 && desk.taskFrame.bottom <= 800.5 && desk.taskFrame.right <= 1280.5 && Math.abs(desk.input.top - deskInput) <= 1 && desk.scrollTop === deskScroll && desk.jumped !== "1", JSON.stringify(deskParts));

  await openPage(320, 900, true);
  await waitFor(`!!document.querySelector(".task-list .bar")`, "narrow task bar");
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  const narrowTasks = await evaluate("window.inspect()");
  await evaluate(`document.querySelector(".task-list .bar").click(); document.querySelector(".subagents .bar").click()`);
  const narrowAgents = await evaluate("window.inspect()");
  check("narrow tall screen keeps each open frame inside the viewport", narrowTasks.taskFrame.top >= 0 && narrowTasks.taskFrame.bottom <= 900.5 && narrowTasks.taskFrame.right <= 320.5 && narrowAgents.agentFrame.top >= 0 && narrowAgents.agentFrame.bottom <= 900.5 && narrowAgents.agentFrame.right <= 320.5 && narrowAgents.taskExpanded === "false", JSON.stringify({ task: narrowTasks.taskFrame, agent: narrowAgents.agentFrame }));

  await call("Page.bringToFront", {}, sessionId);
  await call("Emulation.setFocusEmulationEnabled", { enabled: true }, sessionId);
  const barPoint = async (selector) => evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return { missing: true, ok: false };
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    return {
      missing: false,
      x, y,
      top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height,
      ok: r.width > 8 && r.height > 8 && !!hit && (hit === el || el.contains(hit)),
      hit: hit ? String(hit.className || hit.tagName) : "none",
    };
  })()`);
  const nativeClick = async (point, mobile) => {
    if (!point || point.missing || !Number.isFinite(point.x)) throw new Error("native click missing point");
    if (mobile) {
      await call("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: point.x, y: point.y, radiusX: 1, radiusY: 1, force: 1, id: 1 }],
      }, sessionId);
      await call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }, sessionId);
      return;
    }
    await call("Input.dispatchMouseEvent", { type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1 }, sessionId);
    await call("Input.dispatchMouseEvent", { type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1 }, sessionId);
  };
  const expanded = async () => evaluate(`({ tasks: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") ?? "missing", agents: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") ?? "missing" })`);
  for (const [width, height, mobile] of [[390, 844, true], [320, 900, true], [1280, 800, false]]) {
    await openPage(width, height, mobile);
    await waitFor(`!!document.querySelector(".task-list .bar") && !!document.querySelector(".subagents .bar")`, `${width} both bars`);
    await waitSettled(`${width} collapsed`);
    const reading = await evaluate("document.querySelector('#log').scrollTop");
    const inputTop = await evaluate("document.querySelector('#input').getBoundingClientRect().top");
    await evaluate(`(() => {
      window.__hitFrames = [];
      const sample = () => {
        const next = {
          tasks: document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") ?? "missing",
          agents: document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") ?? "missing",
        };
        const last = window.__hitFrames[window.__hitFrames.length - 1];
        if (!last || last.tasks !== next.tasks || last.agents !== next.agents) window.__hitFrames.push(next);
        window.__hitRaf = requestAnimationFrame(sample);
      };
      if (window.__hitRaf) cancelAnimationFrame(window.__hitRaf);
      sample();
    })()`);
    const collapsedHits = { task: await barPoint(".task-list .bar"), agent: await barPoint(".subagents .bar") };
    check(`${width} collapsed both entries are the hit target`, collapsedHits.task.ok && collapsedHits.agent.ok, JSON.stringify(collapsedHits));
    await nativeClick(collapsedHits.task, mobile);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const tasksOpen = await expanded();
    const tasksOpenHits = { task: await barPoint(".task-list .bar"), agent: await barPoint(".subagents .bar") };
    check(`${width} native click opens tasks and both entries stay hittable`, tasksOpen.tasks === "true" && tasksOpen.agents !== "true" && tasksOpenHits.task.ok && tasksOpenHits.agent.ok, JSON.stringify({ tasksOpen, tasksOpenHits }));
    await nativeClick(tasksOpenHits.agent, mobile);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const agentsOpen = await expanded();
    const agentsOpenHits = { task: await barPoint(".task-list .bar"), agent: await barPoint(".subagents .bar") };
    check(`${width} native click opens subagents and both entries stay hittable`, agentsOpen.agents === "true" && agentsOpen.tasks !== "true" && agentsOpenHits.task.ok && agentsOpenHits.agent.ok, JSON.stringify({ agentsOpen, agentsOpenHits }));
    if (width === 390 && agentsOpen.agents === "true") {
      await evaluate(`Promise.all([...document.querySelectorAll(".composer-panels .fold")].flatMap((el) => [...el.getAnimations()].map((anim) => anim.finished.catch(() => {}))))`);
      const lastCard = await evaluate(`(() => {
        const list = document.querySelector(".subagents .list");
        const card = list?.querySelector(".card:last-child");
        const view = card?.querySelector(".view");
        if (!list || !card || !view) return { missing: true };
        list.scrollTop = list.scrollHeight;
        const lr = list.getBoundingClientRect();
        const cr = card.getBoundingClientRect();
        const vr = view.getBoundingClientRect();
        const x = vr.left + vr.width / 2;
        const y = vr.top + vr.height / 2;
        const hit = document.elementFromPoint(x, y);
        return {
          missing: false, x, y,
          title: card.querySelector(".title")?.textContent ?? "",
          fully: cr.top >= lr.top - 1 && cr.bottom <= lr.bottom + 1 && cr.height > 20,
          viewHit: !!hit && (hit === view || view.contains(hit)),
          hit: hit ? String(hit.className || hit.tagName) : "none",
        };
      })()`);
      check("last subagent card is fully in the list and its view is the hit target", lastCard.fully === true && lastCard.viewHit === true && lastCard.title === "任务A-8", JSON.stringify(lastCard));
      if (!lastCard.missing) {
        let detail = null;
        await evaluate(`window.__viewDetail = null; window.__onView = (event) => { window.__viewDetail = { id: event.detail.id, parentId: event.detail.parentId }; }; window.addEventListener("hp-view-subagent", window.__onView);`);
        await nativeClick(lastCard, mobile);
        await new Promise((resolve) => setTimeout(resolve, 40));
        detail = await evaluate(`window.__viewDetail`);
        await evaluate(`window.removeEventListener("hp-view-subagent", window.__onView)`);
        check("native click on 查看 reaches the last card", detail?.id === "a8" && detail.parentId === "A", JSON.stringify(detail));
      }
    }
    await nativeClick(agentsOpenHits.task, mobile);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const back = await expanded();
    const backHits = { task: await barPoint(".task-list .bar"), agent: await barPoint(".subagents .bar") };
    const paint = await evaluate("window.__hitFrames");
    await waitSettled(`${width} task restored`);
    const readingAfter = await evaluate("({ scroll: document.querySelector('#log').scrollTop, jumped: document.documentElement.dataset.jumped ?? '', input: document.querySelector('#input').getBoundingClientRect().top })");
    const hitParts = {
      backTasks: back.tasks === "true",
      backAgentsClosed: back.agents !== "true",
      hitTask: !!backHits.task.ok,
      hitAgent: !!backHits.agent.ok,
      paintTask: paint.some((frame) => frame.tasks === "true" && frame.agents !== "true"),
      paintAgent: paint.some((frame) => frame.agents === "true" && frame.tasks !== "true"),
      paintExclusive: !paint.some((frame) => frame.tasks === "true" && frame.agents === "true"),
      scrollSame: readingAfter.scroll === reading,
      notJumped: readingAfter.jumped !== "1",
      inputOk: Math.abs(readingAfter.input - inputTop) <= 1,
      reading,
      inputTop,
      readingAfter,
      inputDelta: Math.abs(readingAfter.input - inputTop),
      paintCount: paint.length,
      bothOpenFrames: paint.filter((frame) => frame.tasks === "true" && frame.agents === "true").length,
    };
    console.log(`DIAG${width} ${JSON.stringify(hitParts)}`);
    console.log(`HIT${width} ${JSON.stringify({ collapsedHits, tasksOpen, tasksOpenHits, agentsOpen, agentsOpenHits, back, backHits, paint, readingAfter, reading, inputTop })}`);
    check(`${width} native task then subagent then task keeps one open and both entries hit`, back.tasks === "true" && back.agents !== "true" && backHits.task.ok && backHits.agent.ok && paint.some((frame) => frame.tasks === "true" && frame.agents !== "true") && paint.some((frame) => frame.agents === "true" && frame.tasks !== "true") && !paint.some((frame) => frame.tasks === "true" && frame.agents === "true") && readingAfter.scroll === reading && readingAfter.jumped !== "1" && Math.abs(readingAfter.input - inputTop) <= 1, JSON.stringify({ back, backHits, paint, readingAfter, hitParts }));
  }

  for (const [width, height] of [[320, 568], [390, 844], [1280, 800]]) {
    await openPage(width, height, width < 600);
    for (const mode of ["both", "tasks", "agents", "empty"]) {
      for (const browserShown of [false, true]) {
        await evaluate(`window.harness.setSession(${JSON.stringify(mode === "tasks" || mode === "empty" ? "T" : "A")}); window.harness.setTasks(${mode === "agents" || mode === "empty" ? "[]" : '[{ id: "solo", content: "单栏任务", status: "pending" }]'}); window.harness.setBrowser(${browserShown});`);
        await new Promise((resolve) => setTimeout(resolve, 180));
        const stableInput = await evaluate("document.querySelector('#input').getBoundingClientRect().top");
        for (const type of ["task-list", "subagents"]) {
          const point = await barPoint(`.${type} .bar`);
          const expected = mode === "both" || (type === "task-list" ? mode === "tasks" : mode === "agents");
          check(`${width} ${mode} browser=${browserShown} ${type} presence`, point.missing !== expected, JSON.stringify(point));
          if (!expected || point.missing) continue;
          check(`${width} ${mode} ${type} entry reachable`, point.ok, JSON.stringify(point));
          await nativeClick(point, width < 600);
          await new Promise((resolve) => setTimeout(resolve, 320));
          const geometry = await evaluate(`(() => {
            const shell = document.querySelector('.panel-shell').getBoundingClientRect();
            const bar = document.querySelector('.${type} .bar');
            const list = document.querySelector('.${type} ${type === "task-list" ? "ul" : ".list"}');
            list.scrollTop = list.scrollHeight;
            const last = list.lastElementChild.getBoundingClientRect();
            const listBox = list.getBoundingClientRect();
            const firstBar = document.querySelector('.panel-shell .bar').getBoundingClientRect();
            return { open: bar.getAttribute('aria-expanded'), top: shell.top, bottom: shell.bottom, right: shell.right, lastVisible: last.top >= listBox.top - 1 && last.bottom <= listBox.bottom + 1 && last.bottom <= firstBar.top + 1, input: document.querySelector('#input').getBoundingClientRect().top };
          })()`);
          check(`${width} ${mode} browser=${browserShown} ${type} popup bounded and last row above summaries`, geometry.open === "true" && geometry.top >= 0 && geometry.bottom <= height && geometry.right <= width && geometry.lastVisible && geometry.input === stableInput, JSON.stringify(geometry));
          await nativeClick(await barPoint(`.${type} .bar`), width < 600);
          await new Promise((resolve) => setTimeout(resolve, 320));
        }
        if (mode === "empty") check(`${width} empty has no frame`, await evaluate("getComputedStyle(document.querySelector('.panel-shell')).display === 'none'"));
        const browserPoint = await barPoint(".browser-task button");
        check(`${width} ${mode} browser entry presence/reachability`, browserShown ? browserPoint.ok : browserPoint.missing, JSON.stringify(browserPoint));
        if (browserShown) {
          await evaluate("window.__browserEvent = null; window.addEventListener('hp-live-open', (event) => { window.__browserEvent = event.detail; }, { once: true });");
          await nativeClick(browserPoint, width < 600);
          const event = await evaluate("window.__browserEvent");
          check(`${width} ${mode} native browser entry event`, event?.scope === "task" && event.active === true, JSON.stringify(event));
        }
      }
    }
  }

  const entryChrome = async (label) => {
    const chrome = await evaluate(`(() => {
      const bordered = (selector) => {
        let node = document.querySelector(selector);
        const chain = [];
        while (node) {
          const style = getComputedStyle(node);
          chain.push(node.className || node.tagName);
          if (style.display !== "contents" && parseFloat(style.borderTopWidth) >= 1 && parseFloat(style.borderLeftWidth) >= 1 && parseFloat(style.borderTopLeftRadius) >= 8) {
            const r = node.getBoundingClientRect();
            return { top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height, radius: style.borderTopLeftRadius, border: style.borderTopWidth, cls: String(node.className || "") };
          }
          node = node.parentElement;
        }
        return { missing: true, chain };
      };
      const shell = document.querySelector(".panel-shell");
      const shellStyle = shell ? getComputedStyle(shell) : null;
      const task = bordered(".task-list .bar");
      const agent = bordered(".subagents .bar");
      return {
        task,
        agent,
        shellBorder: shellStyle ? shellStyle.borderTopWidth : null,
        shellBg: shellStyle ? shellStyle.backgroundColor : null,
      };
    })()`);
    const gap = chrome.task && chrome.agent && chrome.task.left != null && chrome.agent.left != null ? chrome.agent.left - chrome.task.right : null;
    const same = gap != null && Math.abs(chrome.task.left - chrome.agent.left) < 1 && Math.abs(chrome.task.right - chrome.agent.right) < 1;
    const shellClear = chrome.shellBorder === "0px" && (chrome.shellBg === "rgba(0, 0, 0, 0)" || chrome.shellBg === "transparent");
    const independent = !chrome.task?.missing && !chrome.agent?.missing && !same && gap >= 7.5 && parseFloat(chrome.task.radius) >= 8 && parseFloat(chrome.agent.radius) >= 8 && shellClear;
    console.log(`CHROME ${label} ${JSON.stringify({ gap, same, shellClear, task: chrome.task, agent: chrome.agent, shellBorder: chrome.shellBorder, shellBg: chrome.shellBg })}`);
    check(`${label} entries keep separate rounded borders and an 8px gap`, independent, JSON.stringify({ gap, same, shellBorder: chrome.shellBorder, shellBg: chrome.shellBg, task: chrome.task, agent: chrome.agent }));
    return chrome;
  };
  const installSampler = async () => evaluate(`window.__sampleFold = (selector, ratio) => {
    const fold = document.querySelector(selector);
    if (!fold) return { missing: true, reason: "no fold" };
    const anims = [...fold.getAnimations()];
    const timing = anims.map((anim) => {
      const spec = anim.effect && anim.effect.getTiming ? anim.effect.getTiming() : {};
      return { prop: anim.transitionProperty || "", easing: spec.easing || "", duration: spec.duration, play: anim.playState };
    });
    for (const anim of anims) {
      try {
        anim.pause();
        const duration = Number(anim.effect.getTiming().duration) || 0;
        anim.currentTime = duration * ratio;
      } catch (error) {}
    }
    void fold.offsetWidth;
    const style = getComputedStyle(fold);
    const matrix = new DOMMatrix(style.transform);
    const r = fold.getBoundingClientRect();
    const frame = fold.closest(".frame");
    const bar = frame ? frame.querySelector(".bar") : null;
    const br = bar ? bar.getBoundingClientRect() : null;
    const shell = document.querySelector(".panel-shell");
    const sr = shell ? shell.getBoundingClientRect() : null;
    const list = fold.querySelector("ul, .list");
    const lr = list ? list.getBoundingClientRect() : null;
    return {
      missing: anims.length === 0,
      timing,
      opacity: Number(style.opacity),
      easing: style.transitionTimingFunction,
      duration: style.transitionDuration,
      property: style.transitionProperty,
      scaleX: matrix.a,
      scaleY: matrix.d,
      box: { top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2 },
      bar: br ? { top: br.top, left: br.left, right: br.right, bottom: br.bottom, width: br.width, cx: br.left + br.width / 2 } : null,
      shell: sr ? { width: sr.width, top: sr.top, left: sr.left, right: sr.right } : null,
      list: lr ? { top: lr.top, bottom: lr.bottom, width: lr.width, height: lr.height } : null,
      inert: fold.hasAttribute("inert"),
      pe: style.pointerEvents,
      expanded: bar ? bar.getAttribute("aria-expanded") : null,
    };
  }`);
  const finishFolds = () => evaluate(`(() => {
    for (const fold of document.querySelectorAll(".composer-panels .fold")) {
      for (const anim of fold.getAnimations()) {
        try { anim.finish(); } catch (error) { anim.cancel(); }
      }
    }
    return true;
  })()`);
  for (const [width, height, mobile] of [[320, 568], [390, 844], [1280, 800]]) {
    await openPage(width, height, width < 600);
    await waitFor(`!!document.querySelector(".task-list .bar") && !!document.querySelector(".subagents .bar")`, `${width} fold bars`);
    await evaluate(`Promise.all([...document.querySelectorAll(".composer-panels .slot-tasks, .composer-panels .slot-agents, .composer-panels .panel-shell")].flatMap((el) => [...el.getAnimations()].map((anim) => anim.finished.catch(() => {}))))`);
    await installSampler();
    await entryChrome(`${width} collapsed`);
    const slotTiming = await evaluate(`getComputedStyle(document.querySelector(".slot-tasks")).transitionTimingFunction`);
    check(`${width} split easing stays cubic-bezier`, String(slotTiming).includes("cubic-bezier") && String(slotTiming).includes("0.32") && String(slotTiming).includes("0.72"), slotTiming);
    for (const [which, selector] of [["tasks", ".task-list .fold"], ["subagents", ".subagents .fold"]]) {
      const barSelector = which === "tasks" ? ".task-list .bar" : ".subagents .bar";
      const inputBefore = await evaluate(`document.querySelector("#input").getBoundingClientRect().top`);
      await nativeClick(await barPoint(barSelector), mobile);
      await new Promise((resolve) => setTimeout(resolve, 40));
      const start = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 0)`);
      const mid = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 0.5)`);
      const end = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 1)`);
      console.log(`MOTION ${width} ${which} ${JSON.stringify({ start, mid, end })}`);
      const linearEase = String(start.easing || "").includes("linear") && !String(start.easing || "").includes("0.32");
      const near = start.box && start.bar && start.box.height > 1 && start.box.height < 48 && start.box.cx >= start.bar.left - 8 && start.box.cx <= start.bar.right + 8 && start.box.bottom <= start.bar.top + 12 && start.box.bottom >= start.bar.top - 28;
      const denom = (end.scaleY ?? 1) - (start.scaleY ?? 1);
      const progress = Math.abs(denom) < 0.05 ? null : (mid.scaleY - start.scaleY) / denom;
      check(`${width} ${which} linear expand starts at its entry`, !start.missing && linearEase && String(start.duration || "").includes("0.2s") && !String(start.duration || "").includes("0.28") && !String(start.property || "").includes("grid-template") && near, JSON.stringify({ missing: start.missing, easing: start.easing, duration: start.duration, property: start.property, box: start.box, bar: start.bar, timing: start.timing }));
      check(`${width} ${which} midpoint is linear not the panel curve`, progress != null && progress > 0.35 && progress < 0.65, JSON.stringify({ progress, start: start.scaleY, mid: mid.scaleY, end: end.scaleY }));
      const full = end.list && end.shell && end.bar && end.list.top >= 0 && end.list.width >= end.shell.width * 0.75 && end.list.bottom <= end.bar.top + 1 && end.opacity > 0.9;
      const inputAfter = await evaluate(`document.querySelector("#input").getBoundingClientRect().top`);
      check(`${width} ${which} open list is full width above the bars and does not push input`, full && Math.abs(inputAfter - inputBefore) <= 1, JSON.stringify({ list: end.list, shell: end.shell, bar: end.bar, opacity: end.opacity, inputBefore, inputAfter }));
      await shot(`fold-${width}-${which}-open.png`);
      await entryChrome(`${width} ${which} open`);
      await finishFolds();
      if (await evaluate(`document.querySelector(${JSON.stringify(barSelector)})?.getAttribute("aria-expanded") === "true"`)) {
        await nativeClick(await barPoint(barSelector), mobile);
        await waitFor(`document.querySelector(${JSON.stringify(barSelector)})?.getAttribute("aria-expanded") !== "true"`, `${width} ${which} reset close`);
        await finishFolds();
      }
      await nativeClick(await barPoint(barSelector), mobile);
      await new Promise((resolve) => setTimeout(resolve, 40));
      const at40 = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 0.4)`);
      check(`${width} ${which} reverse samples a mid-flight frame`, at40.scaleY > 0.3 && at40.scaleY < 0.6, JSON.stringify({ scale: at40.scaleY, box: at40.box, missing: at40.missing }));
      await nativeClick(await barPoint(barSelector), mobile);
      await waitFor(`document.querySelector(${JSON.stringify(barSelector)})?.getAttribute("aria-expanded") !== "true"`, `${width} ${which} closing`);
      const reverseStart = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 0)`);
      const reverseEnd = await evaluate(`window.__sampleFold(${JSON.stringify(selector)}, 1)`);
      const jump = reverseStart.box && at40.box ? Math.hypot(reverseStart.box.cx - at40.box.cx, reverseStart.box.cy - at40.box.cy) : 999;
      const returned = reverseEnd.box && reverseEnd.bar && reverseEnd.box.height > 1 && reverseEnd.box.height < 48 && reverseEnd.box.cx >= reverseEnd.bar.left - 8 && reverseEnd.box.cx <= reverseEnd.bar.right + 8 && reverseEnd.opacity < 0.2;
      console.log(`REVERSE ${width} ${which} ${JSON.stringify({ jump, at40: at40.box, reverseStart: reverseStart.box, reverseEnd: reverseEnd.box, opacity: reverseEnd.opacity, missing: reverseStart.missing })}`);
      check(`${width} ${which} reverse continues from the current frame and returns to the entry`, !reverseStart.missing && jump <= 12 && returned, JSON.stringify({ jump, missing: reverseStart.missing, at40: at40.box, reverseStart: reverseStart.box, reverseEnd: reverseEnd.box, opacity: reverseEnd.opacity }));
      await finishFolds();
      const hidden = await evaluate(`(() => {
        const fold = document.querySelector(${JSON.stringify(selector)});
        const bar = document.querySelector(${JSON.stringify(barSelector)});
        const br = bar.getBoundingClientRect();
        const hit = document.elementFromPoint(br.left + br.width / 2, br.top - 80);
        return { inert: fold.hasAttribute("inert"), pe: getComputedStyle(fold).pointerEvents, expanded: bar.getAttribute("aria-expanded"), hitFold: !!(hit && (hit === fold || fold.contains(hit))), hit: hit ? String(hit.className || hit.tagName) : "none" };
      })()`);
      check(`${width} ${which} closed list is not interactive`, hidden.expanded !== "true" && hidden.inert === true && hidden.pe === "none" && hidden.hitFold === false, JSON.stringify(hidden));
    }
  }
  await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] }, sessionId);
  await openPage(390, 844, true);
  await waitFor(`!!document.querySelector(".task-list .bar")`, "reduced bars");
  const reducedBefore = await evaluate(`(() => {
    const fold = document.querySelector(".task-list .fold");
    const style = getComputedStyle(fold);
    return { property: style.transitionProperty, duration: style.transitionDuration };
  })()`);
  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const reduced = await evaluate(`(() => {
    const fold = document.querySelector(".task-list .fold");
    const running = [...fold.getAnimations()].filter((anim) => anim.playState === "running");
    const r = fold.getBoundingClientRect();
    return { running: running.length, height: r.height, opacity: Number(getComputedStyle(fold).opacity), expanded: document.querySelector(".task-list .bar").getAttribute("aria-expanded") };
  })()`);
  console.log(`REDUCED ${JSON.stringify({ reducedBefore, reduced })}`);
  check("reduced motion has no fold animation", (reducedBefore.property === "none" || reducedBefore.duration === "0s") && reduced.running === 0 && reduced.expanded === "true" && reduced.height > 40 && reduced.opacity > 0.9, JSON.stringify({ reducedBefore, reduced }));
  await shot("fold-390-reduced-open.png");
  await call("Emulation.setEmulatedMedia", { features: [] }, sessionId);

  const splitDir = "/root/hermes-workspace/fullgate-evidence/panel-fold-motion/fold";
  await mkdir(splitDir, { recursive: true });
  const splitShot = async (name) => {
    const { data } = await call("Page.captureScreenshot", { format: "png" }, sessionId);
    const file = path.join(splitDir, name);
    await writeFile(file, Buffer.from(data, "base64"));
    console.log(`SHOT ${file}`);
  };
  const splitGeom = async () => evaluate(`(() => {
    const task = document.querySelector(".slot-tasks");
    const agent = document.querySelector(".slot-agents");
    const taskBar = document.querySelector(".task-list .bar");
    const agentBar = document.querySelector(".subagents .bar");
    const box = document.querySelector("#input");
    const shell = document.querySelector(".panel-shell");
    const rect = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    const taskSlot = rect(task);
    const agentSlot = rect(agent);
    const input = rect(box);
    const shellBox = shell && getComputedStyle(shell).display !== "none" ? rect(shell) : null;
    const visibleBottom = (slot) => slot && slot.width > 8 ? slot.bottom : null;
    const bottoms = [visibleBottom(taskSlot), visibleBottom(agentSlot)].filter((value) => value !== null);
    const gap = bottoms.length && input ? input.top - Math.max(...bottoms) : null;
    const timing = task ? getComputedStyle(task).transitionTimingFunction : "";
    return {
      taskSlot, agentSlot, taskBar: rect(taskBar), agentBar: rect(agentBar), input, shell: shellBox, gap, timing,
      shellWidth: shellBox ? shellBox.width : 0,
    };
  })()`);
  const assertGap = (label, geom) => {
    console.log(`GAP ${label} ${JSON.stringify(geom)}`);
    check(`${label} input gap >= 6px`, geom.gap >= 6, `gap=${geom.gap}`);
  };
  const assertSplit = (label, geom) => {
    const task = geom.taskBar;
    const agent = geom.agentBar;
    const side = task && agent && task.right + 4 <= agent.left;
    const row = task && agent && Math.abs(task.top - agent.top) <= 2;
    const similar = task && agent && Math.abs(task.width - agent.width) <= 12;
    check(`${label} tasks left agents right`, side && row && similar, JSON.stringify({ task, agent, sideGap: task && agent ? agent.left - task.right : null }));
  };
  const assertFull = (label, geom, which) => {
    const slot = which === "tasks" ? geom.taskSlot : geom.agentSlot;
    const other = which === "tasks" ? geom.agentSlot : geom.taskSlot;
    const full = slot && geom.shellWidth > 0 && slot.width >= geom.shellWidth - 2;
    const hidden = !other || other.width <= 2;
    check(`${label} single bar fills the shell`, full && hidden, JSON.stringify({ slot, other, shellWidth: geom.shellWidth }));
  };

  await openPage(390, 844, true);
  await evaluate(`window.harness.setSession("T"); window.harness.setTasks([{ id: "solo", content: "单栏任务", status: "pending" }]);`);
  await waitFor(`!!document.querySelector(".task-list .bar") && !document.querySelector(".subagents .bar")`, "single task bar");
  await waitSettled("single collapsed");
  const singleBefore = await splitGeom();
  assertGap("390 single collapsed", singleBefore);
  assertFull("390 single collapsed", singleBefore, "tasks");
  check("split timing is the panel curve", String(singleBefore.timing).includes("cubic-bezier") && String(singleBefore.timing).includes("0.32") && String(singleBefore.timing).includes("0.72"), singleBefore.timing);
  await splitShot("phone-390-single-collapsed.png");

  await evaluate(`(() => {
    window.__splitWidths = [];
    const sample = () => {
      const slot = document.querySelector(".slot-tasks");
      if (slot) window.__splitWidths.push(Math.round(slot.getBoundingClientRect().width * 10) / 10);
      window.__splitRaf = requestAnimationFrame(sample);
    };
    sample();
  })()`);
  await evaluate(`window.harness.setSession("A"); window.harness.setTasks([{ id: "solo", content: "单栏任务", status: "pending" }]);`);
  await waitFor(`!!document.querySelector(".task-list .bar") && !!document.querySelector(".subagents .bar")`, "split both bars");
  await new Promise((resolve) => setTimeout(resolve, 420));
  const motion = await evaluate(`(() => { cancelAnimationFrame(window.__splitRaf); return window.__splitWidths; })()`);
  const fullW = singleBefore.taskSlot?.width ?? 0;
  const endW = motion[motion.length - 1] ?? 0;
  const mid = motion.filter((width) => width < fullW - 8 && width > endW + 8);
  console.log(`SPLIT-MOTION ${JSON.stringify({ fullW, endW, samples: motion.length, mid: mid.slice(0, 8), first: motion.slice(0, 4), last: motion.slice(-4) })}`);
  check("full-to-split has intermediate widths", mid.length > 0, `full=${fullW} end=${endW} samples=${motion.length}`);

  await waitSettled("390 both collapsed");
  const both390 = await splitGeom();
  assertGap("390 both collapsed", both390);
  assertSplit("390 both collapsed", both390);
  await splitShot("phone-390-both-collapsed.png");

  await evaluate(`document.querySelector(".task-list .bar").click()`);
  await waitFor(`document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") === "true"`, "390 tasks open shot");
  await new Promise((resolve) => setTimeout(resolve, 320));
  const tasksOpenGeom = await evaluate(`(() => {
    const shell = document.querySelector(".panel-shell");
    const list = document.querySelector(".task-list ul");
    const taskBar = document.querySelector(".task-list .bar");
    const agentBar = document.querySelector(".subagents .bar");
    const sr = shell.getBoundingClientRect();
    const lr = list.getBoundingClientRect();
    const tr = taskBar.getBoundingClientRect();
    const ar = agentBar.getBoundingClientRect();
    return {
      contains: shell.contains(list) && shell.contains(taskBar) && shell.contains(agentBar),
      listAbove: lr.bottom <= tr.top + 1,
      side: tr.right + 4 <= ar.left && Math.abs(tr.top - ar.top) <= 2,
      shellTop: sr.top,
      shellBottom: sr.bottom,
    };
  })()`);
  console.log(`OPEN390-TASKS ${JSON.stringify(tasksOpenGeom)}`);
  check("390 open task frame contains list and both bars", tasksOpenGeom.contains && tasksOpenGeom.listAbove && tasksOpenGeom.side, JSON.stringify(tasksOpenGeom));
  await splitShot("phone-390-tasks-open.png");

  await evaluate(`document.querySelector(".subagents .bar").click()`);
  await waitFor(`document.querySelector(".subagents .bar")?.getAttribute("aria-expanded") === "true" && document.querySelector(".task-list .bar")?.getAttribute("aria-expanded") !== "true"`, "390 agents open shot");
  await new Promise((resolve) => setTimeout(resolve, 320));
  const agentsOpenGeom = await evaluate(`(() => {
    const shell = document.querySelector(".panel-shell");
    const list = document.querySelector(".subagents .list");
    const taskBar = document.querySelector(".task-list .bar");
    const agentBar = document.querySelector(".subagents .bar");
    return {
      contains: shell.contains(list) && shell.contains(taskBar) && shell.contains(agentBar),
      listAbove: list.getBoundingClientRect().bottom <= agentBar.getBoundingClientRect().top + 1,
      agents: agentBar.getAttribute("aria-expanded"),
      tasks: taskBar.getAttribute("aria-expanded"),
    };
  })()`);
  console.log(`OPEN390-AGENTS ${JSON.stringify(agentsOpenGeom)}`);
  check("390 open subagent frame contains list and summaries", agentsOpenGeom.contains && agentsOpenGeom.listAbove && agentsOpenGeom.agents === "true" && agentsOpenGeom.tasks !== "true", JSON.stringify(agentsOpenGeom));
  await splitShot("phone-390-subagents-open.png");

  await openPage(1280, 800, false);
  await waitFor(`!!document.querySelector(".task-list .bar") && !!document.querySelector(".subagents .bar")`, "1280 both bars");
  await new Promise((resolve) => setTimeout(resolve, 400));
  await waitSettled("1280 both collapsed");
  const deskBoth = await splitGeom();
  assertGap("1280 both collapsed", deskBoth);
  assertSplit("1280 both collapsed", deskBoth);
  await splitShot("desktop-1280-both-collapsed.png");

  if (failures.length) {
    console.log(`RED ${failures.length}`);
    process.exitCode = 1;
  } else {
    console.log("GREEN task-popup-mutex");
  }
} catch (error) {
  console.error(`HARNESS ${error.stack || error}`);
  process.exitCode = 1;
} finally {
  await stop();
  if (cleanupErrors.length) {
    console.error(`CLEANUP ${cleanupErrors.join("; ")}`);
    process.exitCode = 1;
  }
  if (process.exitCode) process.exit(process.exitCode);
}
