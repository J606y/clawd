// 进行中的回合。回合通过 POST /v1/runs 发起：运行在服务端独立进行，与浏览器连接脱钩，
// 事件流（GET /v1/runs/{id}/events）断了可以凭 last_seq 续传并回放——所以切换会话、刷新页面、
// 网络抖动都不会中断回合。run_id 记在 localStorage，刷新后由 resumeAll() 重新接上。

import { ApiError, api, type ApprovalChoice, type Message, type OutgoingContent } from "./api.js";
import { buildSteerText, formatBytes, type Attachment } from "./attachments.js";
import { getSse } from "./sse.js";
import { sidecar, type SessionRunState } from "./sidecar.js";
import { buildTurns, isTurnStart, type NoteStep, type Step, type ToolStep } from "./transcript.js";
import { currentEffort } from "./effort.js";
import { applyTurnUsage, createTurnUsage, formatTurnTokens } from "./turn-usage.js";
import { clarifyIdentity, parseClarifyEvent, CLARIFY_ANSWER_MAX, type ClarifyAnswer, type ClarifyPrompt } from "./clarify.js";

/** api_server_runs._TOOL_COMPLETED_PREVIEW_MAX_CHARS：达到它说明预览被截断。 */
const PREVIEW_MAX_CHARS = 500;

export type RunStatus = "starting" | "running" | "completed" | "cancelled" | "failed";

export interface ApprovalRequest {
  command: string;
  description: string;
  choices: ApprovalChoice[];
  requestId?: string;
  /** 规范审批要求的整数代次；旧事件可能没有。 */
  executionGeneration?: number;
  pending: boolean;
}

export interface Steer {
  /** 发出时的可见边界；text 转成 note 后仍使用同一步骤索引。历史恢复没有这个证据。 */
  anchor?: { step: number; offset: number };
  /** 用户输入的文字（不含附件正文，附件单独以卡片展示）。 */
  text: string;
  /** 嵌进正文的文本附件，名称保持原样以便下载。 */
  files: { name: string; text: string }[];
  /** 已上传到服务器的文件（含插话时改为上传的图片）。 */
  uploads: { name: string; absPath: string; size: string; image?: boolean }[];
  status: "sending" | "sent" | "failed";
}

export interface LiveRun {
  sessionId: string;
  /** 其他设备发起；用户输入只从持久化消息读取，不从状态记录猜测。 */
  remote?: boolean;
  /** 本回合要用的模型与提供方；空表示不指定（用全局默认）。仅发起时使用，续接的回合为空。 */
  model: string;
  provider: string;
  runId: string;
  idempotencyKey?: string;
  /** 创建请求已发出，但尚无可核验的服务端运行标识；不得据此发布终态。 */
  unverified?: boolean;
  /** 用户放弃等待未知创建结果。状态保持非终态，但不再占着输入框。 */
  locallyReleased?: boolean;
  userText: string;
  /** 原始附件：发送失败时连同正文一起放回输入框。 */
  attachments: Attachment[];
  steers: Steer[];
  status: RunStatus;
  thinking: string;
  steps: Step[];
  text: string;
  approval: ApprovalRequest | null;
  clarify: ClarifyPrompt | null;
  error: string;
  stopping: boolean;
  /** 回合开始的时间（毫秒）。刷新页面后从 localStorage 恢复，所以"已运行多久"不会因刷新清零。 */
  startedAt: number;
  /** 最近一次收到服务端事件的时间（毫秒）。事件流静默越久，越值得问问服务器是不是在等重试。 */
  lastEventAt: number;
  /** 事件流已被服务器清扫（404），后续仅能轮询状态，无法回放事件。 */
  streamGone?: boolean;
  /**
   * 状态轮询的 run/session 与当前订阅不一致，这一次 follow 已退出。
   * 不是 streamGone，不能据此清思考；也不是终态。
   */
  followDropped?: boolean;
  /** 回放事件超限被服务器截断，旧步骤无法通过事件还原。 */
  replayTruncated?: boolean;
  /** Observable output estimate; unavailable after an incomplete replay. */
  usage?: import("./turn-usage.js").TurnUsage;
  tokenLabel?: string;
}

export const RUN_CHANGED = "hp-run-changed";
export const RUN_FINISHED = "hp-run-finished";

const ACTIVE_KEY = "hp_active_runs";
const PENDING_KEY = "hp_pending_admissions";
const NATIVE_STATUSES = ["queued", "running", "waiting_for_approval", "stopping", "completed", "cancelled", "failed", "interrupted"];
const LIVE_NATIVE = ["queued", "running", "waiting_for_approval", "stopping"];
const MAX_RECONNECT = 8;
const MAX_BACKOFF_MS = 15_000;
/** 已 clear 的 runId。每会话只留最近几个，全局只留最近若干会话；超出丢掉最旧的。 */
const RETIRED_PER_SESSION = 8;
const RETIRED_SESSIONS = 32;
/** 事件流已被服务端丢弃后，轮询回合状态的间隔。 */
const STATUS_POLL_MS = 4_000;
/** 事件流至少保持这么久才断，就视为连接健康。 */
const HEALTHY_STREAM_MS = 10_000;
/** 与服务端「订阅早于 _run_streams 注册」同一窗口：20 次、每次 50ms。只用于还没见过事件的 404。 */
const SUBSCRIBE_WAIT_ATTEMPTS = 20;
const SUBSCRIBE_WAIT_MS = 50;
/** 错配拒绝后，同一 session/run/开始代次最多再跟这么多次。不是无限重试。 */
const MISMATCH_RESUB_ATTEMPTS = 3;
const MISMATCH_RESUB_MS = 250;
const MISMATCH_NOTICE = "实时订阅因运行身份不一致已暂停。回合仍在进行，不会采用错配内容。刷新可按原运行标识接回。";
/** 状态终态缺身份、错身份或旧 run。保持进行中，供界面和测试观察；不是 completed，也不是空成功。 */
const UNCONFIRMED_TERMINAL = "无法确认这次运行的终态（返回身份与当前运行不一致或缺身份）";

/**
 * 内容事件的错配检查。出现的 run_id / session_id 必须与当前订阅一致。
 * 缺字段不是错配：事件流经常只有 run_id，不能据此丢弃 message.delta 等正文。
 * 终态结算不用这条规则，见 trustedTerminalIdentity。
 * 不读取、不写 profile；事件路由也没有 session_id 查询参数。
 */
function foreignIdentity(
  run: { runId: string; sessionId: string },
  payload: { run_id?: unknown; session_id?: unknown } | null | undefined,
): boolean {
  if (!payload) return false;
  if (typeof payload.run_id === "string" && payload.run_id !== "" && payload.run_id !== run.runId) return true;
  if (typeof payload.session_id === "string" && payload.session_id !== "" && payload.session_id !== run.sessionId) return true;
  return false;
}

/**
 * 状态终态的正向身份。两枚 id 都必须是非空字符串，且与当前 run 精确相等。
 * 缺任一、空串、错 run、错 session、旧 run 都拒绝。不裁剪、不强制转换。
 */
function trustedTerminalIdentity(
  run: { runId: string; sessionId: string },
  payload: { run_id?: unknown; session_id?: unknown } | null | undefined,
): boolean {
  if (!run.runId || !run.sessionId || !payload) return false;
  return payload.run_id === run.runId && payload.session_id === run.sessionId;
}

const TERMINAL_STATUS = new Set(["completed", "cancelled", "failed", "interrupted"]);

function terminalClaim(payload: { status?: unknown } | null | undefined): string | null {
  if (!payload || typeof payload.status !== "string" || !TERMINAL_STATUS.has(payload.status)) return null;
  return payload.status;
}

interface StoredRun {
  runId: string;
  idempotencyKey?: string;
  userText: string;
  startedAt?: number;
  remote?: boolean;
}

