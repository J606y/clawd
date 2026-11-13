// 面板辅助服务（HK 127.0.0.1:8651，经 nginx panel-sidecar/）：主机状态、技能、配置、服务重启。

import { ApiError, request, type SessionSummary } from "./api.js";
import type { EditBoundary } from "./message-edit.js";

const BASE = new URL("panel-sidecar/", document.baseURI);

export interface ServiceState {
  name: string;
  label: string;
  active: string;
  sub: string;
  memory: number | null;
  memoryMax: number | null;
  runningForS: number | null;
  restartable: boolean;
}

export interface HostStatus {
  host: {
    cpuPercent: number;
    cpuCount: number;
    load: number[];
    uptimeS: number;
    mem: { total: number; available: number };
    swap: { total: number; free: number };
    disk: { total: number; used: number; free: number };
  };
  services: ServiceState[];
  sampledAt: number;
}

export interface Skill {
  name: string;
  category: string;
  description: string;
  version: string;
  path: string;
  enabled: boolean;
  modifiedAt: number;
}

export interface SettingsField {
  key: string;
  type: "boolean" | "number" | "string" | "select" | "list" | "object" | string;
  category: string;
  description: string;
  options: string[] | null;
  clearable: boolean;
  searchable: boolean;
  /** 密钥类字段恒为 null（服务端不外送）。 */
  value: unknown;
  valueSet: boolean | null;
  userSet: boolean;
  /** 非空表示不能在表单里改，内容就是原因。 */
  locked: string;
}
export interface SettingsCategory { id: string; label: string; count: number }
export interface SettingsView { fields: SettingsField[]; categories: SettingsCategory[] }

export interface SkillBackup {
  id: string;
  at: number;
  size: number;
}

export interface ConfigView {
  path: string;
  yaml: string;
  modifiedAt: number;
}

export interface ConfigSetResult {
  ok: boolean;
  key: string;
  value: string | null;
  output: string;
}

export interface FileRoot {
  id: string;
  label: string;
  path: string;
  writable: boolean;
}

export interface FileEntry {
  name: string;
  dir: boolean;
  size: number | null;
  modifiedAt: number;
}

export interface DirListing {
  root: string;
  path: string;
  absPath: string;
  writable: boolean;
  entries: FileEntry[];
}

export interface UploadResult {
  root: string;
  path: string;
  absPath: string;
  size: number;
}

export interface ActivityItem {
  tool: string;
  args: Record<string, unknown>;
  sessionId: string;
  title: string | null;
  source: string | null;
  timestamp: number;
  status: "done" | "failed" | "pending";
}

export interface UsageDay { day: string; sessions: number; input: number; output: number; cacheRead: number; apiCalls: number }
export interface UsageModel { model: string; provider: string; sessions: number; input: number; output: number; apiCalls: number }
export interface UsageSource { source: string; sessions: number; input: number; output: number }
export interface UsageTop { id: string; source: string | null; model: string | null; title: string | null; startedAt: number; totalTokens: number; apiCalls: number; messages: number }
export interface UsageAux { task: string; sessions: number; apiCalls: number; input: number; output: number }
export interface Usage {
  days: number;
  totals: { sessions: number; input: number; output: number; cacheRead: number; apiCalls: number };
  daily: UsageDay[];
  byModel: UsageModel[];
  bySource: UsageSource[];
  top: UsageTop[];
  aux: UsageAux[];
}

export interface LogSource { id: string; label: string; kind: "file" | "journal"; available: boolean }
export interface LogSources { data: LogSource[]; components: string[]; levels: string[] }
export interface LogQuery { source: string; lines: number; level: string; component: string; q: string }
export interface LogResult { source: string; lines: string[]; at: number }

export interface ModelCurrent { provider?: string | null; default?: string | null; base_url?: string | null; api_mode?: string | null }
export interface ProviderInfo { id: string; name: string; baseUrl: string | null; apiMode: string | null; models: string[]; discoverModels: boolean; editable: boolean }
export interface ModelsView { current: ModelCurrent; providers: ProviderInfo[]; excludedProviders: string[] }

export interface CronJobModel { id: string; name: string; enabled: boolean; model: string | null; provider: string | null }
export interface CronModelView {
  cronModel: string | null;
  cronProvider: string | null;
  main: { provider: string | null; model: string | null };
  jobs: CronJobModel[];
}

export interface McpServer {
  name: string;
  transport: "http" | "stdio";
  command: string | null;
  args: string[];
  url: string | null;
  auth: string | null;
  enabled: boolean;
  toolAllowlist: string[] | null;
  envKeys: string[];
  headerKeys: string[];
  editable: boolean;
}

