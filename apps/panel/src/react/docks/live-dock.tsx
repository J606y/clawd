import { useEffect, useLayoutEffect, useRef, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";
import { api } from "@/lib/api";
import { RUN_CHANGED, runs } from "@/lib/runs";
import { sidecar, type LiveState } from "@/lib/sidecar";
import { useUiStore } from "@/stores/ui";
import { clearRfbModule, loadRfb, websockifyUrl, type Rfb } from "./rfb";
import { ConfirmDialog } from "./sheet-presence";
import "./docks.css";

const MOBILE = "(max-width: 768px)";
const STATE_POLL_MS = 3000;
const HEARTBEAT_MS = 15000;
const XK_BACKSPACE = 0xff08;
const XK_RETURN = 0xff0d;
const MAX_ZOOM = 5;

type Link = "loading" | "connecting" | "connected" | "disconnected" | "failed";
type Dialog = null | "stop" | "takeover";

interface LiveScope {
  generation: number;
  sid: string;
  owner: string;
}

interface LiveEls {
  root: () => HTMLElement | null;
  stage: () => HTMLElement | null;
  screen: () => HTMLElement | null;
  input: () => HTMLTextAreaElement | null;
}

function hostOf(url: string | undefined): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.origin : (url.split(":")[0] ?? "");
  } catch {
    return "";
  }
}

function terminalStatus(status: string): boolean {
  return status === "completed" || status === "cancelled" || status === "failed";
}

