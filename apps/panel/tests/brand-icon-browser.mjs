import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage, waitFor } from "./fixtures/cdp-harness.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function hasRect(rects, x, y, width, height, fill) {
  return rects.some((rect) => (
    rect.x === x
    && rect.y === y
    && rect.width === width
    && rect.height === height
    && String(rect.fill).toLowerCase() === fill.toLowerCase()
  ));
}

await withPage(root, "/", async ({ evaluate }) => {
  await waitFor(evaluate, "document.querySelector('.shell .brand .dot svg')", "brand mark", 90000);
  const probe = await evaluate(`(() => {
    const dot = document.querySelector(".shell .brand .dot");
    const brand = dot.closest(".brand");
    const box = dot.getBoundingClientRect();
    const brandBox = brand.getBoundingClientRect();
    const style = getComputedStyle(brand);
    const svg = dot.querySelector("svg");
    const rects = [...svg.querySelectorAll("rect")].map((rect) => ({
      x: rect.getAttribute("x"),
      y: rect.getAttribute("y"),
      width: rect.getAttribute("width"),
      height: rect.getAttribute("height"),
      fill: rect.getAttribute("fill"),
    }));
    const link = document.querySelector('link[rel="icon"]');
    const raw = link ? link.getAttribute("href") || "" : "";
    let favicon = { parsed: false, error: "missing icon link", svg: "", hash: "" };
    try {
      const resolved = link.href;
      const url = new URL(resolved);
      if (url.protocol !== "data:") throw new Error("icon link is not a data URI: " + url.protocol);
      const comma = resolved.indexOf(",");
      if (comma < 0) throw new Error("data URI has no payload");
      const decoded = decodeURIComponent(resolved.slice(comma + 1));
      const doc = new DOMParser().parseFromString(decoded, "image/svg+xml");
      const parserError = doc.querySelector("parsererror");
      if (parserError) throw new Error(parserError.textContent.slice(0, 180));
      favicon = {
        parsed: true,
        error: "",
        svg: doc.documentElement.outerHTML,
        hash: url.hash,
      };
    } catch (error) {
      favicon = { parsed: false, error: String(error), svg: "", hash: "" };
    }
    return {
      width: box.width,
      height: box.height,
      brandHeight: brandBox.height,
      gap: style.gap,
      padding: style.padding,
      fontWeight: style.fontWeight,
      ariaHidden: dot.getAttribute("aria-hidden"),
      hermes: (brand.childNodes[1]?.textContent || "").trim() === "Hermes" || brand.textContent.includes("Hermes"),
      html: svg.outerHTML,
      viewBox: svg.getAttribute("viewBox"),
      shapeRendering: svg.getAttribute("shape-rendering"),
      rects,
      raw,
      favicon,
    };
  })()`);
  console.log(`PROBE ${JSON.stringify({
    width: probe.width,
    height: probe.height,
    brandHeight: probe.brandHeight,
    gap: probe.gap,
    padding: probe.padding,
    fontWeight: probe.fontWeight,
    ariaHidden: probe.ariaHidden,
    pause: probe.html.includes("M10 9v14"),
    viewBox: probe.viewBox,
    rects: probe.rects,
    faviconParsed: probe.favicon.parsed,
    faviconError: probe.favicon.error,
    faviconHash: probe.favicon.hash,
    rawHasHash: probe.raw.includes("#"),
  })}`);
  assert.equal(probe.ariaHidden, "true", "brand icon must stay aria-hidden");
  assert.equal(probe.hermes, true, "Hermes label must stay beside the icon");
  assert.equal(probe.width, 22, ".dot width must stay 22");
  assert.equal(probe.height, 22, ".dot height must stay 22");
  assert.equal(probe.brandHeight, 52, "brand row must not grow");
  assert.equal(probe.html.includes("M10 9v14"), false, `brand still contains pause path M10 9v14: ${probe.html}`);
  assert.equal(probe.viewBox, "40 15 300 300", "brand viewBox drifted");
  assert.equal(probe.shapeRendering, "crispEdges", "brand shape-rendering drifted");
  assert.equal(hasRect(probe.rects, "80", "120", "220", "120", "#D97757"), true, "brand missing body rect 80,120 220x120 #D97757");
  assert.equal(hasRect(probe.rects, "120", "60", "140", "40", "#8B5A2B"), true, "brand missing hat rect 120,60 140x40 #8B5A2B");
  assert.equal(probe.raw.includes("#"), false, "favicon # must be encoded as %23");
  assert.equal(probe.favicon.parsed, true, `favicon link did not parse: ${probe.favicon.error}`);
  assert.equal(probe.favicon.hash, "", "favicon data URI was truncated at #");
  assert.equal(probe.favicon.svg.includes("M10 9v14"), false, "favicon still contains pause path M10 9v14");
  const faviconDoc = probe.favicon.svg;
  assert.equal(faviconDoc.includes('x="80"') && faviconDoc.includes('y="120"') && faviconDoc.includes('width="220"') && faviconDoc.includes('height="120"') && /#D97757/i.test(faviconDoc), true, "favicon missing body rect");
  assert.equal(faviconDoc.includes('x="120"') && faviconDoc.includes('y="60"') && faviconDoc.includes('width="140"') && faviconDoc.includes('height="40"') && /#8B5A2B/i.test(faviconDoc), true, "favicon missing hat rect");
}, "");

console.log("GREEN brand icon");
