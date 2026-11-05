import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { ApiError } from "@/lib/api";
import {
  controlTaskLive,
  getTaskLiveFrame,
  sendTaskLiveInput,
  type TaskInputKey,
  type TaskLiveFrame,
  type TaskLiveStatus,
} from "@/lib/task-live";
import "./docks.css";

const POLL_INTERVAL_MS = 1500;
const HEARTBEAT_MS = 15000;

interface TaskScope {
  generation: number;
  sessionId: string;
  taskId: string;
  targetId: string;
  owner: string;
}

interface TaskOwner {
  owner: string;
  sessionId: string;
  taskId: string;
  targetId: string;
}

function isValidTaskStatus(status: unknown): status is TaskLiveStatus {
  return status === "waiting" || status === "active" || status === "ended";
}

function isValidTaskImage(image: unknown): boolean {
  if (image === null || image === undefined || image === "") return true;
  if (typeof image !== "string") return false;
  return /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(image);
}

function readStatus(data: TaskLiveFrame): unknown {
  return data.status;
}

/** 任务页租约走 live/task/control，和全局 live/takeover 不是一把锁。画面帧只留在这个实例。 */
class TaskController {
  sessionId = "";
  /** 签名没有 active。挂载且 sessionId 非空才拉图；空会话按旧版 active=false 本地结束。 */
  active = false;
  frame: TaskLiveFrame | null = null;
  boundTaskId: string | null = null;
  error: string | null = null;
  loading = true;
  control = false;
  busy = false;
  notice = "";
  textDraft = "";

  private clientId = crypto.randomUUID();
  private leaseOwned = false;
  private generation = 0;
  private pollTimer: number | null = null;
  private beatTimer: number | null = null;
  private inFlightCtrl: AbortController | null = null;
  private disposed = false;
  private inputQueue: Promise<unknown> = Promise.resolve();
  private root: HTMLElement | null = null;
  private mounted = false;
  private silent = false;
  private version = 0;
  private readonly listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = (): number => this.version;

  bind(root: HTMLElement | null): void {
    this.root = root;
  }

