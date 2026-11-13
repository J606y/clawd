// 管理写操作专用。走既有 request。秘密只随请求写入，响应类型不回填明文。
// 删除确认只打 delete-preview / delete-confirm，不把只带 name 的 profiles/delete 当成确认。

import { ApiError, request } from "./api";
import type { McpServer } from "./sidecar.js";

const SIDECAR = new URL("panel-core/", document.baseURI);

// GET /models 的 ProviderInfo.hasKey 写在 sidecar.ts，类型是 boolean | null。
// 这里再合并一次同型字段，避免两份声明打架。旧源码锁是 hasKey?: boolean | null：
// true 已设置，false 未设置，null 读不到。缺字段时页面仍显示状态未知，不收成 false，也不是密钥。
declare module "@/lib/sidecar" {
  interface ProviderInfo {
    hasKey: boolean | null;
  }
}

function post(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

function put(body: unknown): RequestInit {
  return { method: "PUT", body: JSON.stringify(body) };
}

function withoutSecrets<T extends object>(value: T): T {
  const copy = { ...value } as Record<string, unknown>;
  for (const key of ["apiKey", "api_key", "token", "password", "secret", "env", "headers"]) {
    delete copy[key];
  }
  return copy as T;
}

export interface WriteFacts {
  ok?: boolean;
  saved?: boolean;
  applied?: boolean;
  restartRequired?: boolean;
  processEnvChanged?: boolean;
  hotLoaded?: boolean;
  output?: string;
  note?: string;
  key?: string;
  loadedNow?: boolean;
  effectiveNow?: boolean;
  processSwitched?: boolean;
  runningProfile?: string;
  stickyProfile?: string;
  deleted?: boolean;
  name?: string;
}

/** 提示只读生效布尔与说明。不读 stickyProfile：开放对象里它可为 null，不能并进这里的 string。 */
export type EffectNoticeFacts = Omit<WriteFacts, "stickyProfile">;

export interface ConfigWriteResult extends WriteFacts {
  ok: boolean;
  key: string;
  value: string | null;
  output: string;
}

export interface DeletePreview {
  ok: boolean;
  deleted: boolean;
  name: string;
  remove: string[];
  rewrite: string[];
  create: string[];
  paths: string[];
  token: string | null;
  fingerprint: string | null;
  expiresInS: number;
  confirmable: boolean;
  gatewayRunning?: boolean;
  note?: string;
}

export interface DeleteConfirmResult extends WriteFacts {
  ok: boolean;
  deleted: boolean;
  name: string;
  note?: string;
}

export function setSetting(key: string, value: unknown): Promise<WriteFacts> {
  return request<WriteFacts>("settings/set", post({ key, value }), 90000, SIDECAR);
}

export function resetSetting(key: string): Promise<WriteFacts> {
  return request<WriteFacts>("settings/reset", post({ key }), 90000, SIDECAR);
}

export function setConfig(key: string, value: string): Promise<ConfigWriteResult> {
  return request<ConfigWriteResult>("config/set", post({ key, value }), 60000, SIDECAR);
}

export function setPluginEnabled(name: string, enabled: boolean): Promise<WriteFacts> {
  return request<WriteFacts>("plugins/enabled", post({ name, enabled }), 120000, SIDECAR);
}

export function postProfileUse(name: string): Promise<WriteFacts> {
  return request<WriteFacts>("profiles/use", post({ name }), 60000, SIDECAR);
}

export function previewDeleteProfile(name: string): Promise<DeletePreview> {
  return request<DeletePreview>("profiles/delete-preview", post({ name }), 90000, SIDECAR);
}

export function confirmDeleteProfile(body: {
  name: string;
  confirm: string;
  paths: string[];
  token: string;
  fingerprint: string;
}): Promise<DeleteConfirmResult> {
  return request<DeleteConfirmResult>("profiles/delete-confirm", post(body), 90000, SIDECAR);
}

export function savedNotice(result: EffectNoticeFacts): string {
  if (result.applied === true || result.processEnvChanged === true || result.hotLoaded === true) {
    return result.output || result.note || "服务端标记为已在本进程生效。";
  }
  if (result.saved === false || result.ok === false) {
    return "服务端没有确认已保存，不能当成已生效。";
  }
  if (result.restartRequired === true && result.applied === false) {
    return result.output || result.note || "已保存。applied 为 false，待重启后才生效。";
  }
  if (result.applied === false) {
    return result.output || result.note || "已保存。applied 为 false，本进程未生效。";
  }
  return "服务端没有返回 applied 事实，不能当成已生效。";
}

export function pluginNotice(result: WriteFacts, name: string, enabled: boolean): string {
  if (result.loadedNow === true || result.effectiveNow === true || result.applied === true) {
    return "服务端报告插件已在本进程生效，与面板约定不符，请刷新确认。";
  }
  if (result.restartRequired === true && result.loadedNow === false && result.effectiveNow === false) {
    return result.note || `插件「${name}」只改了启用名单。尚未加载，待重启后才生效。`;
  }
  return `插件「${name}」的切换没有返回待重启事实，不能当成已经${enabled ? "启用" : "停用"}并生效。`;
}

export function profileUseNotice(result: WriteFacts, name: string): string {
  if (result.applied === true || result.processSwitched === true) {
    return "服务端报告进程配置档已切换，这不是面板允许的结果，请刷新确认。";
  }
  if (result.applied === false && result.processSwitched === false) {
    return result.note || `只写入了 sticky 文件，没有把「${name}」切换为本进程当前配置档。`;
  }
  return "服务端没有返回 applied: false，不能把这次操作当成当前配置档已切换。";
}

export function profileDeleteNotice(result: DeleteConfirmResult): string {
  if (result.deleted !== true || result.processSwitched === true || result.applied === true) {
    return "服务端没有按预览确认删除，或报告了不允许的进程切换。";
  }
  return result.note || "已删除预览列出的路径。本进程环境未改，当前配置档没有因此切换。";
}

export function previewReady(result: DeletePreview): boolean {
  return result.deleted !== true
    && result.ok === false
    && result.confirmable === true
    && typeof result.token === "string"
    && result.token.length > 0
    && typeof result.fingerprint === "string"
    && result.fingerprint.length === 64
    && Array.isArray(result.paths)
    && result.paths.length > 0
    && result.paths.every((item) => typeof item === "string");
}

export interface SkillEnabledResult extends WriteFacts {
  name: string;
  enabled: boolean;
}

export interface McpCreateInput {
  name: string;
  transport: string;
  command?: string;
  args?: string[];
  url?: string;
  enabled?: boolean;
  /** 只随请求写入，响应里会被丢掉，不能回填。 */
  env?: Record<string, string>;
}

export interface CustomEndpointInput {
  id: string;
  baseUrl: string;
  apiKey?: string;
  models: string[];
}

export interface CustomEndpointResult extends WriteFacts {
  /** 本次是否写入了密钥槽。创建响应是布尔，没有 null，也不是密钥。 */
  hasKey: boolean;
  id?: string;
}

/** 非秘密配置值。秘密行不放 value。 */
export type PluginPublicValue =
  | string
  | number
  | boolean
  | null
  | PluginPublicValue[]
  | { [key: string]: PluginPublicValue };

interface PluginConfigFieldBase {
  key: string;
  label?: string;
  description?: string;
  required?: boolean;
  env?: string;
}

/** 秘密行没有 value。valueSet 只报是否已设置。 */
export interface PluginConfigSecretField extends PluginConfigFieldBase {
  type: "secret";
  secret: true;
  valueSet: boolean;
}

export interface PluginConfigPublicField extends PluginConfigFieldBase {
  type: string;
  secret?: false;
  valueWithheld?: boolean;
  stored?: boolean;
  value?: PluginPublicValue;
  default?: PluginPublicValue;
  choices?: string[];
}

export type PluginConfigField = PluginConfigSecretField | PluginConfigPublicField;

/** GET 与保存回读的 fields 是字段数组。提交体的 fields 仍是改动对象，不是这个数组。 */
export interface PluginConfigResult extends WriteFacts {
  name?: string;
  key?: string;
  fields?: PluginConfigField[];
  managed?: boolean;
  written?: string[];
}

interface MemoryProviderFieldBase {
  key: string;
  label: string;
  description: string;
  placeholder?: string;
  options?: { value: string; label: string }[];
}

export interface MemoryProviderSecretField extends MemoryProviderFieldBase {
  kind: "secret";
  secret: true;
  valueSet: boolean;
}

export interface MemoryProviderPublicField extends MemoryProviderFieldBase {
  kind: string;
  secret: false;
  valueSet: boolean;
  value?: string;
}

export type MemoryProviderField = MemoryProviderSecretField | MemoryProviderPublicField;

export interface MemoryProviderInfo {
  name: string;
  active: boolean;
  builtin: boolean;
  missing: boolean;
  writable: boolean;
  gap: string | null;
  fields: MemoryProviderField[];
}

export interface MemoryProvidersResult {
  data: MemoryProviderInfo[];
  active: string | null;
  activeOmitted: boolean;
  applied: boolean;
  effectiveNow: boolean;
  note?: string;
}

export interface ConnectionTokenStatus {
  key: string;
  required: boolean;
  /** 只报是否已设置，没有令牌明文。 */
  valueSet: boolean;
}

export interface ConnectionPlatformInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  enabledExplicit: boolean | null;
  tokens: ConnectionTokenStatus[];
}

