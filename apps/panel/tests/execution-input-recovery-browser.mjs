import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = path.resolve(fileURLToPath(new URL(".", import.meta.url)), "..");
const head = execSync("git rev-parse HEAD", { cwd: root, encoding: "utf8" }).trim();
const html = "/tests/fixtures/execution-input-recovery.html";
const SID = "sess-input";
const RUN = `run_${"a".repeat(32)}`;
const RUN_B = `run_${"b".repeat(32)}`;

console.log(`CANDIDATE_ROOT=${root}`);
console.log(`HEAD=${head}`);

function world(extra = {}) {
  return {
    sid: SID,
    busy: false,
    run: null,
    execution: null,
    messages: [{ id: 1, role: "user", content: "已有合成消息", timestamp: 1 }],
    approval: { status: 200 },
    stop: { hang: false, status: 200, body: { status: "cancelled", run_id: RUN, session_id: SID } },
    nextRunId: RUN,
    ...extra,
  };
}

async function open(page, spec) {
  const source = `window.__world = ${JSON.stringify(spec)}; window.__requests = [];`;
  await page.call("Page.addScriptToEvaluateOnNewDocument", { source }, page.sessionId);
  await page.call("Page.navigate", { url: `http://127.0.0.1:${page.port}${html}?t=${Date.now()}` }, page.sessionId);
  await waitFor(page.evaluate, "window.fixtureReady === true || Boolean(window.fixtureError)", "ChatPage mounted", 15000);
  const err = await page.evaluate("window.fixtureError || ''");
  if (err) throw new Error(err);
  const loaded = await page.evaluate("window.__loadedFrom || ''");
  console.log(`LOADED_FIXTURE=${loaded}`);
  if (!loaded.includes("/tests/fixtures/execution-input-recovery-fixture.tsx")) {
    throw new Error(`fixture did not load from candidate: ${loaded}`);
  }
  await page.call("Emulation.setFocusEmulationEnabled", { enabled: true }, page.sessionId);
}

async function field(page) {
  return page.evaluate(`(() => {
    const node = document.querySelector('textarea[aria-label="消息"]');
    if (!node) return null;
    return { disabled: node.disabled, value: node.value, sendDisabled: document.querySelector('button[aria-label="发送"]')?.disabled ?? null };
  })()`);
}

async function focusField(page) {
  const ok = await page.evaluate(`(() => {
    const node = document.querySelector('textarea[aria-label="消息"]');
    if (!node) return false;
    node.focus();
    return document.activeElement === node;
  })()`);
  if (!ok) throw new Error(`textarea is not focusable: ${JSON.stringify(await field(page))}`);
}

async function typeText(page, text) {
  await focusField(page);
  await page.call("Input.insertText", { text }, page.sessionId);
  const value = await page.evaluate(`document.querySelector('textarea[aria-label="消息"]')?.value || ""`);
  if (value !== text && !value.endsWith(text)) throw new Error(`native typing did not land: ${JSON.stringify(value)}`);
}

async function pressEnter(page) {
  await page.call("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", text: "\r", unmodifiedText: "\r", windowsVirtualKeyCode: 13 }, page.sessionId);
  await page.call("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }, page.sessionId);
}

async function clickAria(page, label) {
  const box = await page.evaluate(`(() => {
    const node = document.querySelector(${JSON.stringify(`button[aria-label="${label}"]`)});
    if (!node || node.disabled) return null;
    const rect = node.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || (hit !== node && !node.contains(hit))) return null;
    return { x, y };
  })()`);
  if (!box) throw new Error(`missing enabled hittable ${label}`);
  await page.call("Input.dispatchMouseEvent", { type: "mousePressed", x: box.x, y: box.y, button: "left", clickCount: 1 }, page.sessionId);
  await page.call("Input.dispatchMouseEvent", { type: "mouseReleased", x: box.x, y: box.y, button: "left", clickCount: 1 }, page.sessionId);
}

async function clickText(page, text) {
  const box = await page.evaluate(`(() => {
    const node = [...document.querySelectorAll("button")].find((item) => item.textContent === ${JSON.stringify(text)} && !item.disabled);
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || (hit !== node && !node.contains(hit))) return null;
    return { x, y };
  })()`);
  if (!box) throw new Error(`missing enabled hittable button ${text}`);
  await page.call("Input.dispatchMouseEvent", { type: "mousePressed", x: box.x, y: box.y, button: "left", clickCount: 1 }, page.sessionId);
  await page.call("Input.dispatchMouseEvent", { type: "mouseReleased", x: box.x, y: box.y, button: "left", clickCount: 1 }, page.sessionId);
}

