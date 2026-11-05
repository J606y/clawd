import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SECRET_ENV, SECRET_HEADER, LONG_COMMAND, LONG_URL, SPACED_ARGS, ENV_KEYS, HEADER_KEYS, expectedTargets } from "./mcp-path-data.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const evidence = process.env.MCP_PATH_EVIDENCE || "/root/hermes-workspace/mcp-path-evidence";
const phase = process.env.MCP_PATH_PHASE || "run";
const expected = expectedTargets();
const widths = [320, 390, 1280];

function inside(inner, outer, tol = 1) {
  return inner.left >= outer.left - tol
    && inner.right <= outer.right + tol
    && inner.top >= outer.top - tol
    && inner.bottom <= outer.bottom + tol;
}

function measurePage(secretEnv, secretHeader) {
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  };
  const suffixOf = (el) => {
    const text = el.textContent || "";
    if (!text) return null;
    const start = Math.max(0, text.length - 8);
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let seen = 0;
    let node = walker.nextNode();
    while (node) {
      const value = node.textContent || "";
      const next = seen + value.length;
      if (start < next) {
        const range = document.createRange();
        range.setStart(node, start - seen);
        range.setEnd(node, value.length);
        const rects = [...range.getClientRects()];
        const r = rects[rects.length - 1];
        if (!r) return null;
        return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height, text: text.slice(-8) };
      }
      seen = next;
      node = walker.nextNode();
    }
    return null;
  };
  const metrics = (el) => {
    const text = el.textContent || "";
    const style = getComputedStyle(el);
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = [...range.getClientRects()].map((r) => ({
      left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height,
    }));
    const box = boxOf(el);
    const selection = window.getSelection();
    selection.removeAllRanges();
    const selectedRange = document.createRange();
    selectedRange.selectNodeContents(el);
    selection.addRange(selectedRange);
    const selected = selection.toString();
    selection.removeAllRanges();
    return {
      text,
      title: el.getAttribute("title"),
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      textOverflow: style.textOverflow,
      whiteSpace: style.whiteSpace,
      lineClamp: style.webkitLineClamp || "none",
      overflowX: style.overflowX,
      overflowY: style.overflowY,
      maxHeight: style.maxHeight,
      userSelect: style.userSelect,
      rects,
      suffix: suffixOf(el),
      box,
      selected,
    };
  };
  const pane = document.querySelector(".settings-hub .pane");
  const place = (el) => {
    if (!pane || !el) return;
    const pr = pane.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    if (er.top < pr.top + 2) pane.scrollTop -= pr.top - er.top + 8;
    if (er.bottom > pr.bottom - 2) pane.scrollTop += er.bottom - pr.bottom + 8;
  };
  const cards = [...document.querySelectorAll(".mcp-root .card.server")].filter((card) => card.querySelector(".mono")).map((card) => {
    const mono = card.querySelector(".mono");
    const lines = [...card.querySelectorAll(".line")];
    if (mono) place(mono);
    const monoMetrics = mono ? metrics(mono) : null;
    let hit = null;
    if (mono && monoMetrics?.suffix && pane) {
      place(mono);
      const suffix = suffixOf(mono);
      if (suffix) {
        const pr = pane.getBoundingClientRect();
        if (suffix.bottom > pr.bottom - 2) pane.scrollTop += suffix.bottom - pr.bottom + 8;
        if (suffix.top < pr.top + 2) pane.scrollTop -= pr.top - suffix.top + 8;
      }
      const shown = suffixOf(mono);
      if (shown && shown.width > 0 && shown.height > 0) {
        const x = (shown.left + shown.right) / 2;
        const y = (shown.top + shown.bottom) / 2;
        const target = document.elementFromPoint(x, y);
        hit = {
          x, y,
          inViewport: x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight,
          tag: target ? target.tagName : "",
          className: target ? String(target.className) : "",
          onMono: !!target && (target === mono || mono.contains(target)),
          onButton: !!target && !!target.closest("button"),
        };
      }
      if (monoMetrics) monoMetrics.suffix = suffixOf(mono);
    }
    const acts = card.querySelector(".acts");
    return {
      name: card.querySelector("h3")?.textContent || "",
      off: card.hasAttribute("data-off"),
      mono: monoMetrics,
      lines: lines.map(metrics),
      actsTop: acts ? acts.getBoundingClientRect().top : null,
      buttons: [...card.querySelectorAll(".acts button")].map((button) => ({
        label: button.getAttribute("aria-label") || "",
        text: button.textContent || "",
        disabled: button.disabled,
      })),
      switchChecked: card.querySelector(".switch")?.getAttribute("aria-checked") || "",
      cardScrollWidth: card.scrollWidth,
      cardClientWidth: card.clientWidth,
      hit,
    };
  });
  const html = document.documentElement ? document.documentElement.innerHTML : "";
  return {
    error: window.fixtureError || "",
    text: document.body ? document.body.innerText.slice(0, 400) : "",
    htmlHasEnv: html.includes(secretEnv),
    htmlHasHeader: html.includes(secretHeader),
    traffic: window.mcpTraffic || [],
    titles: [...document.querySelectorAll("[title]")].map((el) => el.getAttribute("title") || ""),
    dialogs: document.querySelectorAll("dialog,[role='dialog']").length,
    copyButtons: [...document.querySelectorAll("button")].filter((button) => (button.textContent || "").includes("复制")).length,
    pane: pane ? {
      overflowY: getComputedStyle(pane).overflowY,
      scrollWidth: pane.scrollWidth,
      clientWidth: pane.clientWidth,
      scrollHeight: pane.scrollHeight,
      clientHeight: pane.clientHeight,
      scrollLeft: pane.scrollLeft,
    } : null,
    cards,
    vw: window.innerWidth,
    vh: window.innerHeight,
  };
}

