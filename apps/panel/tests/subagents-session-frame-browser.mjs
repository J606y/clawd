import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const evidence = "/root/hermes-workspace/fullgate-evidence/panel-fold-motion/fold";
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "subagents-ui-"));
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
      browser.kill("SIGTERM");
      await new Promise((resolve) => browser.once("exit", resolve));
    }
    if (server) await server.close();
  } catch (error) {
    cleanupErrors.push(`stop ${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    await rm(owned, { recursive: true, force: true });
  } catch (error) {
    cleanupErrors.push(`rm ${error instanceof Error ? error.message : String(error)}`);
  }
};

const check = (name, ok, detail) => {
  const line = `${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`;
  console.log(line);
  if (!ok) failures.push(line);
};

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
  await call("Page.enable", {}, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const openPage = async (width, height, mobile) => {
    await call("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile }, sessionId);
    await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/subagents-session-frame-fixture.html?vp=${width}` }, sessionId);
    for (let n = 0; n < 80 && !await evaluate("window.ready === true"); n += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!await evaluate("window.ready === true")) throw new Error(`fixture did not initialize at ${width}`);
  };
  const waitFor = async (expression, label) => {
    for (let n = 0; n < 40; n += 1) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error(`timeout: ${label}`);
  };
  const shot = async (name) => {
    const { data } = await call("Page.captureScreenshot", { format: "png" }, sessionId);
    const file = path.join(evidence, name);
    await writeFile(file, Buffer.from(data, "base64"));
    console.log(`SHOT ${file}`);
  };

  await openPage(390, 844, true);
  await waitFor(`!!document.querySelector("#chat .title")`, "chat cards");
  await waitFor(`!!document.querySelector("#running .bar")`, "running bar");
  let snap = await evaluate("window.inspect()");
  console.log(`STATE390 ${JSON.stringify({ titles: snap.titles, barText: snap.barText, expanded: snap.expanded, requests: snap.requests, runningExpanded: snap.runningExpanded })}`);
  check("session A requests limit 50", snap.requests.some((item) => item.session === "A" && item.limit === "50"), JSON.stringify(snap.requests));
  check("session A hides foreign and unassigned", snap.titles.length === 8 && snap.titles.every((title) => title.startsWith("任务A-")), snap.titles.join(","));
  check("session A count ignores foreign running", snap.barText.includes("无进行中") && snap.barText.includes("8 个已结束") && !/\d+ 个进行中/.test(snap.barText), snap.barText);
  check("foreign running does not auto-expand", snap.expanded === "false", snap.expanded);
  check("owned running stays closed on first mount", snap.runningExpanded !== "true" && snap.runningTitle === "进行中的R", `${snap.runningExpanded} ${snap.runningTitle}`);

  await evaluate(`document.querySelector("#running").hidden = true`);
  if (snap.expanded === "true") await evaluate(`document.querySelector("#chat .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const collapsed = await evaluate("window.inspect()");
  check("collapsed bar is inside the bordered frame", collapsed.barInsideFrame && collapsed.expanded === "false", `inside=${collapsed.barInsideFrame} expanded=${collapsed.expanded}`);
  const composerTop = collapsed.composer?.top;
  const scrollBefore = collapsed.scrollTop;
  await evaluate(`document.querySelector("#chat .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const opened = await evaluate("window.inspect()");
  console.log(`GEOM390 ${JSON.stringify({ gapLeft: opened.gapLeft, gapRight: opened.gapRight, listClient: opened.listClient, frame: opened.frame, composer: opened.composer })}`);
  check("open bar stays inside the same frame as cards", opened.barInsideFrame && opened.expanded === "true" && opened.card, opened.barText);
  check("cards have at least 8px side inset", opened.gapLeft >= 8 && opened.gapRight >= 8, `L${opened.gapLeft} R${opened.gapRight}`);
  const oldCap = Math.min(844 * 0.38, 340);
  check("open list can exceed the old 38vh/340 cap", opened.listClient > oldCap + 4, `client=${opened.listClient} old=${oldCap}`);
  check("open frame stays inside the 390 viewport", opened.frame && opened.frame.top >= 0 && opened.frame.bottom <= 844 && opened.frame.left >= 0 && opened.frame.right <= 390.5, JSON.stringify(opened.frame));
  check("expand grows the frame upward", opened.frame && collapsed.frame && opened.frame.height > collapsed.frame.height + 40 && opened.frame.top < collapsed.frame.top - 20, `openH=${opened.frame?.height} closedH=${collapsed.frame?.height}`);
  check("expand does not push the composer", Math.abs((opened.composer?.top ?? 0) - composerTop) <= 1, `before=${composerTop} after=${opened.composer?.top}`);
  check("expand does not jump the message list", opened.scrollTop === scrollBefore && opened.jumped !== "1", `scroll=${opened.scrollTop} jumped=${opened.jumped}`);
  check("bar and view are hit targets", opened.barHit && opened.viewHit, `bar=${opened.barHit} view=${opened.viewHit}`);
  const viewed = await evaluate(`(() => {
    let detail = null;
    const onView = (event) => { detail = event.detail; };
    window.addEventListener("hp-view-subagent", onView);
    document.querySelector("#chat .view").click();
    window.removeEventListener("hp-view-subagent", onView);
    return detail ? { id: detail.id, parentId: detail.parentId, w: detail.origin?.width ?? 0 } : null;
  })()`);
  check("view event keeps id and origin", viewed?.id === "a1" && viewed.parentId === "A" && viewed.w > 0, JSON.stringify(viewed));
  const lastCard = await evaluate(`(() => {
    const list = document.querySelector("#chat .list");
    const overflow = list.scrollHeight > list.clientHeight + 8;
    list.scrollTop = list.scrollHeight;
    const card = list.querySelector(".card:last-child");
    const view = card?.querySelector(".view");
    const lr = list.getBoundingClientRect();
    const cr = card.getBoundingClientRect();
    const vr = view.getBoundingClientRect();
    const hit = document.elementFromPoint(vr.left + vr.width / 2, vr.top + vr.height / 2);
    return {
      overflow,
      fully: cr.top >= lr.top - 1 && cr.bottom <= lr.bottom + 1,
      viewHit: hit === view || view.contains(hit),
      title: card.querySelector(".title")?.textContent ?? "",
    };
  })()`);
  check("last card is fully readable and its view is hittable", lastCard.overflow && lastCard.fully && lastCard.viewHit && lastCard.title === "任务A-8", JSON.stringify(lastCard));
  check("chev timing is unchanged", opened.chevDuration === "0.28s" && opened.chevTiming.includes("0.32") && opened.foldTransition.includes("transform") && !opened.foldTransition.includes("grid-template"), `${opened.chevDuration} ${opened.chevTiming} ${opened.foldTransition}`);
  await shot("subagents-390-open.png");

  await evaluate(`document.querySelector("#chat .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const closed = await evaluate("window.inspect()");
  check("closed bar remains inside the frame", closed.barInsideFrame && closed.expanded === "false", closed.barText);
  check("close does not push the composer or jump", Math.abs((closed.composer?.top ?? 0) - composerTop) <= 1 && closed.scrollTop === scrollBefore && closed.jumped !== "1", `composer=${closed.composer?.top} scroll=${closed.scrollTop}`);
  const tab = await evaluate(`(() => {
    const root = document.querySelector("#chat");
    const view = root.querySelector(".view");
    const before = document.activeElement;
    view.focus();
    const focused = document.activeElement === view;
    if (document.activeElement === view) document.activeElement.blur();
    root.querySelector(".bar").focus();
    return { focused, inert: root.querySelector(".fold").hasAttribute("inert"), active: document.activeElement?.className ?? "" };
  })()`);
  check("closed list is not focusable", tab.focused === false, JSON.stringify(tab));
  await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
  await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
  const afterTab = await evaluate(`(() => {
    const active = document.activeElement;
    const list = document.querySelector("#chat .list");
    return { cls: active?.className ?? "", inList: !!(list && active && list.contains(active)) };
  })()`);
  check("tab after close does not enter the list", afterTab.inList === false, JSON.stringify(afterTab));

  const markB = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.hold.add("B"); window.harness.setSession("B");`);
  await waitFor(`window.harness.requests.slice(${markB}).some((item) => item.session === "B")`, "B request");
  const pendingB = await evaluate("window.inspect()");
  check("A to B delay shows no old titles or counts", pendingB.titles.length === 0 && !pendingB.barText.includes("任务A") && !pendingB.barText.includes("8 个"), pendingB.barText || pendingB.titles.join(","));
  await evaluate(`window.harness.release("B")`);
  await waitFor(`!!document.querySelector("#chat .title")`, "B cards");
  const shownB = await evaluate("window.inspect()");
  check("B shows only B", shownB.titles.length === 1 && shownB.titles[0] === "任务B" && shownB.barText.includes("1 个已结束") && !/\d+ 个进行中/.test(shownB.barText), `${shownB.titles.join(",")} ${shownB.barText}`);

  const markC = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.hold.add("C"); window.harness.setSession("C");`);
  await waitFor(`window.harness.requests.slice(${markC}).some((item) => item.session === "C")`, "C inflight");
  await evaluate(`window.harness.setSession("B"); window.harness.release("C");`);
  await new Promise((resolve) => setTimeout(resolve, 150));
  const lateC = await evaluate("window.inspect()");
  check("late C does not leak onto B", !lateC.titles.includes("迟到C") && lateC.titles.includes("任务B"), lateC.titles.join(","));

  const markD = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.hold.add("D"); window.harness.setSession("D");`);
  await waitFor(`window.harness.requests.slice(${markD}).some((item) => item.session === "D")`, "D inflight");
  const markEmptyLate = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.setListOnly(false); window.harness.setSession(""); window.harness.release("D");`);
  await new Promise((resolve) => setTimeout(resolve, 150));
  const lateEmpty = await evaluate(`({ titles: [...document.querySelectorAll("#chat .title")].map((node) => node.textContent), bar: document.querySelector("#chat .bar")?.textContent ?? "", added: window.harness.requests.slice(${markEmptyLate}) })`);
  check("late D does not leak onto an empty session", lateEmpty.titles.length === 0 && !lateEmpty.bar.includes("迟到D") && lateEmpty.added.every((item) => item.session !== ""), JSON.stringify(lateEmpty));

  const markEmpty = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.setListOnly(false); window.harness.setSession("");`);
  await new Promise((resolve) => setTimeout(resolve, 400));
  const empty = await evaluate(`({ added: window.harness.requests.slice(${markEmpty}), titles: [...document.querySelectorAll("#chat .title")].map((node) => node.textContent), bar: document.querySelector("#chat .bar")?.textContent ?? "" })`);
  check("empty chat does not request global", empty.added.every((item) => item.session !== ""), JSON.stringify(empty.added));
  check("empty chat does not show other tasks", empty.titles.length === 0 && !empty.bar.includes("全局"), `${empty.titles.join(",")} ${empty.bar}`);

  const markList = await evaluate("window.harness.requests.length");
  await evaluate(`window.harness.setListOnly(true); window.harness.setSession("");`);
  await waitFor(`!!document.querySelector("#chat .title")`, "listOnly cards");
  const listed = await evaluate(`window.inspect()`);
  const listReqs = listed.requests.slice(markList);
  check("listOnly empty session still requests global", listReqs.some((item) => item.session === "" && item.limit === "12"), JSON.stringify(listReqs));
  check("listOnly still shows global tasks", listed.titles.includes("全局任务"), listed.titles.join(","));

  await evaluate("window.harness.mountProbe()");
  await waitFor(`document.querySelector("#hook-probe")?.textContent?.startsWith("count:")`, "hook probe");
  const probe = await evaluate(`({ text: document.querySelector("#hook-probe").textContent, requests: window.harness.requests.filter((item) => item.session === "" && item.limit === "12").length })`);
  check("useSubagents default enabled still fetches global", probe.text === "count:1" && probe.requests >= 1, JSON.stringify(probe));

  await openPage(1280, 800, false);
  await waitFor(`!!document.querySelector("#chat .title")`, "desktop cards");
  await evaluate(`document.querySelector("#running").hidden = true`);
  let desk = await evaluate("window.inspect()");
  if (desk.expanded === "true") {
    await evaluate(`document.querySelector("#chat .bar").click()`);
    desk = await evaluate("window.inspect()");
  }
  const deskComposer = desk.composer?.top;
  const deskScroll = desk.scrollTop;
  check("desktop collapsed bar is inside the frame", desk.barInsideFrame && desk.expanded === "false", desk.barText);
  await evaluate(`document.querySelector("#chat .bar").click()`);
  await new Promise((resolve) => setTimeout(resolve, 50));
  desk = await evaluate("window.inspect()");
  console.log(`GEOM1280 ${JSON.stringify({ gapLeft: desk.gapLeft, gapRight: desk.gapRight, listClient: desk.listClient, frame: desk.frame })}`);
  const deskOld = Math.min(800 * 0.38, 340);
  check("desktop cards inset and list exceeds old cap", desk.gapLeft >= 8 && desk.gapRight >= 8 && desk.listClient > deskOld + 4, `L${desk.gapLeft} R${desk.gapRight} h=${desk.listClient}`);
  check("desktop frame stays in viewport and does not push composer", desk.frame && desk.frame.top >= 0 && desk.frame.bottom <= 800 && desk.frame.right <= 1280.5 && Math.abs(desk.composer.top - deskComposer) <= 1 && desk.scrollTop === deskScroll && desk.jumped !== "1", JSON.stringify(desk.frame));
  check("desktop bar and view are hit targets", desk.barHit && desk.viewHit, `bar=${desk.barHit} view=${desk.viewHit}`);
  await shot("subagents-1280-open.png");
  await evaluate(`document.querySelector("#chat .bar").click()`);
  const deskClosed = await evaluate(`(() => { const view = document.querySelector("#chat .view"); view.focus(); return { focused: document.activeElement === view, inside: window.inspect().barInsideFrame }; })()`);
  check("desktop closed list is not focusable and bar stays framed", deskClosed.focused === false && deskClosed.inside, JSON.stringify(deskClosed));

  await evaluate(`window.harness.setListOnly(false); window.harness.setSession("N");`);
  await waitFor(`document.querySelectorAll("#chat .card").length === 25`, "25 session cards");
  const many = await evaluate(`({
    limits: window.harness.requests.filter((item) => item.session === "N").map((item) => item.limit),
    cards: document.querySelectorAll("#chat .card").length,
    titles: document.querySelectorAll("#chat .title").length,
    bar: document.querySelector("#chat .bar")?.textContent ?? "",
  })`);
  console.log(`LIMIT50 ${JSON.stringify(many)}`);
  check("session request uses limit 50 and renders all 25 cards", many.limits.includes("50") && many.cards === 25 && many.titles === 25 && many.bar.includes("25 个已结束") && !many.bar.includes("20 个"), JSON.stringify(many));

  await evaluate(`window.harness.setListOnly(false); window.harness.setSession("R");`);
  await waitFor(`document.querySelector("#chat .title")?.textContent === "进行中的R"`, "switch to running R");
  await new Promise((resolve) => setTimeout(resolve, 250));
  const switched = await evaluate(`({ expanded: document.querySelector("#chat .bar")?.getAttribute("aria-expanded"), title: document.querySelector("#chat .title")?.textContent ?? "" })`);
  console.log(`SWITCH-R ${JSON.stringify(switched)}`);
  check("switching into a running session stays closed", switched.expanded !== "true" && switched.title === "进行中的R", JSON.stringify(switched));
  if (switched.expanded === "true") await evaluate(`document.querySelector("#chat .bar").click()`);
  await evaluate(`document.querySelector("#chat .bar").click()`);
  const manual = await evaluate(`document.querySelector("#chat .bar")?.getAttribute("aria-expanded")`);
  check("uncontrolled bar can still be opened manually", manual === "true", String(manual));
  await evaluate(`window.harness.setSession("B");`);
  await waitFor(`document.querySelector("#chat .title")?.textContent === "任务B"`, "leave R");
  await evaluate(`window.harness.setSession("R");`);
  await waitFor(`document.querySelector("#chat .title")?.textContent === "进行中的R"`, "reenter R");
  await new Promise((resolve) => setTimeout(resolve, 250));
  const back = await evaluate(`({ expanded: document.querySelector("#chat .bar")?.getAttribute("aria-expanded"), title: document.querySelector("#chat .title")?.textContent ?? "" })`);
  console.log(`REENTER-R-BAR ${JSON.stringify(back)}`);
  check("reentering the same running session stays closed", back.expanded !== "true" && back.title === "进行中的R", JSON.stringify(back));

  if (failures.length) {
    console.log(`RED ${failures.length}`);
    process.exitCode = 1;
  } else {
    console.log("GREEN session-frame");
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
