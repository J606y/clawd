// 与 Hermes gateway api_server 通信。所有请求走同源 panel-api/，由 nginx 校验 Cookie 并注入 Bearer。
import { canonicalModel, modelEffortFields } from "./model-effort.js";

export const API_BASE = new URL("panel-api/", document.baseURI);

export type ApiErrorKind = "unauthorized" | "rate-limited" | "unavailable" | "network" | "server";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const MAX_TITLE_ATTEMPTS = 50;
const TITLE_HINTS_KEY = "hp_title_suffix_hints";
const TITLE_HINTS_MAX = 200;

/** 标题 → 上次成功用到的序号。只是加速重名探测的提示，读写失败都不影响建会话。 */
function readTitleHints(): Record<string, number> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(TITLE_HINTS_KEY) ?? "{}");
    return raw && typeof raw === "object" ? (raw as Record<string, number>) : {};
  } catch {
    return {};
  }
}

function writeTitleHint(title: string, n: number): void {
  try {
    const hints = readTitleHints();
    delete hints[title];
    hints[title] = n;
    const keys = Object.keys(hints);
    for (const k of keys.slice(0, Math.max(0, keys.length - TITLE_HINTS_MAX))) delete hints[k];
    localStorage.setItem(TITLE_HINTS_KEY, JSON.stringify(hints));
  } catch {
    // 存储不可用（隐私模式、配额满）：下次从头探测，功能不受影响。
  }
}

/** 任意请求遇到 401 时广播，根组件据此退回登录页。 */
export const AUTH_EXPIRED_EVENT = "hp-auth-expired";

// 只有 401 表示登录失效（nginx 登录门只回 401）；403 是 sidecar/网关的业务拒绝（密钥类配置、锁定项、只读目录等），不能踢回登录页。
function kindFor(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 429) return "rate-limited";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "server";
}

async function parseError(res: Response): Promise<{ message: string; code?: string }> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "error" in body) {
      const err = (body as { error: unknown }).error;
      if (typeof err === "string") return { message: err };
      if (err && typeof err === "object") {
        const message = "message" in err ? String((err as { message: unknown }).message) : `${res.status} ${res.statusText}`.trim();
        const code = "code" in err && typeof (err as { code: unknown }).code === "string" ? (err as { code: string }).code : undefined;
        return { message, code };
      }
    }
  } catch {
    // 非 JSON 响应体（nginx 错误页等），回落到状态行。
  }
  return { message: `${res.status} ${res.statusText}`.trim() };
}

export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 15000, base: URL = API_BASE): Promise<T> {
  init.signal?.throwIfAborted();
  const ctrl = new AbortController();
  let timedOut = false;
  const onOuterAbort = () => ctrl.abort(init.signal?.reason);
  init.signal?.addEventListener("abort", onOuterAbort, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);

  try {
    const res = await fetch(new URL(path, base), {
      ...init,
      signal: ctrl.signal,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
    if (!res.ok) {
      const kind = kindFor(res.status);
      if (kind === "unauthorized") window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      const { message, code } = await parseError(res);
      ctrl.signal.throwIfAborted();
      throw new ApiError(kind, res.status, message, code);
    }
    const text = await res.text();
    ctrl.signal.throwIfAborted();
    try {
      return (text ? JSON.parse(text) : undefined) as T;
    } catch {
      throw new ApiError("server", res.status, "服务端返回了无法解析的数据");
    }
  } catch (e) {
    init.signal?.throwIfAborted();
    if (timedOut) throw new ApiError("network", 0, "请求超时");
    if (e instanceof ApiError) throw e;
    throw new ApiError("network", 0, "网络不可达");
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener("abort", onOuterAbort);
  }
}

// ---- 领域类型（只声明面板用到的字段）----

export interface SessionSummary {
  id: string;
  source: string;
  title: string | null;
  model: string | null;
  started_at: number;
  last_active: number | null;
  message_count: number;
  preview: string | null;
  pinned: boolean;
  archived: boolean;
}

interface SessionListResponse {
  data: SessionSummary[];
  has_more: boolean;
}

export interface HealthResponse {
  status: string;
  version?: string;
}

export interface ToolCall {
  id: string;
  function: { name: string; arguments: string };
}

export type ContentPart = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } } | { type: string };

