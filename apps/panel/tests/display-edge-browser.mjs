import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = path.resolve(import.meta.dirname, "..");
const evidence = "/root/hermes-workspace/fullgate-evidence/display-edge";
const reportPath = process.env.HP_EDGE_REPORT || path.join(evidence, "display-edge-report.json");
const report = {
  sourceRoot: root,
  head: "9a8a86316dbd9e4ffc515acd7fad22479ce99e5a",
  layout: [],
  rects: null,
  thinking: [],
  failures: [],
};

function quote(value) {
  return JSON.stringify(value);
}

async function openCase(page, name, width, height) {
  const token = `${name}-${width}-${Date.now()}`;
  await page.call("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: width < 600,
  }, page.sessionId);
  await page.call("Page.navigate", {
    url: `http://127.0.0.1:${page.port}/tests/display-edge-fixture.html?case=${name}&token=${token}`,
  }, page.sessionId);
  await waitFor(page.evaluate, "window.fixtureReady === true && !window.fixtureError", `${name}@${width}`, 20000);
}

async function measureLayout(page, label) {
  const result = await page.evaluate(`(() => {
    const el = document.querySelector(".hp-chat .scroll");
    const nodes = [...el.querySelectorAll("[data-history-source]")];
    const styles = nodes.slice(0, 3).map((node) => {
      const cs = getComputedStyle(node);
      return { key: node.dataset.historySource, position: cs.position, display: cs.display, marginTop: cs.marginTop, transform: cs.transform, float: cs.cssFloat };
    });
    const steer = document.querySelector(".steer");
    const steerStyle = steer ? getComputedStyle(steer) : null;
    const linear = () => {
      const bounds = el.getBoundingClientRect();
      for (const node of nodes) {
        const rect = node.getBoundingClientRect();
        if (rect.bottom > bounds.top && rect.top < bounds.bottom) {
          return { key: node.dataset.historySource, offset: rect.top - bounds.top };
        }
      }
      return null;
    };
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    const tops = [0, Math.floor(max * 0.2), Math.floor(max * 0.45), Math.floor(max * 0.7), max];
    const samples = [];
    const inversions = [];
    for (const top of tops) {
      el.scrollTop = top;
      const rects = nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return { key: node.dataset.historySource, top: rect.top, bottom: rect.bottom, height: rect.height };
      });
      for (let i = 1; i < rects.length; i += 1) {
        if (rects[i].top + 0.01 < rects[i - 1].top || rects[i].bottom + 0.01 < rects[i - 1].bottom) {
          inversions.push({ scrollTop: el.scrollTop, index: i, prev: rects[i - 1], next: rects[i] });
        }
      }
      const capture = window.__capture();
      const scanned = linear();
      samples.push({
        scrollTop: el.scrollTop,
        capture,
        linear: scanned,
        sameKey: (capture?.key ?? null) === (scanned?.key ?? null),
        offsetDelta: capture && scanned ? Math.abs(capture.offset - scanned.offset) : 0,
      });
    }
    return {
      label: ${quote(label)},
      count: nodes.length,
      overflow: el.scrollHeight > el.clientHeight + 1,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      styles,
      steerMarginTop: steerStyle ? steerStyle.marginTop : null,
      inversions: inversions.slice(0, 8),
      inversionCount: inversions.length,
      samples,
    };
  })()`);
  report.layout.push(result);
  assert.ok(result.count > 8, `${label} rendered sources`);
  assert.ok(result.overflow, `${label} history overflows the real scroller`);
  assert.equal(result.inversionCount, 0, `${label} source rects are not monotonic: ${JSON.stringify(result.inversions[0])}`);
  for (const sample of result.samples) {
    assert.equal(sample.sameKey, true, `${label} capture/linear key mismatch at ${sample.scrollTop}`);
    assert.ok(sample.offsetDelta < 1, `${label} capture/linear offset mismatch at ${sample.scrollTop}`);
  }
}

async function measureRects(page) {
  const result = await page.evaluate(`(() => {
    const el = document.querySelector(".hp-chat .scroll");
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTop = Math.floor(max * 0.5);
    const proto = Element.prototype.getBoundingClientRect;
    let reads = 0;
    Element.prototype.getBoundingClientRect = function counted() {
      if (this.dataset && this.dataset.historySource) reads += 1;
      return proto.call(this);
    };
    let capture;
    try { capture = window.__capture(); }
    finally { Element.prototype.getBoundingClientRect = proto; }
    const bounds = el.getBoundingClientRect();
    let expected = null;
    for (const node of el.querySelectorAll("[data-history-source]")) {
      const rect = node.getBoundingClientRect();
      if (rect.bottom > bounds.top && rect.top < bounds.bottom) {
        expected = { key: node.dataset.historySource, offset: rect.top - bounds.top };
        break;
      }
    }
    return { reads, capture, expected, count: el.querySelectorAll("[data-history-source]").length, scrollTop: el.scrollTop };
  })()`);
  report.rects = result;
  assert.equal(result.count, 1000);
  assert.equal(result.capture?.key, result.expected?.key);
  assert.ok(Math.abs((result.capture?.offset ?? 0) - (result.expected?.offset ?? 0)) < 1);
  assert.ok(result.reads <= 15, `1000-row rect reads ${result.reads} > 15`);
}

