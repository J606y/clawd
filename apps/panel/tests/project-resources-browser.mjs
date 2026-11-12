import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.CLAWD_BROWSER_TMP || process.env.TMPDIR || "/tmp", "prb-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;
let diagnose = async () => {};
const browserErrors = [];

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null && browser.signalCode === null) {
    await new Promise((resolve) => {
      const timer = setTimeout(() => browser.kill("SIGKILL"), 3000);
      browser.once("exit", () => { clearTimeout(timer); resolve(); });
      if (!browser.kill("SIGTERM")) { clearTimeout(timer); resolve(); }
    });
  }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true });
};

try {
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
    "--headless",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"], env: { ...process.env, TMPDIR: owned } });
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
    browser.once("exit", (code, signal) => {
      clearTimeout(timer);
      reject(new Error(`Chromium exited ${code} signal=${signal || "none"}: ${output.slice(-2000)}`));
    });
  });
  socket = new WebSocket(ws);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Runtime.exceptionThrown") browserErrors.push(message.params.exceptionDetails);
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      browserErrors.push(message.params.args.map((arg) => arg.value ?? arg.description ?? arg.type));
    }
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
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  const metrics = { width: 390, height: 844, deviceScaleFactor: 1, mobile: true };
  await call("Emulation.setDeviceMetricsOverride", metrics, sessionId);
  await call("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const captureEvidence = async (name) => {
    if (!process.env.CLAWD_TEST_ARTIFACTS) return;
    const directory = path.resolve(process.env.CLAWD_TEST_ARTIFACTS);
    await mkdir(directory, { recursive: true });
    const { data } = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
    const destination = path.join(directory, `project-resources-${name}.png`);
    await writeFile(destination, Buffer.from(data, "base64"));
    console.log(`SCREENSHOT ${destination}`);
  };
  await call("Runtime.enable", {}, sessionId);
  diagnose = async () => {
    console.error("BROWSER_STATE", JSON.stringify(await evaluate(`({ ready: window.ready, calls: window.calls, text: document.body.innerText.slice(-3000), panels: [...document.querySelectorAll('.project-resources-panel')].map(p => ({state:p.dataset.state,text:p.textContent})), dialogs: [...document.querySelectorAll('dialog')].map(d => ({open:d.open})) })`)));
    console.error("BROWSER_ERRORS", JSON.stringify(browserErrors).slice(-5000));
    await captureEvidence("failure");
  };
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/project-resources-fixture.html` }, sessionId);
  await call("Emulation.setDeviceMetricsOverride", metrics, sessionId);

  const waitFor = async (expression, label) => {
    for (let n = 0; n < 80; n += 1) {
      if (await evaluate(`Boolean(${expression})`)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(label);
  };
  await waitFor("window.ready === true && !!document.querySelector('.project-resources-pills button')", "pills did not render");

  const tap = async (selector) => {
    const point = await evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      const rect = element.getBoundingClientRect();
      const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
      const hit = document.elementFromPoint(x, y);
      return { x, y, hit: !!hit && element.contains(hit) };
    })()`);
    assert.equal(point.hit, true, `touch point misses ${selector}`);
    await call("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: point.x, y: point.y }] }, sessionId);
    await call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }, sessionId);
  };

  const pills = await evaluate(`(() => {
    const buttons = [...document.querySelectorAll(".project-resources-pills button")];
    return buttons.map((button) => {
      const rect = button.getBoundingClientRect();
      return { text: button.textContent, h: rect.height, w: rect.width };
    });
  })()`);
  assert.deepEqual(pills.map((item) => item.text), ["新增档案", "新增指示", "查看记忆"]);
  for (const pill of pills) assert.ok(pill.h >= 44 && pill.w >= 44, `pill touch target ${pill.h}x${pill.w}`);

  await tap(".project-resources-pills button");
  await waitFor(`document.querySelector(".project-resources-panel[data-state='empty']")?.textContent.includes("尚无档案")`, "empty archive sheet");
  const archive = await evaluate(`(() => {
    const dialog = document.querySelector("dialog.project-resources-dialog");
    const panel = dialog?.querySelector(".project-resources-panel");
    const add = panel?.querySelector(".project-resources-add");
    const close = panel?.querySelector(".project-resources-close");
    const input = panel?.querySelector("input[type='file']");
    const panelRect = panel.getBoundingClientRect();
    const addRect = add.getBoundingClientRect();
    const closeRect = close.getBoundingClientRect();
    panel.getAnimations().forEach((anim) => anim.finish());
    return {
      modal: dialog.matches(":modal") && dialog.getAttribute("aria-modal") === "true" && dialog.getAttribute("role") === "dialog",
      inBody: dialog.parentElement === document.body,
      escapedTransform: !dialog.parentElement.closest("#transformed"),
      dialogTransform: getComputedStyle(dialog).transform,
      title: panel.querySelector(".project-resources-title")?.textContent,
      copy: panel.textContent,
      input: input?.type || "",
      addDisabled: add.disabled,
      sheetHeight: panelRect.height / window.innerHeight,
      addH: addRect.height,
      closeH: closeRect.height,
      closeW: closeRect.width,
      paddingBottom: getComputedStyle(panel).paddingBottom,
    };
  })()`);
  assert.equal(archive.modal, true);
  assert.equal(archive.inBody, true);
  assert.equal(archive.escapedTransform, true);
  assert.equal(archive.dialogTransform, "none");
  assert.equal(archive.title, "项目档案");
  assert.match(archive.copy, /新增档案，供 clawd 在「测试项目」中使用。/);
  assert.doesNotMatch(archive.copy, /Claude|记入这个项目|Car/);
  assert.equal(archive.input, "file");
  assert.equal(archive.addDisabled, false);
  assert.ok(archive.sheetHeight > 0.45 && archive.sheetHeight < 0.7, `archive sheet ratio ${archive.sheetHeight}`);
  assert.ok(archive.addH >= 44 && archive.closeH >= 44 && archive.closeW >= 44);
  assert.ok(parseFloat(archive.paddingBottom) >= 12);
  await captureEvidence("files-mobile");

  await call("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
    nativeVirtualKeyCode: 27,
  }, sessionId);
  await waitFor(`!document.querySelector("dialog.project-resources-dialog")`, "escape did not close archive sheet");

  await tap(".project-resources-pills button:nth-child(2)");
  await waitFor(`!!document.querySelector(".project-resources-instructions")`, "instructions textarea");
  const instructions = await evaluate(`(() => {
    const panel = document.querySelector(".project-resources-panel");
    const field = panel.querySelector("textarea");
    const save = panel.querySelector(".project-resources-save");
    return {
      title: panel.querySelector(".project-resources-title")?.textContent,
      placeholder: field.placeholder,
      value: field.value,
      help: panel.querySelector(".project-resources-help")?.textContent,
      saveDisabled: save.disabled,
      ratio: panel.getBoundingClientRect().height / window.innerHeight,
    };
  })()`);
  assert.equal(instructions.title, "指示");
  assert.equal(instructions.placeholder, "使用专业的语气、使用简洁易懂的措辞、您是天体物理学专家等");
  assert.equal(instructions.value, "");
  assert.equal(instructions.help, "指示 clawd 在「测试项目」中的行为与回应方式。");
  assert.equal(instructions.saveDisabled, true);
  assert.ok(instructions.ratio > 0.8, `instructions sheet ratio ${instructions.ratio}`);
  await captureEvidence("instructions-mobile");
  await evaluate(`document.querySelector(".project-resources-close").click()`);
  await waitFor(`!document.querySelector("dialog.project-resources-dialog")`, "instructions did not close");

  await evaluate(`window.mode = "memory"`);
  await tap(".project-resources-pills button:nth-child(3)");
  await waitFor(`!!document.querySelector(".project-resources-memory")`, "memory row");
  const memory = await evaluate(`(() => {
    const row = document.querySelector(".project-resources-memory");
    const rect = row.getBoundingClientRect();
    row.click();
    return { h: rect.height, text: row.textContent, unknown: row.textContent.includes("更新时间未知") };
  })()`);
  assert.ok(memory.h >= 44);
  assert.equal(memory.unknown, true);
  assert.doesNotMatch(memory.text, /Car|Devices|Interests|Mother/);
  await captureEvidence("memory-detail-mobile");
  await waitFor(`document.body.textContent.includes("返回列表") && document.body.textContent.includes("只属于这个项目")`, "memory detail");
  await evaluate(`document.querySelector(".project-resources-back").click()`);
  await waitFor(`!document.querySelector("[data-memory-detail]") && document.body.textContent.includes("项目备忘")`, "back to memory list");
  await captureEvidence("memory-list-mobile");
  await evaluate(`document.querySelector(".project-resources-close").click()`);

  await evaluate(`window.mode = "fail"; document.querySelector(".project-resources-pills button").click()`);
  await waitFor(`document.querySelector(".project-resources-panel[data-state='error']")`, "load error state");
  const failed = await evaluate(`(() => {
    const panel = document.querySelector(".project-resources-panel");
    return {
      text: panel.textContent,
      textarea: !!panel.querySelector("textarea"),
      saveDisabled: panel.querySelector(".project-resources-save")?.disabled ?? true,
      addDisabled: panel.querySelector(".project-resources-add")?.disabled ?? false,
    };
  })()`);
  assert.match(failed.text, /读取失败/);
  assert.doesNotMatch(failed.text, /尚无档案/);
  assert.equal(failed.textarea, false);
  assert.equal(failed.addDisabled, true);
  await evaluate(`document.querySelector(".project-resources-close").click()`);

  await evaluate(`window.mode = "files"; document.querySelector(".project-resources-pills button").click()`);
  await waitFor(`document.querySelector(".project-resources-file")`, "file row");
  const fileRow = await evaluate(`(() => {
    const button = document.querySelector(".project-resources-file");
    return { disabled: button.disabled, label: button.getAttribute("aria-label"), h: button.getBoundingClientRect().height };
  })()`);
  assert.equal(fileRow.disabled, false);
  assert.match(fileRow.label, /下载或打开 仅甲项目.md/);
  assert.ok(fileRow.h >= 44);
  await evaluate(`document.querySelector(".project-resources-close").click()`);

  await evaluate(`
    window.mode = "race";
    window.useKey = false;
    window.projectId = "p1";
    window.projectName = "甲项目";
    window.delays = { p1: 400, p2: 20 };
    window.calls = [];
    window.renderResources();
  `);
  await evaluate(`document.querySelector(".project-resources-pills button").click()`);
  await waitFor(`window.calls.some((item) => item.projectId === "p1")`, "p1 request");
  await evaluate(`
    window.projectId = "p2";
    window.projectName = "乙项目";
    window.renderResources();
  `);
  await waitFor(`document.querySelector(".project-resources")?.dataset.projectId === "p2"`, "switched project");
  await new Promise((resolve) => setTimeout(resolve, 700));
  const raced = await evaluate(`document.querySelector(".project-resources-panel").textContent`);
  assert.match(raced, /仅乙项目\.md/);
  assert.doesNotMatch(raced, /仅甲项目/);
  assert.doesNotMatch(raced, /更多|图钉|记入这个项目/);

  console.log("project-resources browser layout ok");
} catch (error) {
  console.error(error);
  try { await diagnose(); } catch (diagnosticError) { console.error("DIAGNOSTIC_FAILURE", diagnosticError); }
  process.exitCode = 1;
} finally {
  await stop();
}