/** state.db 中的一条消息（GET /api/sessions/{id}/messages 与 run.completed.messages 同构）。 */
export interface Message {
  /** state.db 行号；旁白（lib/commentary.ts）按它对应。 */
  id?: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string | ContentPart[] | null;
  tool_calls?: ToolCall[] | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
  reasoning?: string | null;
  timestamp?: number;
  /** 展示类型是呈现提示，不独立证明来源；旧网关可能把 API 入站误标为通用通知。 */
  display_kind?: string | null;
  display_metadata?: Record<string, unknown> | string | null;
  /** 仅由面板侧车按同会话 admission_id 精确关联；不是编辑权限或真人身份认证。 */
  input_origin?: "api_queue" | "api_steer";
}

interface MessagesResponse {
  data: Message[];
  pagination?: { returned?: number };
}

export interface MessagePage {
  messages: Message[];
  hasMore: boolean;
}

export interface SessionPatch {
  title?: string;
  pinned?: boolean;
}

interface CreateSessionResponse {
  session: SessionSummary;
}

export type ApprovalChoice = "once" | "session" | "always" | "deny";

/** 发给 chat/stream 的消息体：纯文本，或 text + image_url 分段（api_server 只接受这两种）。 */
export type OutgoingContent = string | ({ type: "text"; text: string } | { type: "image_url"; image_url: { url: string } })[];

export interface ModelChoice {
  provider: string;
  providerName: string;
  model: string;
}

export interface ModelOptions {
  current: { provider: string; model: string };
  choices: ModelChoice[];
}

interface ModelOptionsResponse {
  model?: string;
  provider?: string;
  providers?: { slug: string; name: string; authenticated?: boolean; models?: (string | { id?: string; name?: string })[] }[];
}

export interface CronJob {
  id: string;
  name: string;
  prompt: string;
  schedule_display?: string;
  schedule?: { kind?: string; display?: string; expr?: string; minutes?: number };
  enabled: boolean;
  state?: string;
  paused_reason?: string | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  failure_streak?: number;
  deliver?: string;
  model?: string | null;
  skills?: string[] | null;
  repeat?: { times?: number | null; completed?: number };
  created_at?: string;
}

export interface CronJobInput {
  name: string;
  schedule: string;
  prompt: string;
  enabled?: boolean;
}

export interface HealthDetailed {
  status: string;
  version?: string;
  gateway_state?: string;
  readiness?: { checks?: Record<string, { status: string; [k: string]: unknown }> };
  platforms?: Record<string, { state: string; error_message?: string | null; needs_attention?: boolean; updated_at?: string }>;
}

const jobPath = (id: string) => `api/jobs/${encodeURIComponent(id)}`;
const unwrapJob = (r: { job?: CronJob } | CronJob): CronJob => ("job" in r && r.job ? r.job : (r as CronJob));