export type UpdateChannel = "stable" | "beta";

export interface UpdateInfo {
  version: string;
  releaseDate?: string;
  commit: string;
  branch?: string;
  installDir?: string;
  localChanges: string[];
  behindBy: number | null;
  status?: string;
  latestCommit?: string;
  recent?: { sha: string; title: string; date: string }[];
  compareUrl?: string;
  checkedAt?: number;
  channel?: UpdateChannel;
  latestVersion?: string;
  latestTag?: string;
  releaseUrl?: string;
  releaseNotes?: string;
  error?: string;
  stale?: boolean;
}
export interface UpdateRun {
  running: boolean;
  state: "idle" | "running" | "done" | "failed" | "interrupted";
  runId: string | null;
  exitCode?: number | null;
  oldHead?: string;
  newHead?: string;
  changed?: boolean;
  backupDir?: string;
  patchBytes?: number;
  hasUntracked?: boolean;
  logTail: string;
}
export interface PluginInfo { name: string; enabled: boolean; status: string; version: string; description: string; source: string }
export interface ProfileInfo { name: string; active: boolean; model: string | null; gateway: string | null; alias: string | null }
export interface ProfilesView { data: ProfileInfo[]; raw: string | null }

export interface WebhookInfo {
  name: string;
  description: string;
  events: string[];
  deliver: string | null;
  deliverOnly: boolean;
  path: string;
  enabled: boolean;
  secretSet: boolean;
  hasScript: boolean;
  createdAt: string | null;
  prompt: string;
}
export interface WebhooksView { platformEnabled: boolean; data: WebhookInfo[] }
export interface WebhookDelivery { time: string; event: string; route: string; promptLen: number; delivery: string }
export interface WebhookCreate { name: string; prompt: string; description?: string; deliver?: string; events?: string[] }
export interface Subagent {
  id: string;
  parentId: string | null;
  parentTitle: string | null;
  title: string | null;
  model: string | null;
  startedAt: number;
  endedAt: number | null;
  endReason: string | null;
  status: "running" | "stalled" | "done" | "ended";
  messages: number;
  toolCalls: number;
  apiCalls: number;
  tokens: number;
  lastActivityAt: number;
  lastTool: { name: string; args: Record<string, unknown> } | null;
  snippet: string;
}
export interface SubagentsView {
  data: Subagent[];
  running: number;
  now: number;
}
export interface ContextUsage {
  used: number | null;
  window: number | null;
  model?: string;
  calls?: number;
  at?: string;
  out?: number | null;
  cached?: number;
  session?: { input: number; output: number; cacheRead: number; apiCalls: number; messages: number } | null;
  source: "config" | "default" | "unknown" | "no-data";
}
/** 回合是不是在等重试（限流/超时）。retrying 时带原因、第几次、还要等多久。 */
export interface RunWait {
  state: "idle" | "retrying";
  attempt?: number;
  maxAttempts?: number;
  waitSeconds?: number;
  remainingSeconds?: number;
  model?: string;
  summary?: string;
  /** Optional evidence fields; old sidecars remain compatible but cannot prove turn ownership. */
  httpStatus?: number;
  observedAtMs?: number;
  nextRetryAtMs?: number;
  turnStartedAtMs?: number;
  available?: boolean;
  source?: "log";
}
export interface ActionResult { ok: boolean; note?: string }

export interface LiveState {
  running: boolean;
  takenOver: boolean;
  controlledByYou: boolean;
  ownerSessionId?: string;
  leaseRemainingSeconds: number;
  url?: string;
  title?: string;
}

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

/**
 * 上传：原始请求体即文件内容（sidecar 不解析 multipart）。用 XHR 以便拿到进度。
 * `timeoutMs` 为 0 表示不限时（大文件）；照片存档这类不能卡住发送的上传要给上限。
 */
export function uploadFile(file: File, root = "uploads", onProgress?: (fraction: number) => void, timeoutMs = 0): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    // 部分拖拽/粘贴来源的文件没有名字，服务端会拒绝空名。
    const q = new URLSearchParams({ root, name: file.name || (file.type.startsWith("image/") ? "图片" : "文件") });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", new URL(`files/upload?${q}`, BASE));
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // nginx 413 等非 JSON 响应
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadResult);
      else {
        const msg =
          (body as { error?: { message?: string } } | null)?.error?.message ??
          (xhr.status === 413 ? "文件太大" : `上传失败（${xhr.status}）`);
        reject(new ApiError(xhr.status === 401 ? "unauthorized" : "server", xhr.status, msg));
      }
    };
    xhr.onerror = () => reject(new ApiError("network", 0, "网络不可达"));
    xhr.timeout = timeoutMs;
    xhr.ontimeout = () => reject(new ApiError("network", 0, "上传超时"));
    xhr.send(file);
  });
}

