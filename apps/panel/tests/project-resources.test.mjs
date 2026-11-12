import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/tmp", "project-resources-"));
const calls = [];

globalThis.document = { baseURI: "https://panel.example.test/" };
globalThis.window = {
  setTimeout,
  clearTimeout,
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};
globalThis.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
  const entry = {
    href: url.href,
    pathname: url.pathname,
    method: (init?.method || "GET").toUpperCase(),
    body: init?.body ?? null,
    credentials: init?.credentials ?? "",
    headers: init?.headers ?? {},
    signal: init?.signal ?? null,
  };
  calls.push(entry);
  const scripted = responses.shift();
  if (scripted?.hang) {
    return new Promise((resolve, reject) => {
      const fail = () => reject(new DOMException("Aborted", "AbortError"));
      if (init?.signal?.aborted) fail();
      else init?.signal?.addEventListener("abort", fail, { once: true });
    });
  }
  if (scripted?.network) throw new TypeError("network down");
  return new Response(scripted?.body ?? "", {
    status: scripted?.status ?? 200,
    headers: { "Content-Type": scripted?.type ?? "application/json" },
  });
};

const responses = [];
const server = await createServer({
  root,
  configFile: path.join(root, "vite.config.ts"),
  cacheDir: path.join(owned, "vite"),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: "custom",
});

function snapshot(projectId, extra = {}) {
  return {
    project_id: projectId,
    archived: false,
    instructions: "",
    files: [],
    memories: [],
    limits: { file_bytes: 100, instructions_chars: 20 },
    ...extra,
  };
}

function json(value, status = 200) {
  return { status, body: JSON.stringify(value) };
}

