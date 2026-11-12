// 文件页上传目录契约。只读源码，不 import 面板、不上传。
// 本次只准备，不运行。退出码未知。不是验收通过，也不是上线许可。

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const panel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const react = path.join(panel, "src/react");

async function text(rel) {
  return readFile(path.join(react, rel), "utf8");
}

function sliceBetween(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.notEqual(from, -1, start);
  assert.notEqual(to, -1, end);
  return source.slice(from, to);
}

test("uploadFile keeps old calls and only adds a relative dir", async () => {
  const sidecar = await text("lib/sidecar.ts");
  const fn = sliceBetween(sidecar, "function relativeUploadDir", "export function downloadUrl");
  assert.match(fn, /timeoutMs = 0, dir\?: string/);
  assert.equal(fn.includes("timeoutMs = 0): Promise<UploadResult>"), false);
  const queryLine = fn.split("\n").find((line) => line.includes("new URLSearchParams"));
  assert.ok(queryLine);
  assert.match(queryLine, /\{ root, name:/);
  assert.equal(queryLine.includes("dir"), false);
  assert.match(fn, /if \(relative\) q\.set\("dir", relative\)/);
  assert.equal(fn.includes('q.set("dir", dir)'), false);
  assert.match(fn, /dir\.length === 0\) return null/);
  assert.match(fn, /startsWith\("\/"\)/);
  assert.match(fn, /不能上传到绝对路径/);
  const guard = fn.indexOf("不能上传到绝对路径");
  const setDir = fn.indexOf('q.set("dir", relative)');
  const open = fn.indexOf("xhr.open");
  assert.ok(guard !== -1 && setDir !== -1 && open !== -1);
  assert.ok(guard < open);
  assert.ok(fn.indexOf("const relative = relativeUploadDir(dir)") < open);
});

test("files page freezes root and dir and does not leak late results", async () => {
  const page = await text("pages/files/files-page.tsx");
  const upload = sliceBetween(page, "const upload = async", "const remove = async");
  assert.match(upload, /const startedRoot = rootRef\.current/);
  assert.match(upload, /const startedPath = pathRef\.current/);
  assert.match(upload, /const stillThere = \(\) => rootRef\.current === startedRoot && pathRef\.current === startedPath/);
  assert.match(upload, /uploadFile\(file, startedRoot,/);
  assert.match(upload, /0, startedPath\)/);
  assert.match(upload, /queryKeys\.dir\(startedRoot, startedPath\)/);
  assert.match(upload, /result\.absPath/);
  assert.match(upload, /MAX_UPLOAD_BYTES/);
  assert.equal(upload.includes("uploadFile(file, rootRef.current"), false);
  assert.equal(upload.includes("refreshRoot"), false);
  assert.equal(upload.includes("setRoot("), false);
  assert.equal(upload.includes("setPath("), false);
  assert.equal(upload.includes("setConfirm("), false);
  assert.equal(upload.includes("open("), false);
  assert.equal(upload.includes("shown.absPath"), false);
  const late = upload.split("await uploadFile", 2)[1];
  assert.match(late, /if \(mounted\.current && stillThere\(\)\) setNotice\(`已上传/);
  assert.match(late, /if \(mounted\.current && stillThere\(\)\) \{/);
  assert.equal(late.includes("setRoot("), false);
  assert.equal(late.includes("setPath("), false);
  assert.equal(late.includes("setConfirm("), false);
  assert.equal(late.includes("queryKeys.dir(rootRef"), false);
  assert.equal(late.includes("queryKeys.dir(startedRoot, startedPath)"), true);
});

test("existing upload callers stay on the old argument list", async () => {
  const composer = await text("pages/chat/composer.tsx");
  assert.match(composer, /uploadFile\(file, "uploads"\)/);
  assert.match(composer, /uploadFile\(file, "uploads", undefined, ARCHIVE_MS\)/);
  assert.equal(composer.includes('uploadFile(file, "uploads", undefined, ARCHIVE_MS,'), false);
});
