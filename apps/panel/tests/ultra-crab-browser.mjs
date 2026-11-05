import { createServer } from "vite";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/.hermes/cache/scratch", "ultra-crab-"));
let server;
let browser;
let socket;
const pending = new Map();
let sequence = 0;

const stop = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  if (server) await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
};

const MEASURE = `(() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
  };
  const intersects = (a, b) => !!a && !!b && a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y;
  const inside = (inner, outer, eps = 0.5) => !!inner && !!outer
    && inner.x >= outer.x - eps && inner.y >= outer.y - eps
    && inner.right <= outer.right + eps && inner.bottom <= outer.bottom + eps;
  const track = document.querySelector(".effort-picker .track");
  const input = document.querySelector(".effort-picker input[type=range]");
  const label = [...document.querySelectorAll(".effort-picker .ends span")].find((node) => node.textContent === "更聪明") || null;
  const crab = document.querySelector("[data-ultra-crab]");
  const body = crab?.querySelector(".bang") || null;
  const notes = [...document.querySelectorAll("[data-ultra-note]")];
  const thumbSize = track ? parseFloat(getComputedStyle(track).getPropertyValue("--thumb")) : 14;
  const inputBox = box(input);
  const thumbCenter = inputBox
    ? inputBox.x + thumbSize / 2 + (inputBox.width - thumbSize) * (Number(input.value) / Number(input.max || 1))
    : null;
  const after = track ? getComputedStyle(track, "::after") : null;
  const dots = [...document.querySelectorAll(".effort-picker .dots i")].map((el) => ({
    ...box(el),
    color: getComputedStyle(el).backgroundColor,
  }));
  const clipAncestors = (el, sample) => {
    const hits = [];
    if (!el || !sample) return hits;
    for (let node = el.parentElement; node && node !== document.documentElement; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.overflowX === "visible" && style.overflowY === "visible") continue;
      const host = box(node);
      const spills = sample.y < host.y - 0.5 || sample.bottom > host.bottom + 0.5
        || sample.x < host.x - 0.5 || sample.right > host.right + 0.5;
      if (spills) hits.push(String(node.className || node.tagName));
    }
    return hits;
  };
  const noteReport = notes.map((note) => {
    const rects = [...note.querySelectorAll("rect")].map((rect) => ({
      x: rect.getAttribute("x") || "0",
      y: rect.getAttribute("y") || "0",
      width: rect.getAttribute("width"),
      height: rect.getAttribute("height"),
      fill: getComputedStyle(rect).fill,
    }));
    return { box: box(note), rects, fill: getComputedStyle(note).fill, clips: clipAncestors(note, box(note)), animation: getComputedStyle(note).animationName };
  });
  const names = document.getAnimations().map((anim) => anim.animationName);
  return {
    errors: window.fixtureErrors || [],
    actions: window.actions || [],
    open: !!document.querySelector(".effort-picker .panel"),
    value: input?.value ?? null,
    max: input?.max ?? null,
    aria: input?.getAttribute("aria-valuetext") ?? null,
    trackClass: track?.className ?? null,
    track: box(track),
    label: box(label),
    panel: box(document.querySelector(".effort-picker .panel")),
    thumbCenter,
    thumbRight: thumbCenter == null ? null : thumbCenter + thumbSize / 2,
    crab: box(crab),
    body: box(body),
    crabTag: crab?.tagName ?? null,
    inlineSvg: !!(crab && (crab.tagName === "svg" || crab.querySelector("svg:not([href])"))),
    externalImage: !!document.querySelector('.effort-picker img, .effort-picker svg image, .effort-picker svg use[*|href*=".svg"]'),
    notes: noteReport,
    dots,
    sparkle: after ? {
      content: after.content,
      display: after.display,
      image: after.backgroundImage,
      name: after.animationName,
    } : null,
    animations: names,
    labelHits: {
      crab: intersects(box(crab), box(label)),
      body: intersects(box(body), box(label)),
      notes: noteReport.filter((note) => intersects(note.box, box(label))).length,
    },
    contained: inside(box(body) || box(crab), box(track)),
    gap: box(label) && box(track) ? box(track).y - box(label).bottom : null,
  };
})()`;

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
  const evaluate = async (expression) => {
    const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const frames = () => evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 700, deviceScaleFactor: 1, mobile: true,
  }, sessionId);
  await call("Page.navigate", { url: `http://127.0.0.1:${port}/tests/ultra-crab-fixture.html` }, sessionId);
  for (let n = 0; n < 80; n += 1) {
    if (await evaluate("window.fixtureReady === true && !!document.querySelector('.effort-picker .trigger')")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const trigger = await evaluate(`(() => {
    const button = document.querySelector(".effort-picker .trigger");
    const rect = button.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    if (!(button === hit || button.contains(hit))) throw new Error("trigger occluded");
    return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
  })()`);
  await call("Input.dispatchMouseEvent", { type: "mousePressed", ...trigger, button: "left", clickCount: 1 }, sessionId);
  await call("Input.dispatchMouseEvent", { type: "mouseReleased", ...trigger, button: "left", clickCount: 1 }, sessionId);
  await frames();
  await evaluate(`(() => {
    const input = document.querySelector(".effort-picker input[type=range]");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, input.max);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await frames();
  const selected = await evaluate(MEASURE);
  console.log(`ULTRA_SELECTED ${JSON.stringify({
    aria: selected.aria,
    trackClass: selected.trackClass,
    track: selected.track,
    label: selected.label,
    thumbCenter: selected.thumbCenter,
    sparkle: selected.sparkle && { content: selected.sparkle.content, name: selected.sparkle.name, display: selected.sparkle.display },
    dots: selected.dots.length,
    crab: selected.crab,
    notes: selected.notes.length,
    actions: selected.actions,
    errors: selected.errors,
  })}`);

  const failures = [];
  const check = (name, ok, detail) => {
    if (ok) console.log(`PASS ${name}`);
    else {
      failures.push(name);
      console.log(`FAIL ${name} ${detail}`);
    }
  };
  const white = selected.sparkle
    && selected.sparkle.content !== "none"
    && /255,\s*255,\s*255/.test(selected.sparkle.image)
    && selected.sparkle.name === "hp-effort-sparkle"
    && selected.dots.length >= 4
    && selected.dots.every((dot) => dot.color === "rgb(255, 255, 255)");
  check("白色亮点还在", white, JSON.stringify({
    content: selected.sparkle?.content,
    name: selected.sparkle?.name,
    dots: selected.dots.map((dot) => dot.color),
  }));

  const sampled = await evaluate(`(() => {
    const track = document.querySelector(".effort-picker .track").getBoundingClientRect();
    const label = [...document.querySelectorAll(".effort-picker .ends span")].find((node) => node.textContent === "更聪明").getBoundingClientRect();
    const bang = document.querySelector(".bang");
    const notes = [...document.querySelectorAll("[data-ultra-note]")];
    const anims = document.querySelector(".effort-picker").getAnimations({ subtree: true })
      .filter((anim) => ["bang", "hat", "gt", "st", "lg", "fl", "fly"].includes(anim.animationName));
    for (const anim of anims) anim.pause();
    const hits = [];
    let minTop = Infinity;
    let maxTop = -Infinity;
    for (let t = 0; t <= 1100; t += 50) {
      for (const anim of anims) anim.currentTime = t;
      const body = bang.getBoundingClientRect();
      const inside = body.x >= track.x - 0.5 && body.y >= track.y - 0.5 && body.right <= track.right + 0.5 && body.bottom <= track.bottom + 0.5 && body.height <= track.height + 0.5 && body.height >= track.height * 0.7;
      if (notes[0]) {
        const top = notes[0].getBoundingClientRect().y;
        minTop = Math.min(minTop, top);
        maxTop = Math.max(maxTop, top);
      }
      if (!inside || body.y < label.bottom && body.right > label.x && body.x < label.right && body.bottom > label.y) {
        hits.push({ t, inside, h: body.height, y: body.y, bottom: body.bottom, trackBottom: track.bottom });
      }
    }
    for (const anim of anims) anim.play();
    return { frames: 23, hits, rose: maxTop - minTop, anims: anims.length };
  })()`);
  check("动画全程螃蟹在轨道内", sampled.hits.length === 0 && sampled.rose > 8 && sampled.anims > 0, JSON.stringify(sampled));

  const crabBox = selected.body || selected.crab;
  const crabInTrack = selected.aria === "Ultra"
    && selected.trackClass?.includes("ultra")
    && selected.inlineSvg
    && !selected.externalImage
    && crabBox
    && selected.contained
    && crabBox.height <= selected.track.height + 0.5
    && crabBox.height >= selected.track.height * 0.7;
  check("选中 Ultra 后螃蟹在轨道内且高度不大于轨道", crabInTrack, JSON.stringify({
    aria: selected.aria,
    tag: selected.crabTag,
    img: selected.img,
    crab: selected.crab,
    body: selected.body,
    track: selected.track,
    contained: selected.contained,
  }));
  const whiteBlock = async (center, top, height) => {
    const clip = {
      x: Math.max(0, center - 8),
      y: Math.max(0, top + height / 2 - 10),
      width: 16,
      height: 20,
      scale: 1,
    };
    const shot = await call("Page.captureScreenshot", { format: "png", clip, fromSurface: true }, sessionId);
    const file = path.join(owned, "thumb-sample.png");
    await writeFile(file, Buffer.from(shot.data, "base64"));
    const counted = spawn("python3", ["-c", `from PIL import Image\nim=Image.open(${JSON.stringify(file)}).convert("RGB")\nn=sum(1 for px in im.getdata() if px[0]>245 and px[1]>245 and px[2]>245)\nprint(n)`], { stdio: ["ignore", "pipe", "pipe"] });
    const text = await new Promise((resolve, reject) => {
      let out = "";
      counted.stdout.on("data", (chunk) => { out += chunk; });
      counted.on("error", reject);
      counted.on("exit", (code) => code === 0 ? resolve(out.trim()) : reject(new Error(out)));
    });
    return Number(text);
  };
  const ultraWhite = await whiteBlock(selected.thumbCenter, selected.track.y, selected.track.height);
  check("Ultra 档没有白色滑块", ultraWhite < 12, String(ultraWhite));

  const spread = await evaluate(`(() => {
    const guitar = document.querySelector(".guitar");
    const notes = [...document.querySelectorAll("[data-ultra-note]")];
    if (!guitar || notes.length !== 6) return { near: false, left: false, right: false, up: false, down: false, reason: "missing" };
    const box = guitar.getBoundingClientRect();
    const dist = (point) => {
      const x = Math.max(box.x, Math.min(point.x, box.right));
      const y = Math.max(box.y, Math.min(point.y, box.bottom));
      return Math.hypot(point.x - x, point.y - y);
    };
    const center = (el) => {
      const rect = el.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    };
    for (const note of notes) note.style.animationDelay = "0s";
    const anims = notes.map((note) => note.getAnimations().find((anim) => anim.animationName === "fly"));
    if (anims.some((anim) => !anim)) return { near: false, left: false, right: false, up: false, down: false, reason: "no-fly" };
    for (const anim of anims) anim.pause();
    const sample = (frac) => notes.map((note, index) => {
      const timing = anims[index].effect.getComputedTiming();
      anims[index].currentTime = timing.duration * frac;
      return center(note);
    });
    const start = sample(0.03);
    const end = sample(0.12);
    for (const anim of anims) anim.play();
    for (const note of notes) note.style.animationDelay = "";
    const vectors = end.map((point, index) => ({ dx: point.x - start[index].x, dy: point.y - start[index].y }));
    return {
      near: start.every((point) => dist(point) <= 28),
      left: vectors.some((vector) => vector.dx < -8),
      right: vectors.some((vector) => vector.dx > 8),
      up: vectors.some((vector) => vector.dy < -8),
      down: vectors.some((vector) => vector.dy > 8),
      dists: start.map((point) => Math.round(dist(point))),
      vectors: vectors.map((vector) => ({ dx: Math.round(vector.dx), dy: Math.round(vector.dy) })),
    };
  })()`);
  check("音符从吉他旁边向周围发散", spread.near && spread.left && spread.right && spread.up && spread.down, JSON.stringify(spread));

  const sized = await evaluate(`(() => {
    const label = document.querySelector(".effort-picker .cur");
    const node = label && [...label.childNodes].find((child) => child.nodeType === Node.TEXT_NODE && child.textContent.includes("a"));
    if (!label || !node) return { reason: "no-a" };
    const style = getComputedStyle(label);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const font = style.fontWeight + " " + style.fontSize + " " + style.fontFamily;
    ctx.font = font;
    const width = Math.ceil(ctx.measureText("a").width) + 4;
    const size = Math.ceil(parseFloat(style.fontSize)) + 12;
    canvas.width = width;
    canvas.height = size;
    ctx.font = font;
    ctx.fillStyle = "#000";
    ctx.textBaseline = "alphabetic";
    ctx.fillText("a", 2, size - 4);
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let min = canvas.height;
    let max = 0;
    for (let y = 0; y < canvas.height; y += 1) {
      for (let x = 0; x < canvas.width; x += 1) {
        if (pixels[(y * canvas.width + x) * 4 + 3] > 0) {
          min = Math.min(min, y);
          max = Math.max(max, y);
        }
      }
    }
    const letter = { height: max - min + 1 };
    const crab = document.querySelector(".bang").getBoundingClientRect();
    const center = { x: crab.x + crab.width / 2, y: crab.y + crab.height / 2 };
    const notes = [...document.querySelectorAll("[data-ultra-note]")];
    for (const note of notes) note.style.animationDelay = "0s";
    const anims = notes.map((note) => note.getAnimations().find((anim) => anim.animationName === "fly"));
    if (anims.some((anim) => !anim)) return { reason: "no-fly", letterH: letter.height };
    for (const anim of anims) anim.pause();
    const boxes = notes.map((note, i) => {
      const timing = anims[i].effect.getComputedTiming();
      anims[i].currentTime = timing.duration * 0.1;
      const rect = note.getBoundingClientRect();
      return { h: rect.height, w: rect.width, x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
    });
    for (const anim of anims) anim.play();
    for (const note of notes) note.style.animationDelay = "";
    const covers = boxes.some((box) => center.x >= box.x && center.x <= box.right && center.y >= box.y && center.y <= box.bottom);
    const matched = boxes.every((box) => Math.abs(box.h - letter.height) <= 2);
    return { letterH: Math.round(letter.height * 10) / 10, heights: boxes.map((box) => Math.round(box.h * 10) / 10), covers, matched };
  })()`);
  check("音符和 Ultra 的 a 差不多且不挡住螃蟹", sized.matched && !sized.covers, JSON.stringify(sized));

  const burst = await evaluate(`(() => {
    const notes = [...document.querySelectorAll("[data-ultra-note]")];
    const anims = notes.map((note) => note.getAnimations().find((anim) => anim.animationName === "fly")).filter(Boolean);
    if (anims.length !== 6) return { sequential: false, appeared: false, reason: "no-fly" };
    for (const anim of anims) anim.pause();
    const counts = [];
    for (let t = 0; t <= 8000; t += 100) {
      for (const anim of anims) anim.currentTime = t;
      counts.push(notes.filter((note) => parseFloat(getComputedStyle(note).opacity) > 0.4).length);
    }
    for (const anim of anims) anim.play();
    return { start: counts[0], max: Math.max(...counts), appeared: counts.includes(1), sequential: counts.every((count) => count <= 1) };
  })()`);
  check("音符逐个飞出", burst.sequential && burst.appeared && burst.start <= 1, JSON.stringify(burst));

  const noteShape = selected.notes.length === 6 && selected.notes.every((note, index) => {
    const expected = index % 2 === 0 ? "rgb(167, 139, 250)" : "rgb(139, 92, 246)";
    const rects = note.rects.map((rect) => `${rect.x},${rect.y},${rect.width}x${rect.height}`);
    return note.fill === expected
      && rects.join("|") === "0,0,12x9|9,-24,3x24|12,-24,9x5|15,-19,6x6"
      && note.rects.every((rect) => rect.fill === expected)
      && note.clips.length === 0
      && note.animation === "fly";
  });
  const clearOfLabel = crabInTrack && noteShape && !selected.labelHits.crab && !selected.labelHits.body;
  check("螃蟹不与「更聪明」盒子相交", clearOfLabel, JSON.stringify({
    hits: selected.labelHits,
    notes: selected.notes.map((note) => ({ fill: note.fill, rects: note.rects, clips: note.clips, box: note.box })),
  }));

  await evaluate(`(() => {
    const input = document.querySelector(".effort-picker input[type=range]");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, "1");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await frames();
  const left = await evaluate(`(() => {
    const names = document.getAnimations().map((anim) => anim.animationName);
    return {
      aria: document.querySelector(".effort-picker input[type=range]")?.getAttribute("aria-valuetext"),
      crab: !!document.querySelector("[data-ultra-crab]"),
      notes: document.querySelectorAll("[data-ultra-note]").length,
      bang: !!document.querySelector(".effort-picker .bang"),
      fly: names.filter((name) => ["bang", "hat", "gt", "st", "lg", "fl", "fly", "wob"].includes(name)),
    };
  })()`);
  check("切走 Ultra 后动画节点不在", left.aria !== "Ultra" && !left.crab && left.notes === 0 && !left.bang && left.fly.length === 0, JSON.stringify(left));
  const other = await evaluate(MEASURE);
  const otherWhite = await whiteBlock(other.thumbCenter, other.track.y, other.track.height);
  check("其他档位仍有白色滑块", otherWhite > 30, String(otherWhite));

  await evaluate(`(() => {
    const input = document.querySelector(".effort-picker input[type=range]");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, input.max);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await frames();
  const crabStart = await evaluate(`(() => {
    const crab = document.querySelector("[data-ultra-crab]");
    const track = document.querySelector(".effort-picker .track").getBoundingClientRect();
    if (!crab) return null;
    const rect = crab.getBoundingClientRect();
    return { x: rect.x, y: rect.y + rect.height / 2, left: track.x + 12, startX: rect.x + rect.width / 2 };
  })()`);
  await evaluate(`((startX, y, left) => {
    const crab = document.querySelector("[data-ultra-crab]");
    const event = (type, x, buttons) => new PointerEvent(type, {
      bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1, pointerType: "touch", isPrimary: true, button: 0, buttons,
    });
    crab.dispatchEvent(event("pointerdown", startX, 1));
    crab.dispatchEvent(event("pointermove", left, 1));
  })(${crabStart.startX}, ${crabStart.y}, ${crabStart.left})`);
  await frames();
  const crabMid = await evaluate(`(() => {
    const crab = document.querySelector("[data-ultra-crab]");
    const input = document.querySelector(".effort-picker input[type=range]");
    return { x: crab ? crab.getBoundingClientRect().x : null, value: input.value, max: input.max };
  })()`);
  await evaluate(`((left, y) => {
    const crab = document.querySelector("[data-ultra-crab]");
    if (!crab) return;
    crab.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true, cancelable: true, clientX: left, clientY: y, pointerId: 1, pointerType: "touch", isPrimary: true, button: 0, buttons: 0,
    }));
  })(${crabStart.left}, ${crabStart.y})`);
  const crabDrag = {
    ok: crabStart && crabMid.x != null && Number(crabMid.value) < Number(crabMid.max) && crabMid.x < crabStart.x - 8,
    before: crabStart?.x,
    mid: crabMid.x,
    value: crabMid.value,
    max: crabMid.max,
  };
  check("拖螃蟹向左能离开 Ultra", crabDrag.ok, JSON.stringify(crabDrag));
  await evaluate(`(() => {
    const input = document.querySelector(".effort-picker input[type=range]");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, input.max);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await frames();
  await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] }, sessionId);
  await frames();
  const reduced = await evaluate(`(() => {
    const style = (selector) => {
      const el = document.querySelector(selector);
      return el ? getComputedStyle(el).animationName : null;
    };
    return { bang: style(".bang"), hat: style(".hat"), fly: style("[data-ultra-note]"), guitar: style(".guitar") };
  })()`);
  check("减少动效时停掉螃蟹动画", reduced.bang === "none" && reduced.hat === "none" && reduced.fly === "none" && reduced.guitar === "none", JSON.stringify(reduced));

  if (failures.length) {
    console.log(`RED failures=${failures.length} ${failures.join(" | ")}`);
    assert.fail(failures.join(" | "));
  }
  console.log("GREEN ultra crab geometry");
} finally {
  await stop();
}