try {
  const lib = await server.ssrLoadModule("/src/react/lib/project-resources.ts");
  const tsx = await readFile(path.join(root, "src/react/pages/projects/project-resources.tsx"), "utf8");
  const css = await readFile(path.join(root, "src/react/pages/projects/project-resources.css"), "utf8");

  await test("component source keeps the frozen export and does not call the old memory API", () => {
    assert.match(tsx, /export function ProjectResources\(props: \{/);
    assert.match(tsx, /projectId: string;/);
    assert.match(tsx, /projectName: string;/);
    assert.match(tsx, /archived: boolean;/);
    assert.match(tsx, /新增档案/);
    assert.match(tsx, /新增指示/);
    assert.match(tsx, /查看记忆/);
    assert.match(tsx, /尚无档案/);
    assert.match(tsx, /供 clawd 在「/);
    assert.match(tsx, /指示 clawd 在「/);
    assert.match(tsx, /行为与回应方式/);
    assert.match(tsx, /showModal\(/);
    assert.match(tsx, /role="dialog"/);
    assert.match(tsx, /aria-modal="true"/);
    assert.match(tsx, /type="file"/);
    assert.match(tsx, /AbortController/);
    assert.match(tsx, /acceptScopedResult/);
    assert.match(tsx, /更新时间未知|formatResourceUpdated/);
    assert.doesNotMatch(tsx, /memory\.read|memory\.update|projectsApi\.memory|session_id|Authorization|Bearer /);
    assert.doesNotMatch(tsx, /Claude|檔案|記憶|專案檔案|记入这个项目|Car|Devices|Interests|Mother/);
    assert.match(css, /\.project-resources-/);
    assert.match(css, /safe-area-inset-bottom/);
    assert.match(css, /min-height:\s*44px/);
    assert.doesNotMatch(css, /(^|\n)button\s*\{/);
  });

  await test("real project error envelopes display human messages instead of machine codes", async () => {
    assert.equal(lib.resourcesErrorMessage({ error: "file_exists", message: "同名档案已存在，未覆盖" }, 409), "同名档案已存在，未覆盖");
    assert.equal(lib.resourcesErrorMessage({ error: { message: "内部中文说明" }, message: "外层说明" }, 400), "内部中文说明");
    responses.push(json({ error: "resources_read_failed", message: "项目资源读取失败，未当作空项目" }, 500));
    await assert.rejects(() => lib.readProjectResources("p1"), /项目资源读取失败/);
    responses.push(json({ error: "instructions_rejected", message: "指示未通过安全检查" }, 400));
    await assert.rejects(() => lib.saveProjectInstructions("p1", "草稿"), /指示未通过安全检查/);
  });

  await test("schema rejects empty success and preserves null timestamps", () => {
    assert.throws(() => lib.parseProjectResources(undefined, "p1"), /空的|无法识别/);
    assert.throws(() => lib.parseProjectResources({}, "p1"), /空对象/);
    assert.throws(() => lib.parseProjectResources({ success: true }, "p1"), /空成功/);
    assert.throws(() => lib.parseProjectResources(snapshot("other"), "p1"), /不属于当前项目/);
    assert.throws(() => lib.parseProjectResources({ ...snapshot("p1"), instructions: undefined }, "p1"), /指示缺失/);
    assert.throws(() => lib.parseProjectResources({ ...snapshot("p1"), files: undefined }, "p1"), /档案列表缺失/);
    assert.throws(() => lib.parseProjectResources({ ...snapshot("p1"), memories: undefined }, "p1"), /记忆列表缺失/);
    assert.throws(() => lib.parseProjectResources({
      ...snapshot("p1"),
      memories: [{ id: "m", title: "甲", content: "文", updated_at: "昨天" }],
    }, "p1"), /没有编造时间/);
    const parsed = lib.parseProjectResources(snapshot("p1", {
      files: [{ name: "笔记.md", size: 4, updated_at: null, content_type: "text/markdown" }],
      memories: [{ id: "m1", title: "备忘", content: "只属于这个项目", updated_at: null }],
    }), "p1");
    assert.equal(parsed.files[0].updated_at, null);
    assert.equal(parsed.memories[0].updated_at, null);
    assert.equal(parsed.instructions, "");
    assert.equal(parsed.files.length, 1);
    assert.throws(() => lib.parseInstructionsWrite({ success: true }, "p1"), /缺少内容|没有保存成功|空的/);
    assert.throws(() => lib.parseInstructionsWrite({ success: true, project_id: "other", instructions: "甲" }, "p1"), /不属于/);
    assert.deepEqual(lib.parseInstructionsWrite({ success: true, project_id: "p1", instructions: "" }, "p1").instructions, "");
    assert.throws(() => lib.parseFileWrite({ success: true, project_id: "p1" }, "p1"), /缺少档案/);
    assert.throws(() => lib.parseFileWrite({ success: true, project_id: "p1", file: { name: "../x", size: 1, updated_at: 1, content_type: "text/plain" } }, "p1"), /档案名/);
  });

  await test("time and upload policy do not invent data or overwrite same names", () => {
    const now = 1_700_000_000_000;
    assert.equal(lib.formatResourceUpdated(null, now), "更新时间未知");
    assert.equal(lib.formatResourceUpdated(undefined, now), "更新时间未知");
    assert.equal(lib.formatResourceUpdated(now / 1000 - 30, now), "刚刚更新");
    assert.equal(lib.formatResourceUpdated(now / 1000 - 120, now), "2 分钟前更新");
    assert.equal(lib.uploadBlockedReason([{ name: "笔记.md" }], { name: "笔记.md", size: 3 }, 100, false), "已有同名档案「笔记.md」，没有覆盖");
    assert.match(lib.uploadBlockedReason([], { name: "大.md", size: 101 }, 100, false), /没有上传/);
    assert.match(lib.uploadBlockedReason([], { name: "../x", size: 1 }, 100, false), /没有请求/);
    assert.match(lib.uploadBlockedReason([], { name: "a.md", size: 1 }, 100, true), /已归档/);
    assert.equal(lib.uploadBlockedReason([], { name: "a.md", size: 1 }, 100, false), "");
    assert.match(lib.instructionsBlockedReason(null, 20, false, false), /不能保存/);
    assert.match(lib.instructionsBlockedReason("超过二十个字的指示草稿内容", 4, false, true), /没有保存/);
    assert.equal(lib.instructionsBlockedReason("", 20, false, true), "");
    const guard = lib.createOperationGuard();
    const first = guard.begin("p1");
    const second = guard.begin("p2");
    assert.equal(guard.matches(first), false);
    assert.equal(guard.matches(second), true);
    assert.equal(lib.acceptScopedResult(first, guard.current(), "p1"), false);
    assert.equal(lib.acceptScopedResult(second, guard.current(), "p2"), true);
    assert.equal(lib.acceptScopedResult(second, guard.current(), "p1"), false);
  });

  await test("read and save use the resources contract without a session", async () => {
    calls.length = 0;
    responses.push(json(snapshot("p1", { instructions: "已有指示" })));
    const loaded = await lib.readProjectResources("p1");
    assert.equal(loaded.instructions, "已有指示");
    assert.equal(calls[0].method, "GET");
    assert.match(calls[0].pathname, /\/api\/projects\/p1\/resources$/);
    assert.equal(calls[0].credentials, "same-origin");
    assert.doesNotMatch(calls[0].href, /session_id|memory/);

    calls.length = 0;
    responses.push(json({ success: true }));
    await assert.rejects(() => lib.saveProjectInstructions("p1", "草稿"), /没有当成成功|缺少内容|没有保存成功/);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "PUT");
    assert.match(calls[0].pathname, /\/resources\/instructions$/);
    assert.deepEqual(JSON.parse(calls[0].body), { content: "草稿" });
    assert.equal(calls[0].headers["Content-Type"], "application/json");

    calls.length = 0;
    responses.push(json({ success: true, project_id: "p1", instructions: "草稿" }));
    responses.push(json(snapshot("p1", { instructions: "另一份" })));
    const mismatched = await lib.saveProjectInstructions("p1", "草稿");
    assert.equal(mismatched.confirmed, false);
    assert.equal(mismatched.instructions, "另一份");
    assert.equal(calls.length, 2);
    assert.equal(calls[1].method, "GET");

    calls.length = 0;
    responses.push(json({ success: true, project_id: "p1", instructions: "草稿" }));
    responses.push(json(snapshot("p1", { instructions: "草稿" })));
    const confirmed = await lib.saveProjectInstructions("p1", "草稿");
    assert.equal(confirmed.confirmed, true);
    assert.equal(calls.some((item) => item.href.includes("/memory")), false);
  });

  await test("aborted and mismatched reads are not successful empty projects", async () => {
    calls.length = 0;
    responses.push({ hang: true });
    const ctrl = new AbortController();
    const pending = lib.readProjectResources("p1", ctrl.signal);
    ctrl.abort();
    await assert.rejects(pending, (error) => error?.name === "AbortError");

    calls.length = 0;
    responses.push(json(snapshot("other")));
    await assert.rejects(() => lib.readProjectResources("p1"), /不属于当前项目/);
    responses.push(json({}));
    await assert.rejects(() => lib.readProjectResources("p1"), /空对象|没有当成成功/);
  });

  await test("upload posts multipart file and rejects conflict or empty success", async () => {
    const seen = [];
    class FakeXHR {
      upload = { onprogress: null };
      status = 200;
      responseText = "";
      withCredentials = false;
      headers = {};
      open(method, url) {
        this.method = method;
        this.url = url;
      }
      setRequestHeader(key, value) { this.headers[key] = value; }
      abort() { this.onabort?.(); }
      send(body) {
        this.body = body;
        seen.push(this);
        const scripted = xhrScripts.shift();
        if (scripted) {
          this.status = scripted.status ?? 200;
          this.responseText = scripted.body ?? "";
          if (scripted.hang) return;
        }
        this.onload?.();
      }
    }
    const xhrScripts = [];
    globalThis.XMLHttpRequest = FakeXHR;
    const file = typeof File === "function"
      ? new File(["hi"], "笔记.md", { type: "text/markdown" })
      : Object.assign(new Blob(["hi"], { type: "text/markdown" }), { name: "笔记.md" });

    xhrScripts.push({ status: 200, body: JSON.stringify({ success: true }) });
    await assert.rejects(() => lib.uploadProjectFile("p1", file), /缺少档案|没有当成成功/);
    assert.equal(seen[0].method, "POST");
    assert.match(seen[0].url, /\/panel-api\/api\/projects\/p1\/resources\/files$/);
    assert.equal(seen[0].withCredentials, true);
    assert.equal(seen[0].headers["Content-Type"], undefined);
    assert.equal(seen[0].headers.Authorization, undefined);
    assert.equal(seen[0].body.get("file").name, "笔记.md");

    xhrScripts.push({ status: 409, body: JSON.stringify({ error: { message: "已有同名档案，没有覆盖" } }) });
    await assert.rejects(() => lib.uploadProjectFile("p1", file), /没有覆盖/);

    xhrScripts.push({
      status: 200,
      body: JSON.stringify({
        success: true,
        project_id: "p1",
        file: { name: "笔记.md", size: 2, updated_at: 10, content_type: "text/markdown" },
      }),
    });
    const uploaded = await lib.uploadProjectFile("p1", file, undefined, () => {});
    assert.equal(uploaded.file.name, "笔记.md");
    assert.equal(uploaded.project_id, "p1");

    const ctrl = new AbortController();
    xhrScripts.push({ hang: true });
    const hanging = lib.uploadProjectFile("p1", file, ctrl.signal);
    ctrl.abort();
    await assert.rejects(hanging, (error) => error?.name === "AbortError");
    seen[seen.length - 1].onload?.();
    await assert.rejects(Promise.resolve(hanging), (error) => error?.name === "AbortError");

    calls.length = 0;
    await assert.rejects(() => lib.downloadProjectFile("p1", "../secret.md"), /没有请求/);
    assert.equal(calls.length, 0);
  });
  await test("ordinary names pass; one backslash, controls, and foreign ids stay rejected", () => {
    assert.equal(lib.resourceFileNameError("笔记.md"), "");
    assert.equal(lib.resourceFileNameError("notes.md"), "");
    assert.match(lib.resourceFileNameError("dir\\file.md"), /没有请求/);
    assert.match(lib.resourceFileNameError("a\u0000.md"), /没有请求/);
    assert.match(lib.resourceFileNameError("a\u001f.md"), /没有请求/);
    assert.match(lib.resourceFileNameError("a\u007f.md"), /没有请求/);
    assert.match(lib.resourceFileNameError("../x"), /没有请求/);
    assert.match(lib.resourceFileNameError("a/b.md"), /没有请求/);
    assert.equal(lib.uploadBlockedReason([], { name: "笔记.md", size: 1 }, 100, false), "");
    assert.equal(lib.uploadBlockedReason([], { name: "notes.md", size: 1 }, 100, false), "");
    assert.match(lib.uploadBlockedReason([], { name: "dir\\file.md", size: 1 }, 100, false), /没有请求/);
    assert.throws(() => lib.resourceFilePath("p1", "dir\\file.md"), /没有请求/);
    assert.throws(() => lib.resourceFilePath("p1", "a\u0000.md"), /没有请求/);
    assert.equal(lib.resourceFilePath("p1", "笔记.md"), `api/projects/p1/resources/files/${encodeURIComponent("笔记.md")}`);
    assert.throws(() => lib.parseInstructionsWrite({ success: true }, "p1"), /缺少内容|没有保存成功|空的/);
    assert.throws(() => lib.parseInstructionsWrite({ success: true }, "p1"), (error) => !/不属于/.test(String(error)));
    assert.throws(() => lib.parseInstructionsWrite({ success: true, project_id: "other" }, "p1"), /不属于/);
    assert.throws(() => lib.parseInstructionsWrite({ success: true, project_id: "other", instructions: "甲" }, "p1"), /不属于/);
    assert.throws(() => lib.parseInstructionsWrite({ success: true, instructions: "甲" }, "p1"), /缺少项目身份/);
    assert.throws(() => lib.parseFileWrite({ success: true }, "p1"), /缺少档案|没有当成成功/);
    assert.throws(() => lib.parseFileWrite({ success: true }, "p1"), (error) => !/不属于/.test(String(error)));
    assert.throws(() => lib.parseFileWrite({ success: true, project_id: "other", file: { name: "笔记.md", size: 1, updated_at: 1, content_type: "text/plain" } }, "p1"), /不属于/);
    assert.throws(() => lib.parseFileWrite({ success: true, file: { name: "笔记.md", size: 1, updated_at: 1, content_type: "text/plain" } }, "p1"), /缺少项目身份/);
  });

} finally {
  await server.close();
  await rm(owned, { recursive: true, force: true }).catch(() => undefined);
}
