import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const now = Date.now() / 1000;
const finished = [
  { id: 1, role: "user", content: "run it", timestamp: now - 200 },
  { id: 2, role: "assistant", content: "已在后台启动", timestamp: now - 150 },
];

function gate(extra) {
  return {
    sessionId: "sess",
    sessions: [{ id: "sess", title: "合成会话" }, { id: "other", title: "另一会话" }],
    messages: { sess: finished, other: finished },
    background: {
      sess: { status: 200, processes: [] },
      other: { status: 200, processes: [] },
    },
    run: { sess: { busy: false, run: null }, other: { busy: false, run: null } },
    execution: { sess: { busy: false, execution: null }, other: { busy: false, execution: null } },
    stop: { hang: false, status: 200, body: { status: "cancelled", busy: false } },
    ...extra,
  };
}

const page = (evaluate, expression) => evaluate(`(() => { ${expression} })()`);

async function open(evaluate, spec) {
  await page(evaluate, `window.__gate = ${JSON.stringify(spec)}`);
  await waitFor(evaluate, "window.fixtureReady === true", "ChatPage mounted");
  const err = await evaluate("window.fixtureError || ''");
  if (err) throw new Error(err);
}

async function backgroundLine(evaluate) {
  await open(evaluate, gate());
  await waitFor(evaluate, "document.body.innerText.includes('已在后台启动')", "finished turn");
  const empty = await evaluate("document.body.innerText");
  let red = "";
  try {
    if (!/后台还有 /.test(empty) || !/个进程在运行/.test(empty)) {
      throw new Error(`RED_CONTROL empty background does not show the line: ${JSON.stringify(empty.slice(0, 180))}`);
    }
    red = "RED_CONTROL unexpectedly passed";
  } catch (error) {
    red = `RED_CONTROL background-line empty-processes assertion failed as required: ${error.message}`;
  }
  console.log(red);
  if (!red.includes("failed as required")) throw new Error(red);

  await page(evaluate, `window.__gate.background.sess = { status: 200, processes: [{ id: "proc_1", command: "python3 farm.py", startedAt: ${now - 125} }] }`);
  await evaluate("window.__refetchBackground('sess')");
  await waitFor(evaluate, "(document.querySelector('.bg-procs')?.textContent || '').includes('后台还有')", "background summary");
  const shown = await evaluate("document.body.innerText");
  const procs = await evaluate("document.querySelector('.bg-procs')?.textContent || ''");
  if (!/后台还有 /.test(shown)) throw new Error(`missing 后台还有: ${shown.slice(0, 300)}`);
  if (!/个进程在运行/.test(shown)) throw new Error(`missing 个进程在运行: ${shown.slice(0, 300)}`);
  if (!procs.includes("python3 farm.py")) throw new Error(`missing command: ${procs}`);

  await evaluate("window.__observe('sess')");
  await waitFor(evaluate, "!document.body.innerText.includes('个进程在运行')", "live layer hides background line");
  console.log("GREEN background-line");
}

async function failedQuery(evaluate) {
  await open(evaluate, gate({
    background: {
      sess: { status: 200, processes: [{ id: "proc_1", command: "sleep 999", startedAt: null }] },
      other: { status: 200, processes: [{ id: "proc_other", command: "evil.py", startedAt: null }] },
    },
  }));
  await waitFor(evaluate, "(document.querySelector('.bg-procs')?.textContent || '').includes('sleep 999')", "initial background list");
  const beforeFail = await evaluate("document.querySelector('.bg-procs')?.textContent || ''");
  if (!beforeFail.includes("sleep 999")) throw new Error("setup missing sleep 999");

  await page(evaluate, "window.__gate.background.sess = { status: 503 }");
  await evaluate("window.__refetchBackground('sess')");
  await new Promise((resolve) => setTimeout(resolve, 300));
  const afterFail = await evaluate("document.querySelector('.bg-procs')?.textContent || ''");
  if (!afterFail.includes("sleep 999")) {
    throw new Error(`failed refetch dropped the last list: ${afterFail.slice(0, 300)}`);
  }
  if (afterFail.includes("evil.py")) throw new Error("failed refetch painted another session");

  await page(evaluate, "window.__gate.background.sess.hang = true; window.__gate.background.sess.status = 200; window.__gate.background.sess.processes = [{ id: 'late', command: 'late-sess.py', startedAt: null }]");
  const refetch = evaluate("window.__refetchBackground('sess')");
  await waitFor(evaluate, "Boolean(window.__held && window.__held['bg:sess'])", "held old-session background query");
  await evaluate("window.__select('other')");
  await waitFor(evaluate, "(document.querySelector('.bg-procs')?.textContent || '').includes('evil.py')", "other session list");
  await evaluate("window.__release('bg:sess', 200, { processes: [{ id: 'late', command: 'late-sess.py', startedAt: null }] })");
  await refetch.catch(() => undefined);
  await new Promise((resolve) => setTimeout(resolve, 250));
  const afterLate = await evaluate("document.querySelector('.bg-procs')?.textContent || ''");
  if (afterLate.includes("late-sess.py") || afterLate.includes("sleep 999")) {
    throw new Error(`late reply for a session that is not open replaced the open list: ${afterLate.slice(0, 400)}`);
  }
  if (!afterLate.includes("evil.py")) throw new Error(`open session list disappeared: ${afterLate.slice(0, 400)}`);
  console.log("GREEN failed-query-and-foreign-session");
}

const name = process.argv[2];
try {
  await withPage(root, "/tests/fixtures/react-chat-gate.html", async ({ evaluate, logs }) => {
    try {
      if (name === "background-line") await backgroundLine(evaluate);
      else if (name === "failed-query") await failedQuery(evaluate);
      else throw new Error(`unknown case ${name}`);
    } catch (error) {
      const state = await evaluate("JSON.stringify({ ready: window.fixtureReady, err: window.fixtureError, href: location.href, scripts: [...document.scripts].map((s) => s.src), html: document.body.innerHTML.slice(0, 800) })").catch((e) => String(e));
      console.error("PAGE", state);
      console.error("LOGS", logs.join("\n"));
      throw error;
    }
  });
} catch (error) {
  console.error(error.stack || error.message);
  process.exit(1);
}