function shrinks(frames) {
  const bad = [];
  for (let i = 1; i < frames.length; i += 1) {
    if (!frames[i].body.startsWith(frames[i - 1].body)) bad.push({ at: i, prev: frames[i - 1].body.slice(0, 120), next: frames[i].body.slice(0, 120) });
  }
  return bad;
}

async function setStream(page, text, status, pace, runId, extra) {
  await page.evaluate(`window.__setStream(${quote(text)}, ${quote(status)}, ${pace ? "true" : "false"}, ${quote(runId)}, ${extra ? quote(extra) : "undefined"})`);
}

async function collect(page, ms) {
  return page.evaluate(`window.__collect(${ms})`);
}

async function paint(page, ms = 180) {
  const frames = await collect(page, ms);
  const last = frames.at(-1) || await page.evaluate("window.__read()");
  return { frames, last };
}

function assertHeld(view, label) {
  assert.equal(view.hasThought, false, `${label} opened Thought early`);
  assert.equal(view.thought, "", `${label} thought leaked: ${view.thought}`);
  assert.equal(view.body, "", `${label} body leaked: ${view.body}`);
}

async function runThinking(page) {
  const cases = [];
  const record = (name, extra) => {
    cases.push({ name, ...extra });
    report.thinking = cases;
  };
  const closed = "THINKING:\nEDGE_PRIVATE_NOTE stays private\n$]\nEDGE_PUBLIC_ANSWER";
  const unclosed = "THINKING:\nEDGE_PRIVATE_NOTE stays private";
  const open = "THINKING:\nEDGE_UNCLOSED_BODY never closes";
  const approval = { description: "edge approval", command: "echo edge", choices: ["once", "deny"], pending: true };

  await setStream(page, unclosed, "running", false, "immediate-close");
  const heldNow = await paint(page, 220);
  for (const frame of heldNow.frames) assertHeld(frame, "immediate-close hold");
  await setStream(page, closed, "running", false, "immediate-close");
  const afterClose = await paint(page, 420);
  const immediate = [...heldNow.frames, ...afterClose.frames];
  record("immediate-close", { held: heldNow.last, frames: immediate, shrinks: shrinks(immediate), source: await page.evaluate("window.__run.text") });

  const spanFrames = [];
  for (const partial of ["T", "THIN", "THINKING:", "THINKING:\n", unclosed]) {
    await setStream(page, partial, "running", true, "marker-span");
    const painted = await paint(page, 140);
    for (const frame of painted.frames) assertHeld(frame, "marker-span " + JSON.stringify(partial));
    spanFrames.push(...painted.frames);
  }
  await setStream(page, closed, "running", true, "marker-span");
  const pacedAfter = await paint(page, 700);
  const paced = [...spanFrames, ...pacedAfter.frames];
  record("marker-span-paced-close", { frames: paced, shrinks: shrinks(paced), source: await page.evaluate("window.__run.text") });

  await setStream(page, "THINKING:\nEDGE_UNCLOSED", "running", false, "unclosed");
  const midOpen = await paint(page, 160);
  for (const frame of midOpen.frames) assertHeld(frame, "unclosed partial");
  await setStream(page, open, "running", false, "unclosed");
  const runningOpen = await paint(page, 180);
  for (const frame of runningOpen.frames) assertHeld(frame, "unclosed running");
  await setStream(page, open, "completed", false, "unclosed");
  const doneOpen = await paint(page, 360);
  const unclosedFrames = [...midOpen.frames, ...runningOpen.frames, ...doneOpen.frames];
  record("unclosed-to-done", { frames: unclosedFrames, shrinks: shrinks(unclosedFrames), final: doneOpen.last, source: await page.evaluate("window.__run.text") });

  for (const status of ["failed", "cancelled"]) {
    await setStream(page, open, "running", false, "end-" + status);
    const stillHeld = await paint(page, 140);
    for (const frame of stillHeld.frames) assertHeld(frame, status + " before end");
    await setStream(page, open, status, false, "end-" + status);
    const released = await paint(page, 280);
    const endFrames = [...stillHeld.frames, ...released.frames];
    record("unclosed-to-" + status, { frames: endFrames, shrinks: shrinks(endFrames), final: released.last, source: await page.evaluate("window.__run.text") });
  }

  await setStream(page, open, "running", true, "stop-release");
  const beforeStop = await paint(page, 140);
  for (const frame of beforeStop.frames) assertHeld(frame, "before stop");
  await setStream(page, open, "running", true, "stop-release", { stopping: true });
  const stopped = await paint(page, 700);
  const stopFrames = [...beforeStop.frames, ...stopped.frames];
  record("unclosed-after-stop", { frames: stopFrames, shrinks: shrinks(stopFrames), final: stopped.last, source: await page.evaluate("window.__run.text") });

  await setStream(page, unclosed, "running", false, "approval-hold", { approval });
  const approvalHold = await paint(page, 220);
  for (const frame of approvalHold.frames) assertHeld(frame, "approval hold");
  record("approval-not-terminal", { frames: approvalHold.frames, shrinks: shrinks(approvalHold.frames), final: approvalHold.last, source: await page.evaluate("window.__run.text") });

  const guide = "THINKING: a guide to clearer writing.";
  await setStream(page, "THINKING:", "running", false, "guide");
  const guideHeld = await paint(page, 160);
  for (const frame of guideHeld.frames) assertHeld(frame, "guide marker");
  await setStream(page, guide, "running", false, "guide");
  const guideShown = await paint(page, 360);
  const guideFrames = [...guideHeld.frames, ...guideShown.frames];
  record("real-guide", { frames: guideFrames, shrinks: shrinks(guideFrames), final: guideShown.last, source: await page.evaluate("window.__run.text") });

  await setStream(page, "The public sentence stays visible.", "running", false, "the-prose");
  const theShown = await paint(page, 240);
  record("leading-the", { frames: theShown.frames, shrinks: shrinks(theShown.frames), final: theShown.last, source: await page.evaluate("window.__run.text") });

  const prose = "EDGE_NORMAL_PROSE grows steadily without a marker.";
  await setStream(page, "EDGE_NORMAL", "running", true, "prose");
  const proseA = await paint(page, 250);
  await setStream(page, prose, "running", true, "prose");
  const proseB = await paint(page, 800);
  const proseFrames = [...proseA.frames, ...proseB.frames];
  record("normal-prose", { frames: proseFrames, shrinks: shrinks(proseFrames), final: proseB.last, source: await page.evaluate("window.__run.text") });

  const tick = "THINKING:\ncode `tick` stays\n$]\nEDGE_TICK_BODY";
  await setStream(page, "THINKING:\ncode `tick`", "running", false, "tick");
  const tickPartial = await paint(page, 200);
  await setStream(page, tick, "running", false, "tick");
  const tickShown = await paint(page, 360);
  const tickFrames = [...tickPartial.frames, ...tickShown.frames];
  record("rejected-backtick", { frames: tickFrames, shrinks: shrinks(tickFrames), final: tickShown.last, partial: tickPartial.last, source: await page.evaluate("window.__run.text") });

  const middle = "Prefix stays. THINKING:\nnot a protocol\n$]\nEDGE_MIDDLE";
  await setStream(page, "Prefix stays. THINKING:\nnot", "running", false, "middle");
  const middlePartial = await paint(page, 180);
  await setStream(page, middle, "running", false, "middle");
  const middleShown = await paint(page, 320);
  const middleFrames = [...middlePartial.frames, ...middleShown.frames];
  record("middle-marker", { frames: middleFrames, shrinks: shrinks(middleFrames), final: middleShown.last, partial: middlePartial.last, source: await page.evaluate("window.__run.text") });

  await setStream(page, unclosed, "running", false, "run-a");
  const runA = await paint(page, 140);
  for (const frame of runA.frames) assertHeld(frame, "run-a");
  await setStream(page, "EDGE_OTHER_RUN visible", "running", false, "run-b");
  const runB = await paint(page, 240);
  const switchFrames = [...runA.frames, ...runB.frames];
  record("run-switch", { frames: switchFrames, shrinks: shrinks(switchFrames), final: runB.last, source: await page.evaluate("window.__run.text") });

  for (const item of cases) {
    assert.equal(item.source, item.frames.at(-1)?.source ?? item.source, item.name + " run.text mutated or unread");
    assert.deepEqual(item.shrinks, [], item.name + " body shrank: " + JSON.stringify(item.shrinks[0]));
  }
  const byName = Object.fromEntries(cases.map((item) => [item.name, item]));
  const immediateFinal = byName["immediate-close"].frames.at(-1);
  assert.equal(byName["immediate-close"].source, closed);
  assert.match(immediateFinal.body, /EDGE_PUBLIC_ANSWER/);
  assert.doesNotMatch(immediateFinal.body, /EDGE_PRIVATE_NOTE|THINKING:|\$]/);
  assert.match(immediateFinal.thought, /EDGE_PRIVATE_NOTE/);
  const pacedFinal = byName["marker-span-paced-close"].frames.at(-1);
  assert.match(pacedFinal.body, /EDGE_PUBLIC_ANSWER/);
  assert.doesNotMatch(pacedFinal.body, /EDGE_PRIVATE_NOTE|THINKING:|\$]/);
  assert.match(pacedFinal.thought, /EDGE_PRIVATE_NOTE/);
  for (const name of ["unclosed-to-done", "unclosed-to-failed", "unclosed-to-cancelled", "unclosed-after-stop"]) {
    const final = byName[name].final;
    assert.match(final.body, /EDGE_UNCLOSED_BODY/, name);
    assert.equal(final.thought, "", name);
    assert.equal(final.hasThought, false, name);
  }
  const stopFinal = byName["unclosed-after-stop"].final.body;
  const stopBodies = byName["unclosed-after-stop"].frames.map((frame) => frame.body);
  assert.ok(stopBodies.some((body) => body.length > 0 && body.length < stopFinal.length && stopFinal.startsWith(body)), "stop release skipped body animation");
  assertHeld(byName["approval-not-terminal"].final, "approval final");
  const guideFinal = byName["real-guide"].final;
  assert.match(guideFinal.body, /THINKING: a guide to clearer writing/);
  assert.equal(guideFinal.thought, "");
  assert.equal(guideFinal.hasThought, false);
  assert.match(byName["leading-the"].final.body, /The public sentence stays visible/);
  assert.equal(byName["leading-the"].final.hasThought, false);
  const proseFinal = byName["normal-prose"].final;
  assert.match(proseFinal.body, /EDGE_NORMAL_PROSE grows steadily/);
  assert.equal(proseFinal.hasThought, false);
  assert.ok(byName["normal-prose"].frames.some((frame) => frame.body.includes("EDGE_NORMAL") && frame.body.length < prose.length), "normal prose lost paced growth");
  const tickFinal = byName["rejected-backtick"].final;
  assert.match(byName["rejected-backtick"].partial.body, /THINKING:/);
  assert.match(tickFinal.body, /EDGE_TICK_BODY/);
  assert.match(tickFinal.body, /THINKING:/);
  assert.equal(tickFinal.hasThought, false);
  const middleFinal = byName["middle-marker"].final;
  assert.match(byName["middle-marker"].partial.body, /Prefix stays/);
  assert.match(middleFinal.body, /EDGE_MIDDLE/);
  assert.match(middleFinal.body, /Prefix stays/);
  assert.equal(middleFinal.hasThought, false);
  assert.match(byName["run-switch"].final.body, /EDGE_OTHER_RUN/);
  assert.doesNotMatch(byName["run-switch"].final.body, /EDGE_PRIVATE_NOTE/);
  assert.equal(byName["run-switch"].final.thought, "");
}