const server = await createServer({
  configFile: path.join(root, "vite.config.ts"),
  root,
  cacheDir: path.join(evidence, "vite"),
  server: { host: "127.0.0.1", port: 0, strictPort: false },
  logLevel: "error",
});
let browser;
let socket;
const pending = new Map();
let sequence = 0;
const failures = [];
const reports = [];

try {
  await mkdir(evidence, { recursive: true });
  if (LONG_COMMAND.includes(" ") || LONG_COMMAND.length < 160) failures.push("oracle long command is not a long spaceless token");
  if (LONG_URL.includes(" ") || LONG_URL.length < 200 || !LONG_URL.startsWith("http://")) failures.push("oracle url is not a long spaceless http address");
  if (!SPACED_ARGS.some((arg) => arg.includes(" "))) failures.push("oracle spaced args missing an internal space");
  if (expected.bing.length > 40 || !expected.bing.includes(" ")) failures.push("oracle short command is not short");
  if (expected.aux_ac !== LONG_COMMAND || expected["long-http"] !== LONG_URL) failures.push("oracle target mapping drifted from component join rules");

  await server.listen();
  const port = server.httpServer.address().port;
  console.log(`vite port ${port}`);
  browser = spawn("/usr/bin/chromium", [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--remote-debugging-port=0",
    `--user-data-dir=${path.join(evidence, "profile")}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const ws = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Chromium startup timeout")), 30000);
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
    const job = pending.get(message.id);
    if (!job) return;
    pending.delete(message.id);
    clearTimeout(job.timer);
    if (message.error) job.reject(new Error(JSON.stringify(message.error)));
    else job.resolve(message.result);
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timeout`));
    }, 30000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const expression = `(${measurePage.toString()})(${JSON.stringify(SECRET_ENV)}, ${JSON.stringify(SECRET_HEADER)})`;

  for (const width of widths) {
    const height = width >= 1000 ? 900 : 844;
    const { browserContextId } = await call("Target.createBrowserContext");
    const { targetId } = await call("Target.createTarget", { url: "about:blank", browserContextId });
    const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
    const evaluate = async (code) => {
      const result = await call("Runtime.evaluate", { expression: code, awaitPromise: true, returnByValue: true }, sessionId);
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await call("Page.enable", {}, sessionId);
    await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] }, sessionId);
    await call("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 2,
      mobile: width < 500,
    }, sessionId);
    await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/mcp-path-fixture.html` }, sessionId);
    let report = null;
    for (let n = 0; n < 80; n += 1) {
      try {
        report = await evaluate(expression);
      } catch (error) {
        report = { error: String(error), cards: [] };
      }
      if (report?.cards?.length === 4) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!report || report.cards?.length !== 4) {
      failures.push(`width ${width} setup failed: ${report?.error || report?.text || "no cards"}`);
      continue;
    }
    if (!report.pane || report.pane.clientHeight < 200) {
      failures.push(`width ${width} fixture pane collapsed: ${JSON.stringify(report.pane)}`);
      continue;
    }
    reports.push({ width, report });
    console.log(`width ${width} pane ${report.pane.clientWidth}x${report.pane.clientHeight} overflowY=${report.pane.overflowY}`);
    for (const card of report.cards) {
      const mono = card.mono;
      const want = expected[card.name];
      const tag = `${width} ${card.name}`;
      if (!mono) {
        failures.push(`${tag} missing .mono`);
        continue;
      }
      console.log(`${tag} textOverflow=${mono.textOverflow} whiteSpace=${mono.whiteSpace} clamp=${mono.lineClamp} scroll=${mono.scrollWidth}/${mono.clientWidth} x ${mono.scrollHeight}/${mono.clientHeight} lines=${mono.rects.length} suffixW=${mono.suffix?.width ?? "none"}`);
      if (mono.text !== want) failures.push(`${tag} text mismatch`);
      if (mono.title !== want) failures.push(`${tag} title is not the full target`);
      if (mono.scrollWidth > mono.clientWidth + 1) failures.push(`${tag} horizontal clip scrollWidth ${mono.scrollWidth} > clientWidth ${mono.clientWidth}`);
      if (mono.scrollHeight > mono.clientHeight + 1) failures.push(`${tag} vertical clip scrollHeight ${mono.scrollHeight} > clientHeight ${mono.clientHeight}`);
      if (mono.textOverflow === "ellipsis") failures.push(`${tag} text-overflow ellipsis`);
      if (mono.whiteSpace === "nowrap") failures.push(`${tag} white-space nowrap`);
      if (mono.lineClamp !== "none") failures.push(`${tag} line-clamp ${mono.lineClamp}`);
      if (mono.maxHeight !== "none") failures.push(`${tag} max-height ${mono.maxHeight}`);
      if (mono.userSelect === "none") failures.push(`${tag} user-select none`);
      if (mono.selected !== want) failures.push(`${tag} text is not selectable`);
      if (!mono.suffix || mono.suffix.width <= 0 || mono.suffix.height <= 0) failures.push(`${tag} suffix range has no box`);
      else if (!inside(mono.suffix, mono.box)) failures.push(`${tag} suffix outside mono box`);
      if (mono.rects.length === 0) failures.push(`${tag} no line rects`);
      for (const rect of mono.rects) {
        if (!inside(rect, mono.box)) failures.push(`${tag} line rect outside mono box`);
      }
      if ((card.name === "aux_ac" || card.name === "long-http") && mono.rects.length < 2) failures.push(`${tag} long token did not wrap`);
      if ((card.name === "aux_ac" || card.name === "long-http") && mono.clientHeight < 32) failures.push(`${tag} long token height did not grow (${mono.clientHeight})`);
      if (card.name === "chrome-devtools" && width < 500 && mono.rects.length < 2) failures.push(`${tag} spaced command did not wrap on phone`);
      if (card.name === "chrome-devtools" && width < 500 && mono.clientHeight < 32) failures.push(`${tag} spaced command height did not grow (${mono.clientHeight})`);
      if (card.name === "bing" && mono.rects.length !== 1) failures.push(`${tag} short command wrapped unexpectedly (${mono.rects.length} lines)`);
      if (card.actsTop == null || card.actsTop < mono.box.bottom - 1) failures.push(`${tag} actions overlap or missing`);
      if (card.cardScrollWidth > card.cardClientWidth + 1) failures.push(`${tag} card horizontal overflow`);
      if (!card.hit?.onMono || card.hit.onButton || !card.hit.inViewport) failures.push(`${tag} suffix not hittable in viewport ${JSON.stringify(card.hit)}`);
      const testButton = card.buttons.find((button) => button.label === `测试连接 ${card.name}`);
      const deleteButton = card.buttons.find((button) => button.label === `删除 ${card.name}`);
      if (!testButton || testButton.disabled || testButton.text !== "测试连接") failures.push(`${tag} test button missing or changed`);
      if (!deleteButton || deleteButton.disabled || deleteButton.text !== "删除") failures.push(`${tag} delete button missing or changed`);
      const wantChecked = card.name === "long-http" ? "false" : "true";
      if (card.switchChecked !== wantChecked) failures.push(`${tag} switch state ${card.switchChecked}`);
      if (card.name === "long-http" && !card.off) failures.push(`${tag} disabled card lost data-off`);
    }
    const bing = report.cards.find((card) => card.name === "bing");
    const http = report.cards.find((card) => card.name === "long-http");
    const envLine = bing?.lines?.[0];
    const headerLine = http?.lines?.[0];
    if (!envLine || ENV_KEYS.some((key) => !envLine.text.includes(key)) || !envLine.text.includes("（值已隐藏）")) failures.push(`${width} env keys not fully shown`);
    if (envLine && (envLine.scrollWidth > envLine.clientWidth + 1 || envLine.scrollHeight > envLine.clientHeight + 1 || envLine.textOverflow === "ellipsis")) failures.push(`${width} env line clipped`);
    if (!headerLine || HEADER_KEYS.some((key) => !headerLine.text.includes(key)) || !headerLine.text.includes("（值已隐藏）")) failures.push(`${width} header keys not fully shown`);
    if (headerLine && (headerLine.scrollWidth > headerLine.clientWidth + 1 || headerLine.textOverflow === "ellipsis")) failures.push(`${width} header line clipped`);
    if (report.htmlHasEnv || report.htmlHasHeader) failures.push(`${width} secret value leaked into DOM`);
    if (report.titles.some((title) => title.includes(SECRET_ENV) || title.includes(SECRET_HEADER))) failures.push(`${width} secret value leaked into title`);
    if (report.dialogs !== 0 || report.copyButtons !== 0) failures.push(`${width} added dialog or copy button`);
    if (report.pane.overflowY !== "auto") failures.push(`${width} pane overflowY ${report.pane.overflowY}`);
    if (report.pane.scrollWidth > report.pane.clientWidth + 1 || report.pane.scrollLeft !== 0) failures.push(`${width} pane horizontal scroll`);
    const writes = report.traffic.filter((item) => item.method !== "GET" || !new URL(item.url).pathname.endsWith("/panel-sidecar/mcp"));
    if (writes.length) failures.push(`${width} unexpected request ${JSON.stringify(writes)}`);
    if (!report.traffic.some((item) => item.method === "GET" && item.url.includes("/panel-sidecar/mcp"))) failures.push(`${width} list GET missing`);

    await evaluate(`(() => {
      const pane = document.querySelector(".settings-hub .pane");
      const card = [...document.querySelectorAll(".mcp-root .server")].find((node) => node.querySelector("h3")?.textContent === "aux_ac");
      if (pane && card) pane.scrollTop += card.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    })()`);
    const shot = await call("Page.captureScreenshot", { format: "png" }, sessionId);
    const shotPath = path.join(evidence, `${phase}-${width}.png`);
    await writeFile(shotPath, Buffer.from(shot.data, "base64"));
    console.log(`screenshot ${shotPath}`);
    if (width < 500) {
      await evaluate(`(() => {
        const pane = document.querySelector(".settings-hub .pane");
        const card = [...document.querySelectorAll(".mcp-root .server")].find((node) => node.querySelector("h3")?.textContent === "long-http");
        if (pane && card) pane.scrollTop += card.getBoundingClientRect().top - pane.getBoundingClientRect().top;
      })()`);
      const urlShot = await call("Page.captureScreenshot", { format: "png" }, sessionId);
      const urlPath = path.join(evidence, `${phase}-${width}-url.png`);
      await writeFile(urlPath, Buffer.from(urlShot.data, "base64"));
      console.log(`screenshot ${urlPath}`);
    }
  }
  await writeFile(path.join(evidence, `${phase}-metrics.json`), JSON.stringify({ failures, reports }, null, 2));
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("mcp-path layout acceptance passed");
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  try {
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
    if (browser && browser.exitCode === null && browser.signalCode === null) {
      const exited = new Promise((resolve) => browser.once("exit", resolve));
      browser.kill("SIGTERM");
      await exited;
    }
  } finally {
    await server.close();
  }
}
