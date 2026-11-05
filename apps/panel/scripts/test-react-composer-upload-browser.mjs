import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "..");
const label = process.argv[2] || "run";
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "composer-upload-"));
const evidence = process.env.HP_TEST_EVIDENCE_DIR || owned;
mkdirSync(evidence, { recursive: true });
const compilerLog = path.join(evidence, `${label}-compiler.log`);
if (process.env.REACT_COMPILER === "0") delete process.env.REACT_COMPILER;
process.env.REACT_COMPILER_LOG = compilerLog;

const composerPath = path.join(root, "src/react/pages/chat/composer.tsx");
const composerHash = createHash("sha256").update(readFileSync(composerPath)).digest("hex");
const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  cacheDir: path.join(owned, "vite"),
  server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
  logLevel: "error",
});

let browser;
let socket;
let cleanupError;
const pending = new Map();
let sequence = 0;
const failures = [];

const stop = async () => {
  try {
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  } catch (error) {
    cleanupError = cleanupError || error;
  }
  for (const waiter of pending.values()) clearTimeout(waiter.timer);
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => browser.once("exit", resolve)),
      new Promise((resolve) => setTimeout(() => {
        if (browser.exitCode === null) browser.kill("SIGKILL");
        resolve();
      }, 5000)),
    ]);
  }
  try {
    await server.close();
  } catch (error) {
    cleanupError = cleanupError || error;
  }
  try {
    await rm(owned, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } catch (error) {
    cleanupError = cleanupError || error;
  }
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  await server.listen();
  const port = server.httpServer.address().port;
  console.log(`candidate ${composerHash}`);
  console.log(`compiler REACT_COMPILER=${process.env.REACT_COMPILER ?? "unset"} log=${compilerLog}`);
  console.log(`vite http://127.0.0.1:${port} cache=${path.join(owned, "vite")}`);
  console.log("viewport 390x844 headless Chromium; not a physical iOS device");
  browser = spawn("/usr/bin/chromium", [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(owned, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("FIXTURE: Chromium startup timeout")), 20000);
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
      reject(new Error(`FIXTURE: ${method} timeout`));
    }, 30000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  }, sessionId);
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) {
      throw new Error(`FIXTURE: ${JSON.stringify(result.exceptionDetails)}`);
    }
    return result.result.value;
  };
  await call("Page.navigate", {
    url: `http://127.0.0.1:${port}/tests/react-composer-upload-fixture.html`,
  }, sessionId);
  let ready = false;
  let lastDiag = "";
  for (let n = 0; n < 150; n += 1) {
    ready = await evaluate("window.__ready === true && Boolean(document.querySelector(\"textarea[aria-label='消息']\"))");
    if (ready) break;
    lastDiag = await evaluate("JSON.stringify({ ready: window.__ready === true, errors: window.__errors, text: document.body.innerText.slice(0, 300) })");
    await sleep(200);
  }
  if (!ready) throw new Error(`FIXTURE: composer did not mount: ${lastDiag}`);
  const loaded = await evaluate("performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => name.includes('composer'))");
  console.log(`loaded ${JSON.stringify(loaded)}`);
  if (!loaded.some((name) => name.includes("/src/react/pages/chat/composer.tsx"))) {
    throw new Error(`FIXTURE: production composer was not requested: ${JSON.stringify(loaded)}`);
  }
  let compilerText = "";
  for (let n = 0; n < 20 && !compilerText.includes("composer.tsx"); n += 1) {
    try {
      compilerText = readFileSync(compilerLog, "utf8");
    } catch {
      compilerText = "";
    }
    if (!compilerText.includes("composer.tsx")) await sleep(100);
  }
  const composerEvents = compilerText.split("\n").filter((line) => line.includes("composer.tsx"));
  console.log(`compiler-composer ${composerEvents.join(" | ") || "missing"}`);
  if (composerEvents.length === 0) {
    throw new Error(`FIXTURE: React Compiler did not process composer.tsx: ${compilerText.slice(0, 800)}`);
  }

  const read = () => evaluate("window.__read()");
  const frames = async () => {
    await evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await sleep(80);
  };
  const reset = async (spec) => {
    await evaluate(`window.__reset(${JSON.stringify(spec)})`);
    for (let n = 0; n < 40; n += 1) {
      const snap = await read();
      if (snap.mounted && snap.value === (spec.value ?? "") && snap.uploads === 0) return snap;
      await sleep(50);
    }
    throw new Error(`FIXTURE: reset did not settle ${JSON.stringify(await read())}`);
  };
  const pick = async (specs) => {
    const before = (await read()).uploads;
    await evaluate(`window.__pickFiles(${JSON.stringify(specs)})`);
    return before;
  };
  const waitUploads = async (count) => {
    for (let n = 0; n < 40; n += 1) {
      const snap = await read();
      if (snap.uploads >= count) return snap;
      await sleep(50);
    }
    throw new Error(`FIXTURE: upload request did not enter, uploads=${(await read()).uploads}`);
  };
  const typeInto = async (text) => {
    const focused = await evaluate(`(() => {
      const box = document.querySelector("textarea[aria-label='消息']");
      if (!box) return false;
      box.focus();
      const end = box.value.length;
      box.setSelectionRange(end, end);
      return document.activeElement === box && box.selectionStart === end;
    })()`);
    if (!focused) throw new Error("FIXTURE: textarea did not take focus at end");
    await call("Input.insertText", { text }, sessionId);
    await frames();
  };
  const switchOnly = async (sessionId) => {
    await evaluate(`window.__switchSession(${JSON.stringify(sessionId)})`);
    for (let n = 0; n < 40; n += 1) {
      const snap = await read();
      if (snap.domSession === sessionId && snap.storeSession === sessionId) {
        if (!snap.mounted || !snap.sameDraft || !snap.sameBox) {
          throw new Error(`FIXTURE: session switch remounted composer or replaced the draft object ${JSON.stringify(snap)}`);
        }
        return snap;
      }
      await sleep(50);
    }
    throw new Error(`FIXTURE: session switch did not commit ${JSON.stringify(await read())}`);
  };
  const release = async (index, status, name) => {
    const body = status >= 200 && status < 300
      ? { root: "uploads", path: name, absPath: `/fixture/uploads/${name}`, size: 4 }
      : { error: { message: "夹具拒绝上传" } };
    const ok = await evaluate(`window.releaseUpload(${index}, ${status}, ${JSON.stringify(body)})`);
    if (!ok) throw new Error(`FIXTURE: could not release upload ${index}`);
    await frames();
  };
  const assertProduct = (ok, message) => {
    if (!ok) throw new Error(message);
  };

  const imageSeed = Array.from({ length: 6 }, (_, index) => ({
    id: `seed-${index}`,
    kind: "image",
    name: `seed-${index}.png`,
    dataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    bytes: 70,
  }));
  const existingFile = {
    id: "att-existing",
    kind: "file",
    name: "已有.pdf",
    absPath: "/fixture/uploads/已有.pdf",
    bytes: 12,
  };

  const cases = [
    ["上传时新增草稿", async () => {
      await reset({ value: "", sessionId: "session-a" });
      await pick([{ name: "shot.png", kind: "png" }]);
      const entered = await waitUploads(1);
      if (!entered.uploadUrls[0].includes("files/upload") || !entered.uploadUrls[0].includes("shot.png")) {
        throw new Error(`FIXTURE: unexpected upload url ${entered.uploadUrls[0]}`);
      }
      await typeInto("上传期间输入");
      const before = await read();
      if (before.value !== "上传期间输入") throw new Error(`FIXTURE: typing did not land: ${JSON.stringify(before)}`);
      if (before.names.includes("shot.png")) throw new Error("FIXTURE: attachment appeared before release");
      await release(0, 200, "shot.png");
      const after = await read();
      assertProduct(after.value === "上传期间输入" && after.storeValue === "上传期间输入", `upload success deleted draft: before=${JSON.stringify(before.value)} after=${JSON.stringify(after.value)} store=${JSON.stringify(after.storeValue)}`);
      assertProduct(after.names.includes("shot.png") && after.storeNames.includes("shot.png"), `attachment missing after success: ${JSON.stringify(after.names)}`);
    }],
    ["上传时修改草稿", async () => {
      await reset({ value: "原有文字", sessionId: "session-a" });
      await pick([{ name: "edit.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await evaluate("window.__replaceText('改成新的')");
      await frames();
      const before = await read();
      if (before.value !== "改成新的") throw new Error(`FIXTURE: replace did not land: ${JSON.stringify(before)}`);
      await release(0, 200, "edit.pdf");
      const after = await read();
      assertProduct(after.value === "改成新的" && after.storeValue === "改成新的", `modify was overwritten: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("edit.pdf"), `modified draft lost attachment: ${JSON.stringify(after.names)}`);
    }],
    ["上传时删空草稿", async () => {
      await reset({ value: "要删掉", sessionId: "session-a" });
      await pick([{ name: "clear.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await evaluate("window.__replaceText('')");
      await frames();
      const before = await read();
      if (before.value !== "") throw new Error(`FIXTURE: clear did not land: ${JSON.stringify(before)}`);
      await release(0, 200, "clear.pdf");
      const after = await read();
      assertProduct(after.value === "" && after.storeValue === "", `cleared draft was restored: ${JSON.stringify(after.value)}`);
      assertProduct(after.names.includes("clear.pdf"), `cleared draft lost attachment: ${JSON.stringify(after.names)}`);
    }],
    ["原有文字和原有附件", async () => {
      await reset({ value: "原有文字", attachments: [existingFile], sessionId: "session-a" });
      await pick([{ name: "another.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await typeInto("再补一句");
      const before = await read();
      if (before.value !== "原有文字再补一句") throw new Error(`FIXTURE: append did not land: ${JSON.stringify(before)}`);
      await release(0, 200, "another.pdf");
      const after = await read();
      assertProduct(after.value === "原有文字再补一句" && after.storeValue === after.value, `existing text lost: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("已有.pdf") && after.names.includes("another.pdf"), `existing attachment lost: ${JSON.stringify(after.names)}`);
    }],
    ["多个附件逐项完成", async () => {
      await reset({ value: "", sessionId: "session-a" });
      await pick([{ name: "one.pdf", kind: "pdf" }, { name: "two.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await typeInto("第一段");
      if ((await read()).value !== "第一段") throw new Error("FIXTURE: first typing did not land");
      await release(0, 200, "one.pdf");
      await waitUploads(2);
      const mid = await read();
      assertProduct(mid.value === "第一段" && mid.names.includes("one.pdf"), `first completion deleted text or attachment: ${JSON.stringify(mid)}`);
      await typeInto("第二段");
      const before = await read();
      if (before.value !== "第一段第二段") throw new Error(`FIXTURE: second typing did not land: ${JSON.stringify(before)}`);
      await release(1, 200, "two.pdf");
      const after = await read();
      assertProduct(after.value === "第一段第二段" && after.storeValue === after.value, `sequential completion deleted text: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("one.pdf") && after.names.includes("two.pdf"), `sequential completion dropped an attachment: ${JSON.stringify(after.names)}`);
    }],
    ["上传失败不清字", async () => {
      await reset({ value: "失败前", sessionId: "session-a" });
      await pick([{ name: "bad.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await typeInto("也留");
      const before = await read();
      if (before.value !== "失败前也留") throw new Error(`FIXTURE: failure typing did not land: ${JSON.stringify(before)}`);
      await release(0, 500, "bad.pdf");
      const after = await read();
      assertProduct(after.value === "失败前也留" && after.storeValue === after.value, `upload failure cleared text: ${JSON.stringify(after)}`);
      assertProduct(!after.names.includes("bad.pdf"), `failed upload still added attachment: ${JSON.stringify(after.names)}`);
      assertProduct(after.flash.includes("夹具拒绝上传"), `failure flash missing: ${after.flash}`);
    }],
    ["归档失败不清字", async () => {
      await reset({ value: "", sessionId: "session-a" });
      await pick([{ name: "archive.png", kind: "png" }]);
      await waitUploads(1);
      await typeInto("归档失败也留");
      const before = await read();
      if (before.value !== "归档失败也留") throw new Error(`FIXTURE: archive typing did not land: ${JSON.stringify(before)}`);
      await release(0, 500, "archive.png");
      const after = await read();
      assertProduct(after.value === "归档失败也留" && after.storeValue === after.value, `archive failure cleared text: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("archive.png"), `archive failure dropped the compressed image: ${JSON.stringify(after.names)}`);
      assertProduct(after.flash.includes("原图未能存档"), `archive failure flash missing: ${after.flash}`);
    }],
    ["压缩与归档等待时输入", async () => {
      await reset({ value: "", sessionId: "session-a" });
      await evaluate("window.__holdBitmap = true; window.__bitmapEntered = 0; true");
      await pick([{ name: "wide.bmp", kind: "bmp" }]);
      let held = false;
      for (let n = 0; n < 40; n += 1) {
        held = await evaluate("window.__bitmapEntered > 0");
        if (held) break;
        await sleep(50);
      }
      if (!held) throw new Error(`FIXTURE: image compression was not entered: ${JSON.stringify(await read())} errors=${JSON.stringify(await evaluate("window.__errors"))}`);
      await typeInto("压缩时");
      if ((await read()).value !== "压缩时") throw new Error("FIXTURE: typing during compression did not land");
      await evaluate("window.__releaseBitmap()");
      await waitUploads(1);
      await typeInto("归档时");
      const before = await read();
      if (before.value !== "压缩时归档时") throw new Error(`FIXTURE: typing during archive did not land: ${JSON.stringify(before)}`);
      await release(0, 200, "wide.bmp");
      const after = await read();
      assertProduct(after.value === "压缩时归档时" && after.storeValue === after.value, `compression/archive completion deleted text: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("wide.bmp"), `compressed image missing: ${JSON.stringify(after.names)}`);
    }],
    ["切换会话并卸载后不覆盖", async () => {
      await reset({ value: "甲会话原文", sessionId: "session-a" });
      await pick([{ name: "old.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await evaluate("window.__unmount()");
      for (let n = 0; n < 40; n += 1) {
        if (!(await read()).mounted) break;
        await sleep(50);
      }
      if ((await read()).mounted) throw new Error("FIXTURE: composer did not unmount");
      await evaluate("window.__setSession({ sessionId: 'session-b', value: '乙会话原文', attachments: [] })");
      await evaluate("window.__mount()");
      let shown = false;
      for (let n = 0; n < 40; n += 1) {
        const snap = await read();
        if (snap.mounted && snap.value === "乙会话原文" && snap.storeSession === "session-b") {
          shown = true;
          break;
        }
        await sleep(50);
      }
      if (!shown) throw new Error(`FIXTURE: new session did not render before release: ${JSON.stringify(await read())}`);
      await release(0, 200, "old.pdf");
      const after = await read();
      assertProduct(after.value === "乙会话原文" && after.storeValue === "乙会话原文", `old upload overwrote the new session: ${JSON.stringify(after)}`);
      assertProduct(!after.names.includes("old.pdf") && !after.storeNames.includes("old.pdf"), `old upload attached to the new session: ${JSON.stringify(after)}`);
      assertProduct(after.storeSession === "session-b", `session id changed: ${after.storeSession}`);
    }],
    ["同草稿不卸载换会话不写回", async () => {
      await reset({ value: "同一草稿", sessionId: "session-a" });
      if (!await evaluate("window.__pin()")) throw new Error("FIXTURE: composer pin failed");
      await pick([{ name: "stale-session.pdf", kind: "pdf" }]);
      await waitUploads(1);
      const held = await switchOnly("session-b");
      if (held.value !== "同一草稿" || held.names.length !== 0) {
        throw new Error(`FIXTURE: switch changed the held draft before release ${JSON.stringify(held)}`);
      }
      await typeInto("乙补充");
      const typed = await read();
      if (typed.value !== "同一草稿乙补充" || typed.storeValue !== typed.value) {
        throw new Error(`FIXTURE: typing after same-draft switch did not land ${JSON.stringify(typed)}`);
      }
      await release(0, 200, "stale-session.pdf");
      const after = await read();
      assertProduct(after.domSession === "session-b" && after.storeSession === "session-b" && after.sameBox, `session switch remounted or left A: ${JSON.stringify(after)}`);
      assertProduct(after.value === "同一草稿乙补充" && after.storeValue === "同一草稿乙补充", `old upload changed B text: ${JSON.stringify(after)}`);
      assertProduct(!after.names.includes("stale-session.pdf") && !after.storeNames.includes("stale-session.pdf"), `old upload wrote an attachment into B: ${JSON.stringify(after)}`);
      assertProduct(after.flash === "", `old upload changed B flash: ${JSON.stringify(after.flash)}`);
    }],
    ["A到B再到A不重新认领", async () => {
      await reset({ value: "往返原文", sessionId: "session-a" });
      if (!await evaluate("window.__pin()")) throw new Error("FIXTURE: composer pin failed");
      await pick([{ name: "round-trip.pdf", kind: "pdf" }]);
      await waitUploads(1);
      const atB = await switchOnly("session-b");
      if (atB.value !== "往返原文") throw new Error(`FIXTURE: B did not keep the same draft text ${JSON.stringify(atB)}`);
      const back = await switchOnly("session-a");
      if (back.value !== "往返原文" || back.names.length !== 0 || !back.sameDraft) {
        throw new Error(`FIXTURE: A→B→A replaced the draft object or text ${JSON.stringify(back)}`);
      }
      await release(0, 200, "round-trip.pdf");
      const after = await read();
      assertProduct(after.domSession === "session-a" && after.storeSession === "session-a" && after.sameBox && after.sameDraft, `return to A remounted or replaced the draft: ${JSON.stringify(after)}`);
      assertProduct(after.value === "往返原文" && after.storeValue === "往返原文", `old upload changed text after A→B→A: ${JSON.stringify(after)}`);
      assertProduct(!after.names.includes("round-trip.pdf") && !after.storeNames.includes("round-trip.pdf"), `old upload was re-owned after returning to A: ${JSON.stringify(after)}`);
      assertProduct(after.flash === "", `old upload changed flash after A→B→A: ${JSON.stringify(after.flash)}`);
    }],
    ["B的新上传不被A的finally解除", async () => {
      await reset({ value: "甲原文", sessionId: "session-a" });
      if (!await evaluate("window.__pin()")) throw new Error("FIXTURE: composer pin failed");
      await pick([{ name: "a-held.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await switchOnly("session-b");
      await switchOnly("session-a");
      await switchOnly("session-b");
      await pick([{ name: "b-live.pdf", kind: "pdf" }]);
      await waitUploads(2);
      let busy = false;
      let before = null;
      for (let n = 0; n < 40; n += 1) {
        before = await read();
        if (before.busy) {
          busy = true;
          break;
        }
        await sleep(50);
      }
      if (!busy || !before) throw new Error(`FIXTURE: B upload did not enter preparing ${JSON.stringify(before)}`);
      if (before.value !== "甲原文" || before.names.length !== 0 || before.domSession !== "session-b" || !before.sameBox) {
        throw new Error(`FIXTURE: B upload setup changed the draft or remounted ${JSON.stringify(before)}`);
      }
      await release(0, 200, "a-held.pdf");
      const after = await read();
      assertProduct(after.busy, `A finally cleared B preparing: ${JSON.stringify(after)}`);
      assertProduct(after.value === "甲原文" && after.storeValue === "甲原文", `A finally changed B text: ${JSON.stringify(after)}`);
      assertProduct(!after.names.includes("a-held.pdf") && !after.storeNames.includes("a-held.pdf"), `A finally wrote into B while B had its own upload: ${JSON.stringify(after)}`);
      assertProduct(after.flash === "", `A finally changed B flash: ${JSON.stringify(after.flash)}`);
      assertProduct(after.domSession === "session-b" && after.sameBox, `B composer was remounted before A finally: ${JSON.stringify(after)}`);
      await release(1, 200, "b-live.pdf");
      const done = await read();
      assertProduct(!done.busy, `B preparing stayed after its own upload finished: ${JSON.stringify(done)}`);
      assertProduct(done.names.includes("b-live.pdf") && done.storeNames.includes("b-live.pdf"), `B upload lost its attachment: ${JSON.stringify(done)}`);
      assertProduct(!done.names.includes("a-held.pdf") && !done.storeNames.includes("a-held.pdf"), `A attachment appeared when B finished: ${JSON.stringify(done)}`);
      assertProduct(done.value === "甲原文" && done.storeValue === "甲原文", `B completion changed text: ${JSON.stringify(done)}`);
    }],
    ["同会话并行上传保持preparing", async () => {
      await reset({ value: "", sessionId: "session-a" });
      await pick([{ name: "parallel-a.pdf", kind: "pdf" }]);
      await waitUploads(1);
      await pick([{ name: "parallel-b.pdf", kind: "pdf" }]);
      await waitUploads(2);
      await typeInto("并行");
      const started = await read();
      if (started.value !== "并行" || !started.busy || started.uploads !== 2) {
        throw new Error(`FIXTURE: parallel uploads were not both held ${JSON.stringify(started)}`);
      }
      await release(1, 200, "parallel-b.pdf");
      const mid = await read();
      assertProduct(mid.busy, `first of two parallel uploads cleared preparing: ${JSON.stringify(mid)}`);
      assertProduct(mid.value === "并行" && mid.storeValue === "并行", `parallel completion changed text: ${JSON.stringify(mid)}`);
      assertProduct(mid.names.includes("parallel-b.pdf") && mid.storeNames.includes("parallel-b.pdf"), `later parallel upload dropped its attachment: ${JSON.stringify(mid)}`);
      assertProduct(!mid.names.includes("parallel-a.pdf"), `earlier parallel upload attached before it was released: ${JSON.stringify(mid.names)}`);
      await release(0, 200, "parallel-a.pdf");
      const after = await read();
      assertProduct(!after.busy, `preparing stayed after the last parallel upload: ${JSON.stringify(after)}`);
      assertProduct(after.names.includes("parallel-a.pdf") && after.names.includes("parallel-b.pdf"), `parallel uploads lost an attachment: ${JSON.stringify(after.names)}`);
      assertProduct(after.storeNames.includes("parallel-a.pdf") && after.storeNames.includes("parallel-b.pdf"), `parallel uploads lost a stored attachment: ${JSON.stringify(after.storeNames)}`);
      assertProduct(after.value === "并行" && after.storeValue === "并行", `last parallel completion changed text: ${JSON.stringify(after)}`);
    }],
    ["图片数量上限", async () => {
      await reset({ value: "上限", attachments: imageSeed, sessionId: "session-a" });
      await pick([{ name: "seventh.png", kind: "png" }]);
      await frames();
      const after = await read();
      if (after.uploads !== 0) throw new Error(`FIXTURE: seventh image started an upload: ${JSON.stringify(after.uploadUrls)}`);
      assertProduct(after.flash.includes("一条消息最多 6 张图片"), `image cap flash missing: ${after.flash}`);
      assertProduct(after.names.length === 6 && !after.names.includes("seventh.png"), `image cap added another image: ${JSON.stringify(after.names)}`);
      assertProduct(after.value === "上限", `image cap cleared text: ${after.value}`);
    }],
    ["50MB上限", async () => {
      await reset({ value: "太大", sessionId: "session-a" });
      await pick([{ name: "huge.pdf", kind: "huge" }]);
      await frames();
      await sleep(200);
      const after = await read();
      if (after.uploads !== 0) throw new Error(`FIXTURE: oversized file started an upload: ${JSON.stringify(after.uploadUrls)}`);
      assertProduct(after.flash.includes("超过 50MB"), `size cap flash missing: ${after.flash}`);
      assertProduct(!after.names.includes("huge.pdf"), `oversized file was added: ${JSON.stringify(after.names)}`);
      assertProduct(after.value === "太大", `size cap cleared text: ${after.value}`);
    }],
    ["处理中不发送", async () => {
      await reset({ value: "先写", sessionId: "session-a" });
      await pick([{ name: "hold.pdf", kind: "pdf" }]);
      await waitUploads(1);
      const busy = await read();
      if (!busy.busy) throw new Error(`FIXTURE: preparing indicator missing: ${JSON.stringify(busy)}`);
      await evaluate("window.__pressEnter()");
      await frames();
      const during = await read();
      assertProduct(during.sends === 0, `send fired while preparing: ${during.sends}`);
      assertProduct(during.value === "先写", `preparing send changed text: ${during.value}`);
      await release(0, 200, "hold.pdf");
      const after = await read();
      assertProduct(after.value === "先写" && after.names.includes("hold.pdf") && after.sends === 0, `preparing send case lost draft or sent: ${JSON.stringify(after)}`);
    }],
  ];

  for (const [name, run] of cases) {
    try {
      await run();
      console.log(`PASS ${name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.startsWith("FIXTURE:")) throw error;
      failures.push(`${name}: ${message}`);
      console.error(`FAIL ${name}: ${message}`);
    }
  }
  const native = await evaluate("({ sends: window.__nativeSends, urls: window.__nativeUrls, fetches: window.__fetches })");
  console.log(`native-xhr ${native.sends} fetches ${native.fetches.length}`);
  if (native.sends !== 0) failures.push(`real XHR escaped: ${JSON.stringify(native.urls)}`);
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("PASS composer upload draft");
  }
} catch (error) {
  console.error(error instanceof Error ? error.stack || error.message : error);
  process.exitCode = 1;
} finally {
  await stop();
  if (cleanupError) {
    console.error(`CLEANUP ${cleanupError instanceof Error ? cleanupError.message : cleanupError}`);
    process.exitCode = 1;
  }
  console.log(`exit ${process.exitCode ?? 0}`);
}
