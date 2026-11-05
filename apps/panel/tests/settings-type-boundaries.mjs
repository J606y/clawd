// 设置与分页类型边界。只准备，不运行。退出码未知。
// 不是验收通过，也不是上线许可。运行前需要能加载 TypeScript 的测试器，并先有 document.baseURI。
// 不要指向生产 home，不要读生产凭据。

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const react = path.join(root, "src/react");
const browser = new EventTarget();
browser.setTimeout = setTimeout;
browser.clearTimeout = clearTimeout;
Object.defineProperty(globalThis, "document", { configurable: true, value: { baseURI: "https://panel.example.test/" } });
Object.defineProperty(globalThis, "window", { configurable: true, value: browser });

const { readPluginConfigResult, savedNotice, setPluginConfig } = await import("../src/react/lib/admin-api.ts");
const { collectSubagentPages, SUBAGENT_SERVER_MAX } = await import("../src/react/lib/sidecar.ts");

async function source(rel) {
  return readFile(path.join(react, rel), "utf8");
}

function row(id) {
  return { id };
}

test("source: six type errors are closed without casts, bangs, or invented totals", async () => {
  const admin = await source("lib/admin-api.ts");
  const sidecar = await source("lib/sidecar.ts");
  const queries = await source("lib/queries.ts");
  const config = await source("pages/settings/config-tab.tsx");
  assert.match(admin, /request<unknown>\(`plugins\/config\?/);
  assert.match(admin, /\.then\(readPluginConfigResult\)/);
  const putAt = admin.indexOf("export function setPluginConfig");
  const skillAt = admin.indexOf("export function skillNotice");
  assert.notEqual(putAt, -1);
  assert.ok(skillAt > putAt);
  const putFn = admin.slice(putAt, skillAt);
  assert.match(putFn, /return request<unknown>\("plugins\/config", put\(\{ name, fields \}\), 90000, SIDECAR\)\.then\(readPluginConfigResult\);/);
  assert.match(putFn, /不是仅 ack/);
  assert.equal(putFn.includes("request<PluginConfigResult>"), false);
  assert.equal(putFn.includes(".then(withoutSecrets)"), false);
  assert.equal(putFn.includes("getPluginConfig"), false);
  assert.equal(putFn.includes("plugins/config?"), false);
  assert.equal(putFn.includes(" as "), false);
  assert.equal(putFn.includes("!"), false);
  assert.equal(/\bas any\b/.test(putFn), false);
  assert.equal(admin.includes(".then(withoutSecrets)"), true);
  assert.equal(admin.includes("as PluginConfigResult"), false);
  assert.equal(admin.includes("as unknown"), false);
  assert.equal(admin.includes("@ts-ignore"), false);
  assert.equal(admin.includes("@ts-expect-error"), false);
  assert.match(admin, /export type AdvancedRead = EffectNoticeFacts & \{/);
  assert.match(admin, /value: PluginPublicValue;/);
  assert.match(sidecar, /asserts view is SubagentsView & \{ total: number \}/);
  assert.match(sidecar, /if \(total === null\) throw subagentUnavailable\(\)/);
  assert.equal(sidecar.includes("page.total!"), false);
  assert.equal(sidecar.includes("total!"), false);
  assert.equal(sidecar.includes("?? 0"), false);
  assert.match(sidecar, /不能证明收齐时抛错，不返回半份，也不把失败变成空数组/);
  assert.match(sidecar, /profile 不能在每一页上证明为同一个值时省略字段，不写 default/);
  assert.match(queries, /usePluginConfig\(name: string, enabled = true\): UseQueryResult<PluginConfigResult>/);
  assert.match(queries, /useAdvanced\(key: string, enabled = true\): UseQueryResult<AdvancedRead>/);
  assert.match(queries, /sidecar\.collectSubagents\(sessionId, limit, signal\)/);
  assert.equal(config.includes("interface AdvancedView extends WriteFacts"), false);
  assert.equal(config.includes("AdvancedView"), false);
  assert.match(config, /function advancedNotice\(result: AdvancedRead\)/);
  assert.match(config, /savedNotice\(result\)/);
  assert.match(config, /值不回显。\*\*\*、空字符串或删掉键都会保留原值/);
  assert.match(config, /保存不等于本进程已生效/);
  assert.match(config, /保存只写本进程配置文件，applied 保持 false/);
  assert.match(admin, /已保存。applied 为 false，本进程未生效/);
  assert.equal(admin.includes("存储完成改为当前生效"), false);
});

test("plugin config guard rejects unknown shapes and does not invent an empty field list", async () => {
  await assert.rejects(() => readPluginConfigResult(undefined), (error) => error.code === "plugin_config_invalid" && /不是对象/.test(error.message));
  await assert.rejects(() => readPluginConfigResult(null), (error) => error.code === "plugin_config_invalid");
  await assert.rejects(() => readPluginConfigResult(["not-an-object"]), (error) => error.code === "plugin_config_invalid");
  await assert.rejects(() => readPluginConfigResult({}), (error) => {
    return error.code === "plugin_config_invalid" && /缺少 name/.test(error.message) && !Array.isArray(error.fields);
  });
  await assert.rejects(() => readPluginConfigResult({ name: "demo", key: "demo" }), (error) => {
    return error.code === "plugin_config_invalid"
      && /不能当成空列表/.test(error.message)
      && !Array.isArray(error.fields);
  });
  await assert.rejects(() => readPluginConfigResult({
    name: "demo",
    key: "demo",
    fields: {},
  }), (error) => error.code === "plugin_config_invalid" && /不是数组/.test(error.message));
  await assert.rejects(() => readPluginConfigResult({
    name: "demo",
    key: "demo",
    fields: [{ key: "token", type: "secret", secret: true, valueSet: true, value: "plain-secret" }],
  }), (error) => error.code === "plugin_config_invalid" && /明文/.test(error.message));
  for (const applied of ["true", 1, null]) {
    await assert.rejects(() => readPluginConfigResult({
      name: "demo",
      key: "demo",
      fields: [],
      applied,
    }), (error) => error.code === "plugin_config_invalid" && /布尔/.test(error.message));
  }
});

test("plugin config guard keeps real booleans, strips top-level secrets, and does not force effect true", async () => {
  const read = readPluginConfigResult({
    name: "demo",
    key: "demo",
    apiKey: "must-not-return",
    token: "must-not-return",
    fields: [
      { key: "token", type: "secret", secret: true, valueSet: false, env: "DEMO_TOKEN" },
      { key: "mode", type: "string", secret: false, value: "plain", stored: true, choices: ["plain"] },
    ],
    saved: false,
    applied: false,
    effectiveNow: false,
    note: "kept",
  });
  assert.equal(read.name, "demo");
  assert.equal(read.fields[1].value, "plain");
  assert.equal(read.fields[0].valueSet, false);
  assert.equal("value" in read.fields[0], false);
  assert.equal(read.apiKey, undefined);
  assert.equal(read.token, undefined);
  assert.equal(read.saved, false);
  assert.equal(read.applied, false);
  assert.equal(read.effectiveNow, false);
  assert.equal(read.hotLoaded, undefined);
  assert.equal(savedNotice(read), "服务端没有确认已保存，不能当成已生效。");

  const stored = readPluginConfigResult({
    name: "demo",
    key: "demo",
    fields: [],
    saved: true,
    applied: false,
  });
  assert.equal(savedNotice(stored), "已保存。applied 为 false，本进程未生效。");
  assert.equal(savedNotice(stored).includes("当前生效"), false);

  const omitted = readPluginConfigResult({ name: "demo", key: "demo", fields: [] });
  assert.equal("applied" in omitted, false);
  assert.equal("saved" in omitted, false);
  assert.equal(savedNotice(omitted).includes("不能当成已生效"), true);
});

test("missing subagent total is an error, and a missing offset uses the requested offset", async () => {
  await assert.rejects(collectSubagentPages(async () => ({
    data: [row("a")],
    running: 0,
    now: 1,
  }), 1), (error) => error.code === "subagents_unavailable" && !Array.isArray(error.data) && error.data !== "");

  const omittedOffset = await collectSubagentPages(async (offset, limit) => ({
    data: [row("only")],
    running: 0,
    total: 1,
    now: 4,
    limit,
    ...(offset === 0 ? {} : { offset }),
  }), 50);
  assert.equal(omittedOffset.total, 1);
  assert.equal(omittedOffset.offset, 0);
  assert.equal(omittedOffset.data.length, 1);
  assert.equal("profileKey" in omittedOffset, false);
  assert.equal(omittedOffset.profileKey, undefined);

  await assert.rejects(collectSubagentPages(async (offset, limit) => ({
    data: [row("a")],
    running: 0,
    total: 1,
    now: 1,
    limit,
    offset: offset + 1,
  }), 1), (error) => error.code === "subagents_unavailable");
});

test("collector still includes the 51st child, and a mid-walk failure is not an empty list", async () => {
  assert.equal(SUBAGENT_SERVER_MAX, 50);
  const calls = [];
  const view = await collectSubagentPages(async (offset, limit) => {
    calls.push([offset, limit]);
    const ids = Array.from({ length: Math.min(limit, 51 - offset) }, (_, index) => row(`c-${offset + index}`));
    return { data: ids, running: 0, total: 51, now: 9, limit, offset };
  }, 50);
  assert.deepEqual(calls, [[0, 50], [50, 50]]);
  assert.equal(view.data.length, 51);
  assert.equal(view.data[50].id, "c-50");
  assert.equal(view.offset, 0);
  assert.equal(view.total, 51);

  await assert.rejects(collectSubagentPages(async (offset, limit) => {
    if (offset === 0) return { data: [row("kept")], running: 0, total: 2, now: 1, limit, offset };
    throw new Error("page-2-down");
  }, 1), (error) => error.message === "page-2-down" && !Array.isArray(error.data));
});

const PUT_URL = "https://panel.example.test/panel-sidecar/plugins/config";

function putSchema(extra = {}) {
  return {
    ok: true,
    saved: true,
    name: "demo",
    key: "image_gen/fal",
    written: ["mode"],
    fields: [
      { key: "token", type: "secret", secret: true, label: "Token", description: "", required: false, valueSet: true, env: "DEMO_TOKEN" },
      { key: "mode", type: "string", secret: false, label: "Mode", description: "", required: false, stored: true, value: "plain", choices: ["plain"] },
    ],
    applied: false,
    processEnvChanged: false,
    hotLoaded: false,
    effectiveNow: false,
    loadedNow: false,
    restartRequired: false,
    processSwitched: false,
    note: "已写入当前进程配置档的 plugins.entries.image_gen/fal.settings，并回读了非秘密字段。没有在本进程加载或重载插件。",
    ...extra,
  };
}

test("setPluginConfig validates the PUT body and does not re-GET or invent fields", async () => {
  const calls = [];
  let payload = "";
  const previous = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init && init.method, body: init && init.body });
    return { ok: true, status: 200, statusText: "OK", text: async () => payload };
  };
  try {
    const rejects = [
      ["", /不是对象/],
      ["{}", /缺少 name/],
      [JSON.stringify({ ok: true, saved: true, loadedNow: false }), /缺少 name/],
      [JSON.stringify({ name: "demo", key: "demo", saved: true, ok: true }), /不能当成空列表/],
      [JSON.stringify({ name: "demo", key: "demo", fields: {}, saved: true }), /不是数组/],
      [JSON.stringify({
        name: "demo",
        key: "demo",
        saved: true,
        ok: true,
        loadedNow: false,
        fields: [{ key: "token", type: "secret", secret: true, valueSet: true, value: "sk-live-secret" }],
      }), /明文/],
      [JSON.stringify({
        name: "demo",
        key: "demo",
        fields: [],
        saved: true,
        applied: "false",
        loadedNow: false,
      }), /布尔/],
    ];
    for (const [body, pattern] of rejects) {
      payload = body;
      const before = calls.length;
      await assert.rejects(() => setPluginConfig("demo", { mode: "plain" }), (error) => {
        return error.code === "plugin_config_invalid" && pattern.test(error.message) && !Array.isArray(error.fields);
      });
      assert.equal(calls.length, before + 1);
      const sent = calls[calls.length - 1];
      assert.equal(sent.method, "PUT");
      assert.equal(sent.url, PUT_URL);
      assert.equal(new URL(sent.url).search, "");
      assert.deepEqual(JSON.parse(sent.body), { name: "demo", fields: { mode: "plain" } });
    }

    payload = JSON.stringify(putSchema({ apiKey: "must-not-return", token: "must-not-return" }));
    const before = calls.length;
    const result = await setPluginConfig("demo", { mode: "plain" });
    assert.equal(calls.length, before + 1);
    assert.equal(calls[calls.length - 1].method, "PUT");
    assert.equal(calls[calls.length - 1].url, PUT_URL);
    assert.equal(result.name, "demo");
    assert.equal(result.key, "image_gen/fal");
    assert.deepEqual(result.written, ["mode"]);
    assert.equal(result.saved, true);
    assert.equal(result.ok, true);
    assert.equal(result.applied, false);
    assert.equal(result.loadedNow, false);
    assert.equal(result.effectiveNow, false);
    assert.equal(result.hotLoaded, false);
    assert.equal(result.processEnvChanged, false);
    assert.equal(result.restartRequired, false);
    assert.equal(result.processSwitched, false);
    assert.equal(result.fields.length, 2);
    assert.equal(result.fields[0].valueSet, true);
    assert.equal("value" in result.fields[0], false);
    assert.equal(result.fields[0].env, "DEMO_TOKEN");
    assert.equal(result.fields[1].value, "plain");
    assert.deepEqual(result.fields[1].choices, ["plain"]);
    assert.equal(result.apiKey, undefined);
    assert.equal(result.token, undefined);
    assert.equal(result.note.includes("没有在本进程加载"), true);
    assert.equal(result.note.includes("当前生效"), false);
    assert.equal(savedNotice(result), result.note);
    const withoutNote = { ...result };
    delete withoutNote.note;
    delete withoutNote.output;
    assert.equal(savedNotice(withoutNote), "已保存。applied 为 false，本进程未生效。");
    assert.equal(savedNotice(withoutNote).includes("当前生效"), false);
  } finally {
    globalThis.fetch = previous;
  }
});