export function downloadUrl(root: string, path: string): string {
  return new URL(`files/download?${new URLSearchParams({ root, path })}`, BASE).toString();
}

export interface ImageModel {
  id: string;
  label: string;
  provider: string;
  providerName: string;
  protocol: string;
}

export interface ImageItem {
  id: string;
  createdAt: number;
  model: string;
  provider: string;
  providerName: string;
  label: string;
  prompt: string;
  aspect: string;
  absPath: string;
  bytes: number;
  status: string;
  error: string;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

/** 会话派生、此刻仍在运行的后台进程（sidecar 读 Hermes 的进程登记并核实进程还活着）。 */
export interface BackgroundProcess {
  id: string;
  /** Hermes 已脱敏、截断到 300 字的启动命令。 */
  command: string;
  /** 启动时间（epoch 秒）；登记里没有可信时间时为 null。 */
  startedAt: number | null;
}

export interface SessionExecution {
  id: string;
  kind: "cli";
  started_at: number;
}

export interface SessionRunState {
  run: { run_id: string; session_id: string; status: string; created_at: number; updated_at: number } | null;
  busy: boolean;
  acquired_at?: number;
}

export interface SessionFolder { id: string; name: string; collapsed: boolean }
export interface SessionFolders { folders: SessionFolder[]; assignments: Record<string, string> }
export type SessionFolderAction =
  | { action: "create"; name: string }
  | { action: "rename"; id: string; name: string }
  | { action: "collapse"; id: string; collapsed: boolean }
  | { action: "delete"; id: string }
  | { action: "move"; sessionId: string; folderId: string | null };

export const sidecar = {
  activeSessions: (signal?: AbortSignal) =>
    request<{ data: SessionSummary[] }>("sessions/active", { signal }, 5000, BASE).then((r) => {
      if (!Array.isArray(r?.data)) throw new ApiError("server", 0, "活动对话查询返回格式异常");
      return r.data;
    }),
  sessionFolders: () => request<SessionFolders>("sessions/folders", {}, 10000, BASE),
  updateSessionFolders: (body: SessionFolderAction) => request<SessionFolders>("sessions/folders", post(body), 10000, BASE),
  sessionBackground: (sessionId: string, signal?: AbortSignal) =>
    request<{ processes?: unknown }>(`sessions/background?${new URLSearchParams({ session_id: sessionId })}`, { signal }, 5000, BASE)
      .then((r) => {
        // 形状不对（旧版 sidecar、代理错误页）按失败处理：调用方据此保留上次结果，而不是当成「没有」或渲染出错。
        if (!Array.isArray(r?.processes)) throw new ApiError("server", 0, "后台进程查询返回格式异常");
        return r.processes as BackgroundProcess[];
      }),
  sessionRun: (sessionId: string, signal?: AbortSignal) =>
    request<SessionRunState>(`sessions/run?${new URLSearchParams({ session_id: sessionId })}`, { signal }, 5000, BASE),
  resolveRun: (sessionId: string, idempotencyKey: string, signal?: AbortSignal) =>
    request<SessionRunState & { resolution: { idempotency_key: string; state: "found" | "not_found" } }>(
      `sessions/run/resolve?${new URLSearchParams({ session_id: sessionId, idempotency_key: idempotencyKey })}`, { signal }, 5000, BASE),
  sessionExecution: (sessionId: string, signal?: AbortSignal) =>
    request<{ busy: boolean; execution: SessionExecution | null }>(`sessions/execution?${new URLSearchParams({ session_id: sessionId })}`, { signal }, 5000, BASE),
  stopExecution: (sessionId: string, executionId: string) =>
    request<{ status: "cancelled"; busy: false }>("sessions/execution/stop", post({ session_id: sessionId, execution_id: executionId, confirm: "stop-cli" }), 10000, BASE),
  editBranch: (body: EditBoundary & { sessionId: string; requestId: string; model: string; provider: string }) =>
    request<{ session: SessionSummary }>("sessions/edit-branch", post(body), 15000, BASE).then((r) => r.session),
  status: () => request<HostStatus>("status", {}, 15000, BASE),
  restart: (name: string, force = false) => request<{ ok: boolean }>(`services/${encodeURIComponent(name)}/restart${force ? "?force=1" : ""}`, post({}), 180000, BASE),
  skills: () => request<{ data: Skill[] }>("skills", {}, 40000, BASE).then((r) => r.data),
  skill: (path: string) =>
    request<{ path: string; content: string; modifiedAt: number }>(`skills/${path.split("/").map(encodeURIComponent).join("/")}`, {}, 15000, BASE),
  saveSkill: (path: string, content: string, baseModifiedAt: number) =>
    request<{ ok: boolean; unchanged: boolean; modifiedAt: number; backup?: string }>(
      "skills/save", post({ path, content, baseModifiedAt }), 30000, BASE),
  skillBackups: (path: string) =>
    request<{ data: SkillBackup[] }>(`skill-backups?${new URLSearchParams({ path })}`, {}, 15000, BASE).then((r) => r.data),
  skillBackup: (path: string, id: string) =>
    request<{ id: string; content: string }>(`skill-backup?${new URLSearchParams({ path, id })}`, {}, 15000, BASE),
  setSkillEnabled: (name: string, enabled: boolean) => request<{ ok: boolean }>("skills/enabled", post({ name, enabled }), 90000, BASE),
  updatePlan: () => request<{ plan: string; exitCode: number }>("update/plan", {}, 100000, BASE),
  updateStatus: () => request<UpdateRun>("update/status", {}, 15000, BASE),
  updateStart: (tag?: string, channel?: UpdateChannel) =>
    request<{ ok: boolean; runId: string }>(
      "update/start",
      post({ confirm: "update", tag, channel }),
      60000,
      BASE,
    ),
  updateCheck: (force = false, channel?: UpdateChannel) => {
    const params = new URLSearchParams();
    if (force) params.set("force", "1");
    if (channel) params.set("channel", channel);
    const query = params.toString();
    return request<UpdateInfo>(`update/check${query ? `?${query}` : ""}`, {}, 30000, BASE);
  },
  settings: () => request<SettingsView>("settings", {}, 90000, BASE),
  setSetting: (key: string, value: unknown) => request<{ ok: boolean }>("settings/set", post({ key, value }), 90000, BASE),
  resetSetting: (key: string) => request<{ ok: boolean }>("settings/reset", post({ key }), 90000, BASE),
  config: () => request<ConfigView>("config", {}, 15000, BASE),
  setConfig: (key: string, value: string) => request<ConfigSetResult>("config/set", post({ key, value }), 60000, BASE),
  fileRoots: () => request<{ data: FileRoot[] }>("files/roots", {}, 15000, BASE).then((r) => r.data),
  listDir: (root: string, path: string) => request<DirListing>(`files?${new URLSearchParams({ root, path })}`, {}, 15000, BASE),
  deleteFile: (root: string, path: string) => request<{ ok: boolean }>("files/delete", post({ root, path }), 15000, BASE),
  activity: (limit = 60) => request<{ data: ActivityItem[] }>(`activity?limit=${limit}`, {}, 20000, BASE).then((r) => r.data),
  liveState: (signal?: AbortSignal, clientId?: string) =>
    request<LiveState>(clientId === undefined ? "live/state" : `live/state?${new URLSearchParams({ client_id: clientId })}`, { signal }, 8000, BASE),
  /** 面板控制器租约 / 心跳（45 秒）；不暂停原生 agent，不是全局浏览器互斥锁。 */
  liveTakeover: (clientId: string, sessionId: string) => request<{ ok: boolean; leaseSeconds: number }>("live/takeover", post({ clientId, sessionId }), 10000, BASE),
  liveRelease: (clientId: string, keepalive = false) => request<{ ok: boolean }>("live/release", { ...post({ clientId }), keepalive }, 8000, BASE),
  /** inserted=false：未确认可编辑焦点，未输入；调用方应提示重新聚焦，不能静默改走全局按键。 */
  liveType: (text: string, clientId: string) => request<{ ok: boolean; inserted: boolean }>("live/type", post({ text, clientId }), 8000, BASE),
  commentary: (ids: number[], signal?: AbortSignal) =>
    request<{ items: Record<string, string[]> }>("sessions/commentary", { ...post({ ids }), signal }, 10000, BASE),
  subagents: (sessionId = "", limit = 50) =>
    request<SubagentsView>(`subagents?${new URLSearchParams({ session: sessionId, limit: String(limit) })}`, {}, 15000, BASE),
  context: (sessionId: string) => request<ContextUsage>(`context?${new URLSearchParams({ session: sessionId })}`, {}, 15000, BASE),
  runWait: (sessionId: string, startedAt?: number, signal?: AbortSignal) => request<RunWait>(`run-wait?${new URLSearchParams({ session: sessionId, ...(startedAt ? { started_at_ms: String(startedAt) } : {}) })}`, { signal }, 8000, BASE),
  usage: (days: number) => request<Usage>(`usage?days=${days}`, {}, 20000, BASE),
  logSources: () => request<LogSources>("logs/sources", {}, 15000, BASE),
  logs: (q: LogQuery) => request<LogResult>(`logs?${new URLSearchParams({ source: q.source, lines: String(q.lines), level: q.level, component: q.component, q: q.q })}`, {}, 30000, BASE),
  models: () => request<ModelsView>("models", {}, 30000, BASE),
  setDefaultModel: (model: string, provider?: string) => request<ActionResult>("models/default", post({ model, provider: provider ?? null }), 90000, BASE),
  cronModel: () => request<CronModelView>("cron-model", {}, 30000, BASE),
  /** model 为空串 = 清除，回到跟随主模型。 */
  setCronModel: (model: string, provider?: string) => request<ActionResult>("cron-model", post({ model, provider: provider ?? null }), 90000, BASE),
  setProviderModels: (provider: string, models: string[]) => request<ActionResult>("models/provider-models", post({ provider, models }), 90000, BASE),
  setProviderDiscover: (provider: string, discover: boolean) => request<ActionResult>("models/provider-discover", post({ provider, discover }), 90000, BASE),
  setExcludedProviders: (providers: string[]) => request<ActionResult>("models/excluded", post({ providers }), 90000, BASE),
  mcp: () => request<{ data: McpServer[] }>("mcp", {}, 30000, BASE).then((r) => r.data),
  setMcpEnabled: (name: string, enabled: boolean) => request<ActionResult>("mcp/enabled", post({ name, enabled }), 90000, BASE),
  removeMcp: (name: string) => request<ActionResult>("mcp/remove", post({ name }), 90000, BASE),
  testMcp: (name: string) => request<{ ok: boolean; output: string }>("mcp/test", post({ name }), 90000, BASE),
  plugins: () => request<{ data: PluginInfo[] }>("plugins", {}, 90000, BASE).then((r) => r.data),
  setPluginEnabled: (name: string, enabled: boolean) => request<ActionResult>("plugins/enabled", post({ name, enabled }), 120000, BASE),
  profiles: () => request<ProfilesView>("profiles", {}, 40000, BASE),
  profileDetail: (name: string) => request<{ name: string; text: string }>(`profiles/${encodeURIComponent(name)}`, {}, 40000, BASE),
  createProfile: (name: string, cloneFrom: string | null, description: string) =>
    request<ActionResult>("profiles/create", post({ name, cloneFrom, description }), 150000, BASE),
  useProfile: (name: string) => request<ActionResult>("profiles/use", post({ name }), 60000, BASE),
  deleteProfile: (name: string) => request<ActionResult>("profiles/delete", post({ name }), 90000, BASE),
  webhooks: () => request<WebhooksView>("webhooks", {}, 30000, BASE),
  webhookDeliveries: (route = "", limit = 30) => request<{ data: WebhookDelivery[] }>(`webhooks/deliveries?${new URLSearchParams({ route, limit: String(limit) })}`, {}, 30000, BASE).then((r) => r.data),
  createWebhook: (body: WebhookCreate) => request<{ ok: boolean; name: string; path: string; secret: string }>("webhooks/create", post(body), 90000, BASE),
  deleteWebhook: (name: string) => request<ActionResult>("webhooks/delete", post({ name }), 90000, BASE),
  setWebhookEnabled: (name: string, enabled: boolean) => request<ActionResult>("webhooks/enabled", post({ name, enabled }), 30000, BASE),
  testWebhook: (name: string) => request<{ ok: boolean; output: string }>("webhooks/test", post({ name }), 90000, BASE),
  imageModels: () => request<{ data: ImageModel[] }>("image/models", {}, 15000, BASE).then((r) => r.data),
  imageHistory: () => request<{ data: ImageItem[] }>("image/history", {}, 15000, BASE).then((r) => r.data),
  imageGenerate: (model: string, prompt: string, aspect: string, signal?: AbortSignal) =>
    request<{ item: ImageItem; historySaved: boolean }>("image/generate", { ...post({ model, prompt, aspect }), signal }, 150000, BASE),
  imageDelete: (id: string) => request<{ ok: boolean }>("image/delete", post({ id }), 15000, BASE),
};