export interface ConnectionPlatformsResult {
  data: ConnectionPlatformInfo[];
  applied: boolean;
  effectiveNow: boolean;
  note?: string;
}

export interface ToolsetInfo {
  name: string;
  enabled: boolean;
  description: string;
  platform: string;
  configOnly: boolean;
}

export interface ToolsetsResult {
  data: ToolsetInfo[];
  applied: boolean;
  effectiveNow: boolean;
  note?: string;
}

/** 旧的窄别名。工具集和连接开关已改用各自的回读类型，不再用它掩盖缺字段。 */
export interface NamedEnabled extends WriteFacts {
  name?: string;
  id?: string;
  enabled?: boolean;
}

export interface ToolsetEnabledResult extends WriteFacts {
  name: string;
  enabled: boolean;
  platform: string;
  configOnly: boolean;
}

export interface ConnectionEnabledResult extends WriteFacts {
  id: string;
  enabled: boolean;
}

export interface MemoryProviderConfigResult extends WriteFacts {
  name: string;
  /** 只回读本次提交的字段。秘密行只有 valueSet，没有 value。 */
  fields: MemoryProviderField[];
}

export interface McpCreateResult extends WriteFacts {
  name?: string;
  /** 回读没有 env / headers 明文。 */
  server?: McpServer;
}