function loadStored(): Record<string, StoredRun> {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(ACTIVE_KEY) ?? "{}");
    if (!raw || typeof raw !== "object") return {};
    const out: Record<string, StoredRun> = {};
    for (const [sid, v] of Object.entries(raw as Record<string, unknown>)) {
      const r = v as Partial<StoredRun> | null;
      if (r && (typeof r.runId === "string" || typeof r.idempotencyKey === "string")) {
        const runId = typeof r.runId === "string" ? r.runId : "";
        const idempotencyKey = typeof r.idempotencyKey === "string" ? r.idempotencyKey : undefined;
        if (runId || idempotencyKey) {
          out[sid] = {
            runId,
            ...(idempotencyKey ? { idempotencyKey } : {}),
            userText: typeof r.userText === "string" ? r.userText : "",
            ...(r.remote === true ? { remote: true } : {}),
            ...(typeof r.startedAt === "number" && Number.isFinite(r.startedAt) ? { startedAt: r.startedAt } : {}),
          };
        }
      }
    }
    return out;
  } catch {
    return {};
  }
}

function saveStored(map: Record<string, StoredRun>): void {
  try {
    window.localStorage.setItem(ACTIVE_KEY, JSON.stringify(map));
  } catch {
    // 存储不可用（隐私模式等）：只是刷新后无法自动接回，当前页面内不受影响。
  }
}

function remember(sessionId: string, entry: StoredRun | null): void {
  const map = loadStored();
  if (entry) map[sessionId] = entry;
  else delete map[sessionId];
  saveStored(map);
}

interface PendingAdmission {
  sessionId: string;
  idempotencyKey: string;
  userText: string;
  startedAt: number;
}

function loadPending(): PendingAdmission[] {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(PENDING_KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    const out: PendingAdmission[] = [];
    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const row = item as Partial<PendingAdmission>;
      if (typeof row.sessionId !== "string" || !row.sessionId) continue;
      if (typeof row.idempotencyKey !== "string" || !row.idempotencyKey) continue;
      out.push({
        sessionId: row.sessionId,
        idempotencyKey: row.idempotencyKey,
        userText: typeof row.userText === "string" ? row.userText : "",
        startedAt: typeof row.startedAt === "number" && Number.isFinite(row.startedAt) ? row.startedAt : 0,
      });
    }
    return out;
  } catch {
    return [];
  }
}

function savePending(list: PendingAdmission[]): void {
  try {
    window.localStorage.setItem(PENDING_KEY, JSON.stringify(list));
  } catch {
    // 存储不可用时只影响刷新后的补接，当前页内的放开不受影响。
  }
}

function parkAdmission(sessionId: string, idempotencyKey: string, userText: string, startedAt: number): void {
  if (!sessionId || !idempotencyKey) return;
  const list = loadPending();
  if (list.some((item) => item.sessionId === sessionId && item.idempotencyKey === idempotencyKey)) return;
  list.push({ sessionId, idempotencyKey, userText, startedAt });
  savePending(list);
}

function forgetPending(sessionId: string, idempotencyKey: string): void {
  const list = loadPending();
  const next = list.filter((item) => item.sessionId !== sessionId || item.idempotencyKey !== idempotencyKey);
  if (next.length !== list.length) savePending(next);
}

function admissionRecord(
  sessionId: string,
  key: string,
  state: { run?: { run_id?: unknown; session_id?: unknown; created_at?: unknown; updated_at?: unknown } | null; resolution?: { idempotency_key?: unknown; state?: unknown } } | null | undefined,
): { run_id: string; session_id: string; created_at: number; updated_at: number } | null {
  const record = state?.run;
  if (!state?.resolution || state.resolution.idempotency_key !== key || state.resolution.state !== "found" || !record) return null;
  if (typeof record.run_id !== "string" || !/^run_[0-9a-f]{32}$/.test(record.run_id)) return null;
  if (record.session_id !== sessionId) return null;
  if (typeof record.created_at !== "number" || typeof record.updated_at !== "number") return null;
  if (!Number.isFinite(record.created_at) || !Number.isFinite(record.updated_at) || record.created_at < 0 || record.updated_at < record.created_at) return null;
  return { run_id: record.run_id, session_id: record.session_id, created_at: record.created_at, updated_at: record.updated_at };
}

const isLive = (s: RunStatus): boolean => s === "starting" || s === "running";

/** 等待 ms 毫秒；中止、网络恢复或页面回到前台时提前结束（此时重连最有可能成功）。 */
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = (): void => {
      window.clearTimeout(t);
      signal.removeEventListener("abort", done);
      window.removeEventListener("online", done);
      document.removeEventListener("visibilitychange", onVisible);
      resolve();
    };
    const onVisible = (): void => {
      if (document.visibilityState === "visible") done();
    };
    const t = window.setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
    window.addEventListener("online", done, { once: true });
    document.addEventListener("visibilitychange", onVisible);
  });
}

/** 初始订阅重试。不碰 document：测试夹具和断线路径都不能靠 visibility 监听才等待。 */
function subscribeWait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = (): void => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = window.setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

class RunStore extends EventTarget {
  private readonly runs = new Map<string, LiveRun>();
  private readonly ctrls = new Map<string, AbortController>();
  /** 状态查询与流结束可能同时到达：每个回合只发布一次终态。 */
  private readonly finished = new WeakSet<LiveRun>();
  /** 正在进行的 stop 操作 Promise，按 LiveRun 实例去重，防止跨 run 或替换 run 污染。 */
  private readonly stoppingOps = new WeakMap<LiveRun, Promise<void>>();
  private readonly recoveryOps = new WeakMap<LiveRun, Promise<boolean>>();
  private readonly resubAttempts = new WeakMap<LiveRun, number>();
  private readonly resubOps = new WeakMap<LiveRun, Promise<void>>();
  private readonly resubCancel = new WeakMap<LiveRun, AbortSignal | undefined>();
  private sweeping = false;
  private sweepAgain = false;
  /** 仅刷新接回的本机回合需要从历史补回页面内存里的插话。 */
  private readonly resumedLocal = new WeakSet<LiveRun>();
  /** 收尾 clear 后的 runId。成功发现另一个 runId 也不删更早的墓碑。 */
  private readonly retiredIds = new Map<string, string[]>();
  private readonly retiredSessions: string[] = [];

  constructor() {
    super();
    if (typeof window !== "undefined") {
      window.addEventListener("online", () => { void this.sweepPending(); });
    }
  }

  isRestoredLocal(sessionId: string, runId: string): boolean {
    const run = this.runs.get(sessionId);
    return !!run && run.runId === runId && this.resumedLocal.has(run);
  }