async function bounded<T>(job: (signal: AbortSignal) => Promise<T>, ms = 8000): Promise<T> {
  const ctrl = new AbortController();
  let timer = 0;
  try {
    return await Promise.race([
      job(ctrl.signal),
      new Promise<never>((_, reject) => {
        timer = window.setTimeout(() => {
          ctrl.abort();
          reject(new Error("请求超时，状态尚未确认"));
        }, ms);
      }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
}

function combineSignals(signals: AbortSignal[]): AbortSignal {
  const ctrl = new AbortController();
  const abort = () => ctrl.abort();
  for (const signal of signals) {
    if (signal.aborted) {
      ctrl.abort();
      return ctrl.signal;
    }
    signal.addEventListener("abort", abort, { once: true });
  }
  return ctrl.signal;
}

function inputData(event: InputEvent): string {
  if (event.data) return event.data;
  const withTransfer = event as InputEvent & { dataTransfer?: DataTransfer | null };
  return withTransfer.dataTransfer?.getData("text/plain") ?? "";
}

/** 租约和连接只活在这个实例。clientId 不写入 storage，也不进 Query。 */
class LiveController {
  sessionId = "";
  sessionTitle = "";
  link: Link = "loading";
  control = false;
  mobile = false;
  live: LiveState | null = null;
  dialog: Dialog = null;
  busy = false;
  kbd = false;
  notice = "";
  stateFresh = false;
  closed = false;

  generation = 0;
  private clientId = crypto.randomUUID();
  private leaseOwned = false;
  private leaseRevision = 0;
  private connectionGeneration = 0;
  private boundSessionId: string | null = null;
  private readonly unreleasedOwners = new Set<string>();
  private dialogRunId = "";
  private taskStopped = false;
  private stoppedRunId = "";
  private rfb: Rfb | null = null;
  private pollTimer = 0;
  private beatTimer = 0;
  private heartbeatGeneration: number | null = null;
  private pollCtrl: AbortController | null = null;
  private typing: Promise<unknown> = Promise.resolve();
  private composing = false;
  private compositionScope: LiveScope | null = null;
  private compositionCommit: { text: string; expires: number } | null = null;
  private pasted: { text: string; expires: number } | null = null;
  private tapY = 0;
  private zoom = { s: 1, x: 0, y: 0 };
  private base = { w: 0, h: 0 };
  private pointers = new Map<number, { x: number; y: number }>();
  private pinch: { d: number; s: number; mx: number; my: number; x: number; y: number } | null = null;
  private lastTap = 0;
  private readonly mql = window.matchMedia(MOBILE);
  private readonly resize = new ResizeObserver(() => this.layout());
  private els: LiveEls | null = null;
  private mounted = false;
  private silent = false;
  private version = 0;
  private readonly listeners = new Set<() => void>();

  constructor() {
    this.mobile = this.mql.matches;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = (): number => this.version;

  bind(els: LiveEls): void {
    this.els = els;
  }

  private emit(): void {
    if (this.silent) return;
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  private scope(): LiveScope {
    return { generation: this.generation, sid: this.sessionId, owner: this.clientId };
  }

  private current(scope: LiveScope): boolean {
    return this.mounted && !this.closed && scope.generation === this.generation && scope.sid === this.sessionId && scope.owner === this.clientId;
  }

  private input(): HTMLTextAreaElement | null {
    return this.els?.input() ?? null;
  }

  private stage(): HTMLElement | null {
    return this.els?.stage() ?? null;
  }

  private screen(): HTMLElement | null {
    return this.els?.screen() ?? null;
  }

  get running(): boolean {
    return !!this.sessionId && runs.isActive(this.sessionId) && !(this.taskStopped && runs.get(this.sessionId)?.runId === this.stoppedRunId);
  }

  get takenByOther(): boolean {
    return !!this.live?.takenOver && !this.live.controlledByYou && this.unreleasedOwners.size === 0;
  }

  get canTake(): boolean {
    return this.link === "connected" && this.stateFresh && !this.takenByOther && !this.busy && !this.closed && !!this.sessionId && this.unreleasedOwners.size === 0;
  }

  get needsRelease(): boolean {
    return this.control || this.unreleasedOwners.size > 0;
  }

  get releaseLabel(): string {
    return this.control ? "交还控制" : "重试交还控制";
  }

  subtitle(): string {
    const host = hostOf(this.live?.url);
    if (!this.stateFresh) return `状态不可用${host ? ` · 最近页面 ${host}（旧状态）` : ""}`;
    return [
      this.unreleasedOwners.size ? "控制权交还未确认" : this.takenByOther ? "其他窗口正在控制" : this.control ? "由你控制" : "旁观",
      this.taskStopped ? "任务已停止，不会自动恢复" : "",
      this.live?.running ? host || this.live.title || "浏览器运行中" : "浏览器未运行",
    ].filter(Boolean).join(" · ");
  }

  heading(): string {
    if (this.control) return "完全由你掌控";
    if (this.running) return this.sessionTitle || "clawd 正在执行";
    return "实时画面";
  }

  stopping(): boolean {
    return !!runs.get(this.sessionId)?.stopping;
  }

  private revoke(): string | null {
    const owner = this.leaseOwned || this.busy ? this.clientId : null;
    this.generation += 1;
    this.clientId = crypto.randomUUID();
    window.clearTimeout(this.beatTimer);
    window.clearTimeout(this.pollTimer);
    this.pollCtrl?.abort();
    this.pollCtrl = null;
    this.control = false;
    this.leaseOwned = false;
    this.busy = false;
    this.dialog = null;
    this.dialogRunId = "";
    this.typing = Promise.resolve();
    this.composing = false;
    this.compositionScope = null;
    this.compositionCommit = null;
    this.pasted = null;
    const field = this.input();
    field?.blur();
    if (field) field.value = "";
    this.kbd = false;
    this.applyMode();
    this.emit();
    return owner;
  }

  private reportRelease(owner: string, error: unknown, scope: LiveScope): void {
    this.unreleasedOwners.add(owner);
    const text = `交还控制尚未确认：${error instanceof Error ? error.message : "服务不可用"}。已禁用输入；请重试，或等待租约到期。`;
    if (this.current(scope)) {
      this.notice = text;
      this.emit();
    } else {
      console.warn(text);
    }
  }

  private async releaseOwner(owner: string, keepalive = false, scope = this.scope()): Promise<boolean> {
    this.unreleasedOwners.add(owner);
    try {
      const result = await bounded(() => sidecar.liveRelease(owner, keepalive));
      if (!result.ok) throw new Error("服务器未确认释放");
      const state = await bounded((signal) => sidecar.liveState(signal, owner));
      if (state.controlledByYou) throw new Error("服务器仍报告该客户端持有控制权");
      this.unreleasedOwners.delete(owner);
      this.emit();
      return true;
    } catch (error) {
      this.reportRelease(owner, error, scope);
      return false;
    }
  }

  private releaseKnown(owner: string | null, scope: LiveScope): void {
    const owners = new Set(this.unreleasedOwners);
    if (owner) owners.add(owner);
    for (const id of owners) void this.releaseOwner(id, true, scope);
  }

  private readonly onRunChanged = (event: Event): void => {
    const detail: unknown = event instanceof CustomEvent ? event.detail : undefined;
    if (detail !== this.sessionId) return;
    const run = runs.get(this.sessionId);
    const stoppedRun = this.taskStopped && run?.runId === this.stoppedRunId;
    const stoppingRun = this.busy && !!this.dialogRunId && run?.runId === this.dialogRunId;
    if (runs.isActive(this.sessionId) && !stoppedRun && !stoppingRun) {
      this.taskStopped = false;
      this.stoppedRunId = "";
      if (this.busy || this.control || this.leaseOwned) {
        const owner = this.revoke();
        this.notice = "检测到新任务执行，已禁用人工输入并退回旁观。";
        if (owner) void this.releaseOwner(owner);
        this.schedulePoll(0);
      }
    }
    this.emit();
  };

  private readonly onMql = (): void => {
    this.mobile = this.mql.matches;
    this.resetZoom();
    this.onViewport();
    this.emit();
  };

  private readonly onVisibility = (): void => {
    if (document.visibilityState !== "visible") {
      const scope = this.scope();
      const owner = this.revoke();
      this.stateFresh = false;
      this.notice = "已退回旁观；回到前台后重新读取状态。";
      this.releaseKnown(owner, scope);
      this.emit();
      return;
    }
    this.onRecovery();
  };

  private readonly onPageHide = (): void => {
    const scope = this.scope();
    const owner = this.revoke();
    this.stateFresh = false;
    this.releaseKnown(owner, scope);
  };

  private readonly onRecovery = (): void => {
    if (!this.mounted || this.closed || document.visibilityState !== "visible") return;
    const scope = this.scope();
    this.releaseKnown(this.revoke(), scope);
    this.stateFresh = false;
    this.notice = "正在重新读取状态；当前为旁观，重新接管才会开放输入。";
    void this.connect();
    this.schedulePoll(0);
    this.emit();
  };

  private readonly onViewport = (): void => {
    const root = this.els?.root() ?? null;
    const viewport = window.visualViewport;
    if (!root) return;
    if (!viewport || !this.mobile) {
      root.style.removeProperty("top");
      root.style.removeProperty("height");
      root.style.removeProperty("bottom");
      return;
    }
    root.style.top = `${viewport.offsetTop}px`;
    root.style.height = `${viewport.height}px`;
    root.style.bottom = "auto";
    this.keepTapVisible();
  };

  attach(): void {
    if (this.mounted) return;
    this.silent = false;
    this.closed = false;
    this.mounted = true;
    this.boundSessionId = this.sessionId;
    runs.addEventListener(RUN_CHANGED, this.onRunChanged);
    this.mql.addEventListener("change", this.onMql);
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("pagehide", this.onPageHide);
    window.addEventListener("pageshow", this.onRecovery);
    window.addEventListener("online", this.onRecovery);
    window.visualViewport?.addEventListener("resize", this.onViewport);
    window.visualViewport?.addEventListener("scroll", this.onViewport);
    const stage = this.stage();
    if (stage) this.resize.observe(stage);
    this.onViewport();
    void this.connect();
    this.schedulePoll(0);
  }

  detach(): void {
    if (!this.mounted && this.closed) return;
    this.silent = true;
    runs.removeEventListener(RUN_CHANGED, this.onRunChanged);
    this.mql.removeEventListener("change", this.onMql);
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("pagehide", this.onPageHide);
    window.removeEventListener("pageshow", this.onRecovery);
    window.removeEventListener("online", this.onRecovery);
    window.visualViewport?.removeEventListener("resize", this.onViewport);
    window.visualViewport?.removeEventListener("scroll", this.onViewport);
    this.resize.disconnect();
    const scope = this.scope();
    const owner = this.revoke();
    this.mounted = false;
    this.closed = true;
    this.releaseKnown(owner, scope);
    const rfb = this.rfb;
    this.rfb = null;
    this.connectionGeneration += 1;
    rfb?.disconnect();
  }

  setSession(id: string, title: string): void {
    const titleChanged = this.sessionTitle !== title;
    this.sessionTitle = title;
    if (this.sessionId === id) {
      if (titleChanged) this.emit();
      return;
    }
    const previous = this.boundSessionId;
    this.sessionId = id;
    if (!this.mounted || previous === null) {
      this.emit();
      return;
    }
    if (previous !== id) {
      const scope = this.scope();
      const owner = this.revoke();
      this.boundSessionId = id;
      this.live = null;
      this.stateFresh = false;
      this.taskStopped = false;
      this.stoppedRunId = "";
      this.notice = "";
      this.pointers.clear();
      this.pinch = null;
      this.resetZoom();
      this.releaseKnown(owner, scope);
      void this.connect();
      this.schedulePoll(0);
      this.emit();
      return;
    }
    this.boundSessionId = id;
    this.emit();
  }

  private async connect(): Promise<void> {
    const target = this.screen();
    if (!target || !this.mounted || this.closed) return;
    if (this.control || this.leaseOwned || this.busy) {
      const oldScope = this.scope();
      const owner = this.revoke();
      this.releaseKnown(owner, oldScope);
    }
    const scope = this.scope();
    const connection = ++this.connectionGeneration;
    const oldRfb = this.rfb;
    this.rfb = null;
    oldRfb?.disconnect();
    this.link = "loading";
    this.emit();
    try {
      const RfbCtor = await loadRfb();
      if (!this.current(scope) || connection !== this.connectionGeneration) return;
      this.link = "connecting";
      const rfb = new RfbCtor(target, websockifyUrl(), { shared: true });
      this.rfb = rfb;
      rfb.resizeSession = false;
      const currentConnection = () => this.mounted && !this.closed && this.sessionId === scope.sid && connection === this.connectionGeneration && this.rfb === rfb;
      rfb.addEventListener("connect", () => {
        if (!currentConnection()) return;
        this.link = "connected";
        this.applyMode();
        this.schedulePoll(0);
        this.emit();
      });
      rfb.addEventListener("disconnect", () => {
        if (!currentConnection()) return;
        const oldScope = this.scope();
        const owner = this.revoke();
        this.link = "disconnected";
        this.stateFresh = false;
        this.notice = "连接已断开，当前是旧画面；已退回旁观。";
        this.releaseKnown(owner, oldScope);
        this.schedulePoll(0);
        this.emit();
      });
      this.applyMode();
      this.emit();
    } catch (error) {
      if (!this.current(scope) || connection !== this.connectionGeneration) return;
      clearRfbModule();
      this.link = "failed";
      this.notice = `无法连接远程画面：${error instanceof Error ? error.message : "组件不可用"}`;
      this.emit();
    }
  }

  private applyMode(): void {
    const rfb = this.rfb;
    if (!rfb) return;
    rfb.viewOnly = !this.control;
    rfb.showDotCursor = this.control;
    rfb.scaleViewport = true;
    rfb.clipViewport = false;
    rfb.dragViewport = false;
    rfb.dragScrolls = this.control && this.mobile;
    this.applyZoom();
  }

  private layout(): void {
    const stage = this.stage();
    if (!stage) return;
    if (!this.kbd || !this.base.w) this.base = { w: stage.clientWidth, h: stage.clientHeight };
    this.keepTapVisible();
  }

  private schedulePoll(delay = STATE_POLL_MS): void {
    window.clearTimeout(this.pollTimer);
    if (!this.mounted || this.closed) return;
    this.pollTimer = window.setTimeout(() => void this.poll(), delay);
  }

  private async poll(): Promise<void> {
    if (!this.mounted || this.closed || document.visibilityState !== "visible" || this.pollCtrl) return;
    const scope = this.scope();
    const ctrl = new AbortController();
    const revision = this.leaseRevision;
    this.pollCtrl = ctrl;
    try {
      const state = await bounded((signal) => sidecar.liveState(combineSignals([ctrl.signal, signal]), scope.owner), 5000);
      if (!this.current(scope) || this.pollCtrl !== ctrl || revision !== this.leaseRevision) return;
      this.live = state;
      this.stateFresh = true;
      if (this.link === "connected" && /^(正在重新读取状态|连接已断开|状态不可用，)/.test(this.notice)) {
        this.notice = "连接与状态已恢复；当前为旁观，重新接管才会开放输入。";
      }
      if (!state.takenOver) this.unreleasedOwners.clear();
      if (this.control && (!state.controlledByYou || state.ownerSessionId !== scope.sid || state.leaseRemainingSeconds <= 0)) {
        const owner = this.revoke();
        this.notice = "控制权已失效或转移到其他窗口；已禁用输入。";
        if (owner) void this.releaseOwner(owner);
        this.schedulePoll();
      }
    } catch (error) {
      if (!this.current(scope) || this.pollCtrl !== ctrl || revision !== this.leaseRevision || ctrl.signal.aborted) return;
      this.stateFresh = false;
      if (this.control) {
        const owner = this.revoke();
        if (owner) void this.releaseOwner(owner);
        this.schedulePoll();
      }
      this.notice = `状态不可用，当前页面信息已过期：${error instanceof Error ? error.message : "请稍后重试"}。保持旁观。`;
    } finally {
      if (this.pollCtrl === ctrl) {
        this.pollCtrl = null;
        if (this.current(scope)) this.schedulePoll();
      }
      if (this.current(scope)) this.emit();
    }
  }

  requestTakeover(): void {
    if (this.busy || this.control || this.link !== "connected") return;
    if (this.running) {
      this.dialogRunId = runs.get(this.sessionId)?.runId ?? "";
      this.dialog = "takeover";
      this.emit();
      return;
    }
    void this.startControl(false);
  }

  private async proveIdle(stopFirst: boolean, scope = this.scope()): Promise<boolean> {
    const sid = scope.sid;
    const discovered = await bounded((signal) => sidecar.sessionRun(sid, signal));
    if (!this.current(scope)) return false;
    const run = discovered.run;
    if (run && run.session_id !== sid) throw new Error("任务所属会话不匹配，保持旁观");
    if (run && !terminalStatus(run.status)) {
      if (run.status !== "running" && run.status !== "starting") throw new Error("任务状态未知，无法安全接管");
      if (!stopFirst) {
        this.dialogRunId = run.run_id;
        this.dialog = "takeover";
        this.emit();
        return false;
      }
      if (!this.dialogRunId || run.run_id !== this.dialogRunId) throw new Error("任务已变化，请重新确认停止");
      if (runs.get(sid)?.runId === run.run_id && runs.isActive(sid)) {
        await bounded(() => runs.stop(sid));
        if (!this.current(scope)) return false;
        const local = runs.get(sid);
        if (local?.runId === run.run_id && local.error.startsWith("停止失败")) throw new Error(local.error);
      } else {
        await bounded(() => api.stopRun(run.run_id));
        if (!this.current(scope)) return false;
      }
    } else if (stopFirst && run?.run_id !== this.dialogRunId) {
      throw new Error("任务已变化，请重新确认停止");
    }
    if (run) {
      const deadline = Date.now() + 8000;
      for (;;) {
        const native = await bounded((signal) => api.runStatus(run.run_id, signal), 2000);
        if (!this.current(scope)) return false;
        if ((native.run_id && native.run_id !== run.run_id) || (native.session_id && native.session_id !== sid)) {
          throw new Error("停止结果不匹配，保持旁观");
        }
        if (terminalStatus(native.status)) break;
        if (!stopFirst || Date.now() >= deadline || (native.status !== "running" && native.status !== "starting")) {
          throw new Error("尚未确认任务停止，不能接管");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 400));
      }
    }
    const current = await bounded((signal) => sidecar.sessionRun(sid, signal));
    if (!this.current(scope)) return false;
    if (current.busy || (current.run && (!terminalStatus(current.run.status) || current.run.run_id !== run?.run_id))) {
      throw new Error("浏览器仍在执行或有新任务，保持旁观");
    }
    if (stopFirst) {
      this.taskStopped = true;
      this.stoppedRunId = run?.run_id ?? "";
    }
    return true;
  }

  async startControl(stopFirst: boolean): Promise<void> {
    if (this.busy || this.control || !this.sessionId || this.link !== "connected") return;
    const expectedRun = this.dialogRunId;
    this.revoke();
    this.dialogRunId = expectedRun;
    this.dialog = null;
    this.busy = true;
    this.notice = stopFirst ? "正在停止任务并确认结果，再接管控制权…" : "正在确认任务状态与控制权…";
    this.emit();
    const scope = this.scope();
    try {
      if (!await this.proveIdle(stopFirst, scope) || !this.current(scope)) return;
      if (await this.acquire(scope) && this.current(scope)) {
        if (this.running) throw new Error("检测到新任务执行，保持旁观");
        this.control = true;
        this.notice = this.taskStopped ? "任务已停止，你已接管；交还控制不会自动恢复任务。" : "已接管浏览器；交还控制可退回旁观。";
        this.applyMode();
        this.scheduleHeartbeat(scope);
      }
    } catch (error) {
      if (this.current(scope)) {
        const owner = this.revoke();
        this.notice = error instanceof Error ? error.message : "接管失败，保持旁观";
        if (owner) void this.releaseOwner(owner, false, this.scope());
        this.schedulePoll(0);
      }
    } finally {
      if (this.current(scope)) {
        this.busy = false;
        this.schedulePoll(0);
        this.emit();
      }
    }
  }

  private async acquire(scope: LiveScope): Promise<boolean> {
    const request = sidecar.liveTakeover(scope.owner, scope.sid).then((result) => {
      if (!this.current(scope) && result.ok) void this.releaseOwner(scope.owner, true, scope);
      return result;
    });
    const result = await bounded(() => request, 6000);
    if (!this.current(scope)) return false;
    if (!result.ok) throw new Error("控制权未获确认");
    this.leaseRevision += 1;
    this.leaseOwned = true;
    const state = await bounded((signal) => sidecar.liveState(signal, scope.owner), 2000);
    if (!this.current(scope)) return false;
    if (!state.controlledByYou || state.ownerSessionId !== scope.sid || state.leaseRemainingSeconds <= 0) {
      throw new Error("控制权所属会话不匹配或租约已过期");
    }
    this.live = state;
    this.stateFresh = true;
    this.emit();
    return true;
  }

  private scheduleHeartbeat(scope: LiveScope): void {
    window.clearTimeout(this.beatTimer);
    const due = Date.now() + HEARTBEAT_MS;
    this.beatTimer = window.setTimeout(() => {
      if (!this.current(scope) || !this.control) return;
      if (Date.now() > due + 1500) {
        const owner = this.revoke();
        this.notice = "控制心跳错过，已禁用输入；请重新接管。";
        if (owner) void this.releaseOwner(owner);
        this.schedulePoll(0);
        this.emit();
        return;
      }
      void this.heartbeat(scope);
    }, HEARTBEAT_MS);
  }

  private async heartbeat(scope = this.scope()): Promise<void> {
    if (!this.current(scope) || !this.control || this.heartbeatGeneration === scope.generation) return;
    this.heartbeatGeneration = scope.generation;
    try {
      const state = await bounded((signal) => sidecar.liveState(signal, scope.owner), 2000);
      if (!this.current(scope)) return;
      if (!state.controlledByYou || state.ownerSessionId !== scope.sid || state.leaseRemainingSeconds <= 0) {
        throw new Error("租约已失效或属于其他会话");
      }
      const discovery = await bounded((signal) => sidecar.sessionRun(scope.sid, signal), 2000);
      if (!this.current(scope)) return;
      if (discovery.busy || (discovery.run && !terminalStatus(discovery.run.status))) throw new Error("任务正在执行，不能续租");
      if (await this.acquire(scope) && this.current(scope)) this.scheduleHeartbeat(scope);
    } catch (error) {
      if (!this.current(scope)) return;
      const owner = this.revoke();
      this.notice = `控制心跳失败：${error instanceof Error ? error.message : "状态未知"}。已禁用输入，保持旁观。`;
      if (owner) void this.releaseOwner(owner);
      this.schedulePoll(0);
      this.emit();
    } finally {
      if (this.heartbeatGeneration === scope.generation) this.heartbeatGeneration = null;
    }
  }

  async finish(): Promise<void> {
    const oldOwner = this.revoke();
    const scope = this.scope();
    this.busy = true;
    this.notice = "正在交还控制；已禁用人工输入…";
    this.emit();
    const owners = new Set(this.unreleasedOwners);
    if (oldOwner) owners.add(oldOwner);
    let released = true;
    for (const owner of owners) released = await this.releaseOwner(owner, false, scope) && released;
    if (!this.current(scope)) return;
    this.busy = false;
    if (released) this.notice = this.taskStopped ? "已交还控制；已停止的任务不会自动恢复。" : "已交还控制。";
    this.schedulePoll(0);
    this.emit();
  }

  requestStop(): void {
    if (this.busy || !this.running) return;
    this.dialogRunId = runs.get(this.sessionId)?.runId ?? "";
    this.dialog = "stop";
    this.emit();
  }

  async confirmStop(): Promise<void> {
    if (this.busy || !this.dialogRunId) {
      this.notice = "任务标识尚未确认，保持旁观。";
      this.dialog = null;
      this.emit();
      return;
    }
    const scope = this.scope();
    this.dialog = null;
    this.busy = true;
    this.notice = "正在停止任务并确认原生状态…";
    this.emit();
    try {
      if (await this.proveIdle(true, scope) && this.current(scope)) {
        this.notice = "任务已停止；浏览器保持打开，任务不会自动恢复。";
      }
    } catch (error) {
      if (this.current(scope)) this.notice = `停止尚未确认：${error instanceof Error ? error.message : "状态未知"}`;
    } finally {
      if (this.current(scope)) {
        this.busy = false;
        this.schedulePoll(0);
        this.emit();
      }
    }
  }

  closeDialog(): void {
    this.dialog = null;
    this.emit();
  }

  dismissNotice(): void {
    this.notice = "";
    this.emit();
  }

  close(): void {
    const root = this.els?.root() ?? null;
    const scope = this.scope();
    const owner = this.revoke();
    this.closed = true;
    this.releaseKnown(owner, scope);
    root?.dispatchEvent(new CustomEvent("hp-live-close", { bubbles: true, composed: true }));
    this.emit();
  }

  reconnect(): void {
    void this.connect();
  }

  private get inputAllowed(): boolean {
    return this.control && this.stateFresh && this.link === "connected" && this.mounted && !this.closed;
  }

  toggleKeyboard(open: boolean): void {
    const field = this.input();
    if (!field || (open && !this.inputAllowed)) return;
    if (open) field.focus();
    else field.blur();
    this.kbd = open;
    this.emit();
  }

  private enqueue(job: (scope: LiveScope, rfb: Rfb) => Promise<unknown> | void): void {
    if (!this.inputAllowed || !this.rfb) return;
    const scope = this.scope();
    const rfb = this.rfb;
    this.typing = this.typing.then(async () => {
      if (!this.current(scope) || !this.inputAllowed || this.rfb !== rfb) return;
      await job(scope, rfb);
    }).catch((error: unknown) => {
      if (!this.current(scope)) return;
      const owner = this.revoke();
      this.notice = `输入结果未确认：${error instanceof Error ? error.message : "连接中断"}。不会重发或重复按键；已退回旁观。`;
      if (owner) void this.releaseOwner(owner);
      this.schedulePoll(0);
      this.emit();
    });
  }

  private sendText(text: string): void {
    if (!text || !this.inputAllowed) return;
    const chars = Array.from(text);
    const boundedText = chars.slice(0, 2000).join("");
    if (chars.length > 2000) {
      this.notice = "文字超过 2000 字，仅输入前 2000 字；剩余内容未发送。";
      this.emit();
    }
    this.enqueue(async (scope, rfb) => {
      const result = await bounded(() => sidecar.liveType(boundedText, scope.owner), 6000);
      if (!this.current(scope) || !this.inputAllowed || this.rfb !== rfb) return;
      if (!result.ok) throw new Error("服务器未确认文本插入");
      if (result.inserted === false) this.typeKeys(boundedText, scope, rfb);
      else if (result.inserted !== true) throw new Error("服务器返回的文本结果不明确");
    });
  }

  private typeKeys(text: string, scope: LiveScope, rfb: Rfb): void {
    if (!this.current(scope) || !this.inputAllowed || this.rfb !== rfb) return;
    for (const ch of text) {
      const cp = ch.codePointAt(0) ?? 0;
      if (ch === "\n") rfb.sendKey(XK_RETURN, "Enter");
      else rfb.sendKey(cp < 0x100 ? cp : 0x1000000 + cp, null);
    }
  }

  sendKey(keysym: number, code: string): void {
    this.enqueue((scope, rfb) => {
      if (!this.current(scope)) return;
      rfb.sendKey(keysym, code);
    });
  }

  onKeyDown(event: KeyboardEvent, generation: number): void {
    if (generation !== this.generation) return;
    if (!this.composing) {
      this.compositionCommit = null;
      this.pasted = null;
      const field = this.input();
      if (field) field.value = "";
    }
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter") {
      event.preventDefault();
      this.sendKey(XK_RETURN, "Enter");
    } else if (event.key === "Backspace") {
      event.preventDefault();
      this.sendKey(XK_BACKSPACE, "Backspace");
    }
  }

  onBeforeInput(event: InputEvent, generation: number): void {
    if (generation !== this.generation) return;
    if (this.composing || event.isComposing || event.inputType === "insertCompositionText") return;
    event.preventDefault();
    const paste = this.pasted;
    this.pasted = null;
    const text = inputData(event);
    if (event.inputType === "insertFromPaste" && paste && Date.now() <= paste.expires && (!text || text === paste.text)) return;
    const commit = this.compositionCommit;
    this.compositionCommit = null;
    if (commit && Date.now() <= commit.expires && event.data === commit.text && (event.inputType === "insertText" || event.inputType === "insertReplacementText" || event.inputType === "insertFromComposition")) {
      const field = this.input();
      if (field) field.value = "";
      return;
    }
    if (event.inputType === "insertText" || event.inputType === "insertReplacementText" || event.inputType === "insertFromComposition" || event.inputType === "insertFromPaste") {
      this.sendText(text);
    } else if (event.inputType === "deleteContentBackward") this.sendKey(XK_BACKSPACE, "Backspace");
    else if (event.inputType === "insertLineBreak" || event.inputType === "insertParagraph") this.sendKey(XK_RETURN, "Enter");
  }

  onCompositionStart(generation: number): void {
    if (generation !== this.generation) return;
    this.composing = true;
    this.compositionScope = this.inputAllowed ? this.scope() : null;
    this.compositionCommit = null;
  }

  onPaste(event: ClipboardEvent, generation: number): void {
    if (generation !== this.generation) return;
    event.preventDefault();
    this.compositionScope = null;
    this.compositionCommit = null;
    this.composing = false;
    const text = event.clipboardData?.getData("text/plain") ?? "";
    this.sendText(text);
    this.pasted = text ? { text, expires: Date.now() + 250 } : null;
    const field = this.input();
    if (field) field.value = "";
  }

  onCompositionEnd(event: CompositionEvent, generation: number): void {
    if (generation !== this.generation) return;
    const scope = this.compositionScope;
    this.compositionScope = null;
    this.composing = false;
    if (!scope || !this.current(scope) || !this.inputAllowed) {
      this.compositionCommit = null;
      return;
    }
    this.sendText(event.data);
    this.compositionCommit = event.data ? { text: event.data, expires: Date.now() + 250 } : null;
  }

  onInput(generation: number): void {
    if (generation !== this.generation || this.composing) return;
    this.compositionCommit = null;
    this.pasted = null;
    const field = this.input();
    if (field) field.value = "";
  }

  onBlur(generation: number): void {
    if (generation !== this.generation) return;
    this.kbd = false;
    this.emit();
  }

  private keepTapVisible(): void {
    const stage = this.stage();
    if (stage && this.mobile && this.control && this.kbd) this.zoom.y = stage.clientHeight * 0.4 - this.tapY;
    this.applyZoom();
  }

  private resetZoom(): void {
    this.zoom = { s: 1, x: 0, y: 0 };
    this.applyZoom();
  }

  private applyZoom(): void {
    const screen = this.screen();
    const stage = this.stage();
    if (!screen || !stage) return;
    if (!this.mobile || !this.base.w) {
      screen.style.cssText = "";
      return;
    }
    const { s } = this.zoom;
    const w = this.base.w * s;
    const h = this.base.h * s;
    this.zoom.x = Math.min(0, Math.max(stage.clientWidth - w, this.zoom.x));
    this.zoom.y = Math.min(0, Math.max(stage.clientHeight - h, this.zoom.y));
    screen.style.cssText = `left:${this.zoom.x}px;top:${this.zoom.y}px;width:${w}px;height:${h}px`;
  }

  private stagePoint(x: number, y: number): { x: number; y: number } {
    const rect = this.stage()?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: x - rect.left, y: y - rect.top };
  }

  onPointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    const stage = this.stage();
    if (!stage || !this.mobile) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (!this.control) stage.setPointerCapture(event.pointerId);
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()] as [{ x: number; y: number }, { x: number; y: number }];
      const mid = this.stagePoint((a.x + b.x) / 2, (a.y + b.y) / 2);
      this.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), s: this.zoom.s, mx: mid.x, my: mid.y, x: this.zoom.x, y: this.zoom.y };
      return;
    }
    if (this.pointers.size !== 1) return;
    if (this.control) {
      this.tapY = this.stagePoint(event.clientX, event.clientY).y - this.zoom.y;
      return;
    }
    const now = Date.now();
    if (now - this.lastTap < 300) {
      const point = this.stagePoint(event.clientX, event.clientY);
      if (this.zoom.s > 1) this.resetZoom();
      else {
        const k = 2.5 / this.zoom.s;
        this.zoom = { s: 2.5, x: point.x - (point.x - this.zoom.x) * k, y: point.y - (point.y - this.zoom.y) * k };
        this.applyZoom();
      }
      this.lastTap = 0;
    } else {
      this.lastTap = now;
    }
  }

  onPointerMove(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!this.mobile) return;
    const prev = this.pointers.get(event.pointerId);
    if (!prev) return;
    const cur = { x: event.clientX, y: event.clientY };
    this.pointers.set(event.pointerId, cur);
    if (this.pointers.size === 2 && this.pinch) {
      const [a, b] = [...this.pointers.values()] as [{ x: number; y: number }, { x: number; y: number }];
      const pinch = this.pinch;
      const s = Math.max(1, Math.min(MAX_ZOOM, (pinch.s * Math.hypot(a.x - b.x, a.y - b.y)) / pinch.d));
      const mid = this.stagePoint((a.x + b.x) / 2, (a.y + b.y) / 2);
      const cx = (pinch.mx - pinch.x) / pinch.s;
      const cy = (pinch.my - pinch.y) / pinch.s;
      this.zoom = { s, x: mid.x - cx * s, y: mid.y - cy * s };
      this.applyZoom();
    } else if (this.pointers.size === 1 && !this.control && this.zoom.s > 1) {
      this.zoom.x += cur.x - prev.x;
      this.zoom.y += cur.y - prev.y;
      this.applyZoom();
    }
  }

  onPointerUp(event: ReactPointerEvent<HTMLDivElement>): void {
    this.pointers.delete(event.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
  }
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M2.5 7.5 5.5 10.5 11.5 3.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1.4" fill="none" />
      <rect x="4.5" y="4.5" width="5" height="5" rx="1" fill="currentColor" />
    </svg>
  );
}