/** 开放对象回读。value 是掩码 JSON，不是 WriteFacts 的字符串。stickyProfile 可为 null，不能与 WriteFacts 的 string 相交掉。生效字段保持布尔，不收成 true。 */
export type AdvancedRead = EffectNoticeFacts & {
  key?: string;
  plan?: "advanced-object" | "masked-advanced";
  value: PluginPublicValue;
  userSet?: boolean;
  secretLeaves?: string[];
  profileHome?: string;
  configPath?: string;
  profileSource?: string;
  stickyProfile?: string | null;
  stickyKnown?: boolean | null;
  stickyNote?: string;
};

export function setSkillEnabled(name: string, enabled: boolean): Promise<SkillEnabledResult> {
  return request<SkillEnabledResult>("skills/enabled", post({ name, enabled }), 90000, SIDECAR);
}

export function createMcp(input: McpCreateInput): Promise<McpCreateResult> {
  return request<McpCreateResult>("mcp/servers", post(input), 90000, SIDECAR).then(withoutSecrets);
}

export function createCustomEndpoint(input: CustomEndpointInput): Promise<CustomEndpointResult> {
  return request<CustomEndpointResult>("models/custom-endpoints", post(input), 90000, SIDECAR).then(withoutSecrets);
}

export function getAdvanced(key: string): Promise<AdvancedRead> {
  return request<AdvancedRead>(`settings/advanced?${new URLSearchParams({ key })}`, {}, 90000, SIDECAR);
}

export function setAdvanced(key: string, value: unknown): Promise<AdvancedRead> {
  return request<AdvancedRead>("settings/advanced", post({ key, value }), 90000, SIDECAR).then(withoutSecrets);
}

export function listToolsets(): Promise<ToolsetsResult> {
  return request<ToolsetsResult>("tools/toolsets", {}, 90000, SIDECAR);
}

export function setToolsetEnabled(name: string, enabled: boolean): Promise<ToolsetEnabledResult> {
  return request<ToolsetEnabledResult>("tools/toolsets/enabled", post({ name, enabled }), 90000, SIDECAR);
}

export function listMemoryProviders(): Promise<MemoryProvidersResult> {
  return request("memory/providers", {}, 90000, SIDECAR);
}

export function setMemoryProviderConfig(name: string, fields: Record<string, unknown>): Promise<MemoryProviderConfigResult> {
  return request<MemoryProviderConfigResult>("memory/providers/config", put({ name, fields }), 90000, SIDECAR).then(withoutSecrets);
}