  restoreSteers(sessionId: string, messages: Message[]): void {
    const run = this.runs.get(sessionId);
    if (!run || !this.isActive(sessionId) || run.remote || !this.resumedLocal.has(run) || run.steers.length) return;
    let boundary = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (isTurnStart(messages[i]!)) {
        boundary = i;
        break;
      }
    }
    // 最新页不含本轮起点时不猜测，加载更早历史后再尝试。
    if (boundary < 0) return;
    const restored: Steer[] = buildTurns(messages.slice(boundary + 1).filter((m) => m.role === "user" && m.display_kind === "steer"))
      .flatMap((turn) => turn.role === "user" && turn.steer
        ? [{ text: turn.text, files: turn.files, uploads: turn.uploads, status: "sent" as const }]
        : []);
    if (!restored.length) return;
    run.steers = restored;
    this.changed(run);
  }

  get(sessionId: string): LiveRun | undefined {
    return this.runs.get(sessionId);
  }

  isActive(sessionId: string): boolean {
    const r = this.runs.get(sessionId);
    return !!r && !r.locallyReleased && (r.status === "starting" || r.status === "running");
  }

  /** 这个 runId 已经收尾并 clear。不同 runId 仍可发现。 */
  isRetired(sessionId: string, runId: string): boolean {
    return !!runId && (this.retiredIds.get(sessionId)?.includes(runId) ?? false);
  }

  private rememberRetired(sessionId: string, runId: string): void {
    if (!runId) return;
    const at = this.retiredSessions.indexOf(sessionId);
    if (at >= 0) this.retiredSessions.splice(at, 1);
    this.retiredSessions.push(sessionId);
    const list = this.retiredIds.get(sessionId) ?? [];
    const seen = list.indexOf(runId);
    if (seen >= 0) list.splice(seen, 1);
    list.push(runId);
    while (list.length > RETIRED_PER_SESSION) list.shift();
    this.retiredIds.set(sessionId, list);
    while (this.retiredSessions.length > RETIRED_SESSIONS) {
      const drop = this.retiredSessions.shift();
      if (drop) this.retiredIds.delete(drop);
    }
  }

  /** 回合结束且聊天视图已从服务器重新加载历史后调用。删除后必须通知，否则界面留着旧快照，和历史各画一遍。 */
  clear(sessionId: string): void {
    if (this.isActive(sessionId)) return;
    const run = this.runs.get(sessionId);
    if (!run || !this.runs.delete(sessionId)) return;
    this.rememberRetired(sessionId, run.runId);
    this.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: sessionId }));
  }

  private changed(run: LiveRun): void {
    this.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: run.sessionId }));
  }

  /** 所有确定终态共用此入口；关订阅不等于停止服务端任务。 */
  private finish(run: LiveRun, clearStorage = true): void {
    if (isLive(run.status) || this.runs.get(run.sessionId) !== run || this.finished.has(run)) return;
    this.finished.add(run);
    run.stopping = false;
    const ctrl = this.ctrls.get(run.sessionId);
    this.ctrls.delete(run.sessionId);
    if (clearStorage) {
      remember(run.sessionId, null);
    }
    ctrl?.abort();
    this.changed(run);
    this.dispatchEvent(new CustomEvent(RUN_FINISHED, { detail: run.sessionId }));
    void this.sweepPending();
  }

  /**
   * `model`/`provider` 是该会话选定的模型。/v1/runs 不读取会话模型锁，不显式带上就会落到全局默认模型。
   * provider 为空时按模型名反查（刷新页面后只知道模型名）。
   */
  start(sessionId: string, text: string, attachments: Attachment[], content: OutgoingContent, model = "", provider = "", accepted?: { run_id: string; status: string }): void {
    if (this.isActive(sessionId)) return;
    const previous = this.runs.get(sessionId);
    const stored = loadStored()[sessionId];
    if (previous?.unverified && !previous.runId && previous.idempotencyKey) {
      parkAdmission(sessionId, previous.idempotencyKey, previous.userText, previous.startedAt);
    }
    if (stored?.idempotencyKey && !stored.runId) {
      parkAdmission(sessionId, stored.idempotencyKey, stored.userText, stored.startedAt ?? Date.now());
    }
    const oldCtrl = this.ctrls.get(sessionId);
    const idempotencyKey = crypto.randomUUID();
    const run: LiveRun = {
      sessionId,
      model,
      provider,
      runId: "",
      idempotencyKey,
      userText: text,
      attachments,
      steers: [],
      status: "starting",
      thinking: "",
      steps: [],
      text: "",
      approval: null,
      clarify: null,
      error: "",
      stopping: false,
      startedAt: Date.now(),
      lastEventAt: Date.now(),
    };
    if (accepted) {
      run.runId = accepted.run_id;
      run.status = "running";
      remember(sessionId, { runId: run.runId, userText: text, startedAt: run.startedAt });
    } else {
      remember(sessionId, { runId: "", idempotencyKey, userText: text, startedAt: run.startedAt });
    }
    this.runs.set(sessionId, run);
    const ctrl = new AbortController();
    this.ctrls.set(sessionId, ctrl);
    // 先替换再中止旧循环，避免旧的待核验收尾误发 RUN_FINISHED。
    if (oldCtrl && oldCtrl !== ctrl) oldCtrl.abort();
    this.changed(run);
    void this.consume(run, accepted ? null : content, ctrl.signal);
  }

  /** 接上其他设备发起的任务，不提交 input、不改变模型。 */
  observe(sessionId: string, runId: string, startedAt: number, stopping = false): boolean {
    if (this.isActive(sessionId)) return false;
    const held = this.runs.get(sessionId);
    if (held && held.status !== "starting" && held.status !== "running" && (!held.runId || held.runId === runId)) return false;
    this.ctrls.get(sessionId)?.abort();
    const run: LiveRun = {
      sessionId, remote: true, model: "", provider: "", runId, userText: "", attachments: [], steers: [],
      status: "running", thinking: "", steps: [], text: "", approval: null, clarify: null, error: "", stopping,
      startedAt, lastEventAt: Date.now(),
    };
    this.runs.set(sessionId, run);
    remember(sessionId, { runId, userText: "", startedAt, remote: true });
    const ctrl = new AbortController();
    this.ctrls.set(sessionId, ctrl);
    this.changed(run);
    void this.consume(run, null, ctrl.signal);
    return true;
  }

  /** 发起运行并跟随其事件流。`resume` 为真时直接接上已有的 run_id（刷新后）。 */
  private async consume(run: LiveRun, content: OutgoingContent | null, signal: AbortSignal): Promise<void> {
    let creationSubmitted = false;
    const current = () => this.runs.get(run.sessionId) === run && !this.finished.has(run);
    try {
      if (content !== null) {
        const input = typeof content === "string" ? content : [{ role: "user", content }];
        const provider = run.model ? run.provider || (await api.providerOf(run.model)) : "";
        if (!current() || signal.aborted) return;
        // 反查不出提供方时不能不带模型就发：/v1/runs 会静默改用全局默认模型。宁可不发，原话放回输入框。
        if (run.model && !provider) {
          throw new ApiError("server", 0, `暂时无法确认模型「${run.model}」的提供方，消息未发送；请稍后重试，或在模型菜单里重新选择`);
        }
        const effort = currentEffort(run.sessionId);
        const fields = await api.runModelFields(run.model, provider, effort);
        if (!current() || signal.aborted) return;
        if ("require_model_lock" in fields && fields.require_model_lock) {
          await api.lockModel(run.sessionId, fields.model, fields.provider);
          if (!current() || signal.aborted) return;
        }
        if (run.stopping) {
          // 创建请求尚未发出，可安全取消本地准备。
          run.status = "cancelled";
          return;
        }
        creationSubmitted = true;
        try {
          const started = await api.startRun({ input, session_id: run.sessionId, ...fields }, signal, run.idempotencyKey);
          if (!current()) return;
          run.runId = started.run_id;
          run.status = "running";
          remember(run.sessionId, { runId: run.runId, idempotencyKey: run.idempotencyKey, userText: run.userText, startedAt: run.startedAt });
          this.changed(run);
        } catch (error) {
          if (!current()) return;
          if (!creationSubmitted || run.runId) throw error;
          run.unverified = true;
          run.status = "starting";
          run.error = "创建结果待核验：正在按原请求标识恢复；不会重复创建或停止未知任务。";
          this.changed(run);
        }
      }
      if (!run.runId && run.unverified) await this.awaitAdmission(run, signal);
      if (current() && run.runId && isLive(run.status) && !signal.aborted) {
        await this.follow(run, signal);
      }
    } catch (e) {
      if (!current()) return;
      if (creationSubmitted && !run.runId) {
        // 网络失败不等于服务端失败。保留身份与准入锁，不冒认、不重发、不发布结束事件。
        run.unverified = true;
        run.status = "starting";
        run.error = "创建结果待核验：尚未确认运行标识，已保留请求标识；不会重复创建或停止未知任务。";
        this.changed(run);
      } else if (isLive(run.status)) {
        // 状态重查可能已确认终态并主动关流；不能把已完成/失败改成 cancelled。
        if (signal.aborted) {
          run.status = "cancelled";
        } else {
          run.status = "failed";
          run.error = e instanceof ApiError ? e.message : (e instanceof Error ? e.message : "连接中断");
        }
        run.clarify = null;
      }
    } finally {
      if (current()) {
        if (!run.unverified && isLive(run.status) && signal.aborted) {
          run.status = "cancelled";
          run.clarify = null;
        }
        this.finish(run, !run.unverified);
      }
    }
  }

  /** 订阅事件流直到回合结束；流断开但回合还在跑时按 last_seq 重连（服务端会回放漏掉的事件）。 */
  private async follow(run: LiveRun, signal: AbortSignal): Promise<void> {
    let seq = -1;
    let failures = 0;
    let subscribeWaits = 0;
    // 服务端在回合无人订阅 300s 后会丢弃事件缓冲（再订阅 404），但回合本身照跑：此后只能轮询状态直到结束。
    let streamGone = false;
    while (!signal.aborted && isLive(run.status)) {
      const openedAt = Date.now();
      if (!streamGone) {
        try {
          for await (const ev of getSse(`v1/runs/${encodeURIComponent(run.runId)}/events?last_seq=${seq}`, signal)) {
            if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
            const held = this.runs.get(run.sessionId);
            if (held && held !== run) return;
            let data: Record<string, unknown>;
            try {
              data = JSON.parse(ev.data) as Record<string, unknown>;
            } catch {
              continue;
            }
            if (run.streamGone !== false) run.streamGone = false;
            failures = 0;
            subscribeWaits = SUBSCRIBE_WAIT_ATTEMPTS;
            if (typeof data.seq === "number") seq = Math.max(seq, data.seq);
            if (!foreignIdentity(run, data)) this.acceptFollow(run);
            this.applyRunEvent(run, data);
            this.changed(run);
            if (!isLive(run.status)) return;
          }
        } catch (e) {
          if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
          // 认证过期等不可恢复的错误直接抛给上层；其余按断线处理。
          if (e instanceof ApiError && e.kind === "unauthorized") throw e;
          if (e instanceof ApiError && e.status === 404) {
            // 还没见过事件时，404 可能只是流尚未注册。先按服务端同一窗口重试，不要立刻放弃实时订阅。
            // 但丢失的执行器可以在这个窗口里已经有终态：原生缓存仍报 running 时，不能让排队的降级帧拖过对账。
            if (seq < 0 && subscribeWaits < SUBSCRIBE_WAIT_ATTEMPTS) {
              if (await this.settleLostExecutor(run, signal)) return;
              if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
              subscribeWaits += 1;
              await subscribeWait(SUBSCRIBE_WAIT_MS, signal);
              if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
              continue;
            }
            streamGone = true;
            run.streamGone = true;
            this.changed(run);
          }
        }
      }
      // 流撑过了一段时间才断（空闲被代理/浏览器掐断，长工具/长思考期间没有事件）是正常现象，不是故障：不计入失败。
      if (Date.now() - openedAt >= HEALTHY_STREAM_MS) failures = 0;
      // 流结束了却没看到终止事件：以服务端记录的状态为准。
      try {
        const st = await api.runStatus(run.runId, signal);
        if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
        const held = this.runs.get(run.sessionId);
        if (held && held !== run) return;
        // 错配或缺身份的终态不能结算，也不能改写 runId。断线本身不是 completed。
        // 内容事件仍允许缺字段；状态体必须正向匹配后才能 finish。
        if (foreignIdentity(run, st) || (terminalClaim(st) && !trustedTerminalIdentity(run, st))) {
          if (terminalClaim(st) && !trustedTerminalIdentity(run, st)) this.noteUnconfirmedTerminal(run);
          this.dropFollow(run);
          return;
        }
        if (this.acceptFollow(run)) this.changed(run);
        if (this.finishFromStatus(run, st)) return;
        await this.reconcile(run.sessionId);
        if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
        if (streamGone) {
          // 回合仍在服务器上跑，只是看不到实时事件：保持"进行中"，慢速轮询，不消耗重试次数。
          failures = 0;
          await sleep(STATUS_POLL_MS, signal);
          continue;
        }
      } catch (e) {
        if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
        if (e instanceof ApiError && e.status === 404) {
          // 网关已不保留这次运行：可能是正常完成后过了保留期（手机久放后台），也可能是网关重启丢了记录。
          // 只信任 sidecar 记下的同一次运行的终态；查不到就如实说无法确认，不把它说成失败或成功。
          let recorded: SessionRunState["run"] = null;
          try {
            recorded = (await sidecar.sessionRun(run.sessionId, signal)).run;
          } catch {
            // 查询失败同样无法确认结果，走下方明确的说明。
          }
          if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return;
          if (this.finishFromStatus(run, recorded)) return;
          run.status = "failed";
          run.approval = null;
          run.clarify = null;
          run.error = "无法确认这次运行的结果（运行记录已被清理或服务已重启），请以上方历史为准";
          return;
        }
        if (streamGone) {
          // streamGone 下服务端仍在执行：状态轮询遇网络错误不消耗重试次数，保持进行中
          await sleep(STATUS_POLL_MS, signal);
          continue;
        }
      }
      // 设备明确离线时不消耗重试次数，等 online / 回到前台再连。
      if (navigator.onLine && ++failures > MAX_RECONNECT) {
        run.status = "failed";
        run.clarify = null;
        run.error = "与服务器的连接中断，回合可能仍在服务器上继续，稍后刷新查看";
        return;
      }
      await sleep(navigator.onLine ? Math.min(1000 * 2 ** failures, MAX_BACKOFF_MS) : MAX_BACKOFF_MS, signal);
    }
  }

  /**
   * 错配 follow 退出后，按同一 session、run、startedAt 代次有界重订阅。
   * 页面切走、中止、终态或 run 被替换则停止。不采用错配正文，不清思考，不标 completed。
   */
  resubscribe(sessionId: string, runId: string, generation: number, cancel?: AbortSignal): boolean {
    const run = this.runs.get(sessionId);
    const own = this.ctrls.get(sessionId)?.signal;
    if (!run || !own || run.followDropped !== true || run.runId !== runId || run.startedAt !== generation) return false;
    if (!isLive(run.status) || own.aborted || cancel?.aborted || this.finished.has(run)) return false;
    if ((this.resubAttempts.get(run) ?? 0) >= MISMATCH_RESUB_ATTEMPTS) {
      this.noteMismatchSilence(run);
      return false;
    }
    this.resubCancel.set(run, cancel);
    if (this.resubOps.has(run)) return true;
    const requestKey = run.idempotencyKey ?? "";
    const op = this.boundedResubscribe(run, own, sessionId, runId, generation, requestKey);
    this.resubOps.set(run, op);
    void op.finally(() => {
      if (this.resubOps.get(run) === op) this.resubOps.delete(run);
    });
    return true;
  }

  private dropFollow(run: LiveRun): void {
    if (run.followDropped) return;
    run.followDropped = true;
    if (this.runs.get(run.sessionId) === run) this.changed(run);
  }

  /** 拒绝不可信终态后留在进行中，并写明原因。不改成 completed，也不把 error 清成空成功。 */
  private noteUnconfirmedTerminal(run: LiveRun): void {
    if (!isLive(run.status) || this.finished.has(run) || this.runs.get(run.sessionId) !== run) return;
    if (run.error !== UNCONFIRMED_TERMINAL) run.error = UNCONFIRMED_TERMINAL;
    this.changed(run);
  }

  private acceptFollow(run: LiveRun): boolean {
    const dropped = run.followDropped === true;
    const notice = run.error === MISMATCH_NOTICE || run.error === UNCONFIRMED_TERMINAL;
    if (!dropped && !notice) return false;
    if (dropped) run.followDropped = false;
    if (notice) run.error = "";
    return true;
  }

  private subscriptionCurrent(
    run: LiveRun,
    sessionId: string,
    runId: string,
    generation: number,
    requestKey: string,
    own: AbortSignal,
  ): boolean {
    const cancel = this.resubCancel.get(run);
    if (own.aborted || cancel?.aborted || this.finished.has(run)) return false;
    if (this.runs.get(sessionId) !== run || run.runId !== runId || run.startedAt !== generation) return false;
    if ((run.idempotencyKey ?? "") !== requestKey) return false;
    return isLive(run.status);
  }

  private async boundedResubscribe(
    run: LiveRun,
    own: AbortSignal,
    sessionId: string,
    runId: string,
    generation: number,
    requestKey: string,
  ): Promise<void> {
    while ((this.resubAttempts.get(run) ?? 0) < MISMATCH_RESUB_ATTEMPTS) {
      if (!this.subscriptionCurrent(run, sessionId, runId, generation, requestKey, own)) return;
      await subscribeWait(MISMATCH_RESUB_MS, this.resubCancel.get(run) ?? own);
      if (!this.subscriptionCurrent(run, sessionId, runId, generation, requestKey, own)) return;
      this.resubAttempts.set(run, (this.resubAttempts.get(run) ?? 0) + 1);
      await this.follow(run, own);
      if (this.runs.get(sessionId) === run && !this.finished.has(run) && !isLive(run.status)) {
        this.finish(run);
        return;
      }
      if (!this.subscriptionCurrent(run, sessionId, runId, generation, requestKey, own)) return;
      if (!run.followDropped) return;
    }
    if (!this.subscriptionCurrent(run, sessionId, runId, generation, requestKey, own)) return;
    this.noteMismatchSilence(run);
  }

  private noteMismatchSilence(run: LiveRun): void {
    if (!isLive(run.status) || this.finished.has(run) || this.runs.get(run.sessionId) !== run) return;
    if (run.error) return;
    run.error = MISMATCH_NOTICE;
    this.changed(run);
  }

  /** 只结算当前 run/session 的 completed/failed/cancelled/interrupted。无正向身份则拒绝。 */
  private finishFromStatus(
    run: LiveRun,
    payload: { run_id?: unknown; session_id?: unknown; status?: unknown; error?: unknown } | null | undefined,
  ): boolean {
    const status = terminalClaim(payload);
    if (!isLive(run.status) || !status || !trustedTerminalIdentity(run, payload)) return false;
    if (run.error === UNCONFIRMED_TERMINAL) run.error = "";
    const error = typeof payload?.error === "string" ? payload.error : undefined;
    switch (status) {
      case "completed":
        run.status = "completed";
        run.approval = null;
        run.clarify = null;
        return true;
      case "cancelled":
        run.status = "cancelled";
        run.approval = null;
        run.clarify = null;
        return true;
      case "failed":
      case "interrupted":
        run.status = "failed";
        run.approval = null;
        run.clarify = null;
        run.error = error || (status === "interrupted" ? "回合被中断" : "执行失败");
        return true;
      default:
        return false;
    }
  }

  /** /v1/runs 事件名与 chat/stream 不同，统一翻译成 apply() 认识的那套。 */
  private applyRunEvent(run: LiveRun, d: Record<string, unknown>): void {
    if (foreignIdentity(run, d) || !isLive(run.status)) return;
    // 事件流终态仍走缺字段兼容，不在这里要求 session_id。可信状态体才走 finishFromStatus。
    if (
      d.event === "run.completed" || d.event === "run.cancelled"
      || d.event === "run.failed" || d.event === "run.interrupted" || d.event === "error"
    ) {
      if (run.error === UNCONFIRMED_TERMINAL) run.error = "";
    }
    const usage = run.usage ??= createTurnUsage();
    if (typeof d.seq === "number" && Number.isInteger(d.seq) && d.seq >= 0) {
      if (d.seq <= usage.lastSeq) return;
      if ((usage.lastSeq >= 0 && d.seq !== usage.lastSeq + 1) || (usage.lastSeq < 0 && d.seq > 1)) usage.available = false;
      usage.lastSeq = d.seq;
    } else {
      usage.available = false;
    }
    if (d.event === "replay.truncated" || run.replayTruncated || run.streamGone) usage.available = false;
    switch (d.event) {
      case "message.delta":
        this.apply(run, "assistant.delta", { delta: d.delta });
        break;
      case "message.interim":
        // 回合中途的旁白（GPT/Codex 的 phase=commentary 等）：api_server_runs.py 的 _interim_cb。
        this.apply(run, "assistant.commentary", { text: d.text, already_streamed: d.already_streamed });
        break;
      case "reasoning.available":
        this.apply(run, "tool.progress", { tool_name: "_thinking", delta: d.text });
        break;
      case "tool.started":
        this.apply(run, "tool.started", {
          tool_name: d.tool,
          preview: d.preview,
          args: null,
          call_id: d.call_id ?? d.tool_call_id ?? d.id,
        });
        break;
      case "tool.completed":
        this.apply(run, d.error ? "tool.failed" : "tool.completed", {
          tool_name: d.tool,
          preview: d.preview,
          call_id: d.call_id ?? d.tool_call_id ?? d.id,
        });
        break;
      case "approval.request":
      case "approval.resolved":
      case "approval.cancelled":
      case "clarify.request":
      case "clarify.settled":
      case "run.completed":
      case "run.cancelled":
        this.apply(run, String(d.event), d);
        break;
      case "run.failed":
        this.apply(run, "run.failed", d);
        break;
      case "run.interrupted":
        run.status = "failed";
        run.approval = null;
        run.clarify = null;
        run.error = "回合被中断";
        break;
      case "replay.truncated":
        run.replayTruncated = true;
        this.changed(run);
        break;
      default:
        // subagent.* 等：这里不展示（子代理另有专门的面板）。
        break;
    }
    run.tokenLabel = formatTurnTokens(usage);
  }

  /** Resolve only the original admission; a missing record is not a failed create. */
  recover(sessionId: string): Promise<boolean> {
    const run = this.runs.get(sessionId);
    const signal = this.ctrls.get(sessionId)?.signal;
    if (!run?.unverified || run.runId || !run.idempotencyKey || !signal || signal.aborted) return Promise.resolve(false);
    const pending = this.recoveryOps.get(run);
    if (pending) return pending;
    const operation = this.resolveAdmission(run, signal);
    this.recoveryOps.set(run, operation);
    void operation.then(() => { if (this.recoveryOps.get(run) === operation) this.recoveryOps.delete(run); });
    return operation;
  }

  private async resolveAdmission(run: LiveRun, signal: AbortSignal): Promise<boolean> {
    const key = run.idempotencyKey!;
    const current = (): boolean => this.runs.get(run.sessionId) === run && run.unverified === true
      && !run.runId && !signal.aborted && !this.finished.has(run);
    try {
      const state = await sidecar.resolveRun(run.sessionId, key, signal);
      if (!current()) return false;
      const record = state?.run;
      if (state?.resolution?.idempotency_key !== key || state.resolution.state !== "found" || !record
          || typeof record.run_id !== "string" || !/^run_[0-9a-f]{32}$/.test(record.run_id)
          || record.session_id !== run.sessionId || !Number.isFinite(record.created_at)
          || !Number.isFinite(record.updated_at) || record.created_at < 0 || record.updated_at < record.created_at) return false;
      // Confirm native access and identity; sidecar discovery alone cannot authorize takeover.
      const status = await api.runStatus(record.run_id, signal);
      if (!current() || status?.run_id !== record.run_id || status.session_id !== run.sessionId
          || !["queued", "running", "waiting_for_approval", "stopping", "completed", "cancelled", "failed", "interrupted"].includes(status.status)) return false;
      run.runId = record.run_id;
      run.unverified = false;
      run.locallyReleased = false;
      run.error = "";
      run.status = "running";
      remember(run.sessionId, { runId: run.runId, idempotencyKey: key, userText: run.userText, startedAt: run.startedAt });
      forgetPending(run.sessionId, key);
      if (this.finishFromStatus(run, status)) this.finish(run);
      else this.changed(run);
      return true;
    } catch (error) {
      if (current()) {
        const message = error instanceof ApiError && error.kind === "unauthorized"
          ? "创建结果待核验：登录已失效，请重新登录后恢复。"
          : "创建结果待核验：恢复查询暂不可用，将在网络恢复后重试；不会重复创建或停止未知任务。";
        if (run.error !== message) { run.error = message; this.changed(run); }
      }
      return false;
    }
  }

  private async awaitAdmission(run: LiveRun, signal: AbortSignal): Promise<void> {
    while (!signal.aborted && this.runs.get(run.sessionId) === run && run.unverified && !run.runId) {
      await this.recover(run.sessionId);
      if (!signal.aborted && this.runs.get(run.sessionId) === run && run.unverified && !run.runId) await sleep(STATUS_POLL_MS, signal);
    }
  }

  /** 页面（重新）打开后，把上次还在跑的回合重新接上。 */
  resumeAll(): string[] {
    const resumed: string[] = [];
    for (const [sessionId, stored] of Object.entries(loadStored())) {
      if (this.runs.has(sessionId)) continue;
      const run: LiveRun = {
        sessionId,
        model: "",
        provider: "",
        runId: stored.runId,
        idempotencyKey: stored.idempotencyKey,
        remote: stored.remote,
        userText: stored.userText,
        attachments: [],
        steers: [],
        status: stored.runId ? "running" : "starting",
        thinking: "",
        steps: [],
        text: "",
        approval: null,
        clarify: null,
        error: "",
        stopping: false,
        // 刷新后接回：开始时间沿用刷新前记下的；最近事件时间从现在算起（接上后服务端会回放漏掉的事件，随即刷新它）。
        startedAt: stored.startedAt ?? Date.now(),
        lastEventAt: Date.now(),
      };
      this.runs.set(sessionId, run);
      if (!run.remote) this.resumedLocal.add(run);
      const ctrl = new AbortController();
      this.ctrls.set(sessionId, ctrl);
      this.changed(run);
      if (run.runId) {
        void this.consume(run, null, ctrl.signal);
      } else {
        // 只有请求标识、没有可验证 run_id：保留非终态，不猜测其他任务的身份。
        // 用户已经明确放弃等待的标识只留在待恢复列表里，刷新不能再占住输入。
        run.unverified = true;
        run.status = "starting";
        if (loadPending().some((item) => item.sessionId === sessionId && item.idempotencyKey === stored.idempotencyKey)) {
          run.locallyReleased = true;
        }
        run.error = "创建结果待核验：尚未确认运行标识，已保留请求标识；不会重复创建或停止未知任务。";
        this.changed(run);
      }
      resumed.push(sessionId);
      if (!run.runId) void this.consume(run, null, ctrl.signal);
    }
    void this.sweepPending();
    return resumed;
  }

  private apply(run: LiveRun, event: string, d: Record<string, unknown>): void {
    run.lastEventAt = Date.now();
    applyTurnUsage(run.usage ??= createTurnUsage(), event, d);
    switch (event) {
      case "run.started":
      case "message.started":
        if (isLive(run.status)) run.status = "running";
        break;
      case "assistant.delta":
        run.text += typeof d.delta === "string" ? d.delta : "";
        break;
      case "assistant.commentary": {
        const text = typeof d.text === "string" ? d.text : "";
        if (text.trim()) {
          run.steps = [...run.steps, { kind: "note", text } satisfies NoteStep];
          // 已随 delta 流出的旁白不应留在正文里；切掉后只剩空白（旁白前的换行）也一并清掉。
          if (d.already_streamed && run.text.endsWith(text)) run.text = run.text.slice(0, -text.length);
          if (!run.text.trim()) run.text = "";
        }
        break;
      }
      case "tool.progress":
        if (d.tool_name === "_thinking" && typeof d.delta === "string") {
          run.thinking = d.delta;
        }
        break;
      case "tool.started": {
        // 保留原文与插话锚点；文本格式兼容只在展示层执行。
        let nextSteps = run.steps;
        if (run.text.trim()) {
          nextSteps = [...nextSteps, { kind: "note", text: run.text }];
        }
        run.text = "";
        const args = d.args && typeof d.args === "object" ? (d.args as Record<string, unknown>) : null;
        const callId = typeof (d.call_id ?? d.tool_call_id ?? d.id) === "string" ? String(d.call_id ?? d.tool_call_id ?? d.id) : undefined;
        const newStep: ToolStep = {
          kind: "tool",
          id: callId || `live-${nextSteps.length}`,
          name: String(d.tool_name ?? "tool"),
          args,
          preview: typeof d.preview === "string" ? d.preview : null,
          status: "running",
          output: null,
        };
        run.steps = [...nextSteps, newStep];
        break;
      }
      case "tool.completed":
      case "tool.failed": {
        const callId = typeof (d.call_id ?? d.tool_call_id ?? d.id) === "string" ? String(d.call_id ?? d.tool_call_id ?? d.id) : undefined;
        const name = d.tool_name ?? d.tool;
        // 网关的工具事件目前不带调用 id，按名称匹配最早一个运行中的同名步骤。这是精确的：Hermes 并行执行
        // 一批工具时，开始与完成事件都按调用顺序发出（agent/tool_executor.py 的启动顺序闸门与
        // _append_batch_results），被拦截的调用两种事件都不发。将来带上 id 时优先按 id 匹配。
        // 不能按 preview 匹配：开始事件的 preview 是参数摘要，完成事件的是结果摘要，偶然相等会配错。
        let stepIndex = callId
          ? run.steps.findIndex((s): s is ToolStep => s.kind === "tool" && s.status === "running" && s.id === callId)
          : -1;
        if (stepIndex < 0) {
          stepIndex = run.steps.findIndex((s): s is ToolStep => s.kind === "tool" && s.status === "running" && s.name === name);
        }
        if (stepIndex >= 0) {
          const oldStep = run.steps[stepIndex] as ToolStep;
          const status = event === "tool.failed" ? "failed" : "done";
          let output = oldStep.output;
          let outputPartial = oldStep.outputPartial;
          // /v1/runs 的完成事件带结果预览（已脱敏、≤500 字）：执行中就能看到输出。
          if (typeof d.preview === "string" && d.preview) {
            output = d.preview;
            outputPartial = d.preview.length >= PREVIEW_MAX_CHARS - 3;
          }
          const updatedStep: ToolStep = {
            ...oldStep,
            status,
            output,
            ...(outputPartial !== undefined ? { outputPartial } : {}),
          };
          run.steps = run.steps.map((s, idx) => (idx === stepIndex ? updatedStep : s));
        }
        break;
      }
      case "approval.request": {
        const requestId = typeof d.prompt_id === "string" && d.prompt_id
          ? d.prompt_id
          : typeof d.request_id === "string" && d.request_id
            ? d.request_id
            : undefined;
        const generation = typeof d.execution_generation === "number" && Number.isInteger(d.execution_generation)
          ? d.execution_generation
          : undefined;
        run.approval = {
          command: typeof d.command === "string" ? d.command : "",
          description: typeof d.description === "string" ? d.description : "",
          choices: (Array.isArray(d.choices) ? d.choices : ["once", "deny"]) as ApprovalChoice[],
          ...(requestId ? { requestId } : {}),
          ...(generation !== undefined ? { executionGeneration: generation } : {}),
          pending: false,
        };
        break;
      }
      case "approval.resolved":
      case "approval.cancelled":
        run.approval = null;
        break;
      case "clarify.request": {
        const next = parseClarifyEvent(d);
        if (!next) break;
        if (run.clarify && clarifyIdentity(run.clarify) === clarifyIdentity(next)) break;
        run.clarify = next;
        break;
      }
      case "clarify.settled": {
        const id = typeof d.prompt_id === "string" ? d.prompt_id : typeof d.request_id === "string" ? d.request_id : "";
        if (!run.clarify) break;
        if (!id) {
          run.clarify = null;
          break;
        }
        const rest = run.clarify.questions.filter((q) => q.requestId !== id);
        run.clarify = rest.length ? { ...run.clarify, questions: rest, pending: false, error: "" } : null;
        break;
      }
      case "run.completed":
        run.status = "completed";
        run.approval = null;
        run.clarify = null;
        break;
      case "run.cancelled":
        run.status = "cancelled";
        run.approval = null;
        run.clarify = null;
        break;
      case "run.failed":
        run.status = "failed";
        run.approval = null;
        run.clarify = null;
        run.error = typeof d.error === "string" ? d.error : "执行失败";
        break;
      case "error":
        run.status = "failed";
        run.approval = null;
        run.clarify = null;
        run.error = typeof d.message === "string" ? d.message : "执行失败";
        break;
    }
  }

  async respondApproval(sessionId: string, choice: ApprovalChoice): Promise<void> {
    const run = this.runs.get(sessionId);
    const approval = run?.approval;
    if (!run || !approval || !run.runId || approval.pending) return;
    if (!approval.choices.includes(choice)) return;
    const pendingApproval: ApprovalRequest = { ...approval, pending: true };
    run.approval = pendingApproval;
    this.changed(run);
    let requestId = approval.requestId;
    let generation = approval.executionGeneration;
    const usableId = (id: unknown): id is string => typeof id === "string" && id.length > 0 && id.length <= 256;
    const usableGeneration = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value);
    if (!usableId(requestId) || !usableGeneration(generation)) {
      try {
        const status = await api.runStatus(run.runId) as {
          run_id?: unknown;
          session_id?: unknown;
          execution_generation?: unknown;
          pending_controls?: unknown;
        };
        if (this.runs.get(sessionId) !== run || run.approval !== pendingApproval || this.finished.has(run)) return;
        if (foreignIdentity(run, status)) return;
        const controls = Array.isArray(status.pending_controls) ? status.pending_controls : [];
        const prompts = controls.filter((item): item is { prompt_id: string; execution_generation?: unknown } =>
          Boolean(item) && typeof item === "object" && (item as { kind?: unknown }).kind === "approval"
          && usableId((item as { prompt_id?: unknown }).prompt_id));
        if (prompts.length === 1) {
          if (!usableId(requestId)) requestId = prompts[0].prompt_id;
          if (!usableGeneration(generation)) {
            generation = usableGeneration(prompts[0].execution_generation)
              ? prompts[0].execution_generation
              : usableGeneration(status.execution_generation)
                ? status.execution_generation
                : generation;
          }
        }
      } catch {
        // 查不到身份不能当成已经同意。
      }
    }
    if (!usableId(requestId) || !usableGeneration(generation)) {
      if (this.runs.get(sessionId) !== run || run.approval !== pendingApproval || this.finished.has(run)) return;
      run.approval = { ...pendingApproval, pending: false };
      run.error = usableId(requestId)
        ? "审批提交失败：缺少可核验的审批代次，未提交"
        : "审批提交失败：缺少可核验的审批身份，未提交";
      this.changed(run);
      return;
    }
    try {
      await api.approve(run.runId, choice, requestId, generation);
      if (run.approval === pendingApproval) run.approval = null;
      if (this.runs.get(sessionId) === run && run.error.startsWith("审批提交失败")) run.error = "";
    } catch (e) {
      if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
      if (e instanceof ApiError && e.status === 409) {
        // 409：审批已过期、已被处理或流已清扫。撤销卡片并重查服务端真实状态。
        if (run.approval === pendingApproval) run.approval = null;
        run.error = "";
        try {
          const st = await api.runStatus(run.runId);
          if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
          if (this.finishFromStatus(run, st)) this.finish(run);
          else if (terminalClaim(st) && !trustedTerminalIdentity(run, st)) this.noteUnconfirmedTerminal(run);
        } catch (err) {
          if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
          if (err instanceof ApiError && err.status === 404) {
            run.status = "failed";
            run.approval = null;
            run.clarify = null;
            run.error = "运行记录已不存在（可能被中断或服务已重启）";
            this.finish(run);
          }
        }
      } else {
        if (run.approval === pendingApproval) {
          run.approval = { ...pendingApproval, pending: false };
        }
        run.error = e instanceof ApiError ? `审批提交失败：${e.message}` : "审批提交失败";
      }
    }
    this.changed(run);
  }

  async respondClarify(sessionId: string, answers: ClarifyAnswer[]): Promise<void> {
    const run = this.runs.get(sessionId);
    const prompt = run?.clarify;
    if (!run || !prompt || !run.runId || prompt.pending || !answers.length) return;
    const expected = new Set(prompt.questions.map((q) => q.requestId));
    if (answers.length !== expected.size || answers.some((item) => !expected.has(item.requestId) || !item.answer || item.answer.length > CLARIFY_ANSWER_MAX)) return;
    const snapshot: ClarifyPrompt = { ...prompt, pending: true, error: "" };
    run.clarify = snapshot;
    this.changed(run);
    try {
      for (const item of answers) {
        if (this.runs.get(sessionId) !== run || run.clarify !== snapshot) return;
        await api.clarify(run.runId, {
          request_id: item.requestId,
          execution_generation: snapshot.generation,
          answer: item.answer,
        });
      }
      if (run.clarify === snapshot) run.clarify = null;
    } catch (e) {
      if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
      if (e instanceof ApiError && e.status === 409) {
        if (run.clarify === snapshot) run.clarify = null;
        try {
          const st = await api.runStatus(run.runId);
          if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
          if (this.finishFromStatus(run, st)) this.finish(run);
          else if (terminalClaim(st) && !trustedTerminalIdentity(run, st)) this.noteUnconfirmedTerminal(run);
        } catch (err) {
          if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
          if (err instanceof ApiError && err.status === 404) {
            run.status = "failed";
            run.approval = null;
            run.clarify = null;
            run.error = "运行记录已不存在（可能被中断或服务已重启）";
            this.finish(run);
          }
        }
      } else if (run.clarify === snapshot) {
        run.clarify = {
          ...snapshot,
          pending: false,
          error: e instanceof ApiError ? `回答提交失败：${e.message}` : "回答提交失败",
        };
      }
    }
    this.changed(run);
  }

  /** 运行中追加指令。服务端只在回合执行中接受（工具执行、模型思考期间均可）。 */
  async steer(sessionId: string, text: string, attachments: Attachment[] = []): Promise<boolean> {
    const run = this.runs.get(sessionId);
    if (!run || run.status !== "running" || !run.runId || run.stopping) return false;
    const entry: Steer = {
      text,
      files: attachments.flatMap((a) => (a.kind === "text" ? [{ name: a.name, text: a.text }] : [])),
      uploads: attachments.flatMap((a) => (a.kind === "file" ? [{ name: a.name, absPath: a.absPath, size: formatBytes(a.bytes), ...(a.image ? { image: true } : {}) }] : [])),
      status: "sending",
      anchor: { step: run.steps.length, offset: run.text.length },
    };
    run.steers = [...run.steers, entry];
    this.changed(run);
    let sent = false;
    try {
      await api.steer(run.runId, buildSteerText(text, attachments));
      sent = true;
    } catch {
      sent = false;
    }
    const steerIndex = run.steers.indexOf(entry);
    if (steerIndex >= 0) {
      const updatedEntry: Steer = { ...entry, status: sent ? "sent" : "failed" };
      run.steers = run.steers.map((s, idx) => (idx === steerIndex ? updatedEntry : s));
    }
    this.changed(run);
    return sent;
  }

  /**
   * 事件流 404 且尚未见过事件时，原生状态仍可能停在 running。
   * 状态体必须带当前 run_id 与 session_id 才能结算。查不到则只接受 sidecar 上同一次运行、会话空闲的终态。
   * 缺身份不是 completed。
   */
  private async settleLostExecutor(run: LiveRun, signal: AbortSignal): Promise<boolean> {
    if (signal.aborted || !isLive(run.status) || this.finished.has(run) || this.runs.get(run.sessionId) !== run) return false;
    try {
      const st = await api.runStatus(run.runId, signal);
      if (signal.aborted || !isLive(run.status) || this.finished.has(run) || this.runs.get(run.sessionId) !== run) return false;
      if (this.finishFromStatus(run, st)) {
        this.finish(run);
        return true;
      }
      if (terminalClaim(st) && !trustedTerminalIdentity(run, st)) this.noteUnconfirmedTerminal(run);
    } catch {
      // 状态体缺失不是终态。下面只接受同一次运行的 sidecar 终态。
    }
    if (signal.aborted || !isLive(run.status) || this.finished.has(run)) return false;
    try {
      await this.reconcile(run.sessionId);
    } catch {
      return false;
    }
    return !isLive(run.status) || this.finished.has(run);
  }

  /** Exact-run, idle-session evidence may settle a stale native cache; never infer completion from silence. */
  async reconcile(sessionId: string, state?: SessionRunState): Promise<void> {
    const run = this.runs.get(sessionId);
    if (!run || !isLive(run.status) || !run.runId || this.finished.has(run)) return;
    const current = () => this.runs.get(sessionId) === run && isLive(run.status) && !this.finished.has(run);
    const observed = state ?? await sidecar.sessionRun(sessionId);
    if (!current() || observed.busy !== false) return;
    const record = observed.run;
    if (!record || record.session_id !== sessionId || !trustedTerminalIdentity(run, record)) return;
    if (this.finishFromStatus(run, record)) this.finish(run);
  }

  /** 放弃等待未知创建结果：不向服务端发停止，不发结束事件，不丢请求标识。 */
  private releaseUnverified(run: LiveRun): void {
    if (run.runId || !run.unverified) return;
    if (run.idempotencyKey) parkAdmission(run.sessionId, run.idempotencyKey, run.userText, run.startedAt);
    run.locallyReleased = true;
    run.stopping = false;
    this.changed(run);
  }

  private async sweepPending(): Promise<void> {
    if (this.sweeping) {
      this.sweepAgain = true;
      return;
    }
    this.sweeping = true;
    try {
      do {
        this.sweepAgain = false;
        for (const item of loadPending()) {
          try {
            await this.settlePending(item);
          } catch {
            // 查询失败不能放开仍在核验的锁，也不能对未知任务发停止。
          }
        }
      } while (this.sweepAgain);
    } finally {
      this.sweeping = false;
    }
  }

  private async settlePending(item: PendingAdmission): Promise<void> {
    const blocked = (): boolean => {
      const run = this.runs.get(item.sessionId);
      return !!run && this.isActive(item.sessionId) && run.idempotencyKey !== item.idempotencyKey;
    };
    const sameAdmission = (): boolean => {
      const run = this.runs.get(item.sessionId);
      return !!run?.unverified && !run.runId && run.idempotencyKey === item.idempotencyKey;
    };
    if (sameAdmission()) {
      if (await this.recover(item.sessionId)) forgetPending(item.sessionId, item.idempotencyKey);
      return;
    }
    let state: Awaited<ReturnType<typeof sidecar.resolveRun>> | null = null;
    try {
      state = await sidecar.resolveRun(item.sessionId, item.idempotencyKey);
    } catch {
      return;
    }
    if (blocked()) return;
    if (sameAdmission()) {
      if (await this.recover(item.sessionId)) forgetPending(item.sessionId, item.idempotencyKey);
      return;
    }
    const record = admissionRecord(item.sessionId, item.idempotencyKey, state);
    if (!record) return;
    if (blocked()) return;
    let status: Awaited<ReturnType<typeof api.runStatus>> | null = null;
    try {
      status = await api.runStatus(record.run_id);
    } catch {
      return;
    }
    if (blocked() || sameAdmission()) {
      if (sameAdmission() && await this.recover(item.sessionId)) forgetPending(item.sessionId, item.idempotencyKey);
      return;
    }
    if (!status || status.run_id !== record.run_id || status.session_id !== item.sessionId || !NATIVE_STATUSES.includes(status.status)) return;
    if (!LIVE_NATIVE.includes(status.status)) {
      if (!this.isActive(item.sessionId)) forgetPending(item.sessionId, item.idempotencyKey);
      return;
    }
    if (this.isActive(item.sessionId)) return;
    const startedAt = record.created_at > 1e12 ? record.created_at : record.created_at * 1000;
    if (this.observe(item.sessionId, record.run_id, startedAt, status.status === "stopping")) {
      forgetPending(item.sessionId, item.idempotencyKey);
    }
  }

  async stop(sessionId: string): Promise<void> {
    const run = this.runs.get(sessionId);
    if (!run || !this.isActive(sessionId)) return;
    const existingOp = this.stoppingOps.get(run);
    if (existingOp) return existingOp;

    const op = (async () => {
      run.stopping = true;
      this.changed(run);
      if (!run.runId) {
        if (run.unverified) {
          this.releaseUnverified(run);
          return;
        }
        // 发起请求已在进行中：记录停止意图，等待 run_id 产生后再调用 stop
        await new Promise<void>((resolve, reject) => {
          const timeout = setTimeout(() => {
            cleanup();
            reject(new Error("等待任务启动超时，停止未完成"));
          }, 30000);
          const cleanup = () => {
            clearTimeout(timeout);
            this.removeEventListener(RUN_CHANGED, check);
            this.removeEventListener(RUN_FINISHED, check);
          };
          const check = () => {
            if (!this.isActive(sessionId) || this.runs.get(sessionId) !== run) {
              cleanup();
              resolve();
              return;
            }
            if (run.unverified) {
              cleanup();
              this.releaseUnverified(run);
              resolve();
              return;
            }
            if (run.runId) {
              cleanup();
              this.stoppingOps.delete(run);
              this.stop(sessionId).then(resolve, reject);
            }
          };
          this.addEventListener(RUN_CHANGED, check);
          this.addEventListener(RUN_FINISHED, check);
        });
        return;
      }
      try {
        const result = await api.stopRun(run.runId);
        if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
        if (result && typeof result === "object" && "status" in result && typeof result.status === "string") {
          const body = result as { run_id?: unknown; session_id?: unknown; status?: unknown; error?: unknown };
          if (foreignIdentity(run, body)) {
            run.stopping = false;
            if (terminalClaim(body)) this.noteUnconfirmedTerminal(run);
            else this.changed(run);
            return;
          }
          const state = await sidecar.sessionRun(sessionId);
          if (this.runs.get(sessionId) !== run || this.finished.has(run)) return;
          // An API observer can be cancelled while its CLI owner still holds the turn.
          if (state.busy === false && this.finishFromStatus(run, body)) {
            this.finish(run);
            return;
          }
          // 无身份的终态字符串不能结算。busy===false 时只接受 sidecar 上同一次运行的终态；否则清 stopping，避免假 busy。
          if (state.busy === false && terminalClaim(body) && !trustedTerminalIdentity(run, body)) {
            await this.reconcile(sessionId, state);
            if (this.runs.get(sessionId) !== run || this.finished.has(run) || !isLive(run.status)) return;
            run.stopping = false;
            this.noteUnconfirmedTerminal(run);
          }
        }
      } catch (e) {
        let settledByReconcile = false;
        if (e instanceof ApiError && e.status === 409 && e.code === "run_not_active") {
          try {
            await this.reconcile(sessionId);
            if (this.runs.get(sessionId) !== run || this.finished.has(run) || !isLive(run.status)) {
              settledByReconcile = true;
            }
          } catch (lookupError) {
            // Preserve the original stop error; unavailable evidence must not end a live run.
            console.warn("停止后的任务状态核验失败", lookupError);
          }
        }
        if (settledByReconcile) {
          // 409 且通过核验确认任务已不在活动/已终态：按成功核验收尾，不抛错
          return;
        }
        if (this.runs.get(sessionId) === run && !this.finished.has(run) && isLive(run.status)) {
          run.stopping = false;
          run.error = e instanceof ApiError ? `停止失败：${e.message}` : "停止失败，请重试";
          this.changed(run);
        }
        // 如果是 409 conflict（目标非活动或状态不匹配），属于业务既定冲突，已在 LiveRun 设置 error 说明停止失败，不产生未处理异常；
        // 其他非 409 异常（如服务端拒绝、500 等）显式抛出供调用方捕获。
        if (e instanceof ApiError && e.status === 409) {
          return;
        }
        throw e;
      }
    })();

    this.stoppingOps.set(run, op);
    try {
      await op;
    } finally {
      this.stoppingOps.delete(run);
    }
  }
}

export const runs = new RunStore();
