// 设置客户端类型契约。只读三个源码文件，不 import 面板模块。
// 本次只准备，不运行。退出码未知。不是验收通过，也不是上线许可。

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const reactLib = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/react/lib");

async function text(name) {
  return readFile(path.join(reactLib, name), "utf8");
}

function sliceBetween(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.notEqual(from, -1, start);
  assert.notEqual(to, -1, end);
  return source.slice(from, to);
}

test("plugin config get is a field array and put submits an object", async () => {
  const admin = await text("admin-api.ts");
  const queries = await text("queries.ts");
  assert.match(admin, /fields\?: PluginConfigField\[\]/);
  assert.match(admin, /setPluginConfig\(name: string, fields: Record<string, unknown>\)/);
  assert.match(queries, /usePluginConfig\(name: string, enabled = true\): UseQueryResult<PluginConfigResult>/);
  assert.match(queries, /useSetPluginConfig\(\): UseMutationResult<PluginConfigResult, Error, \{ name: string; fields: Record<string, unknown> \}>/);
  assert.equal(queries.includes("fields?: Record<string, unknown>"), false);
  assert.equal(queries.includes("data?: unknown[]"), false);
});

test("provider hasKey is boolean or null and is not a secret", async () => {
  const sidecar = await text("sidecar.ts");
  const admin = await text("admin-api.ts");
  const provider = sliceBetween(sidecar, "export interface ProviderInfo", "export interface ModelsView");
  assert.match(provider, /hasKey: boolean \| null;/);
  assert.match(admin, /hasKey: boolean \| null;/);
  assert.match(admin, /hasKey\?: boolean \| null/);
  assert.equal(provider.includes("apiKey"), false);
  assert.equal(provider.includes("value?:"), false);
});

test("memory, connection, and plugin hooks use the real results", async () => {
  const queries = await text("queries.ts");
  assert.match(queries, /useMemoryProviders\(enabled = true\): UseQueryResult<MemoryProvidersResult>/);
  assert.match(queries, /useConnectionPlatforms\(enabled = true\): UseQueryResult<ConnectionPlatformsResult>/);
  assert.match(queries, /useSetMemoryProviderConfig\(\): UseMutationResult<MemoryProviderConfigResult/);
  assert.match(queries, /useSetConnectionPlatformEnabled\(\): UseMutationResult<ConnectionEnabledResult/);
  assert.match(queries, /useToolsets\(enabled = true\): UseQueryResult<ToolsetsResult>/);
  assert.match(queries, /useCreateCustomEndpoint\(\): UseMutationResult<CustomEndpointResult, Error, CustomEndpointInput>/);
});

test("secret rows do not declare a plaintext value", async () => {
  const admin = await text("admin-api.ts");
  const blocks = [
    sliceBetween(admin, "export interface PluginConfigSecretField", "export interface PluginConfigPublicField"),
    sliceBetween(admin, "export interface MemoryProviderSecretField", "export interface MemoryProviderPublicField"),
    sliceBetween(admin, "export interface ConnectionTokenStatus", "export interface ConnectionPlatformInfo"),
  ];
  for (const block of blocks) {
    assert.equal(/\bvalue\??:/.test(block), false);
    assert.equal(block.includes("apiKey"), false);
  }
  assert.match(admin, /valueSet: boolean/);
  const tokens = admin.split("export interface ConnectionTokenStatus", 2)[1].split("export interface ConnectionPlatformInfo", 2)[0];
  assert.equal(tokens.includes("apiKey?: string"), false);
});

test("effect facts stay boolean and are not forced true", async () => {
  const files = await Promise.all(["admin-api.ts", "queries.ts", "sidecar.ts"].map(text));
  for (const source of files) {
    assert.equal(source.includes("applied: true"), false);
    assert.equal(source.includes("effectiveNow: true"), false);
    assert.equal(source.includes("hotLoaded: true"), false);
    assert.equal(source.includes("loadedNow: true"), false);
    assert.equal(source.includes("processEnvChanged: true"), false);
  }
  const admin = files[0];
  assert.match(admin, /applied: boolean/);
  assert.match(admin, /effectiveNow: boolean/);
  assert.match(admin, /restartRequired\?: boolean/);
});