export function listConnectionPlatforms(): Promise<ConnectionPlatformsResult> {
  return request("connections/platforms", {}, 90000, SIDECAR);
}

export function setConnectionPlatformEnabled(id: string, enabled: boolean): Promise<ConnectionEnabledResult> {
  return request<ConnectionEnabledResult>("connections/platforms/enabled", post({ id, enabled }), 90000, SIDECAR).then(withoutSecrets);
}

function pluginConfigInvalid(message: string): ApiError {
  return new ApiError("server", 0, message, "plugin_config_invalid");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPluginPublicValue(value: unknown): value is PluginPublicValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isPluginPublicValue);
  if (isRecord(value)) return Object.values(value).every(isPluginPublicValue);
  return false;
}

function optionalBoolean(row: Record<string, unknown>, key: string): boolean | undefined {
  if (!Object.prototype.hasOwnProperty.call(row, key)) return undefined;
  const value = row[key];
  if (typeof value !== "boolean") throw pluginConfigInvalid(`${key} 必须是布尔，不能把缺省或其它类型收成 true`);
  return value;
}

function optionalString(row: Record<string, unknown>, key: string): string | undefined {
  if (!Object.prototype.hasOwnProperty.call(row, key)) return undefined;
  const value = row[key];
  if (typeof value !== "string") throw pluginConfigInvalid(`${key} 必须是字符串`);
  return value;
}

function readPluginConfigField(value: unknown): PluginConfigField {
  if (!isRecord(value)) throw pluginConfigInvalid("插件配置字段不是对象");
  if (typeof value.key !== "string" || value.key.length === 0) throw pluginConfigInvalid("插件配置字段缺少 key");
  if (typeof value.type !== "string" || value.type.length === 0) throw pluginConfigInvalid("插件配置字段缺少 type");
  const hasSecret = Object.prototype.hasOwnProperty.call(value, "secret");
  if (hasSecret && typeof value.secret !== "boolean") throw pluginConfigInvalid("插件配置字段 secret 必须是布尔");
  const isSecret = (hasSecret && value.secret === true) || value.type === "secret";
  if (isSecret && (value.type !== "secret" || (hasSecret && value.secret !== true))) {
    throw pluginConfigInvalid("插件配置秘密字段标记不一致");
  }
  const field: PluginConfigFieldBase = { key: value.key };
  const label = optionalString(value, "label");
  if (label !== undefined) field.label = label;
  const description = optionalString(value, "description");
  if (description !== undefined) field.description = description;
  const required = optionalBoolean(value, "required");
  if (required !== undefined) field.required = required;
  const env = optionalString(value, "env");
  if (env !== undefined) field.env = env;
  if (isSecret) {
    if (Object.prototype.hasOwnProperty.call(value, "value") || Object.prototype.hasOwnProperty.call(value, "default")) {
      throw pluginConfigInvalid("秘密字段不能带回明文");
    }
    if (!Object.prototype.hasOwnProperty.call(value, "valueSet") || typeof value.valueSet !== "boolean") {
      throw pluginConfigInvalid("秘密字段缺少布尔 valueSet");
    }
    return { ...field, type: "secret", secret: true, valueSet: value.valueSet };
  }
  const pub: PluginConfigPublicField = { ...field, type: value.type };
  if (hasSecret) pub.secret = false;
  if (Object.prototype.hasOwnProperty.call(value, "value")) {
    if (!isPluginPublicValue(value.value)) throw pluginConfigInvalid("字段 value 不是可公开的 JSON");
    pub.value = value.value;
  }
  if (Object.prototype.hasOwnProperty.call(value, "default")) {
    if (!isPluginPublicValue(value.default)) throw pluginConfigInvalid("字段 default 不是可公开的 JSON");
    pub.default = value.default;
  }
  const withheld = optionalBoolean(value, "valueWithheld");
  if (withheld !== undefined) pub.valueWithheld = withheld;
  const stored = optionalBoolean(value, "stored");
  if (stored !== undefined) pub.stored = stored;
  if (Object.prototype.hasOwnProperty.call(value, "choices")) {
    const choices = value.choices;
    if (!Array.isArray(choices) || !choices.every((item): item is string => typeof item === "string")) {
      throw pluginConfigInvalid("字段 choices 必须是字符串数组");
    }
    pub.choices = choices;
  }
  return pub;
}

