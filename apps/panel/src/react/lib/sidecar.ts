// 面板辅助服务（HK 127.0.0.1:8651，经 nginx panel-sidecar/）：主机状态、技能、配置、服务重启。

import { API_BASE, ApiError, request, type SessionSummary } from "./api.js";
import type { EditBoundary } from "./message-edit.js";

const BASE = new URL("panel-sidecar/", document.baseURI);

export interface ServiceState {
  name: string;
  label: string;
  active: string;
  load?: string;
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
  saved?: boolean;
  applied?: boolean;
  restartRequired?: boolean;
  processEnvChanged?: boolean;
  hotLoaded?: boolean;
  effectiveNow?: boolean;
  loadedNow?: boolean;
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
export interface ProviderInfo {
  id: string;
  name: string;
  baseUrl: string | null;
  apiMode: string | null;
  models: string[];
  discoverModels: boolean;
  editable: boolean;
  /** true 已设置，false 未设置，null 读不到。不是密钥。当前 GET 总是带此字段。 */
  hasKey: boolean | null;
}
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
export interface KanbanTask {
  id: string;
  title: string;
  status: string;
  assignee: string | null;
  priority: number;
  created_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  workspace: string | null;
  session_id: string | null;
}

export interface KanbanComment {
  id: number;
  author: string;
  body: string;
  created_at: number;
}

export interface KanbanEvent {
  id: number;
  kind: string;
  payload: Record<string, unknown> | null;
  created_at: number;
  run_id: number | null;
}

export interface KanbanTaskDetail extends KanbanTask {
  body: string | null;
  comments: KanbanComment[];
  events: KanbanEvent[];
}

export interface KanbanTasks {
  tasks: KanbanTask[];
  statuses: string[];
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
  /** 运行响应可选字段。缺省不是错配；禁止用 title 填。 */
  delegationId?: string | null;
  todoId?: string | null;
  profileKey?: string | null;
  executionStatus?: string | null;
}
export interface SubagentsView {
  data: Subagent[];
  running: number;
  stalled?: number;
  total?: number;
  now: number;
  limit?: number;
  offset?: number;
  /** SessionDB 所属 profile。缺省时消费方不得猜 default 或从 URL 取。 */
  profileKey?: string | null;
}
export interface ContextUsage {
  used: number | null;
  window: number | null;
  model?: string;
  calls?: number;
  at?: number | string;
  out?: number | null;
  cached?: number;
  session?: { input: number; output: number; cacheRead: number; apiCalls: number; messages: number } | null;
  source: "config" | "cached" | "default" | "unknown" | "no-data";
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
export interface ActionResult {
  ok: boolean;
  note?: string;
  saved?: boolean;
  applied?: boolean;
  effectiveNow?: boolean;
  loadedNow?: boolean;
  restartRequired?: boolean;
  processEnvChanged?: boolean;
  hotLoaded?: boolean;
  processSwitched?: boolean;
}

export interface LiveState {
  running: boolean;
  takenOver: boolean;
  controlledByYou: boolean;
  ownerSessionId?: string;
  leaseRemainingSeconds: number;
  url?: string;
  title?: string;
}

export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

/**
 * 上传：原始请求体即文件内容（核心 /api/uploads，不解析 multipart）。用 XHR 以便拿到进度。
 * `timeoutMs` 为 0 表示不限时（大文件）；照片存档这类不能卡住发送的上传要给上限。
 */
function relativeUploadDir(dir: string | undefined): string | null {
  if (typeof dir !== "string" || dir.length === 0) return null;
  if (dir.startsWith("/") || dir.startsWith("\\") || dir.includes("\0") || /^[A-Za-z]:[\\/]/.test(dir)) {
    throw new ApiError("server", 400, "不能上传到绝对路径");
  }
  return dir;
}

export function uploadFile(file: File, root = "uploads", onProgress?: (fraction: number) => void, timeoutMs = 0, dir?: string): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const relative = relativeUploadDir(dir);
    // 部分拖拽/粘贴来源的文件没有名字，服务端会拒绝空名。
    const q = new URLSearchParams({ root, name: file.name || (file.type.startsWith("image/") ? "图片" : "文件") });
    if (relative) q.set("dir", relative);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", new URL(`api/uploads?${q}`, API_BASE));
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
export interface SessionFolders {
  folders: SessionFolder[];
  assignments: Record<string, string>;
  /** 仅 GET ?sessions=1 返回。已归属、但不在侧栏已加载页里的会话摘要。 */
  sessions?: SessionSummary[];
}
export type SessionFolderAction =
  | { action: "create"; name: string }
  | { action: "rename"; id: string; name: string }
  | { action: "collapse"; id: string; collapsed: boolean }
  | { action: "delete"; id: string }
  | { action: "move"; sessionId: string; folderId: string | null };

/** 服务端 list_subagents 的页上限。超过会被拒绝，不能靠加大 limit 代替翻页。 */
export const SUBAGENT_SERVER_MAX = 50;
/** 一次查询最多收齐的 child。超出则失败，不把已拿到的页当作全部。 */
export const SUBAGENT_MAX_CHILDREN = 2000;
/** total 变化、空页或重复身份时整表重读的次数。用尽仍不稳定就失败。 */
export const SUBAGENT_MAX_ATTEMPTS = 3;

export type SubagentPageFetch = (
  offset: number,
  limit: number,
  signal?: AbortSignal,
) => Promise<SubagentsView>;

class SubagentSnapshotMoved extends Error {
  constructor() {
    super("subagent snapshot moved");
    this.name = "SubagentSnapshotMoved";
  }
}

function subagentPageSize(limit: number): number {
  if (!Number.isInteger(limit) || limit < 1) {
    throw new ApiError("server", 0, "子代理分页参数不合法", "subagents_invalid");
  }
  return Math.min(limit, SUBAGENT_SERVER_MAX);
}

function subagentUnavailable(): ApiError {
  return new ApiError("server", 0, "子代理状态暂不可用", "subagents_unavailable");
}

/** 页上没回显 offset 时，用本次请求的 offset。不把缺失编成 0，除非请求本身就是 0。回显不一致则拒绝。 */
function pageOffsetFact(view: SubagentsView, requested: number): number {
  if (view.offset === undefined) return requested;
  if (view.offset !== requested) throw subagentUnavailable();
  return view.offset;
}

function assertSubagentPage(
  view: SubagentsView,
  limit: number,
  offset: number,
): asserts view is SubagentsView & { total: number } {
  if (!view || !Array.isArray(view.data) || typeof view.running !== "number" || typeof view.total !== "number") {
    throw subagentUnavailable();
  }
  if (!Number.isInteger(view.total) || view.total < 0 || !Number.isInteger(view.running) || view.running < 0 || view.running > view.total) {
    throw subagentUnavailable();
  }
  if (typeof view.now !== "number" || !Number.isFinite(view.now)) throw subagentUnavailable();
  if (view.stalled !== undefined && (!Number.isInteger(view.stalled) || view.stalled < 0 || view.stalled > view.total)) {
    throw subagentUnavailable();
  }
  if (view.limit !== undefined && view.limit !== limit) throw subagentUnavailable();
  const echoed = pageOffsetFact(view, offset);
  if (echoed !== offset) throw subagentUnavailable();
  if (view.data.length > limit) throw subagentUnavailable();
}

/** 稳定身份是 child 会话 id。不用 delegationId、todoId、task_index 或标题。 */
function childIdentity(row: Subagent): string {
  if (!row || typeof row.id !== "string" || row.id.length === 0 || row.id.length > 512) throw subagentUnavailable();
  return row.id;
}

function provenProfileKey(pages: readonly SubagentsView[]): string | undefined {
  if (pages.length === 0) return undefined;
  const first = pages[0].profileKey;
  if (typeof first !== "string" || first.length === 0) return undefined;
  for (const page of pages) {
    if (page.profileKey !== first) return undefined;
    for (const row of page.data) {
      if (row.profileKey !== first) return undefined;
    }
  }
  return first;
}

function withoutProfile(row: Subagent): Subagent {
  if (!("profileKey" in row)) return row;
  const copy: Subagent = { ...row };
  delete copy.profileKey;
  return copy;
}

function finishSubagentView(pages: readonly SubagentsView[], data: readonly Subagent[], pageSize: number): SubagentsView {
  const last = pages[pages.length - 1];
  const profileKey = provenProfileKey(pages);
  const view: SubagentsView = {
    data: profileKey ? [...data] : data.map(withoutProfile),
    running: last.running,
    total: last.total,
    now: last.now,
    limit: pageSize,
    offset: 0,
  };
  if (typeof last.stalled === "number") view.stalled = last.stalled;
  if (profileKey) view.profileKey = profileKey;
  return view;
}

function maxSubagentPages(pageSize: number): number {
  return Math.ceil(SUBAGENT_MAX_CHILDREN / pageSize);
}

async function walkSubagentPages(
  fetchPage: SubagentPageFetch,
  pageSize: number,
  signal?: AbortSignal,
): Promise<SubagentsView> {
  const pages: SubagentsView[] = [];
  const data: Subagent[] = [];
  const seen = new Set<string>();
  let total: number | null = null;
  const pageCap = maxSubagentPages(pageSize);
  for (let pageIndex = 0; pageIndex < pageCap; pageIndex += 1) {
    if (signal?.aborted) throw signal.reason ?? new ApiError("network", 0, "请求已取消");
    const offset = pageIndex * pageSize;
    if (total !== null && offset >= total) {
      if (seen.size === total) return finishSubagentView(pages, data, pageSize);
      throw new SubagentSnapshotMoved();
    }
    const page = await fetchPage(offset, pageSize, signal);
    assertSubagentPage(page, pageSize, offset);
    const pageTotal = page.total;
    if (total === null) {
      if (pageTotal > SUBAGENT_MAX_CHILDREN) {
        throw new ApiError("server", 0, "子代理列表超过有界分页，不能把部分结果当作全部", "subagents_incomplete");
      }
      total = pageTotal;
    } else if (pageTotal !== total) {
      throw new SubagentSnapshotMoved();
    }
    if (total === null) throw subagentUnavailable();
    if (page.data.length === 0) {
      if (total === 0 && offset === 0) return finishSubagentView([page], [], pageSize);
      if (seen.size === total && offset >= total) return finishSubagentView(pages, data, pageSize);
      throw new SubagentSnapshotMoved();
    }
    pages.push(page);
    for (const row of page.data) {
      const id = childIdentity(row);
      if (seen.has(id)) throw new SubagentSnapshotMoved();
      seen.add(id);
      data.push(row);
    }
    if (seen.size > total) throw new SubagentSnapshotMoved();
    if (seen.size === total) return finishSubagentView(pages, data, pageSize);
    if (page.data.length < pageSize) throw new SubagentSnapshotMoved();
  }
  throw new SubagentSnapshotMoved();
}

/**
 * 沿服务端 limit/offset 收齐。返回的仍是 SubagentsView。
 * data 按 child 会话 id 只保留一次；重复身份、空页或 total 变化会使这次快照作废并有界重读。
 * 不能证明收齐时抛错，不返回半份，也不把失败变成空数组。
 * profile 不能在每一页上证明为同一个值时省略字段，不写 default。不改 executionStatus 或验收 status。
 */
export async function collectSubagentPages(
  fetchPage: SubagentPageFetch,
  limit: number,
  signal?: AbortSignal,
): Promise<SubagentsView> {
  const pageSize = subagentPageSize(limit);
  for (let attempt = 0; attempt < SUBAGENT_MAX_ATTEMPTS; attempt += 1) {
    try {
      return await walkSubagentPages(fetchPage, pageSize, signal);
    } catch (error) {
      if (error instanceof SubagentSnapshotMoved) continue;
      throw error;
    }
  }
  throw new ApiError("server", 0, "子代理列表在分页期间变化，不能把不完整结果当作全部", "subagents_incomplete");
}

function requestSubagentsPage(sessionId: string, limit: number, offset = 0, signal?: AbortSignal): Promise<SubagentsView> {
  const params = new URLSearchParams({ parent: sessionId, limit: String(limit) });
  if (offset > 0) params.set("offset", String(offset));
  return request<SubagentsView>(`api/runtime/subagents?${params}`, { signal }, 15000).then((view) => {
    if (!view || !Array.isArray(view.data) || typeof view.running !== "number" || typeof view.total !== "number") {
      throw new ApiError("server", 0, "子代理状态暂不可用");
    }
    return view;
  });
}

export const sidecar = {
  activeSessions: (signal?: AbortSignal) =>
    request<{ data: SessionSummary[] }>("api/runtime/sessions/active", { signal }, 5000).then((r) => {
      if (!Array.isArray(r?.data)) throw new ApiError("server", 0, "活动对话查询返回格式异常");
      return r.data;
    }),
  /** 只读核对旧文件夹。项目事实不得经此写入。 */
  sessionFolders: () => request<SessionFolders>("sessions/folders?sessions=1", {}, 10000, BASE),
  updateSessionFolders: (body: SessionFolderAction): Promise<SessionFolders> => {
    void body;
    return Promise.reject(new ApiError("server", 0, "项目事实不能写入侧车；请使用 /api/projects"));
  },
  sessionBackground: (sessionId: string, signal?: AbortSignal) =>
    request<{ processes?: unknown }>(`api/runtime/sessions/${encodeURIComponent(sessionId)}/background`, { signal }, 5000)
      .then((r) => {
        if (!Array.isArray(r?.processes)) throw new ApiError("server", 0, "后台进程查询返回格式异常");
        return r.processes as BackgroundProcess[];
      }),
  sessionRun: (sessionId: string, signal?: AbortSignal) =>
    request<SessionRunState>(`api/runtime/sessions/${encodeURIComponent(sessionId)}/run`, { signal }, 5000),
  resolveRun: (sessionId: string, idempotencyKey: string, signal?: AbortSignal) =>
    request<SessionRunState & { resolution: { idempotency_key: string; state: "found" | "not_found" } }>(
      `api/runtime/sessions/${encodeURIComponent(sessionId)}/run?${new URLSearchParams({ idempotency_key: idempotencyKey })}`, { signal }, 5000),
  sessionExecution: (sessionId: string, signal?: AbortSignal) =>
    request<{ busy: boolean; execution: SessionExecution | null }>(`api/runtime/sessions/${encodeURIComponent(sessionId)}/execution`, { signal }, 5000)
      .then((state) => {
        if (typeof state.busy !== "boolean" || !("execution" in state)) throw new ApiError("server", 0, "任务查询返回异常");
        return state;
      }),
  stopExecution: async (sessionId: string, executionId: string) => {
    if (/^run_[0-9a-f]{32}$/.test(executionId)) {
      await request(`v1/runs/${encodeURIComponent(executionId)}/stop`, post({}), 10000);
      return { status: "cancelled" as const, busy: false as const };
    }
    if (!/^[0-9a-f]{64}$/.test(executionId)) {
      throw new ApiError("server", 409, "执行器身份不明，未发送停止信号");
    }
    return request<{ status: "signalled" | "cancelled"; busy: boolean }>(
      `api/runtime/sessions/${encodeURIComponent(sessionId)}/execution/stop`,
      post({ session_id: sessionId, execution_id: executionId, confirm: "stop-cli" }),
      15000,
    );
  },
  editBranch: (body: EditBoundary & { sessionId: string; requestId: string; model: string; provider: string }) =>
    request<{ session: SessionSummary }>(`api/runtime/sessions/${encodeURIComponent(body.sessionId)}/edit-branch`, post(body), 15000).then((r) => r.session),
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
  setSkillEnabled: (name: string, enabled: boolean) => request<ActionResult & { name?: string; enabled?: boolean }>("skills/enabled", post({ name, enabled }), 90000, BASE),
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
  kanbanTasks: (status?: string) => {
    const query = status ? `?${new URLSearchParams({ status })}` : "";
    return request<KanbanTasks>(`kanban/tasks${query}`, {}, 15000, BASE);
  },
  kanbanTask: (id: string) =>
    request<KanbanTaskDetail>(`kanban/tasks/${encodeURIComponent(id)}`, {}, 15000, BASE),
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
  commentary: (_ids: number[], _signal?: AbortSignal) =>
    Promise.reject(new ApiError("server", 0, "旁白已随会话消息返回，不再按消息 id 跨会话读取")),
  /** 单页。码头按 id 查找仍走这里，不把 limit=30 改成收齐。offset 0 省略，与服务端默认一致。 */
  subagents: (sessionId = "", limit = 50, offset = 0, signal?: AbortSignal) =>
    requestSubagentsPage(sessionId, limit, offset, signal),
  /** 现有 useSubagents 收齐后直接消费同一 SubagentsView。失败抛错，不回空数组，不另开轮询。 */
  collectSubagents: (sessionId = "", limit = 50, signal?: AbortSignal) =>
    collectSubagentPages(
      (offset, pageLimit, pageSignal) => requestSubagentsPage(sessionId, pageLimit, offset, pageSignal),
      limit,
      signal,
    ),
  context: (sessionId: string) => request<ContextUsage>(`api/runtime/sessions/${encodeURIComponent(sessionId)}/context`, {}, 15000),
  runWait: (sessionId: string, startedAt?: number, signal?: AbortSignal) => request<RunWait>(`run-wait?${new URLSearchParams({ session: sessionId, ...(startedAt ? { started_at_ms: String(startedAt) } : {}) })}`, { signal }, 8000, BASE),
  /** 与 context/subagents 同一 panel-api 通道。不走侧车 /usage。 */
  usage: (days: number) => request<Usage>(`api/runtime/usage?${new URLSearchParams({ days: String(days) })}`, {}, 20000),
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