async function waitClickable(page, text, extra = "true") {
  await waitFor(page.evaluate, `(() => {
    const node = [...document.querySelectorAll("button")].find((item) => item.textContent === ${JSON.stringify(text)} && !item.disabled);
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return Boolean(hit && (hit === node || node.contains(hit))) && (${extra});
  })()`, `enabled hittable ${text}`);
}

async function sendGate(page) {
  return page.evaluate(`(() => {
    const send = document.querySelector('button[aria-label="发送"]');
    const steer = document.querySelector('button[aria-label="插话"]');
    const box = document.querySelector('textarea[aria-label="消息"]');
    return {
      editable: box ? box.disabled === false : false,
      sendEnabled: Boolean(send && !send.disabled),
      steerEnabled: Boolean(steer && !steer.disabled),
    };
  })()`);
}

async function enterDoesNotAdmit(page, draft, count, { keepDraft }) {
  const steerBefore = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/steer")).length`);
  await pressEnter(page);
  const deadline = Date.now() + 500;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(`(() => ({
      admitted: window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length,
      steer: window.__requests.filter((item) => item.url.includes("/steer")).length,
      value: document.querySelector('textarea[aria-label="消息"]')?.value ?? "",
      disabled: document.querySelector('textarea[aria-label="消息"]')?.disabled ?? null,
    }))()`);
    if (last.admitted !== count) throw new Error(`enter created an admission: ${JSON.stringify(last)}`);
    if (last.disabled !== false) throw new Error(`enter locked input: ${JSON.stringify(last)}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (keepDraft && last.value !== draft) throw new Error(`enter swallowed draft: ${JSON.stringify(last)}`);
  if (!keepDraft && last.value !== draft && last.steer === steerBefore) {
    throw new Error(`enter dropped the draft without steer or admission: ${JSON.stringify(last)}`);
  }
}

async function releaseHolds(page) {
  await page.evaluate(`(() => {
    window.__world.holdSessionRun = false;
    window.__world.holdMessages = false;
    const sessionRuns = window.__sessionRunHolds.splice(0);
    sessionRuns.forEach((release) => release());
    const messages = window.__messageHolds.splice(0);
    messages.forEach((release) => release());
    return true;
  })()`);
  await page.evaluate("new Promise((resolve) => setTimeout(resolve, 0))");
  await page.evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
}

function admissions(page) {
  return page.evaluate(`window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs"))`);
}

async function startTurn(page, text) {
  await waitFor(page.evaluate, "Boolean(document.querySelector('textarea[aria-label=\"消息\"]'))", "composer");
  const before = (await admissions(page)).length;
  await typeText(page, text);
  await pressEnter(page);
  await waitFor(page.evaluate, `window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length > ${before}`, "run admission");
  await waitFor(page.evaluate, `window.__streams && window.__streams.size > 0`, "event stream");
  await page.evaluate("window.__mark = window.__requests.length; window.dispatchEvent(new Event('focus'))");
  await waitFor(page.evaluate, "window.__requests.slice(window.__mark).some((item) => item.url.includes('/sessions/run') && !item.url.includes('resolve'))", "busy discovery while local run is active");
  if (!(await page.evaluate("window.__lastSessionRun && window.__lastSessionRun.busy === true"))) {
    throw new Error("setup did not observe a busy local run");
  }
}

async function composerState(page) {
  const box = await field(page);
  const banner = await page.evaluate("document.body.innerText.includes('任务在另一端执行')");
  const notice = await page.evaluate("document.querySelector('.notice')?.textContent || ''");
  return { ...box, banner, notice, run: await page.evaluate("window.__runState()") };
}

async function completion(page) {
  await open(page, world());
  await startTurn(page, "请完成这一轮");
  const runningDraft = "运行中只编辑";
  await typeText(page, runningDraft);
  const runningGate = await sendGate(page);
  if (!runningGate.editable) throw new Error(`running locked typing: ${JSON.stringify(runningGate)}`);
  if (runningGate.sendEnabled) throw new Error(`running allowed a new send: ${JSON.stringify(runningGate)}`);
  await enterDoesNotAdmit(page, runningDraft, 1, { keepDraft: false });
  console.log("GREEN completion-running-separated");

  await page.evaluate(`new Promise((resolve) => {
    const arm = () => {
      if ((window.__messagesInflight || 0) === 0) {
        window.__world.holdMessages = true;
        resolve(true);
        return;
      }
      setTimeout(arm, 20);
    };
    arm();
  })`);
  await page.evaluate(`window.__emit(${JSON.stringify(RUN)}, { event: "run.completed", run_id: ${JSON.stringify(RUN)} })`);
  await waitFor(page.evaluate, "window.__messageHolds.length > 0", "old completion reload held");
  await page.evaluate("window.__world.busy = false; if (window.__world.run) window.__world.run.status = 'completed'; window.dispatchEvent(new Event('focus'))");
  await waitFor(page.evaluate, "document.querySelector('textarea[aria-label=\"消息\"]')?.disabled === false && !document.body.innerText.includes('任务在另一端执行')", "completion unlocks input without refresh", 4000);

  await page.evaluate("window.__world.busy = true; if (window.__world.run) window.__world.run.status = 'running'; window.__mark = window.__requests.length; window.dispatchEvent(new Event('focus'))");
  await waitFor(page.evaluate, "window.__requests.slice(window.__mark).some((item) => item.url.includes('/sessions/run') && !item.url.includes('resolve'))", "late busy poll");
  await new Promise((resolve) => setTimeout(resolve, 150));
  const late = await composerState(page);
  if (late.disabled !== false || late.banner) throw new Error(`late busy re-locked input: ${JSON.stringify(late)}`);
  console.log("GREEN completion-late-busy");

  const stale = { run_id: RUN, session_id: SID, status: "running", created_at: 100, updated_at: 120, execution_generation: 4, pending_controls: [] };
  await page.evaluate(`window.__world.holdSessionRun = true; window.__world.busy = true; window.__world.run = ${JSON.stringify(stale)}; window.dispatchEvent(new Event('focus'))`);
  await waitFor(page.evaluate, "window.__sessionRunHolds.length > 0", "stale busy request held");
  await page.evaluate(`window.__world.holdSessionRun = false; window.__world.busy = false; window.__world.run = ${JSON.stringify({ ...stale, status: "completed" })}; window.__world.nextRunId = ${JSON.stringify(RUN_B)};`);

  const next = "下一句合法发送";
  await typeText(page, next);
  const ready = await sendGate(page);
  if (!ready.editable || !ready.sendEnabled) throw new Error(`completion did not separate typing from send permission: ${JSON.stringify(ready)}`);
  const before = (await admissions(page)).length;
  await pressEnter(page);
  await waitFor(page.evaluate, `window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length === ${before + 1}`, "exactly one new admission");
  const sent = await admissions(page);
  const last = sent[sent.length - 1];
  if (!last.body.includes(next)) throw new Error(`next send dropped the draft: ${last.body}`);
  if (sent.length !== before + 1) throw new Error(`duplicate admission: ${sent.length}`);
  await waitFor(page.evaluate, `window.__runState() && window.__runState().runId === ${JSON.stringify(RUN_B)} && window.__runState().active && window.__streams.has(${JSON.stringify(RUN_B)})`, "new run active before stale cleanup");
  console.log("GREEN completion-next-send");

  await releaseHolds(page);
  const survived = await page.evaluate(`(() => {
    const state = window.__runState();
    const box = document.querySelector('textarea[aria-label="消息"]');
    return {
      state,
      disabled: box ? box.disabled : null,
      stream: Boolean(window.__streams && window.__streams.has(${JSON.stringify(RUN_B)})),
      banner: document.body.innerText.includes("任务在另一端执行"),
      admitted: window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length,
    };
  })()`);
  if (!survived.state || survived.state.runId !== RUN_B || !survived.state.active) throw new Error(`old cleanup broke the new run: ${JSON.stringify(survived)}`);
  if (!survived.stream) throw new Error(`old cleanup closed the new run stream: ${JSON.stringify(survived)}`);
  if (survived.disabled !== false || survived.banner) throw new Error(`old cleanup relocked input: ${JSON.stringify(survived)}`);
  if (survived.admitted !== before + 1) throw new Error(`old cleanup changed admissions: ${JSON.stringify(survived)}`);
  console.log("GREEN completion-old-cleanup");
  console.log("GREEN completion-unlock");
}

async function pause(page, mode) {
  await open(page, world({ stop: mode === "hang" ? { hang: true, status: 200, body: { status: "stopping" } } : mode === "fail" ? { hang: false, status: 500, body: { error: { message: "stop failed", code: "stop_failed" } } } : { hang: false, status: 200, body: { status: "cancelled", run_id: RUN, session_id: SID } } }));
  await startTurn(page, "暂停前的原文");
  const stopButton = await page.evaluate(`(() => {
    const node = document.querySelector('button[aria-label="停止"]');
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, disabled: node.disabled };
  })()`);
  if (!stopButton || stopButton.disabled) throw new Error(`stop control unavailable: ${JSON.stringify(stopButton)}`);
  await page.call("Input.dispatchMouseEvent", { type: "mousePressed", x: stopButton.x, y: stopButton.y, button: "left", clickCount: 1 }, page.sessionId);
  await page.call("Input.dispatchMouseEvent", { type: "mouseReleased", x: stopButton.x, y: stopButton.y, button: "left", clickCount: 1 }, page.sessionId);
  if (mode === "success") {
    await page.evaluate("window.__world.busy = false; if (window.__world.run) window.__world.run.status = 'cancelled'; window.dispatchEvent(new Event('focus'))");
    await waitFor(page.evaluate, "!window.__runState() || ['cancelled','completed','failed'].includes(window.__runState().status)", "stop settled");
  } else if (mode === "fail") {
    await waitFor(page.evaluate, "(window.__runState() && window.__runState().error.includes('停止失败')) || document.body.innerText.includes('停止失败')", "stop failure surfaced");
  } else {
    await waitFor(page.evaluate, "Boolean(window.__held && window.__held.stop)", "stop request held");
  }
  await new Promise((resolve) => setTimeout(resolve, 150));
  const state = await composerState(page);
  if (state.disabled !== false) throw new Error(`RED pause-${mode} locked input: ${JSON.stringify(state)}`);
  const draft = `暂停${mode}后仍可输入`;
  await typeText(page, draft);
  if ((await admissions(page)).length !== 1) throw new Error(`pause-${mode} created another run`);
  if (mode === "success") {
    await waitFor(page.evaluate, `(() => {
      const send = document.querySelector('button[aria-label="发送"]');
      const box = document.querySelector('textarea[aria-label="消息"]');
      return Boolean(send && !send.disabled && box && box.disabled === false && box.value === ${JSON.stringify(draft)});
    })()`, "pause success restores send separately from typing");
    await page.evaluate(`window.__world.nextRunId = ${JSON.stringify(RUN_B)}; window.__world.busy = false;`);
    const before = (await admissions(page)).length;
    await pressEnter(page);
    await waitFor(page.evaluate, `window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length === ${before + 1}`, "pause success sends exactly one");
    const sent = await admissions(page);
    if (!sent.at(-1).body.includes(draft)) throw new Error(`pause success swallowed the draft: ${sent.at(-1).body}`);
  } else {
    const gate = await sendGate(page);
    if (!gate.editable || gate.sendEnabled) throw new Error(`pause-${mode} conflated typing and send: ${JSON.stringify(gate)}`);
    await enterDoesNotAdmit(page, draft, 1, { keepDraft: mode === "hang" });
  }
  console.log(`GREEN pause-${mode}`);
}

async function approval(page) {
  await open(page, world({ approval: { status: 400, code: "invalid_params" } }));
  await startTurn(page, "需要审批的原文");
  await typeText(page, "审批草稿");
  await page.evaluate(`window.__emit(${JSON.stringify(RUN)}, ${JSON.stringify({ event: "approval.request", prompt_id: "prompt-synthetic", execution_generation: 4, command: "synthetic-command", description: "synthetic approval", choices: ["once", "deny"], kind: "approval" })})`);
  await waitFor(page.evaluate, "Boolean(document.querySelector('[aria-label=\"需要你的确认\"]'))", "approval card");
  const before = (await page.evaluate(`window.__requests.filter((item) => item.url.includes("/approval")).length`));
  await waitClickable(page, "Yes");
  await clickText(page, "Yes");
  await waitFor(page.evaluate, `window.__requests.filter((item) => item.url.includes("/approval")).length > ${before} || (window.__runState() && window.__runState().error)`, "approval attempt settled");
  const sent = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/approval"))`);
  const body = sent.at(-1) ? JSON.parse(sent.at(-1).body) : null;
  const expected = { request_id: "prompt-synthetic", execution_generation: 4, choice: "once" };
  if (JSON.stringify(body) !== JSON.stringify(expected)) {
    throw new Error(`RED approval body ${JSON.stringify(body)} !== ${JSON.stringify(expected)}`);
  }
  if (Object.keys(body).length !== 3 || body.choice !== "once") throw new Error(`approval widened scope: ${JSON.stringify(body)}`);
  const failed = await composerState(page);
  if (!failed.run?.approval) throw new Error(`failure cleared the approval card: ${JSON.stringify(failed)}`);
  if (failed.value !== "审批草稿") throw new Error(`failure swallowed the draft: ${JSON.stringify(failed)}`);
  if (sent.length !== before + 1) throw new Error("failure retried or auto-approved");
  await waitClickable(page, "Yes", "document.body.innerText.includes('审批提交失败')");
  await page.evaluate("window.__world.approval = { status: 200 };");
  await clickText(page, "Yes");
  await waitFor(page.evaluate, "!document.querySelector('[aria-label=\"需要你的确认\"]')", "successful approval clears the card");
  const afterOk = await admissions(page);
  if (afterOk.length !== 1) throw new Error(`approval restarted the run: ${afterOk.length}`);
  const still = await field(page);
  if (still.disabled || still.value !== "审批草稿") throw new Error(`approval success locked input or dropped draft: ${JSON.stringify(still)}`);
  await page.evaluate(`window.__emit(${JSON.stringify(RUN)}, ${JSON.stringify({ event: "approval.request", command: "no-id", choices: ["once", "deny"] })});`);
  await waitFor(page.evaluate, "document.body.innerText.includes('no-id')", "identityless card");
  const mark = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/approval")).length`);
  await page.evaluate("window.__world.run.pending_controls = []; window.__world.run.execution_generation = 4;");
  await waitClickable(page, "Yes");
  await clickText(page, "Yes");
  await waitFor(page.evaluate, "document.body.innerText.includes('缺少可核验的审批身份')", "identityless approval is not submitted");
  const extra = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/approval"))`);
  if (extra.length !== mark) throw new Error(`identityless approval posted ${JSON.stringify(extra.slice(mark))}`);
  if (!(await page.evaluate("Boolean(document.querySelector('[aria-label=\"需要你的确认\"]'))"))) throw new Error("identityless failure cleared the card");
  console.log("GREEN approval-contract");
}

async function otherClient(page) {
  const foreign = { run_id: RUN_B, session_id: SID, status: "running", created_at: 50, updated_at: 80, execution_generation: 2, pending_controls: [] };
  await open(page, world({ busy: true, run: foreign, nextRunId: RUN }));
  await waitFor(page.evaluate, `window.__requests.some((item) => item.url.includes("/events") && item.url.includes(${JSON.stringify(RUN_B)})) || (window.__runState() && window.__runState().runId === ${JSON.stringify(RUN_B)})`, "foreign run observed");
  const state = await composerState(page);
  if ((await admissions(page)).length !== 0) throw new Error("foreign activity created a run");
  if (state.disabled !== false) throw new Error(`other-client locked typing: ${JSON.stringify(state)}`);
  if (state.banner !== true && !(await page.evaluate(`window.__runState() && window.__runState().runId === ${JSON.stringify(RUN_B)}`))) {
    throw new Error(`other-client neither observed nor showed the foreign run: ${JSON.stringify(state)}`);
  }
  const draft = "只编辑不发送";
  await typeText(page, draft);
  await waitFor(page.evaluate, `(() => {
    const box = document.querySelector('textarea[aria-label="消息"]');
    const steer = document.querySelector('button[aria-label="插话"]');
    const send = document.querySelector('button[aria-label="发送"]');
    return Boolean(box && box.disabled === false && box.value === ${JSON.stringify(draft)} && steer && !steer.disabled && !(send && !send.disabled));
  })()`, "observed foreign run keeps steer usable without a new send");
  const steerBefore = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/steer")).length`);
  await enterDoesNotAdmit(page, draft, 0, { keepDraft: false });
  const steered = await page.evaluate(`(() => {
    const steers = window.__requests.filter((item) => item.url.includes("/steer"));
    const last = steers.at(-1) || null;
    return {
      admitted: window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length,
      count: steers.length,
      url: last ? last.url : "",
      body: last ? last.body : "",
      editable: document.querySelector('textarea[aria-label="消息"]')?.disabled === false,
    };
  })()`);
  if (steered.admitted !== 0) throw new Error(`enter created an admission: ${JSON.stringify(steered)}`);
  if (steered.count <= steerBefore || !steered.url.includes(RUN_B) || !steered.body.includes(draft)) {
    throw new Error(`enter did not keep the existing steer path: ${JSON.stringify(steered)}`);
  }
  if (!steered.editable) throw new Error(`steer locked input: ${JSON.stringify(steered)}`);
  const buttonDraft = "按钮仍可插话";
  await typeText(page, buttonDraft);
  await waitFor(page.evaluate, `(() => {
    const steer = document.querySelector('button[aria-label="插话"]');
    const box = document.querySelector('textarea[aria-label="消息"]');
    return Boolean(steer && !steer.disabled && box && box.disabled === false && box.value === ${JSON.stringify(buttonDraft)});
  })()`, "steer button still usable after enter");
  const beforeClick = await page.evaluate(`window.__requests.filter((item) => item.url.includes("/steer")).length`);
  const admittedBeforeClick = (await admissions(page)).length;
  await clickAria(page, "插话");
  await waitFor(page.evaluate, `window.__requests.filter((item) => item.url.includes("/steer")).length > ${beforeClick}`, "steer button posts");
  const clicked = await page.evaluate(`(() => {
    const steers = window.__requests.filter((item) => item.url.includes("/steer"));
    const last = steers.at(-1) || null;
    return {
      admitted: window.__requests.filter((item) => item.method === "POST" && item.url.split("?")[0].endsWith("/v1/runs")).length,
      url: last ? last.url : "",
      body: last ? last.body : "",
    };
  })()`);
  if (clicked.admitted !== admittedBeforeClick || !clicked.url.includes(RUN_B) || !clicked.body.includes(buttonDraft)) {
    throw new Error(`steer button did not stay on the observed run: ${JSON.stringify(clicked)}`);
  }
  console.log("GREEN other-client");
}

async function reconnect(page) {
  await open(page, world());
  await startTurn(page, "断流后应接回");
  await page.evaluate(`window.__closeStream(${JSON.stringify(RUN)}); window.__world.run.status = "running"; window.__world.busy = true;`);
  await waitFor(page.evaluate, `window.__requests.filter((item) => item.url.includes("/events")).length >= 2`, "same run resubscribed", 12000);
  const created = await admissions(page);
  if (created.length !== 1) throw new Error(`reconnect created a run: ${created.length}`);
  const state = await composerState(page);
  if (state.banner) throw new Error(`reconnect showed another client: ${JSON.stringify(state)}`);
  if (state.disabled !== false) throw new Error(`reconnect locked typing: ${JSON.stringify(state)}`);
  const draft = "恢复后不新建";
  await typeText(page, draft);
  const gate = await sendGate(page);
  if (!gate.editable || gate.sendEnabled) throw new Error(`reconnect conflated typing and a new send: ${JSON.stringify(gate)}`);
  await enterDoesNotAdmit(page, draft, 1, { keepDraft: false });
  if ((await admissions(page)).length !== 1) throw new Error("single-client recovery created a run");
  if (await page.evaluate("document.body.innerText.includes('任务在另一端执行')")) throw new Error("single-client recovery reported another client");
  console.log("GREEN reconnect");
}

const scenarios = [
  ["completion", completion],
  ["pause-success", (page) => pause(page, "success")],
  ["pause-fail", (page) => pause(page, "fail")],
  ["pause-uncertain", (page) => pause(page, "hang")],
  ["approval", approval],
  ["other-client", otherClient],
  ["reconnect", reconnect],
];

const results = [];
for (const [name, run] of scenarios) {
  try {
    await withPage(root, html, async (page) => {
      try {
        await run(page);
      } catch (error) {
        const extra = await page.evaluate("JSON.stringify({ err: window.fixtureError || '', unhandled: window.__unhandled || [], text: document.body?.innerText?.slice(0, 400) || '' })").catch((probe) => String(probe));
        console.error(`PAGE ${name}`, extra);
        throw error;
      }
    }, `window.__world = ${JSON.stringify(world())}`);
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, error: error.stack || error.message });
    console.error(`FAIL ${name}: ${error.message}`);
  }
}
console.log(`RESULTS ${JSON.stringify(results.map(({ name, ok }) => ({ name, ok })))}`);
if (results.some((item) => !item.ok)) process.exit(1);
console.log("GREEN execution-input-recovery");