const PLUGIN_EFFECT_KEYS = [
  "ok",
  "saved",
  "applied",
  "restartRequired",
  "processEnvChanged",
  "hotLoaded",
  "loadedNow",
  "effectiveNow",
  "processSwitched",
  "deleted",
] as const;

/** 未知 HTTP 先按插件配置 schema 收窄，再剥顶层秘密键。缺 fields 不编成空数组，布尔不收成 true。 */
export function readPluginConfigResult(value: unknown): PluginConfigResult {
  if (!isRecord(value)) throw pluginConfigInvalid("插件配置响应不是对象，不能当成已读取");
  if (typeof value.name !== "string" || value.name.length === 0) throw pluginConfigInvalid("插件配置缺少 name");
  if (typeof value.key !== "string" || value.key.length === 0) throw pluginConfigInvalid("插件配置缺少 key");
  if (!Object.prototype.hasOwnProperty.call(value, "fields")) {
    throw pluginConfigInvalid("插件配置缺少 fields，不能当成空列表");
  }
  if (!Array.isArray(value.fields)) throw pluginConfigInvalid("插件配置 fields 不是数组，不能当成已读取");
  const result: PluginConfigResult = {
    name: value.name,
    key: value.key,
    fields: value.fields.map(readPluginConfigField),
  };
  const note = optionalString(value, "note");
  if (note !== undefined) result.note = note;
  const output = optionalString(value, "output");
  if (output !== undefined) result.output = output;
  const runningProfile = optionalString(value, "runningProfile");
  if (runningProfile !== undefined) result.runningProfile = runningProfile;
  if (Object.prototype.hasOwnProperty.call(value, "stickyProfile")) {
    const sticky = value.stickyProfile;
    if (typeof sticky !== "string") throw pluginConfigInvalid("插件配置 stickyProfile 必须是字符串");
    result.stickyProfile = sticky;
  }
  for (const effect of PLUGIN_EFFECT_KEYS) {
    const flag = optionalBoolean(value, effect);
    if (flag !== undefined) result[effect] = flag;
  }
  const managed = optionalBoolean(value, "managed");
  if (managed !== undefined) result.managed = managed;
  if (Object.prototype.hasOwnProperty.call(value, "written")) {
    const written = value.written;
    if (!Array.isArray(written) || !written.every((item): item is string => typeof item === "string")) {
      throw pluginConfigInvalid("written 必须是字符串数组");
    }
    result.written = written;
  }
  return withoutSecrets(result);
}

export function getPluginConfig(name: string): Promise<PluginConfigResult> {
  return request<unknown>(`plugins/config?${new URLSearchParams({ name })}`, {}, 90000, SIDECAR).then(readPluginConfigResult);
}

export function setPluginConfig(name: string, fields: Record<string, unknown>): Promise<PluginConfigResult> {
  // PUT 成功体含 name、key 与 fields 数组，不是仅 ack。同一 guard 拒绝空体、缺 fields、秘密明文和错类型。不回读 GET，不编字段或布尔。
  return request<unknown>("plugins/config", put({ name, fields }), 90000, SIDECAR).then(readPluginConfigResult);
}

export function skillNotice(result: WriteFacts, name: string, enabled: boolean): string {
  if (result.applied === true || result.effectiveNow === true || result.hotLoaded === true || result.loadedNow === true) {
    return "服务端报告技能开关已在本进程生效，与回读约定不符，请刷新确认。";
  }
  if (result.saved === true && result.applied === false) {
    return result.note || `技能「${name}」已写入禁用名单。applied 为 false，当前对话未改，新对话才读取。`;
  }
  return `技能「${name}」没有返回已保存且未热加载的事实，不能当成已经${enabled ? "启用" : "停用"}。`;
}

export function toolsetNotice(result: WriteFacts, name: string, enabled: boolean): string {
  if (result.effectiveNow === true || result.applied === true || result.hotLoaded === true) {
    return "服务端报告工具集已在当前会话生效，这不是允许的结果，请刷新确认。";
  }
  if (result.saved === true && result.effectiveNow === false && result.applied === false) {
    return result.note || `工具集「${name}」已保存。effectiveNow 为 false，只对下一次会话生效。`;
  }
  return `工具集「${name}」没有返回已保存且未立即生效的事实，不能当成已经${enabled ? "启用" : "停用"}。`;
}
