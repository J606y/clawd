import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const oldId = "b".repeat(64);
const newId = "c".repeat(64);
const cliId = "a".repeat(64);

function gate(sessionId, executionId) {
  const execution = (id) => ({ busy: true, execution: { id, kind: "cli", started_at: id === newId ? 200 : 100 } });
  return {
    sessionId,
    sessions: [{ id: "cli_visible", title: "CLI" }, { id: "cli_old", title: "旧会话" }, { id: "cli_new", title: "新会话" }],
    messages: {
      cli_visible: [{ id: 1, role: "user", content: "synthetic task", timestamp: 1 }],
      cli_old: [{ id: 1, role: "user", content: "synthetic task", timestamp: 1 }],
      cli_new: [{ id: 1, role: "user", content: "synthetic task", timestamp: 1 }],
    },
    background: {},
    run: {
      cli_visible: { busy: true, acquired_at: 100, run: null },
      cli_old: { busy: true, acquired_at: 100, run: null },
      cli_new: { busy: true, acquired_at: 200, run: null },
    },
    execution: {
      cli_visible: execution(cliId),
      cli_old: execution(oldId),
      cli_new: execution(newId),
    },
    stop: { hang: false, status: 200, body: { status: "cancelled", busy: false } },
  };
}

async function open(evaluate, spec) {
  await evaluate(`window.__gate = ${JSON.stringify(spec)}`);
  await waitFor(evaluate, "window.fixtureReady === true || Boolean(window.fixtureError)", "ChatPage mounted", 15000);
  const err = await evaluate("window.fixtureError || ''");
  if (err) throw new Error(err);
  if (!(await evaluate("window.fixtureReady === true"))) throw new Error("fixture did not become ready");
}

function posts(evaluate) {
  return evaluate("window.__requests.filter((item) => item.url.includes('/execution/stop'))");
}

async function twoStep(evaluate) {
  await open(evaluate, gate("cli_visible", cliId));
  await waitFor(evaluate, "document.body.innerText.includes('停止独立任务')", "first stop step");
  const before = await posts(evaluate);
  if (before.length !== 0) throw new Error(`stop posted before confirmation: ${JSON.stringify(before)}`);
  await evaluate("document.querySelector('button.deny').click()");
  await waitFor(evaluate, "document.body.innerText.includes('这会结束该会话的独立 CLI 进程')", "confirmation copy");
  const armed = await posts(evaluate);
  if (armed.length !== 0) throw new Error("arming confirmation posted a stop");
  await evaluate("[...document.querySelectorAll('button')].find((node) => node.textContent === '取消').click()");
  await waitFor(evaluate, "document.body.innerText.includes('停止独立任务') && !document.body.innerText.includes('确认停止')", "cancel returns to first step");
  if ((await posts(evaluate)).length !== 0) throw new Error("cancel posted a stop");
  await evaluate("document.querySelector('button.deny').click()");
  await evaluate("[...document.querySelectorAll('button')].find((node) => node.textContent === '确认停止').click()");
  await waitFor(evaluate, "window.__requests.some((item) => item.url.includes('/execution/stop'))", "confirmed stop");
  const sent = await posts(evaluate);
  if (sent.length !== 1) throw new Error(`expected one stop, got ${JSON.stringify(sent)}`);
  const body = JSON.parse(sent[0].body);
  if (body.session_id !== "cli_visible" || body.execution_id !== cliId || body.confirm !== "stop-cli") {
    throw new Error(`stop payload mismatch: ${sent[0].body}`);
  }
  await waitFor(evaluate, "!document.body.innerText.includes('确认停止')", "execution cleared after stop");
  await evaluate("window.__gate.run.cli_visible = { busy: false, run: null }; window.__gate.execution.cli_visible = { busy: false, execution: null }; window.dispatchEvent(new Event('focus'))");
  await waitFor(evaluate, "!document.body.innerText.includes('任务在另一端执行')", "sync clears busy after the server reports idle", 6000);
  const notice = await evaluate("document.body.innerText.includes('已停止')");
  console.log(`OBSERVE notice-after-cli-stop=${notice}`);
  console.log("GREEN two-step");
}

async function lateStop(evaluate) {
  const spec = gate("cli_old", oldId);
  spec.stop.hang = true;
  await open(evaluate, spec);
  await waitFor(evaluate, "document.body.innerText.includes('停止独立任务')", "old session stop");
  await evaluate("document.querySelector('button.deny').click()");
  await waitFor(evaluate, "document.body.innerText.includes('确认停止')", "old confirmation");
  await evaluate("[...document.querySelectorAll('button')].find((node) => node.textContent === '确认停止').click()");
  await waitFor(evaluate, "Boolean(window.__held && window.__held.stop)", "stop request held");
  await evaluate("window.__select('cli_new')");
  await waitFor(evaluate, "document.body.innerText.includes('停止独立任务')", "new session execution visible");
  await evaluate(`window.__release('stop', 200, { status: 'cancelled', busy: false })`);
  await new Promise((resolve) => setTimeout(resolve, 400));
  const text = await evaluate("document.body.innerText");
  const button = text.includes("停止独立任务") || text.includes("确认停止");
  const busy = text.includes("任务在另一端执行");
  if (!button || !busy || text.includes("已停止")) {
    throw new Error(`late stop cleared the other session: button=${button} busy=${busy} text=${JSON.stringify(text.slice(0, 400))}`);
  }
  console.log("GREEN late-stop");
}

const name = process.argv[2];
try {
  await withPage(root, "/tests/fixtures/react-chat-gate.html", async ({ evaluate, logs }) => {
    try {
      if (name === "two-step") await twoStep(evaluate);
      else if (name === "late-stop") await lateStop(evaluate);
      else throw new Error(`unknown case ${name}`);
    } catch (error) {
      const state = await evaluate("JSON.stringify({ ready: window.fixtureReady, err: window.fixtureError, gate: Boolean(window.__gate), href: location.href })").catch((e) => String(e));
      console.error("PAGE", state);
      console.error("LOGS", logs.join("\n"));
      throw error;
    }
  }, "");
} catch (error) {
  console.error(error.stack || error.message);
  process.exit(1);
}
