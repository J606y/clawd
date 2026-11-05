import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");

test("live confirm is a top-layer dialog, not an in-dock scrim", async () => {
  const dock = await readFile(path.join(root, "src/react/docks/live-dock.tsx"), "utf8");
  const sheet = await readFile(path.join(root, "src/react/docks/sheet-presence.tsx"), "utf8");
  const css = await readFile(path.join(root, "src/react/docks/docks.css"), "utf8");
  assert.match(dock, /ConfirmDialog/);
  assert.doesNotMatch(dock, /className="scrim"/);
  assert.doesNotMatch(dock, /SheetPresence/);
  assert.match(sheet, /showModal\(/);
  assert.match(sheet, /createPortal\(/);
  assert.match(sheet, /document\.body/);
  assert.doesNotMatch(sheet, /display:\s*contents/);
  const modal = css.slice(css.indexOf("dialog.live-confirm:modal"), css.indexOf("dialog.live-confirm::backdrop"));
  assert.match(modal, /transform:\s*none/);
  assert.doesNotMatch(modal, /backdrop-filter/);
  assert.match(css, /dialog\.live-confirm::backdrop[\s\S]*backdrop-filter:\s*blur\(6px\)/);
});
