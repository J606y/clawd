import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const content = [
  "普通外链：[外部网站](https://example.com/docs)",
  "MEDIA:\"/root/hermes-workspace/not-found.png\"",
  `MEDIA:"${png}"`,
].join("\n");

function spec() {
  return {
    sessionId: "sess",
    sessions: [{ id: "sess", title: "图片" }, { id: "other", title: "另一会话" }],
    messages: {
      sess: [{ id: 7, role: "assistant", content, timestamp: 10 }],
      other: [{ id: 1, role: "user", content: "other", timestamp: 1 }],
    },
    background: {},
    run: { sess: { busy: false, run: null }, other: { busy: false, run: null } },
    execution: {},
    stop: { hang: false, status: 200, body: { status: "cancelled", busy: false } },
  };
}

async function open(evaluate) {
  await evaluate(`window.__gate = ${JSON.stringify(spec())}`);
  await waitFor(evaluate, "window.fixtureReady === true", "ChatPage mounted", 15000);
  const err = await evaluate("window.fixtureError || ''");
  if (err) throw new Error(err);
  await waitFor(evaluate, "document.body.innerText.includes('放大查看')", "assistant image actions");
}

async function armStandalone(evaluate) {
  await evaluate(`(() => {
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    window.__openCalls = [];
    window.open = (...args) => { window.__openCalls.push(args.map(String)); return null; };
  })()`);
}

const name = process.argv[2];
try {
  await withPage(root, "/tests/fixtures/react-chat-gate.html", async ({ evaluate, call, sessionId }) => {
    if (name === "pwa-image") {
      await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
      await open(evaluate);
      await armStandalone(evaluate);
      const before = await evaluate("window.__openCalls.length");
      await evaluate("document.querySelector('.assistant-body .actions a').click()");
      await waitFor(evaluate, "Boolean(document.querySelector('dialog[open]'))", "preview dialog");
      const opens = await evaluate("window.__openCalls.length");
      if (opens !== before) {
        throw new Error(`image preview called window.open ${opens - before} time(s): ${await evaluate("JSON.stringify(window.__openCalls)")}`);
      }
      console.log("GREEN pwa-image");
      return;
    }
    if (name === "pwa-external") {
      await open(evaluate);
      await armStandalone(evaluate);
      await evaluate("document.querySelector('.assistant-body a[href*=\"example.com\"]').click()");
      const opens = await evaluate("window.__openCalls.length");
      if (opens !== 1) throw new Error(`external standalone link should window.open once, got ${opens}`);
      console.log("GREEN pwa-external");
      return;
    }
    if (name === "dialog-fit") {
      await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
      await open(evaluate);
      await evaluate("document.querySelector('.assistant-body .actions a').click()");
      await waitFor(evaluate, "Boolean(document.querySelector('dialog[open]'))", "dialog");
      const box = await evaluate(`(() => {
        const dialog = document.querySelector("dialog[open]");
        const rect = dialog.getBoundingClientRect();
        const button = dialog.querySelector("button[data-close]");
        const buttonRect = button.getBoundingClientRect();
        return { modal: dialog.matches(":modal"), width: rect.width, height: rect.height, top: rect.top, left: rect.left, button: button.textContent, bw: buttonRect.width, bh: buttonRect.height, overflow: document.body.style.overflow };
      })()`);
      if (!box.modal) throw new Error("preview dialog is not modal");
      if (box.width > 390 || box.height > 844 || box.top < 0 || box.left < 0) throw new Error(`dialog escapes viewport: ${JSON.stringify(box)}`);
      if (box.button !== "关闭预览" || box.bw <= 0 || box.bh <= 0) throw new Error(`close control missing: ${JSON.stringify(box)}`);
      await evaluate("document.querySelector('dialog[open] button[data-close]').click()");
      await waitFor(evaluate, "!document.querySelector('dialog[open]')", "dialog closed");
      console.log(`OBSERVE body-overflow-while-open=${JSON.stringify(box.overflow)}`);
      console.log("GREEN dialog-fit");
      return;
    }
    if (name === "bad-image") {
      await open(evaluate);
      await waitFor(evaluate, "document.body.innerText.includes('图片无法预览')", "bad image isolated");
      const state = await evaluate(`(() => {
        const arts = [...document.querySelectorAll(".artifact")];
        return arts.map((node) => ({ text: node.textContent, img: Boolean(node.querySelector("img")), err: node.textContent.includes("图片无法预览") }));
      })()`);
      const bad = state.find((item) => item.err);
      const good = state.find((item) => item.img && !item.err);
      if (!bad || !good) throw new Error(`bad image contaminated the other artifact: ${JSON.stringify(state)}`);
      await evaluate("document.querySelector('.artifact img').closest('.artifact').querySelector('.actions a').click()");
      await waitFor(evaluate, "Boolean(document.querySelector('dialog[open] img'))", "good preview");
      const hidden = await evaluate("document.querySelector('dialog[open] img').hidden");
      if (hidden) throw new Error("good image dialog inherited the bad image failure");
      console.log("GREEN bad-image");
      return;
    }
    if (name === "unmount") {
      await open(evaluate);
      await evaluate("document.querySelector('.assistant-body .actions a').click()");
      await waitFor(evaluate, "Boolean(document.querySelector('dialog[open]'))", "dialog before switch");
      await evaluate("window.__select('other')");
      await waitFor(evaluate, "!document.querySelector('dialog[open]')", "dialog removed with the session");
      console.log("GREEN unmount");
      return;
    }
    throw new Error(`unknown case ${name}`);
  });
} catch (error) {
  console.error(error.stack || error.message);
  process.exit(1);
}