test("admin signatures keep read arrays and write objects", async () => {
  const admin = await text("admin-api.ts");
  assert.match(admin, /getPluginConfig\(name: string\): Promise<PluginConfigResult>/);
  assert.match(admin, /listMemoryProviders\(\): Promise<MemoryProvidersResult>/);
  assert.match(admin, /setMemoryProviderConfig\(name: string, fields: Record<string, unknown>\): Promise<MemoryProviderConfigResult>/);
  assert.match(admin, /listConnectionPlatforms\(\): Promise<ConnectionPlatformsResult>/);
  assert.match(admin, /setConnectionPlatformEnabled\(id: string, enabled: boolean\): Promise<ConnectionEnabledResult>/);
  assert.match(admin, /listToolsets\(\): Promise<ToolsetsResult>/);
  assert.match(admin, /setToolsetEnabled\(name: string, enabled: boolean\): Promise<ToolsetEnabledResult>/);
  assert.match(admin, /platform: string;/);
  assert.match(admin, /configOnly: boolean;/);
  assert.match(admin, /createCustomEndpoint\(input: CustomEndpointInput\): Promise<CustomEndpointResult>/);
  assert.match(admin, /hasKey: boolean;/);
  assert.match(admin, /getAdvanced\(key: string\): Promise<AdvancedRead>/);
  assert.match(admin, /setAdvanced\(key: string, value: unknown\): Promise<AdvancedRead>/);
  assert.match(admin, /value: PluginPublicValue;/);
});

test("pagination collector still keeps total, offset, profile, and returned", async () => {
  const sidecar = await text("sidecar.ts");
  const queries = await text("queries.ts");
  assert.match(queries, /queryFn: \(\{ signal \}\) => sidecar\.collectSubagents\(sessionId, limit, signal\)/);
  assert.match(queries, /refetchInterval,/);
  assert.equal(queries.includes("setInterval"), false);
  assert.match(sidecar, /params\.set\("offset", String\(offset\)\)/);
  assert.match(sidecar, /offset: 0,/);
  assert.match(sidecar, /subagents_incomplete/);
  assert.match(sidecar, /不能证明收齐时抛错，不返回半份，也不把失败变成空数组/);
  assert.match(sidecar, /不改 executionStatus 或验收 status/);
  assert.equal(sidecar.includes("executionStatus = \"completed\""), false);
  assert.equal(sidecar.includes("executionStatus: \"completed\""), false);
  assert.match(sidecar, /collectSubagents: \(sessionId = "", limit = 50, signal\?: AbortSignal\)/);
  assert.match(sidecar, /models: \(\) => request<ModelsView>/);
  assert.match(sidecar, /settings: \(\) => request<SettingsView>/);
  assert.match(sidecar, /mcp: \(\) => request/);
  assert.match(sidecar, /asserts view is SubagentsView & \{ total: number \}/);
  assert.match(sidecar, /if \(total === null\) throw subagentUnavailable\(\)/);
  assert.match(sidecar, /function pageOffsetFact/);
  assert.equal(sidecar.includes("page.total!"), false);
  assert.equal(sidecar.includes("total!"), false);
  assert.equal(sidecar.includes("?? 0"), false);
});

test("plugin config put validates the returned schema and does not treat an ack as config", async () => {
  const admin = await text("admin-api.ts");
  const putFn = sliceBetween(admin, "export function setPluginConfig", "export function skillNotice");
  const getFn = sliceBetween(admin, "export function getPluginConfig", "export function setPluginConfig");
  assert.match(putFn, /return request<unknown>\("plugins\/config", put\(\{ name, fields \}\), 90000, SIDECAR\)\.then\(readPluginConfigResult\);/);
  assert.match(putFn, /不是仅 ack/);
  assert.equal(putFn.includes("request<PluginConfigResult>"), false);
  assert.equal(putFn.includes(".then(withoutSecrets)"), false);
  assert.equal(putFn.includes("getPluginConfig"), false);
  assert.equal(putFn.includes("plugins/config?"), false);
  assert.equal(putFn.includes(" as "), false);
  assert.equal(putFn.includes("!"), false);
  assert.equal(/\bas any\b/.test(putFn), false);
  assert.match(getFn, /request<unknown>\(`plugins\/config\?/);
  assert.match(getFn, /\.then\(readPluginConfigResult\)/);
  assert.equal(getFn.includes("request<PluginConfigResult>"), false);
});

test("unknown plugin config is narrowed and advanced sticky is omitted from write facts", async () => {
  const admin = await text("admin-api.ts");
  assert.match(admin, /export function readPluginConfigResult\(value: unknown\): PluginConfigResult/);
  assert.match(admin, /request<unknown>\(`plugins\/config\?/);
  assert.match(admin, /\.then\(readPluginConfigResult\)/);
  assert.match(admin, /export type EffectNoticeFacts = Omit<WriteFacts, "stickyProfile">/);
  assert.match(admin, /export type AdvancedRead = EffectNoticeFacts & \{/);
  assert.match(admin, /stickyProfile\?: string \| null;/);
  assert.match(admin, /savedNotice\(result: EffectNoticeFacts\)/);
  assert.equal(admin.includes("as PluginConfigResult"), false);
  assert.equal(admin.includes("@ts-ignore"), false);
  assert.equal(admin.includes("@ts-expect-error"), false);
  assert.equal(admin.includes("applied: true"), false);
});