export function LiveDock() {
  const selectedId = useUiStore((state) => state.selectedSessionId);
  const liveView = useUiStore((state) => state.liveView);
  const sessionId = selectedId ?? liveView?.sessionId ?? "";
  const sessionTitle = liveView && liveView.sessionId === sessionId ? liveView.sessionTitle : "";
  const ctrlRef = useRef<LiveController | null>(null);
  if (!ctrlRef.current) ctrlRef.current = new LiveController();
  const ctrl = ctrlRef.current;
  const version = useSyncExternalStore(ctrl.subscribe, ctrl.getVersion, ctrl.getVersion);
  const rootRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dialogKind = useRef<Exclude<Dialog, null> | null>(null);
  if (ctrl.dialog) dialogKind.current = ctrl.dialog;

  useLayoutEffect(() => {
    ctrl.bind({
      root: () => rootRef.current,
      stage: () => stageRef.current,
      screen: () => screenRef.current,
      input: () => inputRef.current,
    });
    ctrl.setSession(sessionId, sessionTitle);
    ctrl.attach();
    return () => ctrl.detach();
    // 只在挂载时连接。会话变化走下面的 effect，避免严格模式以外的重复断开。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl]);

  useEffect(() => {
    ctrl.setSession(sessionId, sessionTitle);
  }, [ctrl, sessionId, sessionTitle]);

  const allowed = ctrl.control && ctrl.stateFresh && ctrl.link === "connected" && !ctrl.closed;
  const overlay = ctrl.link === "connected" ? "" : ctrl.link === "failed" ? "无法加载远程画面组件" : ctrl.link === "disconnected" ? "画面已断开" : "正在连接服务器上的浏览器…（休眠时需要几秒唤醒）";

  return (
    <section ref={rootRef} className="dock-live" aria-label="实时画面" data-rev={version}>
      <header>
        <div className="titles">
          <b>{ctrl.heading()}</b>
          {ctrl.subtitle() ? <small title={ctrl.live?.url ?? ""}>{ctrl.subtitle()}</small> : null}
        </div>
        {ctrl.mobile ? (
          <>
            {ctrl.needsRelease ? (
              <button type="button" className="tb primary" disabled={ctrl.busy} aria-label={ctrl.releaseLabel} onClick={() => void ctrl.finish()}>
                {ctrl.releaseLabel}
              </button>
            ) : null}
            <button type="button" className="tb icon" aria-label="收起画面" onClick={() => ctrl.close()}>{CrossIcon()}</button>
          </>
        ) : (
          <>
            {ctrl.running ? (
              <button type="button" className="tb stop" disabled={ctrl.stopping()} onClick={() => ctrl.requestStop()}>
                {StopIcon()}停止
              </button>
            ) : null}
            {ctrl.needsRelease ? (
              <button type="button" className="tb primary" disabled={ctrl.busy} onClick={() => void ctrl.finish()}>
                {CheckIcon()}{ctrl.releaseLabel}
              </button>
            ) : (
              <button type="button" className="tb primary" disabled={!ctrl.canTake} onClick={() => ctrl.requestTakeover()}>取得控制权</button>
            )}
            <button type="button" className="tb icon" aria-label="收起画面" onClick={() => ctrl.close()}>{CrossIcon()}</button>
          </>
        )}
      </header>
      <div
        ref={stageRef}
        className="stage"
        onPointerDown={(event) => ctrl.onPointerDown(event)}
        onPointerMove={(event) => ctrl.onPointerMove(event)}
        onPointerUp={(event) => ctrl.onPointerUp(event)}
        onPointerCancel={(event) => ctrl.onPointerUp(event)}
      >
        <div ref={screenRef} className="screen" />
        {ctrl.link === "connected" ? null : (
          <div className="overlay" role="status">
            <span>{overlay}</span>
            {ctrl.link === "failed" || ctrl.link === "disconnected" ? (
              <button type="button" onClick={() => ctrl.reconnect()}>重新连接</button>
            ) : null}
          </div>
        )}
        {ctrl.notice ? (
          <div className="notice" role="alert" onClick={() => ctrl.dismissNotice()}>{ctrl.notice}</div>
        ) : null}
      </div>
      {ctrl.mobile ? (
        <div className="bar">
          {ctrl.running ? (
            <button type="button" className="big" disabled={ctrl.stopping()} onClick={() => ctrl.requestStop()}>停止任务</button>
          ) : null}
          {ctrl.control ? (
            <div className="pill" role="group" aria-label="输入方式">
              <button type="button" aria-label="键盘" aria-pressed={ctrl.kbd} onClick={() => ctrl.toggleKeyboard(true)}>
                <svg width="26" height="20" viewBox="0 0 26 20" aria-hidden="true"><rect x="1" y="1" width="24" height="18" rx="3" stroke="currentColor" strokeWidth="1.6" fill="none" /><path d="M5 6h2M9 6h2M13 6h2M17 6h2M21 6h0M5 10h2M9 10h2M13 10h2M17 10h2M8 14h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              </button>
              <button type="button" aria-label="手指操作" aria-pressed={!ctrl.kbd} onClick={() => ctrl.toggleKeyboard(false)}>
                <svg width="20" height="22" viewBox="0 0 20 22" aria-hidden="true"><path d="M7 11V3.5a1.5 1.5 0 0 1 3 0V10m0-1.5a1.5 1.5 0 0 1 3 0V11m0-1a1.5 1.5 0 0 1 3 0v5a6 6 0 0 1-6 6h-.5a6 6 0 0 1-4.6-2.2L2.3 15a1.5 1.5 0 0 1 2.2-2L7 15" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            </div>
          ) : (
            <button type="button" className="big primary" disabled={!ctrl.canTake} onClick={() => ctrl.requestTakeover()}>取得控制权</button>
          )}
        </div>
      ) : ctrl.control ? (
        <div className="bar desktop-tools" role="group" aria-label="远程浏览器文字输入">
          <button type="button" className="tb" aria-label="键盘 / 粘贴" aria-pressed={ctrl.kbd} onClick={() => ctrl.toggleKeyboard(!ctrl.kbd)}>键盘 / 粘贴</button>
          <button type="button" className="tb" onClick={() => ctrl.sendKey(XK_RETURN, "Enter")}>回车</button>
          <button type="button" className="tb" onClick={() => ctrl.sendKey(XK_BACKSPACE, "Backspace")}>退格</button>
        </div>
      ) : null}
      <textarea
        key={ctrl.generation}
        ref={inputRef}
        className={ctrl.mobile || !ctrl.kbd ? "ime" : "ime visible"}
        aria-label="向远程浏览器输入文字"
        placeholder="先点选远程输入框，再在此输入或粘贴（最多 2000 字）"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="enter"
        disabled={!allowed}
        onKeyDown={(event) => ctrl.onKeyDown(event.nativeEvent, ctrl.generation)}
        onCompositionStart={() => ctrl.onCompositionStart(ctrl.generation)}
        onCompositionEnd={(event) => ctrl.onCompositionEnd(event.nativeEvent, ctrl.generation)}
        onInput={() => ctrl.onInput(ctrl.generation)}
        onPaste={(event) => ctrl.onPaste(event.nativeEvent, ctrl.generation)}
        onBlur={() => ctrl.onBlur(ctrl.generation)}
        onBeforeInput={(event) => ctrl.onBeforeInput(event.nativeEvent, ctrl.generation)}
      />
      <ConfirmDialog open={ctrl.dialog !== null} onClose={() => ctrl.closeDialog()} labelId="dock-live-dlg-title">
        <div className="dialog">
          {dialogKind.current === "stop" ? (
            <>
              <h3 id="dock-live-dlg-title">停止这项任务？</h3>
              <p>clawd 将放弃当前任务。浏览器保持打开，你可以接管继续操作。</p>
              <button type="button" className="danger" onClick={() => void ctrl.confirmStop()}>停止</button>
              <button type="button" onClick={() => ctrl.closeDialog()}>取消</button>
            </>
          ) : (
            <>
              <h3 id="dock-live-dlg-title">clawd 正在执行任务</h3>
              <p>必须先确认任务已停止，才能开放人工输入。停止后不会自动恢复；浏览器保持打开。</p>
              <button type="button" className="danger" onClick={() => void ctrl.startControl(true)}>停止任务并接管</button>
              <button type="button" onClick={() => ctrl.closeDialog()}>取消</button>
            </>
          )}
        </div>
      </ConfirmDialog>
    </section>
  );
}
