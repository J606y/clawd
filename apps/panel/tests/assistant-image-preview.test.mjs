import test from "node:test";
import { spawn } from "node:child_process";

function runBrowser(name) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [new URL("assistant-image-preview-browser.mjs", import.meta.url).pathname, name], { stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.stderr.on("data", (chunk) => { out += chunk; });
    const timer = setTimeout(() => { child.kill("SIGTERM"); reject(new Error(`browser case timed out\n${out}`)); }, 180000);
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(out || `browser case exited ${code}`));
    });
  });
}

test("PWA standalone image preview does not call window.open", async () => {
  await runBrowser("pwa-image");
});

test("PWA standalone ordinary external links still call window.open", async () => {
  await runBrowser("pwa-external");
});

test("mobile preview dialog is modal, fits the viewport, and exposes a visible close control", async () => {
  await runBrowser("dialog-fit");
});

test("a broken assistant image does not hide a sibling image", async () => {
  await runBrowser("bad-image");
});

test("leaving the session removes an open preview dialog", async () => {
  await runBrowser("unmount");
});
