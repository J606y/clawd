import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "..");
const owned = await mkdtemp(path.join(process.env.TMPDIR || "/root/hermes-workspace/clawd/artifacts/panel-projects/tmp", "project-panel-"));
const calls = [];
globalThis.document = { baseURI: "https://panel.example.test/" };
globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  setTimeout,
  clearTimeout,
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => true,
};
globalThis.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
  calls.push({ href: url.href, method: (init?.method || "GET").toUpperCase(), body: init?.body ?? null });
  if (url.pathname.includes("panel-core")) {
    return new Response(JSON.stringify({ folders: [{ id: "old", name: "旧夹" }], assignments: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
  }
  return new Response(JSON.stringify({ error: "unmocked", message: url.pathname }), { status: 500, headers: { "Content-Type": "application/json" } });
};

const server = await createServer({
  root,
  configFile: path.join(root, "vite.config.ts"),
  cacheDir: path.join(owned, "vite"),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: "custom",
});

function project(id, extra = {}) {
  return {
    id,
    name: extra.name || id,
    primary_path: extra.primary_path === undefined ? `/tmp/${id}` : extra.primary_path,
    archived: extra.archived ?? false,
    created_at: 1,
    ...extra,
  };
}

try {
  const model = await server.ssrLoadModule("/src/react/lib/project-model.ts");
  const send = await server.ssrLoadModule("/src/react/lib/project-send.ts");
  const projects = await server.ssrLoadModule("/src/react/lib/projects.ts");
  const logic = await server.ssrLoadModule("/src/react/shell/session-logic.ts");
  const sidecar = await server.ssrLoadModule("/src/react/lib/sidecar.ts");

  await test("directory is required and relative or tilde paths are rejected before any request", () => {
    assert.equal(model.projectPathError(""), "项目必须绑定绝对目录");
    assert.match(model.projectPathError("work"), /相对路径/);
    assert.match(model.projectPathError("~/work"), /~/);
    assert.match(model.projectPathError("/tmp/../work"), /\.\./);
    assert.equal(model.projectPathError("/tmp/work"), "");
    const existing = model.createProjectBody(" 工作 ", " /tmp/work ", false);
    assert.deepEqual(existing, { name: "工作", primary_path: "/tmp/work", create_directory: false });
    assert.equal(model.createProjectBody("工作", "/tmp/new", true).create_directory, true);
  });

  await test("archive copy does not imply physical deletion and the patch is archived true", () => {
    const text = model.archiveConfirmText("工作");
    assert.match(text, /不会删除会话、项目记忆或工作目录/);
    assert.doesNotMatch(text, /物理删除|移出文件夹|确认删除|删除全部/);
    assert.deepEqual(model.archivePatchBody(), { archived: true });
    assert.deepEqual(model.restorePatchBody(), { archived: false });
  });

  await test("legacy folders are recorded without a guessed directory and are not deleted", () => {
    const needs = model.legacyMappingNeeds([
      { id: "old-1", name: "没有目录", primary_path: "/tmp/guess" },
      { id: "old-1", name: "重复" },
      { id: "  ", name: "空白" },
    ]);
    assert.deepEqual(needs, [{ folder_id: "old-1", name: "没有目录", primary_path: null, status: "needs_explicit_path" }]);
    const stored = new Map();
    const storage = {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
    };
    stored.set(model.LEGACY_MAPPING_KEY, JSON.stringify([{ folder_id: "kept", name: "仍在", primary_path: "/tmp/nope" }]));
    const remembered = model.rememberLegacyMappingNeeds(needs, storage);
    assert.equal(remembered.find((item) => item.folder_id === "kept").primary_path, null);
    assert.equal(remembered.find((item) => item.folder_id === "old-1").primary_path, null);
    assert.equal(remembered.length, 2);
  });

  await test("project sessions keep ids and stay visible when the loaded page is filtered", () => {
    const assigned = [
      model.projectSessionToSummary({ session_id: "off", title: "页外", created_at: 10, last_activity_at: 20, message_count: 3 }),
      model.projectSessionToSummary({ session_id: "cron", title: "定时", created_at: 11, last_activity_at: 21, message_count: 1 }),
    ];
    assert.equal(assigned[0].id, "off");
    assert.equal(assigned[0].pinned, false);
    const loaded = [{
      id: "off", source: "tui", title: "页外已改", model: null, started_at: 10, last_active: 20,
      message_count: 3, preview: "loaded", pinned: true, archived: false,
    }];
    const assignments = { off: "p1", cron: "p1" };
    const items = logic.folderSessionItems(loaded, assigned, "p1", assignments, "", new Set());
    assert.deepEqual(items.map((item) => item.id), ["off", "cron"]);
    assert.equal(items[0].title, "页外已改");
    assert.equal(items[0].pinned, true);
    assert.equal(items.length, 2);
    const liveOnly = loaded.filter((item) => item.source !== "cron");
    const still = logic.folderSessionItems(liveOnly, assigned, "p1", assignments, "", new Set());
    assert.deepEqual(still.map((item) => item.id), ["off", "cron"]);
  });

  await test("project index uses panel-api, keeps a failed membership, and does not fall back to sidecar", async () => {
    calls.length = 0;
    const seen = [];
    globalThis.fetch = async (input, init) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      seen.push({ path: url.pathname, search: url.search, method: (init?.method || "GET").toUpperCase(), body: init?.body ?? null });
      if (url.pathname.includes("panel-core")) {
        return new Response(JSON.stringify({ folders: [{ id: "old", name: "旧夹" }] }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.pathname.endsWith("/api/projects") && (init?.method || "GET") === "GET") {
        return new Response(JSON.stringify({
          projects: [project("p-ok", { name: "好" }), project("p-bad", { name: "坏" })],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.pathname.endsWith("/p-ok/sessions")) {
        return new Response(JSON.stringify({
          project_id: "p-ok",
          sessions: [{ session_id: "s1", title: "一", created_at: 1, last_activity_at: 2, message_count: 4 }],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.pathname.endsWith("/p-bad/sessions")) {
        return new Response(JSON.stringify({ error: "boom", message: "boom" }), { status: 500, headers: { "Content-Type": "application/json" } });
      }
      return new Response("no", { status: 404 });
    };
    const index = await projects.loadProjectIndex();
    assert.equal(index.projects.length, 2);
    assert.equal(index.assignments.s1, "p-ok");
    assert.equal(index.sessions.find((item) => item.id === "s1").id, "s1");
    assert.ok(index.sessionErrors["p-bad"]);
    assert.equal(index.sessions.some((item) => index.assignments[item.id] === "p-bad"), false);
    assert.ok(seen.every((item) => item.path.includes("/panel-api/api/projects")));
    assert.equal(seen.some((item) => item.path.includes("panel-core")), false);
    assert.match(seen[0].search, /include_archived=true/);

    globalThis.fetch = async () => new Response(JSON.stringify({ error: "down" }), { status: 503, headers: { "Content-Type": "application/json" } });
    await assert.rejects(() => projects.loadProjectIndex(), /down|503|不可用|unavailable/i);
    globalThis.fetch = async () => new Response(JSON.stringify({ projects: null }), { status: 200, headers: { "Content-Type": "application/json" } });
    await assert.rejects(() => projects.loadProjectIndex(), /格式异常/);
  });

  await test("create and archive requests stay on the project API and do not write the sidecar", async () => {
    const seen = [];
    globalThis.fetch = async (input, init) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      const method = (init?.method || "GET").toUpperCase();
      seen.push({ path: url.pathname, method, body: init?.body ? JSON.parse(init.body) : null });
      if (method === "POST" && url.pathname.endsWith("/api/projects")) {
        return new Response(JSON.stringify({ project: project("p-new", { name: "新", primary_path: "/tmp/new" }) }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      if (method === "PATCH") {
        return new Response(JSON.stringify({ project: project("p-new", { name: "新", archived: true }) }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("no", { status: 404 });
    };
    await assert.rejects(() => projects.projectsApi.create(model.createProjectBody("新", "relative", false)), /相对路径/);
    assert.equal(seen.length, 0);
    await projects.projectsApi.create(model.createProjectBody("新", "/tmp/new", false));
    await projects.projectsApi.patch("p-new", model.archivePatchBody());
    assert.deepEqual(seen[0].body, { name: "新", primary_path: "/tmp/new", create_directory: false });
    assert.equal(seen[0].path.includes("/panel-api/api/projects"), true);
    assert.deepEqual(seen[1].body, { archived: true });
    assert.equal(seen[1].method, "PATCH");
    assert.equal(seen.some((item) => item.path.includes("panel-core")), false);
    await assert.rejects(() => sidecar.sidecar.updateSessionFolders({ action: "create", name: "不许" }), /不能写入侧车/);
    assert.equal(seen.some((item) => item.path.includes("panel-core")), false);
  });

  await test("selected project must bind before a run, and bind failure is not normal mode", async () => {
    const binds = [];
    const normal = await send.bindCreatedSessionOrBlock({
      sessionId: "s-normal",
      projectId: null,
      bind: async () => { binds.push("normal"); return { success: true, session_id: "s-normal", project_id: null }; },
    });
    assert.deepEqual(normal, { mode: "normal", projectId: null });
    assert.equal(binds.length, 0);

    const bound = await send.bindCreatedSessionOrBlock({
      sessionId: "s1",
      projectId: "p1",
      bind: async (sessionId, projectId) => {
        binds.push({ sessionId, projectId });
        return { success: true, session_id: sessionId, project_id: projectId };
      },
    });
    assert.deepEqual(bound, { mode: "project", projectId: "p1" });

    await assert.rejects(
      () => send.bindCreatedSessionOrBlock({
        sessionId: "s2",
        projectId: "p9",
        bind: async () => { throw Object.assign(new Error("session_busy"), { status: 409, name: "ApiError" }); },
      }),
      (error) => {
        assert.equal(error.name, "ProjectBindBlocked");
        assert.equal(error.projectId, "p9");
        assert.notEqual(error.mode, "normal");
        return true;
      },
    );
    await assert.rejects(
      () => send.bindCreatedSessionOrBlock({
        sessionId: "s3",
        projectId: "p1",
        bind: async () => ({ success: true, session_id: "s3", project_id: null }),
      }),
      /不会改以普通模式发送/,
    );
  });
  await test("wrong-session binding receipt is rejected", async () => {
    await assert.rejects(() => send.bindCreatedSessionOrBlock({
      sessionId: "wanted", projectId: "p1",
      bind: async () => ({ success: true, session_id: "other", project_id: "p1" }),
    }), /不会改以普通模式发送/);
  });

  await test("new-session preparation binds before model lock and allows start only afterward", async () => {
    const events = [];
    await send.prepareCreatedSession({
      sessionId: "new", projectId: "p1",
      bind: async (sid, pid) => { events.push("bind"); return { success: true, session_id: sid, project_id: pid }; },
      lock: async () => { events.push("lock"); },
      remove: async () => { events.push("delete"); },
    });
    events.push("start");
    assert.deepEqual(events, ["bind", "lock", "start"]);
  });

  await test("binding and model-lock failures delete only the new session and never start", async () => {
    for (const failing of ["bind", "lock"]) {
      const events = [];
      await assert.rejects(async () => {
        await send.prepareCreatedSession({
          sessionId: "new", projectId: "p1",
          bind: async (sid, pid) => { events.push("bind"); if (failing === "bind") throw new Error("bind failed"); return { success: true, session_id: sid, project_id: pid }; },
          lock: async () => { events.push("lock"); throw new Error("lock failed"); },
          remove: async (sid) => { events.push(`delete:${sid}`); },
        });
        events.push("start");
      }, /failed/);
      assert.deepEqual(events, failing === "bind" ? ["bind", "delete:new"] : ["bind", "lock", "delete:new"]);
    }
  });

  await test("cleanup failure reports both errors without allowing start", async () => {
    await assert.rejects(() => send.prepareCreatedSession({
      sessionId: "new", projectId: "p1",
      bind: async () => { throw new Error("bind failed"); },
      lock: async () => { assert.fail("lock must not run"); },
      remove: async () => { throw new Error("delete failed"); },
    }), /bind failed.*new.*delete failed/);
  });
} finally {
  await server.close();
  await rm(owned, { recursive: true, force: true });
}