await mkdir(evidence, { recursive: true });
try {
  await withPage(root, "/tests/display-edge-fixture.html?case=layout", async (page) => {
    await waitFor(page.evaluate, "window.fixtureReady === true && !window.fixtureError", "layout", 20000);
    await measureLayout(page, "desktop-varied");
    await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 700, deviceScaleFactor: 1, mobile: true }, page.sessionId);
    await openCase(page, "layout", 390, 700);
    await measureLayout(page, "mobile-varied");
    await openCase(page, "rects", 1280, 800);
    await measureRects(page);
    await openCase(page, "stream", 1280, 800);
    await runThinking(page);
  });
} catch (error) {
  report.failures.push(String(error && error.stack || error));
  await writeFile(reportPath, JSON.stringify(report, null, 2));
  console.error(error);
  process.exit(1);
}
await writeFile(reportPath, JSON.stringify(report, null, 2));
console.log(JSON.stringify({
  layout: report.layout.map((item) => ({ label: item.label, inversions: item.inversionCount, count: item.count })),
  rects: report.rects && { reads: report.rects.reads, key: report.rects.capture?.key },
  thinking: report.thinking.map((item) => ({ name: item.name, shrinks: item.shrinks.length, body: item.frames?.at(-1)?.body?.slice(0, 80) || "" })),
}, null, 2));