const PROVIDER_CACHE_MS = 60_000;
let providerCache: { at: number; choices: ModelChoice[] } | null = null;

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("health", { signal }, 8000),
  listSessions: (opts: { limit?: number; offset?: number; source?: string } = {}, signal?: AbortSignal) => {
    const q = new URLSearchParams();
    q.set("limit", String(opts.limit ?? 50));
    if (opts.offset) q.set("offset", String(opts.offset));
    if (opts.source) q.set("source", opts.source);
    return request<SessionListResponse>(`api/sessions?${q}`, { signal });
  },
  /**
   * Hermes 要求会话标题唯一，重名会 400。标题保持干净，只在重名时追加「(n)」：
   * 从本机记下的该标题上次用到的序号接着试，连续撞 8 次后步长翻倍，常见开场白也只需几次请求。
   */
  createSession: async (title: string): Promise<SessionSummary> => {
    const hints = readTitleHints();
    let n = Math.max(1, hints[title] ?? 1);
    let step = 1;
    for (let attempt = 1; ; attempt++) {
      const candidate = n === 1 ? title : `${title} (${n})`;
      try {
        const r = await request<CreateSessionResponse>("api/sessions", { method: "POST", body: JSON.stringify({ title: candidate }) });
        if (n > 1) writeTitleHint(title, n);
        return r.session;
      } catch (e) {
        const taken = e instanceof ApiError && e.status === 400 && /already in use/i.test(e.message);
        if (!taken || attempt >= MAX_TITLE_ATTEMPTS) throw e;
      }
      if (attempt % 8 === 0) step *= 2;
      n = n === 1 ? 2 : n + step;
    }
  },
  /** 展示完整历史（含已压缩原文）；offset 从最新消息往回数，页内按时间正序。 */
  messages: async (sessionId: string, opts: { limit: number; offset: number }, signal?: AbortSignal): Promise<MessagePage> => {
    const q = new URLSearchParams({ order: "latest", limit: String(opts.limit), offset: String(opts.offset), include_compacted: "true" });
    const r = await request<MessagesResponse>(`api/sessions/${encodeURIComponent(sessionId)}/messages?${q}`, { signal });
    signal?.throwIfAborted();
    // 来源标记只能来自会话范围内的只读关联，不信任原响应的同名扩展字段。
    const messages = r.data.map((row) => { const message = { ...row }; delete message.input_origin; return message; });
    const candidates = messages.filter((m) => m.role === "user" && m.display_kind === "internal_notification" && Number.isSafeInteger(m.id) && m.id! > 0);
    try {
      for (let offset = 0; offset < candidates.length; offset += 200) {
        const batch = candidates.slice(offset, offset + 200);
        const query = new URLSearchParams({ session_id: sessionId, ids: batch.map((m) => String(m.id)).join(",") });
        const result = await request<{ items: Record<string, unknown> }>(`sessions/message-origins?${query}`, { signal }, 4000, new URL("panel-core/", document.baseURI));
        signal?.throwIfAborted();
        if (!result?.items || typeof result.items !== "object" || Array.isArray(result.items)) throw new Error("消息来源响应格式异常");
        for (const message of batch) {
          const origin = result.items[String(message.id)];
          if (origin === "api_queue" || origin === "api_steer") message.input_origin = origin;
        }
      }
    } catch (error) {
      signal?.throwIfAborted();
      if (error instanceof ApiError && error.kind === "unauthorized") throw error;
      // 来源查询失败不能清空已读历史；保守保留原类型，下一次载入重试。
      console.warn("消息来源暂不可用，保留原展示类型", error);
    }
    return { messages, hasMore: (r.pagination?.returned ?? messages.length) >= opts.limit };
  },
  patchSession: (sessionId: string, patch: SessionPatch) =>
    request<unknown>(`api/sessions/${encodeURIComponent(sessionId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteSession: (sessionId: string) => request<unknown>(`api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  forkSession: (sessionId: string) =>
    request<CreateSessionResponse>(`api/sessions/${encodeURIComponent(sessionId)}/fork`, { method: "POST", body: "{}" }).then((r) => r.session),
  /** 只列出已认证且有模型的提供方（api_server 会返回全部内置提供方，多数未配置）。 */
  modelOptions: (): Promise<ModelOptions> =>
    request<ModelOptionsResponse>("api/model/options").then((r) => {
      const seen = new Set<string>();
      const choices = (r.providers ?? [])
        .filter((p) => p.authenticated && p.models?.length)
        .flatMap((p) =>
          p.models!.map((m) => ({ provider: p.slug, providerName: p.name, model: typeof m === "string" ? m : (m.id ?? m.name ?? "") })),
        )
        .filter((c) => c.model)
        .map((c) => ({ ...c, model: canonicalModel(c.model, [c], c.provider) }))
        .filter((c) => {
          const key = JSON.stringify([c.provider, c.model]);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      const provider = r.provider ?? "";
      return { current: { provider, model: canonicalModel(r.model ?? "", choices, provider) }, choices };
    }),
  /**
   * 模型名 → provider slug。请求显式携带 model+provider，动态 Argon 另带 require_model_lock；
   * 刷新页面或切到旧会话后面板只知道会话的模型名，靠这里反查。同名模型出现在多个提供方时
   * 无法判定，返回 ""（调用方据此拒绝发送，而不是猜一个或退回全局默认）。
   */
  providerOf: async (model: string): Promise<string> => {
    if (!model) return "";
    if (!providerCache || Date.now() - providerCache.at > PROVIDER_CACHE_MS) {
      try {
        const { choices } = await api.modelOptions();
        providerCache = { at: Date.now(), choices };
      } catch {
        return "";
      }
    }
    const slugs = new Set(providerCache.choices.filter((c) =>
      canonicalModel(model, [c], c.provider) === canonicalModel(c.model, [c], c.provider),
    ).map((c) => c.provider));
    return slugs.size === 1 ? [...slugs][0]! : "";
  },
  /** 普通发送与编辑重发共用；模型清单不可用时拒绝猜测动态变体。 */
  runModelFields: async (model: string, provider: string, effort: string) => {
    let choices: ModelChoice[] = [];
    if (!model || /^gemini-4-argon-(low|medium|high)$/.test(model)) {
      const options = await api.modelOptions();
      choices = options.choices;
      if (!model) {
        model = options.current.model;
        provider = options.current.provider;
        const fields = modelEffortFields(model, provider, effort, choices);
        // 仅已确认的动态变体显式锁模；其他默认模型保留全局路由，只收敛 Gemini 档位。
        return fields.require_model_lock ? fields : { model_options: fields.model_options };
      }
    }
    provider ||= await api.providerOf(model);
    if (!provider) throw new ApiError("server", 0, `无法确认模型「${model}」的提供方，消息未发送`);
    return modelEffortFields(model, provider, effort, choices);
  },
  /** 会话级模型锁：持久化到会话，之后每个回合都用它。 */
  lockModel: (sessionId: string, model: string, provider: string) =>
    request<unknown>(`api/sessions/${encodeURIComponent(sessionId)}/model`, {
      method: "POST",
      body: JSON.stringify({ model, provider }),
    }),
  /** 运行中插话：只在回合执行中有效，否则 409。 */
  steer: (runId: string, text: string) =>
    request<unknown>(`v1/runs/${encodeURIComponent(runId)}/steer`, { method: "POST", body: JSON.stringify({ input: text }) }),
  healthDetailed: () => request<HealthDetailed>("health/detailed", {}, 10000),
  jobs: () => request<{ jobs: CronJob[] }>("api/jobs").then((r) => r.jobs ?? []),
  createJob: (input: CronJobInput) =>
    request<{ job?: CronJob } | CronJob>("api/jobs", { method: "POST", body: JSON.stringify(input) }).then(unwrapJob),
  updateJob: (id: string, patch: Partial<CronJobInput>) =>
    request<{ job?: CronJob } | CronJob>(jobPath(id), { method: "PATCH", body: JSON.stringify(patch) }).then(unwrapJob),
  deleteJob: (id: string) => request<unknown>(jobPath(id), { method: "DELETE" }),
  pauseJob: (id: string) => request<unknown>(`${jobPath(id)}/pause`, { method: "POST", body: "{}" }),
  resumeJob: (id: string) => request<unknown>(`${jobPath(id)}/resume`, { method: "POST", body: "{}" }),
  runJob: (id: string) => request<unknown>(`${jobPath(id)}/run`, { method: "POST", body: "{}" }, 30000),
  approve: (runId: string, choice: ApprovalChoice, requestId?: string, executionGeneration?: number) => {
    if (typeof requestId !== "string" || requestId.length < 1 || requestId.length > 256
      || typeof executionGeneration !== "number" || !Number.isInteger(executionGeneration)) {
      return Promise.reject(new ApiError("server", 0, "缺少可核验的审批身份或代次，未提交"));
    }
    return request<unknown>(`v1/runs/${encodeURIComponent(runId)}/approval`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, execution_generation: executionGeneration, choice }),
    });
  },
  clarify: (runId: string, body: { request_id: string; execution_generation: number; answer: string }) =>
    request<unknown>(`v1/runs/${encodeURIComponent(runId)}/clarify`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  vault: (runId: string, body: { request_id: string; execution_generation: number; answer: string }) =>
    request<unknown>(`v1/runs/${encodeURIComponent(runId)}/vault`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** 发起一次运行：立即返回 run_id，运行与浏览器连接脱钩（刷新页面不会中断）。 */
  startRun: (body: unknown, signal?: AbortSignal, idempotencyKey?: string) =>
    request<{ run_id: string; status: string }>("v1/runs", {
      method: "POST", body: JSON.stringify(body), signal,
      // 原生持久化需显式启用；sidecar 只读取公开状态，不保存请求正文。
      headers: { "Idempotency-Key": idempotencyKey || crypto.randomUUID() },
    }, 30000),
  runStatus: (runId: string, signal?: AbortSignal) =>
    request<{ status: string; error?: string; run_id?: string; session_id?: string; created_at?: number }>(`v1/runs/${encodeURIComponent(runId)}`, { signal }, 10000),
  stopRun: (runId: string) => request<unknown>(`v1/runs/${encodeURIComponent(runId)}/stop`, { method: "POST", body: "{}" }),
};