  private emit(): void {
    if (this.silent) return;
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  private scope(): TaskScope {
    return {
      generation: this.generation,
      sessionId: this.sessionId,
      taskId: this.boundTaskId || this.frame?.taskId || "",
      targetId: this.frame?.targetId || "",
      owner: this.clientId,
    };
  }

  private isCurrent(scope: { generation: number; sessionId: string; owner: string }): boolean {
    return !this.disposed && this.mounted && scope.generation === this.generation && scope.sessionId === this.sessionId && scope.owner === this.clientId;
  }

  private endedFrame(title?: string): TaskLiveFrame {
    return {
      taskId: this.boundTaskId || "",
      sessionId: this.sessionId,
      status: "ended",
      targetId: null,
      title: title || this.frame?.title || "AI 当前页面",
      image: null,
      cleanupError: "",
    };
  }

  private revokeLocalControl(): TaskOwner | null {
    const prev = this.leaseOwned
      ? {
          owner: this.clientId,
          sessionId: this.sessionId,
          taskId: this.boundTaskId || this.frame?.taskId || "",
          targetId: this.frame?.targetId || "",
        }
      : null;
    this.generation += 1;
    this.clientId = crypto.randomUUID();
    if (this.beatTimer !== null) {
      window.clearTimeout(this.beatTimer);
      this.beatTimer = null;
    }
    this.control = false;
    this.leaseOwned = false;
    this.busy = false;
    this.inputQueue = Promise.resolve();
    return prev;
  }

  private releaseRemoteIfOwned(prev: TaskOwner | null, keepalive = true): void {
    if (!prev) return;
    void controlTaskLive(
      {
        action: "release",
        clientId: prev.owner,
        sessionId: prev.sessionId,
        taskId: prev.taskId,
        targetId: prev.targetId,
      },
      keepalive,
    ).catch(() => undefined);
  }

  attach(): void {
    if (this.mounted) return;
    this.silent = false;
    this.disposed = false;
    this.mounted = true;
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("pagehide", this.onPageHide);
    if (!this.active) {
      this.loading = false;
      this.frame = this.endedFrame("AI 当前页面");
      this.emit();
      return;
    }
    if (this.sessionId) this.startPolling();
  }

  detach(): void {
    if (!this.mounted && this.disposed) return;
    this.silent = true;
    this.disposed = true;
    this.mounted = false;
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("pagehide", this.onPageHide);
    this.stopPolling();
    this.abortInFlight();
    const prev = this.revokeLocalControl();
    this.releaseRemoteIfOwned(prev, true);
  }

  setSession(id: string): void {
    if (this.sessionId === id && (this.mounted || this.sessionId !== "")) {
      if (!this.mounted && id === "" && !this.frame) this.frame = this.endedFrame("AI 当前页面");
      return;
    }
    const wasMounted = this.mounted;
    if (wasMounted && this.sessionId !== id) {
      const prev = this.revokeLocalControl();
      this.releaseRemoteIfOwned(prev, true);
      this.stopPolling();
      this.abortInFlight();
    }
    this.sessionId = id;
    this.active = id !== "";
    this.boundTaskId = null;
    this.frame = null;
    this.error = null;
    this.notice = "";
    this.textDraft = "";
    this.loading = this.active;
    if (!this.active) {
      this.loading = false;
      this.frame = this.endedFrame("AI 当前页面");
    }
    if (wasMounted && this.active && !this.disposed && !document.hidden) this.startPolling();
    this.emit();
  }

  private readonly onPageHide = (): void => {
    const prev = this.revokeLocalControl();
    this.releaseRemoteIfOwned(prev, true);
    this.emit();
  };

  private readonly onVisibility = (): void => {
    if (document.hidden) {
      const prev = this.revokeLocalControl();
      this.releaseRemoteIfOwned(prev, true);
      this.stopPolling();
      this.abortInFlight();
      this.frame = null;
      this.emit();
      return;
    }
    if (!this.active || this.frame?.status === "ended") return;
    this.startPolling();
  };

  private stopPolling(): void {
    if (this.pollTimer !== null) {
      window.clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private abortInFlight(): void {
    this.inFlightCtrl?.abort();
    this.inFlightCtrl = null;
  }

  startPolling(): void {
    this.stopPolling();
    if (this.disposed || document.hidden || !this.sessionId || !this.active) return;
    if (this.frame?.status === "ended") return;
    void this.pollStep();
  }

  private scheduleNext(): void {
    this.stopPolling();
    if (this.disposed || document.hidden || !this.sessionId || !this.active) return;
    if (this.frame?.status === "ended") return;
    this.pollTimer = window.setTimeout(() => void this.pollStep(), POLL_INTERVAL_MS);
  }

  private async pollStep(): Promise<void> {
    if (this.inFlightCtrl || this.disposed || document.hidden || !this.sessionId || !this.active) return;
    if (this.frame?.status === "ended") return;
    const reqSessionId = this.sessionId;
    const reqTaskId = this.boundTaskId;
    const reqClientId = this.leaseOwned ? this.clientId : null;
    const ctrl = new AbortController();
    this.inFlightCtrl = ctrl;
    try {
      const data = await getTaskLiveFrame(reqSessionId, reqTaskId, ctrl.signal, reqClientId);
      if (this.disposed || this.sessionId !== reqSessionId || !this.active || ctrl.signal.aborted) return;
      if (data.sessionId !== reqSessionId) throw new Error(`会话不匹配 (期望 ${reqSessionId}, 实际 ${data.sessionId})`);
      if (reqTaskId && data.taskId !== reqTaskId) throw new Error(`任务不匹配 (期望 ${reqTaskId}, 实际 ${data.taskId})`);
      const status = readStatus(data);
      if (!isValidTaskStatus(status)) throw new Error(`未知任务状态: ${String(status)}`);
      if (data.status === "active" && (!data.taskId || !data.targetId)) throw new Error("任务画面缺少归属信息");
      if (!isValidTaskImage(data.image)) throw new Error("非法图像数据格式");
      this.loading = false;
      this.error = null;
      if (!this.boundTaskId && data.taskId) this.boundTaskId = data.taskId;
      if (data.status === "waiting" || data.status === "ended") {
        if (this.control || this.leaseOwned) {
          const prev = this.revokeLocalControl();
          this.releaseRemoteIfOwned(prev, true);
          if (data.status === "ended") this.notice = "任务已结束，控制权已自动释放。";
        }
        this.frame = { ...data, image: null };
      } else {
        if (this.control && reqClientId && data.controlledByYou === false) {
          this.revokeLocalControl();
          this.notice = "控制租约已失效或转移，已退回旁观。";
        }
        this.frame = data;
      }
    } catch (error) {
      if (ctrl.signal.aborted || this.disposed || this.sessionId !== reqSessionId || !this.active) return;
      this.loading = false;
      this.frame = null;
      if (this.control || this.leaseOwned) {
        const prev = this.revokeLocalControl();
        this.releaseRemoteIfOwned(prev, true);
      }
      this.error = error instanceof ApiError ? (error.message || `请求失败 (${error.status})`) : error instanceof Error ? error.message : "画面获取失败";
    } finally {
      if (this.inFlightCtrl === ctrl) this.inFlightCtrl = null;
      if (!this.disposed && !document.hidden && this.sessionId === reqSessionId && this.active && this.frame?.status !== "ended") {
        this.scheduleNext();
      }
      if (!this.disposed && this.sessionId === reqSessionId) this.emit();
    }
  }

  get canTakeover(): boolean {
    if (!this.active || this.busy || this.control || this.loading || this.error) return false;
    if (!this.sessionId || !this.frame || this.frame.status !== "active") return false;
    if (!this.frame.taskId || !this.frame.targetId) return false;
    if (this.frame.takenOver && !this.frame.controlledByYou) return false;
    return true;
  }

  get takenByOther(): boolean {
    return !!this.frame?.takenOver && !this.frame.controlledByYou && !this.control;
  }

  private scheduleHeartbeat(scope: TaskScope): void {
    if (this.beatTimer !== null) window.clearTimeout(this.beatTimer);
    this.beatTimer = window.setTimeout(() => void this.heartbeat(scope), HEARTBEAT_MS);
  }

  private async heartbeat(scope: TaskScope): Promise<void> {
    if (!this.isCurrent(scope) || !this.control) return;
    try {
      const res = await controlTaskLive({
        action: "takeover",
        clientId: scope.owner,
        sessionId: scope.sessionId,
        taskId: scope.taskId,
        targetId: scope.targetId,
      });
      if (!this.isCurrent(scope)) {
        if (res.controlledByYou) void controlTaskLive({ action: "release", clientId: scope.owner }, true).catch(() => undefined);
        return;
      }
      if (!res.ok || !res.controlledByYou) throw new Error("控制续租未获确认");
      this.scheduleHeartbeat(scope);
    } catch (error) {
      if (!this.isCurrent(scope)) return;
      const prev = this.revokeLocalControl();
      this.releaseRemoteIfOwned(prev, true);
      this.notice = error instanceof Error ? error.message : "控制心跳失效，已退回旁观。";
      this.emit();
    }
  }

  async requestTakeover(): Promise<void> {
    if (!this.canTakeover || !this.frame?.taskId || !this.frame.targetId) return;
    const prev = this.revokeLocalControl();
    this.releaseRemoteIfOwned(prev, true);
    this.busy = true;
    this.notice = "";
    this.emit();
    const scope = this.scope();
    try {
      const deadline = performance.now() + 60000;
      let res: Awaited<ReturnType<typeof controlTaskLive>>;
      for (;;) {
        res = await controlTaskLive({
          action: "takeover",
          clientId: scope.owner,
          sessionId: scope.sessionId,
          taskId: scope.taskId,
          targetId: scope.targetId,
        });
        if (!this.isCurrent(scope)) {
          if (res.controlledByYou) {
            void controlTaskLive(
              {
                action: "release",
                clientId: scope.owner,
                sessionId: scope.sessionId,
                taskId: scope.taskId,
                targetId: scope.targetId,
              },
              true,
            ).catch(() => undefined);
          }
          return;
        }
        if (!res.pending) break;
        this.notice = "等待 AI 当前浏览器操作结束，在安全边界接管…";
        this.emit();
        if (performance.now() >= deadline) throw new Error("等待安全接管超时，未获得控制权；可稍后重试。");
        await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
        if (!this.isCurrent(scope) || !this.active || document.hidden) return;
      }
      if (!res.ok || !res.controlledByYou) throw new Error("服务器未确认当前任务页面的控制权");
      this.leaseOwned = true;
      this.control = true;
      this.notice = "已暂停 AI 浏览器操作；只控制当前任务页，交还后继续。单次最长五分钟。";
      this.scheduleHeartbeat(scope);
    } catch (error) {
      if (!this.isCurrent(scope)) return;
      this.control = false;
      this.leaseOwned = false;
      this.notice = error instanceof Error ? error.message : "接管被拒绝，保持旁观";
    } finally {
      if (this.isCurrent(scope)) {
        this.busy = false;
        this.emit();
      }
    }
  }

  async releaseControl(): Promise<void> {
    const prev = this.revokeLocalControl();
    if (!prev) {
      this.notice = "已退回旁观。";
      this.emit();
      return;
    }
    const scope = this.scope();
    this.busy = true;
    this.emit();
    try {
      await controlTaskLive({
        action: "release",
        clientId: prev.owner,
        sessionId: prev.sessionId,
        taskId: prev.taskId,
        targetId: prev.targetId,
      });
      if (this.isCurrent(scope)) this.notice = "已交还当前任务页面控制权。";
    } catch (error) {
      if (this.isCurrent(scope)) this.notice = `交还控制尚未确认：${error instanceof Error ? error.message : "请稍后重试"}`;
    } finally {
      if (this.isCurrent(scope)) {
        this.busy = false;
        this.emit();
      }
    }
  }

  private enqueueInput(work: (scope: TaskScope) => Promise<void>): void {
    if (!this.control || !this.leaseOwned || this.frame?.status !== "active") return;
    const scope = this.scope();
    if (!scope.taskId || !scope.targetId) return;
    this.inputQueue = this.inputQueue.then(async () => {
      if (!this.isCurrent(scope) || !this.control || !this.leaseOwned) return;
      await work(scope);
    }).catch((error: unknown) => {
      if (!this.isCurrent(scope)) return;
      const prev = this.revokeLocalControl();
      this.releaseRemoteIfOwned(prev, true);
      this.notice = error instanceof Error ? error.message : "输入未确认，已退回旁观。";
      this.emit();
    });
  }

  private ratio(img: HTMLImageElement, clientX: number, clientY: number): { xRatio: number; yRatio: number } | null {
    const rect = img.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const nw = img.naturalWidth || rect.width;
    const nh = img.naturalHeight || rect.height;
    const scale = Math.min(rect.width / nw, rect.height / nh);
    const drawnW = nw * scale;
    const drawnH = nh * scale;
    const localX = clientX - rect.left - (rect.width - drawnW) / 2;
    const localY = clientY - rect.top - (rect.height - drawnH) / 2;
    if (localX < 0 || localY < 0 || localX > drawnW || localY > drawnH) return null;
    return {
      xRatio: Math.max(0, Math.min(1, localX / drawnW)),
      yRatio: Math.max(0, Math.min(1, localY / drawnH)),
    };
  }

  onImageClick(event: { clientX: number; clientY: number; currentTarget: EventTarget | null }): void {
    if (!this.control || !this.leaseOwned) return;
    const img = event.currentTarget instanceof HTMLImageElement ? event.currentTarget : null;
    if (!img) return;
    const point = this.ratio(img, event.clientX, event.clientY);
    if (!point) return;
    this.enqueueInput(async (scope) => {
      await sendTaskLiveInput({
        clientId: scope.owner,
        sessionId: scope.sessionId,
        taskId: scope.taskId,
        targetId: scope.targetId,
        kind: "click",
        xRatio: point.xRatio,
        yRatio: point.yRatio,
      });
      void this.pollStep();
    });
  }

  onImageWheel(event: WheelEvent, img: HTMLImageElement): void {
    if (!this.control || !this.leaseOwned) return;
    const point = this.ratio(img, event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    const deltaX = Math.max(-2000, Math.min(2000, event.deltaX));
    const deltaY = Math.max(-2000, Math.min(2000, event.deltaY));
    this.enqueueInput(async (scope) => {
      await sendTaskLiveInput({
        clientId: scope.owner,
        sessionId: scope.sessionId,
        taskId: scope.taskId,
        targetId: scope.targetId,
        kind: "scroll",
        xRatio: point.xRatio,
        yRatio: point.yRatio,
        deltaX,
        deltaY,
      });
      void this.pollStep();
    });
  }

  setDraft(value: string): void {
    this.textDraft = value;
    this.emit();
  }

  onDraftKey(event: KeyboardEvent): void {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    if (this.textDraft) this.submitText();
    else this.sendKey("Enter");
  }

  submitText(): void {
    if (!this.control || !this.leaseOwned) return;
    const raw = this.textDraft;
    if (!raw) return;
    const bounded = Array.from(raw).slice(0, 2000).join("");
    this.textDraft = "";
    this.emit();
    this.enqueueInput(async (scope) => {
      await sendTaskLiveInput({
        clientId: scope.owner,
        sessionId: scope.sessionId,
        taskId: scope.taskId,
        targetId: scope.targetId,
        kind: "text",
        text: bounded,
      });
      void this.pollStep();
    });
  }

  sendKey(key: TaskInputKey): void {
    if (!this.control || !this.leaseOwned) return;
    this.enqueueInput(async (scope) => {
      await sendTaskLiveInput({
        clientId: scope.owner,
        sessionId: scope.sessionId,
        taskId: scope.taskId,
        targetId: scope.targetId,
        kind: "key",
        key,
      });
      void this.pollStep();
    });
  }

  dismissNotice(): void {
    this.notice = "";
    this.emit();
  }

  close(): void {
    const prev = this.revokeLocalControl();
    this.releaseRemoteIfOwned(prev, true);
    this.root?.dispatchEvent(new CustomEvent("hp-task-live-close", { bubbles: true, composed: true }));
    this.emit();
  }
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3 3l10 10M13 3L3 13" strokeLinecap="round" />
    </svg>
  );
}

export function TaskDock(props: { sessionId: string }) {
  const ctrlRef = useRef<TaskController | null>(null);
  if (!ctrlRef.current) ctrlRef.current = new TaskController();
  const ctrl = ctrlRef.current;
  const version = useSyncExternalStore(ctrl.subscribe, ctrl.getVersion, ctrl.getVersion);
  const rootRef = useRef<HTMLElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useLayoutEffect(() => {
    ctrl.bind(rootRef.current);
    ctrl.setSession(props.sessionId);
    ctrl.attach();
    return () => ctrl.detach();
    // 挂载一次。会话变化不卸载，避免把进行中的轮询误当成组件销毁。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl]);

  useEffect(() => {
    ctrl.setSession(props.sessionId);
  }, [ctrl, props.sessionId]);

  const hasImage = ctrl.frame?.status === "active" && !!ctrl.frame.image;
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const onWheel = (event: WheelEvent) => ctrl.onImageWheel(event, img);
    img.addEventListener("wheel", onWheel, { passive: false });
    return () => img.removeEventListener("wheel", onWheel);
  }, [ctrl, hasImage]);

  const status: TaskLiveStatus = ctrl.frame?.status ?? (ctrl.active ? "active" : "waiting");
  const titleText = ctrl.frame?.title || "AI 当前页面";
  const showInput = ctrl.control && status === "active";

  return (
    <section ref={rootRef} className="dock-task" aria-label="任务画面" data-rev={version}>
      <header>
        <span className="badge" data-status={status}>
          <span className={status === "active" ? "dot pulse" : "dot"} />
          {ctrl.control ? "由你控制" : status === "active" ? "执行中" : status === "ended" ? "已结束" : "等待画面"}
        </span>
        <span className="title" title={titleText}>{titleText}</span>
        <button type="button" className="icon-btn" title="关闭" aria-label="关闭任务画面" onClick={() => ctrl.close()}>
          {CloseIcon()}
        </button>
      </header>
      <div className="viewport" role="region" aria-label="AI 任务画面">
        {ctrl.notice ? (
          <div className="notice" role="status" onClick={() => ctrl.dismissNotice()}>{ctrl.notice}</div>
        ) : null}
        {ctrl.error ? (
          <div className="state-box" role="alert">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="1.8" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <p className="error-text">{ctrl.error}</p>
            <button type="button" onClick={() => ctrl.startPolling()}>重试连接</button>
          </div>
        ) : ctrl.loading && !ctrl.frame ? (
          <div className="state-box"><p>正在连接 AI 页面…</p></div>
        ) : ctrl.frame?.status === "ended" ? (
          <div className="state-box">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 12l2 2 4-4" />
            </svg>
            <p>任务已结束，画面已关闭</p>
            {ctrl.frame.cleanupError ? <p className="error-text" style={{ fontSize: "11px" }}>{ctrl.frame.cleanupError}</p> : null}
          </div>
        ) : ctrl.frame?.status === "active" && ctrl.frame.image ? (
          <img
            ref={imgRef}
            src={ctrl.frame.image}
            alt={ctrl.frame.title || "AI 目标页面截图"}
            data-controlled={ctrl.control ? "true" : "false"}
            draggable={false}
            onClick={(event) => ctrl.onImageClick(event)}
          />
        ) : (
          <div className="state-box"><p>等待 AI 打开页面…</p></div>
        )}
      </div>
      {showInput ? (
        <div className="kbd-bar" role="group" aria-label="任务页面输入">
          <input
            type="text"
            aria-label="输入文字到任务页面"
            placeholder="输入文字发送到当前焦点…"
            value={ctrl.textDraft}
            onChange={(event) => ctrl.setDraft(event.target.value)}
            onKeyDown={(event) => ctrl.onDraftKey(event.nativeEvent)}
          />
          <button type="button" className="primary" onClick={() => ctrl.submitText()}>发送文字</button>
          <button type="button" onClick={() => ctrl.sendKey("Enter")}>Enter</button>
          <button type="button" onClick={() => ctrl.sendKey("Backspace")}>退格</button>
          <button type="button" onClick={() => ctrl.sendKey("Tab")}>Tab</button>
          <button type="button" onClick={() => ctrl.sendKey("Escape")}>Esc</button>
        </div>
      ) : null}
      <footer className="bar" role="group" aria-label="任务画面操作">
        {ctrl.control ? (
          <button type="button" className="bar-btn primary" disabled={ctrl.busy} onClick={() => void ctrl.releaseControl()}>交还控制</button>
        ) : (
          <button type="button" className="bar-btn primary" disabled={!ctrl.canTakeover} onClick={() => void ctrl.requestTakeover()}>
            {ctrl.busy ? "等待安全接管…" : ctrl.takenByOther ? "其他窗口控制中" : "取得控制权"}
          </button>
        )}
        <button type="button" className="bar-btn" aria-label="底部关闭任务画面" onClick={() => ctrl.close()}>关闭画面</button>
      </footer>
    </section>
  );
}
