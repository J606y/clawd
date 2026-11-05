import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type ApprovalChoice, type Message, type SessionSummary } from "@/lib/api";
import { buildContent, type Attachment } from "@/lib/attachments";
import { chatModel, nextChatModel, rememberChatModel } from "@/lib/chat-selection";
import { loadCommentary, type Commentary } from "@/lib/commentary";
import { cachedSessionEffort, currentEffort, hydrateSessionEffort } from "@/lib/effort";
import { ensureSessionEffort, fetchSessionEffort, invalidateSessionEffort, saveSessionEffort } from "@/lib/effort-service";
import { canonicalModel } from "@/lib/model-effort";
import { editBoundary, resendEdited, type EditAttempt } from "@/lib/message-edit";
import { queryKeys, useContextUsage, useCreateSession, useEditBranch, useLockModel, useModelOptions, useSessionBackground, useSessionList, useSessionMessages, useStopExecution, useSubagents } from "@/lib/queries";
import { RUN_CHANGED, RUN_FINISHED, runs, type LiveRun } from "@/lib/runs";
import { prepareCreatedSession, getPendingNewChatProjectId } from "@/lib/project-send";
import { projectsApi } from "@/lib/projects";
import { sidecar, type SessionExecution, type Subagent, type SubagentsView } from "@/lib/sidecar";
import { HISTORY_SYNC_MS, historyReloadMs } from "@/lib/sync-poll";
import { tasksForSession, type TaskExecution } from "@/lib/task-list";
import { liveTextBesideHistory, shouldStripInFlightHistory, turnSourceKey, visibleTurnsBesideLive, type Turn, type UserTurn } from "@/lib/transcript";
import { useLiveRun } from "@/lib/stream";
import { useUiStore } from "@/stores/ui";
import { ChatStyles } from "./chat-styles";
import { disclosureLeavesBottom } from "./scroll-intent";
import { ClarifySheet } from "./clarify-sheet";
import { Composer } from "./composer";
import { LiveTail } from "./live-tail";
import { MessageList, type Anchor, type ListHandle } from "./message-list";
import { ContextRing, EffortPicker, ModelPicker } from "./pickers";
import { ComposerPanels } from "./composer-panels";
import { BrowserTask, HistoryTurn } from "./turns";

const PAGE = 200;
const MAX_PAGE = 500;
const NEXT_TURN_EFFORT = "本回合仍按原来的档，下一回合起生效";

function mergeExpandedWindow(previous: Message[], incoming: Message[], requested: number): Message[] {
  if (previous.length <= requested) return incoming;
  if (incoming.length === 0) {
    const earlierCount = previous.length - requested;
    const seen = new Set<number>();
    const earlier: Message[] = [];
    for (const message of previous.slice(0, earlierCount)) {
      if (typeof message.id === "number" && seen.has(message.id)) continue;
      if (typeof message.id === "number") seen.add(message.id);
      earlier.push(message);
    }
    return earlier;
  }
  const boundary = incoming[0];
  if (!boundary) return incoming;
  let cut = -1;
  if (typeof boundary.id === "number") {
    for (let index = 0; index < previous.length; index += 1) {
      const message = previous[index];
      if (message && message.id === boundary.id) {
        cut = index;
        break;
      }
    }
  }
  const seen = new Set<number>();
  for (const message of incoming) {
    if (typeof message.id === "number") seen.add(message.id);
  }
  const prefix: Message[] = [];
  if (cut >= 0) {
    for (let index = 0; index < cut; index += 1) {
      const message = previous[index];
      if (message) prefix.push(message);
    }
  } else {
    const right = boundary.timestamp;
    for (const message of previous) {
      const left = message.timestamp;
      if (typeof left === "number" && typeof right === "number" && Number.isFinite(left) && Number.isFinite(right) && left < right) prefix.push(message);
    }
  }
  const earlier: Message[] = [];
  for (const message of prefix) {
    if (typeof message.id === "number" && seen.has(message.id)) continue;
    earlier.push(message);
  }
  return [...earlier, ...incoming];
}

function discoveryClosed(sessionId: string, started: LiveRun | undefined, foundId: string): boolean {
  if (foundId && runs.isRetired(sessionId, foundId)) return true;
  const held = runs.get(sessionId);
  if (held && held.status !== "starting" && held.status !== "running") return true;
  return !!started?.runId && runs.get(sessionId) !== started && (!foundId || foundId === started.runId);
}

function discoveryJoined(sessionId: string, started: LiveRun | undefined): boolean {
  return !started && !!runs.get(sessionId);
}

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

function titleFrom(text: string, attachments: Attachment[]): string {
  const line = text.trim().split("\n")[0] || attachments[0]?.name || "";
  return line.length > 40 ? `${line.slice(0, 40)}…` : line || "新对话";
}

function sessionDetail(event: Event): string {
  return event instanceof CustomEvent && typeof event.detail === "string" ? event.detail : "";
}

const EXECUTION_STATES = ["running", "stalled", "returned", "failed", "cancelled", "interrupted", "unknown"] as const;

function cleanExecId(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || text.length > 256 || text.includes("\n") || text.includes("\r") || text.includes("\0")) return "";
  return text;
}

/** 只信子代理响应里的 profile。不写 default，不读 URL，也不用进程档列表代替 SessionDB。 */
function serviceProfileKey(view: SubagentsView): string {
  const top = cleanExecId(view.profileKey);
  if (top) return top;
  const keys = new Set<string>();
  for (const row of view.data) {
    const key = cleanExecId(row.profileKey);
    if (key) keys.add(key);
  }
  if (keys.size === 1) {
    const only = keys.values().next().value;
    return typeof only === "string" && only ? only : "unknown";
  }
  return "unknown";
}

function executionStatusOf(row: Subagent): string {
  const raw = cleanExecId(row.executionStatus).toLowerCase();
  for (const allowed of EXECUTION_STATES) {
    if (allowed === raw) return allowed;
  }
  return "unknown";
}

function taskExecutions(view: SubagentsView, sessionId: string, profileKey: string): TaskExecution[] {
  const out: TaskExecution[] = [];
  for (const row of view.data) {
    if (row.parentId !== sessionId) continue;
    const childId = cleanExecId(row.id);
    const delegationId = cleanExecId(row.delegationId);
    if (!childId && !delegationId) continue;
    const rowProfile = cleanExecId(row.profileKey);
    if (!rowProfile || rowProfile !== profileKey) continue;
    const todoId = cleanExecId(row.todoId);
    out.push({
      ...(delegationId ? { delegationId } : {}),
      ...(childId ? { childId } : {}),
      ...(todoId ? { todoId } : {}),
      sessionId,
      profileKey,
      executionStatus: executionStatusOf(row),
    });
  }
  return out;
}

function useSlotHidden(node: HTMLElement | null): boolean {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const slot = node?.closest(".chat-slot");
    if (!slot) return;
    const read = () => setHidden(slot.hasAttribute("hidden"));
    read();
    const observer = new MutationObserver(read);
    observer.observe(slot, { attributes: true, attributeFilter: ["hidden"] });
    return () => observer.disconnect();
  }, [node]);
  return hidden;
}

function useFramedRun(sessionId: string, slotHidden: boolean) {
  "use no memo";
  const live = useLiveRun(sessionId);
  const [shown, setShown] = useState<LiveRun | null>(live.run);
  const latest = useRef(live.run);
  latest.current = live.run;
  useEffect(() => {
    const next = latest.current;
    const terminal = !next || next.status === "completed" || next.status === "cancelled" || next.status === "failed";
    if (slotHidden && !terminal) return;
    if (document.visibilityState === "hidden") {
      setShown(next);
      return;
    }
    const id = requestAnimationFrame(() => setShown(latest.current));
    return () => cancelAnimationFrame(id);
  }, [live.run, slotHidden]);
  return { live, shown };
}

async function providerFor(model: string, provider: string): Promise<string> {
  if (!model) return provider;
  if (provider) return provider;
  const found = await api.providerOf(model);
  if (!found) {
    throw new ApiError("server", 0, `暂时无法确认模型「${model}」的提供方，消息未发送；请稍后重试，或在模型菜单里重新选择`);
  }
  return found;
}

export function ChatPage() {
  const sessionId = useUiStore((state) => state.selectedSessionId) ?? "";
  const setSelectedSessionId = useUiStore((state) => state.setSelectedSessionId);
  const draft = useUiStore((state) => state.composerDraft);
  const setDraft = useUiStore((state) => state.setComposerDraft);
  const effortBySession = useUiStore((state) => state.effortBySession);
  const effortNext = useUiStore((state) => state.effortNext);
  const setEffortFor = useUiStore((state) => state.setEffortFor);
  const [root, setRoot] = useState<HTMLElement | null>(null);
  const slotHidden = useSlotHidden(root);
  const { live, shown } = useFramedRun(sessionId, slotHidden);
  const liveRef = useRef(live);
  liveRef.current = live;
  const listRef = useRef<ListHandle>(null);
  const taskDockRef = useRef<HTMLDivElement>(null);
  const [override, setOverride] = useState<{ model: string; provider: string } | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [listedSession, setListedSession] = useState(sessionId);
  const [notes, setNotes] = useState<Commentary>(new Map());
  const [hasEarlier, setHasEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [earlierError, setEarlierError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingSend, setPendingSend] = useState("");
  const [preparing, setPreparing] = useState(false);
  const preparingSession = useRef(sessionId);
  useLayoutEffect(() => {
    if (preparingSession.current === sessionId) return;
    preparingSession.current = sessionId;
    setPreparing(false);
  }, [sessionId]);
  const guardPreparing = useCallback((next: boolean) => {
    if ((useUiStore.getState().selectedSessionId ?? "") !== preparingSession.current) return;
    setPreparing(next);
  }, []);
  const [flash, setFlash] = useState("");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [stick, setStick] = useState(true);
  const [atBottom, setAtBottom] = useState(true);
  const [hold, setHold] = useState<Anchor | null>(null);
  const [serverBusy, setServerBusy] = useState(false);
  const [remoteEffort, setRemoteEffort] = useState<{ sessionId: string; level: string } | null>(null);
  const [effortNote, setEffortNote] = useState("");
  const effortGen = useRef(0);
  const [serverExecution, setServerExecution] = useState<SessionExecution | null>(null);
  const [executionConfirm, setExecutionConfirm] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const attempts = useRef(new Map<string, EditAttempt>());
  const prevSession = useRef(sessionId);
  const stickRef = useRef(stick);
  stickRef.current = stick;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const messagesSession = useRef(sessionId);
  const reloadGen = useRef(0);
  const committedReload = useRef(0);
  const supersededPages = useRef(new WeakSet<Message[]>());
  const createSession = useCreateSession();
  const lockModel = useLockModel();
  const editBranch = useEditBranch();
  const stopExecution = useStopExecution();
  const client = useQueryClient();
  const models = useModelOptions();
  const sessions = useSessionList({ limit: 100 }, true);
  const history = useSessionMessages(sessionId, 0, PAGE, sessionId.length > 0);
  const context = useContextUsage(sessionId, sessionId.length > 0);
  const background = useSessionBackground(sessionId, visible && !slotHidden);
  const session = sessions.data?.data.find((item) => item.id === sessionId);
  const remembered = sessionId ? chatModel(sessionId) : nextChatModel();
  const displayModel = override?.model || remembered?.model || session?.model || "";
  const displayProvider = override?.provider || remembered?.provider || "";
  const storedEffort = sessionId ? (effortBySession[sessionId] ?? "medium") : effortNext;
  const shownEffort = sessionId && remoteEffort?.sessionId === sessionId ? remoteEffort.level : storedEffort;
  const localActive = live.isActive;
  const unconfirmed = Boolean(shown?.unverified && !shown.runId);
  const active = (shown?.status === "starting" || shown?.status === "running") && (!unconfirmed || localActive);

  useEffect(() => {
    if (prevSession.current && sessionId !== prevSession.current) setOverride(null);
    prevSession.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    const onVis = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    setEffortNote("");
    if (!sessionId) {
      setRemoteEffort(null);
      return;
    }
    const requested = sessionId;
    const gen = ++effortGen.current;
    invalidateSessionEffort(requested);
    let cancelled = false;
    void (async () => {
      try {
        let view = await fetchSessionEffort(requested);
        if (cancelled || effortGen.current !== gen) return;
        if (!view.effort) {
          const cached = cachedSessionEffort(requested);
          if (cached) {
            view = await saveSessionEffort(requested, cached);
            if (cancelled || effortGen.current !== gen) return;
          }
        }
        if (view.source === "pending" && view.running === false && view.effort) {
          view = await saveSessionEffort(requested, view.effort);
          if (cancelled || effortGen.current !== gen) return;
        }
        if (view.effort) {
          hydrateSessionEffort(requested, view.effort);
          setRemoteEffort({ sessionId: requested, level: view.effort });
        } else {
          setRemoteEffort(null);
        }
        setEffortNote(view.applied === "next_turn" ? NEXT_TURN_EFFORT : "");
      } catch (error) {
        if (cancelled || effortGen.current !== gen) return;
        // 首发后会话行可能尚未可见。404 不阻断、不改项目/普通模式，本地档继续用于本次发送。
        if (error instanceof ApiError && (error.status === 404 || error.code === "session_not_found")) return;
        setNotice(`档位未能从服务器取回：${error instanceof Error ? error.message : "未知错误"}`);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || active || serverBusy) return;
    const requested = sessionId;
    let cancelled = false;
    void (async () => {
      let view;
      try {
        view = await fetchSessionEffort(requested);
      } catch {
        return;
      }
      if (cancelled || useUiStore.getState().selectedSessionId !== requested) return;
      if (view.source !== "pending" || view.running !== false || !view.effort) return;
      try {
        const saved = await saveSessionEffort(requested, view.effort);
        if (cancelled || useUiStore.getState().selectedSessionId !== requested) return;
        if (!saved.effort || saved.applied === "next_turn") return;
        hydrateSessionEffort(requested, saved.effort);
        setRemoteEffort({ sessionId: requested, level: saved.effort });
        setEffortNote("");
      } catch (error) {
        if (cancelled || useUiStore.getState().selectedSessionId !== requested) return;
        setNotice(`档位未能在回合结束后写入会话：${error instanceof Error ? error.message : "未知错误"}`);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId, active, serverBusy]);

  useEffect(() => {
    if (!pendingSend || !shown) return;
    const live = shown.status === "starting" || shown.status === "running" || shown.text || shown.error;
    if (live && shown.userText.trim() === pendingSend.trim()) setPendingSend("");
  }, [pendingSend, shown]);

  useEffect(() => {
    if (sessionId || nextChatModel()) return;
    const gen = sessionId;
    void api.modelOptions().then((options) => {
      if (gen !== "" || useUiStore.getState().selectedSessionId || nextChatModel()) return;
      if (!options.current.model || !options.current.provider) return;
      rememberChatModel("", options.current.model, options.current.provider);
    }).catch(() => undefined);
  }, [sessionId]);

  useLayoutEffect(() => {
    const switched = messagesSession.current !== sessionId;
    if (switched) {
      messagesSession.current = sessionId;
      committedReload.current = 0;
      setMessages([]);
      setListedSession(sessionId);
      setPendingSend("");
      setNotes(new Map());
      setHasEarlier(false);
      setEarlierError("");
      setNotice("");
      setEditingKey(null);
      setHold(null);
      setLoadingEarlier(false);
      setServerBusy(false);
      setServerExecution(null);
      setExecutionConfirm(false);
      setSyncError("");
      setStick(true);
      setAtBottom(true);
    }
    if (!sessionId || !history.data) {
      if (switched) setMessages([]);
      return;
    }
    // 长度只拦同一会话里已展开的更早消息，不能用上一段的长度拦住新会话。
    // 被更新的 reload 作废的查询页不再盖回；同长内容更新仍要进入。
    if (!switched && supersededPages.current.has(history.data.messages)) return;
    if (!switched && (history.data.messages === messagesRef.current || messagesRef.current.length > history.data.messages.length)) return;
    setMessages(history.data.messages);
    setListedSession(sessionId);
    setHasEarlier(history.data.hasMore);
    const requested = sessionId;
    const page = history.data.messages;
    void loadCommentary(page).then((loaded) => {
      if (messagesSession.current !== requested) return;
      setNotes(new Map(loaded));
      liveRef.current.restoreSteers(requested, page);
    });
  }, [history.data, sessionId]);

  const reloadHistory = useCallback(async (id: string): Promise<true | "superseded"> => {
    const gen = ++reloadGen.current;
    const limit = Math.min(MAX_PAGE, Math.max(PAGE, messagesRef.current.length || PAGE));
    try {
      const page = await client.fetchQuery({
        queryKey: queryKeys.messages(id, 0, limit),
        queryFn: ({ signal }) => api.messages(id, { limit, offset: 0 }, signal),
      });
      const markSuperseded = (messages: Message[]) => {
        supersededPages.current.add(messages);
        const cached = client.getQueryData<{ messages?: Message[] }>(queryKeys.messages(id, 0, limit));
        if (cached?.messages) supersededPages.current.add(cached.messages);
      };
      if (messagesSession.current !== id) return true;
      if (gen < committedReload.current) {
        markSuperseded(page.messages);
        return "superseded";
      }
      committedReload.current = gen;
      if (!stickRef.current) setHold(listRef.current?.capture() ?? null);
      setMessages(mergeExpandedWindow(messagesRef.current, page.messages, limit));
      setHasEarlier(page.hasMore);
      const loaded = await loadCommentary(page.messages);
      if (gen < committedReload.current) {
        markSuperseded(page.messages);
        return "superseded";
      }
      if (messagesSession.current !== id) return true;
      setNotes(new Map(loaded));
      liveRef.current.restoreSteers(id, page.messages);
      return true;
    } catch (error) {
      if (reloadGen.current !== gen) return "superseded";
      throw error;
    }
  }, [client]);

  useEffect(() => {
    const finish = (event: Event) => {
      const id = sessionDetail(event);
      if (!id) return;
      if (messagesSession.current === id && !runs.isActive(id)) {
        setServerBusy(false);
        setServerExecution(null);
      }
      const run = runs.get(id);
      void (async () => {
        let loaded: boolean | "superseded" = false;
        try {
          loaded = await reloadHistory(id);
        } catch {
          loaded = false;
        }
        if (loaded === "superseded") return;
        const current = runs.get(id);
        if (current && current !== run) return;
        if (messagesSession.current !== id) {
          if (loaded) liveRef.current.clear(id);
          return;
        }
        if (!loaded) {
          setNotice("回合已结束，但历史重新加载失败；当前显示的是本次回合的临时内容，刷新页面可重试");
          return;
        }
        if (run?.status === "cancelled") setNotice("已停止");
        else if (run?.status === "failed") {
          setNotice(run.error || "执行失败");
          if (!run.runId && !useUiStore.getState().composerDraft.value) {
            setDraft({ value: run.userText, attachments: run.attachments });
          }
        } else setNotice("");
        liveRef.current.clear(id);
        window.setTimeout(() => { void context.refetch(); }, 800);
        window.dispatchEvent(new CustomEvent("hp-session-updated", { detail: id }));
      })();
    };
    runs.addEventListener(RUN_FINISHED, finish);
    return () => runs.removeEventListener(RUN_FINISHED, finish);
  }, [reloadHistory, setDraft, context]);

  useEffect(() => {
    if (!sessionId || slotHidden) return;
    const ctrl = new AbortController();
    let stopped = false;
    const sync = async () => {
      if (stopped || slotHidden || document.visibilityState !== "visible") return;
      const started = runs.get(sessionId);
      const healthy = !!started && runs.isActive(sessionId) && started.streamGone === false && started.replayTruncated !== true;
      try {
        if (!healthy) {
          const state = await sidecar.sessionRun(sessionId, ctrl.signal);
          if (stopped || messagesSession.current !== sessionId) return;
          if (typeof state.busy !== "boolean" || !("run" in state)) throw new Error("任务查询返回异常");
          const found = state.run;
          const foundId = found?.run_id || "";
          const joined = () => discoveryJoined(sessionId, started);
          if (!joined() && !discoveryClosed(sessionId, started, foundId)) {
            if (runs.isActive(sessionId)) await liveRef.current.reconcile(sessionId, state);
            if (stopped || messagesSession.current !== sessionId || joined()) return;
            if (discoveryClosed(sessionId, started, foundId)) return;
            setServerBusy(state.busy);
            if (!state.busy) setServerExecution(null);
            if (state.busy && !runs.isActive(sessionId) && !joined()) {
              try {
                const execution = await sidecar.sessionExecution(sessionId, ctrl.signal);
                if (stopped || messagesSession.current !== sessionId || joined() || discoveryClosed(sessionId, started, foundId)) return;
                setServerExecution(execution.execution);
              } catch {
                if (stopped || messagesSession.current !== sessionId || joined() || discoveryClosed(sessionId, started, foundId)) return;
                setServerExecution(null);
              }
            }
            if (!joined() && found && found.session_id === sessionId && found.run_id && ["queued", "running", "waiting_for_approval", "stopping"].includes(found.status)) {
              const confirmed = await api.runStatus(found.run_id, ctrl.signal);
              if (stopped || messagesSession.current !== sessionId || joined()) return;
              if ((confirmed.session_id && confirmed.session_id !== sessionId) || (confirmed.run_id && confirmed.run_id !== found.run_id)) {
                throw new Error("任务与当前会话不一致");
              }
              if (discoveryClosed(sessionId, started, found.run_id)) {
                const now = runs.get(sessionId);
                if (!now || now.runId === found.run_id) {
                  setServerBusy(false);
                  setServerExecution(null);
                }
                return;
              }
              if (["queued", "running", "waiting_for_approval", "stopping"].includes(confirmed.status) && !runs.isActive(sessionId)) {
                const held = runs.get(sessionId);
                const abandoned = Boolean(held?.unverified && !held.runId);
                if (!abandoned) {
                  const createdAt = typeof confirmed.created_at === "number" ? confirmed.created_at : found.created_at;
                  liveRef.current.observe(sessionId, found.run_id, createdAt * 1000, confirmed.status === "stopping");
                }
              }
            }
          } else if (!joined() && discoveryClosed(sessionId, started, foundId) && (!state.busy || !foundId || runs.isRetired(sessionId, foundId) || (runs.get(sessionId)?.runId === foundId && !runs.isActive(sessionId)))) {
            setServerBusy(false);
            setServerExecution(null);
          }
        }
        setSyncError("");
        const wait = historyReloadMs(runs.isActive(sessionId));
        if (Date.now() - (syncAt.current) >= wait && !editingKey && !loadingEarlier) {
          syncAt.current = Date.now();
          if (runs.get(sessionId) && !runs.isActive(sessionId)) return;
          await reloadHistory(sessionId);
        }
      } catch (error) {
        if (stopped || ctrl.signal.aborted || messagesSession.current !== sessionId || discoveryJoined(sessionId, started)) return;
        setSyncError(error instanceof ApiError && error.kind === "unauthorized" ? "登录已失效，无法同步任务状态" : "任务状态同步暂不可用，正在重试");
      }
    };
    const syncAt = { current: 0 };
    const timer = window.setInterval(() => void sync(), HISTORY_SYNC_MS);
    const wake = () => void sync();
    window.addEventListener("online", wake);
    window.addEventListener("focus", wake);
    window.addEventListener("pageshow", wake);
    document.addEventListener("visibilitychange", wake);
    void sync();
    return () => {
      stopped = true;
      ctrl.abort();
      window.clearInterval(timer);
      window.removeEventListener("online", wake);
      window.removeEventListener("focus", wake);
      window.removeEventListener("pageshow", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [sessionId, slotHidden, editingKey, loadingEarlier, reloadHistory]);

  useEffect(() => {
    if (!sessionId || slotHidden) return;
    const ctrl = new AbortController();
    const kick = () => {
      if (ctrl.signal.aborted) return;
      if ((useUiStore.getState().selectedSessionId ?? "") !== sessionId) return;
      const held = runs.get(sessionId);
      if (!held?.followDropped || !held.runId || !runs.isActive(sessionId)) return;
      if (held.status !== "starting" && held.status !== "running") return;
      runs.resubscribe(sessionId, held.runId, held.startedAt, ctrl.signal);
    };
    kick();
    const onRun = (event: Event) => {
      if (sessionDetail(event) !== sessionId) return;
      kick();
    };
    runs.addEventListener(RUN_CHANGED, onRun);
    return () => {
      ctrl.abort();
      runs.removeEventListener(RUN_CHANGED, onRun);
    };
  }, [sessionId, slotHidden]);

  const ownsHistory = Boolean(shown && (shown.streamGone || shown.replayTruncated));
  const liveView = useMemo(() => {
    if (!shown) return shown;
    const visible = visibleTurnsBesideLive(messages, shown, {
      localActive,
      ownsHistory,
      serverBusy,
      drawnSteers: [],
    }, notes);
    const saved = [...visible].reverse().find((turn) => turn.role === "assistant");
    const text = liveTextBesideHistory(shown.text, saved && saved.role === "assistant" ? saved.text : "");
    if (ownsHistory) return { ...shown, steps: [], thinking: "", text };
    return text === shown.text ? shown : { ...shown, text };
  }, [shown, ownsHistory, messages, localActive, serverBusy, notes]);
  const displayRun = liveView ?? shown;
  const showLive = Boolean(displayRun && (displayRun.status === "starting" || displayRun.status === "running" || displayRun.text || displayRun.error));
  const pendingVisible = Boolean(pendingSend) && !(showLive && displayRun?.userText.trim() === pendingSend.trim());
  const keepRunningTools = localActive && !shouldStripInFlightHistory(shown, { localActive, ownsHistory });
  const turns = useMemo(
    () => visibleTurnsBesideLive(messages, shown, {
      localActive,
      ownsHistory,
      serverBusy,
      drawnSteers: showLive ? (shown?.steers ?? []) : [],
    }, notes),
    [messages, shown, localActive, ownsHistory, serverBusy, showLive, notes],
  );
  const subagentQuery = useSubagents(
    sessionId,
    50,
    visible && !slotHidden && sessionId ? 4000 : false,
    Boolean(sessionId) && !slotHidden,
  );
  const taskItems = useMemo(() => {
    if (subagentQuery.status !== "success" || !subagentQuery.data) {
      return tasksForSession(listedSession, sessionId, messages);
    }
    const profileKey = serviceProfileKey(subagentQuery.data);
    if (profileKey === "unknown") {
      return tasksForSession(listedSession, sessionId, messages);
    }
    return tasksForSession(
      listedSession,
      sessionId,
      messages,
      taskExecutions(subagentQuery.data, sessionId, profileKey),
      { sessionId, profileKey },
    );
  }, [listedSession, sessionId, messages, subagentQuery.status, subagentQuery.data]);
  useLayoutEffect(() => {
    const height = taskDockRef.current?.offsetHeight ?? 0;
    root?.style.setProperty("--task-dock", `${height}px`);
  }, [taskItems, root]);
  const userInHistory = useMemo(() => {
    if (!shown) return false;
    return turns.some((turn) => turn.role === "user" && !turn.steer && turn.text.trim() === shown.userText.trim());
  }, [shown, turns]);
  const restoredAdmission = useRef("");
  useEffect(() => {
    if (!shown?.locallyReleased || !shown.unverified || shown.runId || shown.sessionId !== sessionId) return;
    if (useUiStore.getState().selectedSessionId !== sessionId || runs.get(sessionId)?.idempotencyKey !== shown.idempotencyKey) return;
    const token = `${sessionId}:${shown.idempotencyKey ?? ""}`;
    if (restoredAdmission.current === token) return;
    if (history.isLoading) return;
    if (userInHistory) {
      restoredAdmission.current = token;
      return;
    }
    const current = useUiStore.getState().composerDraft;
    if (current.value || current.attachments.length) {
      restoredAdmission.current = token;
      return;
    }
    if (!shown.userText && shown.attachments.length === 0) return;
    restoredAdmission.current = token;
    setDraft({ value: shown.userText, attachments: shown.attachments });
  }, [shown, sessionId, history.isLoading, userInHistory, setDraft]);

  const rows = useMemo(() => {
    const items: Array<{ key: string; kind: "earlier" | "turn" | "live" | "note"; turn?: Turn }> = [
      { key: "earlier", kind: "earlier" },
    ];
    turns.forEach((turn, index) => {
      items.push({ key: turnSourceKey(turn) ?? `${turn.role}:${index}`, kind: "turn", turn });
    });
    if (showLive && displayRun) {
      items.push({ key: `live:${displayRun.sessionId}:${displayRun.runId || displayRun.startedAt}`, kind: "live" });
    }
    if (notice) items.push({ key: "notice", kind: "note" });
    return items;
  }, [turns, shown, notice, showLive, displayRun]);
  const keys = useMemo(() => rows.map((row) => row.key), [rows]);

  const restore = useCallback((text: string, attachments: Attachment[]) => {
    const current = useUiStore.getState().composerDraft;
    if (current.value || current.attachments.length) return;
    setDraft({ value: text, attachments });
  }, [setDraft]);

  const onSend = useCallback(async () => {
    const held = runs.get(sessionId);
    const abandonedUnconfirmed = Boolean(held?.unverified && !held.runId && !liveRef.current.isActive);
    if (editingKey || preparing) return;
    if (serverBusy && !liveRef.current.isActive && !abandonedUnconfirmed) return;
    const text = draft.value.trim();
    const attachments = draft.attachments;
    if (!text && attachments.length === 0) return;
    const content = buildContent(text, attachments);
    setNotice("");
    setStick(true);
    setAtBottom(true);
    setDraft({ value: "", attachments: [] });
    if (!sessionId) setPendingSend(text);
    try {
      if (sessionId) {
        await ensureSessionEffort(sessionId);
        const provider = await providerFor(displayModel, override ? displayProvider : "");
        liveRef.current.start(sessionId, text, attachments, content, displayModel, provider);
        return;
      }
      setCreating(true);
      const projectId = getPendingNewChatProjectId();
      const picked = override ?? nextChatModel() ?? await api.modelOptions().then((options) => {
        if (!options.current.model || !options.current.provider) return null;
        rememberChatModel("", options.current.model, options.current.provider);
        return options.current;
      });
      if (!picked?.model || !picked.provider) throw new ApiError("server", 0, "还没有可使用的模型");
      const provider = await providerFor(picked.model, picked.provider);
      const created: SessionSummary = await createSession.mutateAsync(titleFrom(text, attachments));
      await prepareCreatedSession({
        sessionId: created.id,
        projectId,
        bind: projectsApi.bind,
        lock: () => lockModel.mutateAsync({ sessionId: created.id, model: picked.model, provider }),
        remove: api.deleteSession,
      });
      rememberChatModel(created.id, picked.model, provider);
      setEffortFor(currentEffort(""), created.id);
      // 先绑再锁再发。档位行尚未落下时不得挡住发送，也不得改走普通模式。
      liveRef.current.start(created.id, text, attachments, content, picked.model, provider);
      setSelectedSessionId(created.id);
      const createdId = created.id;
      void saveSessionEffort(createdId, currentEffort(createdId)).then((saved) => {
        if (useUiStore.getState().selectedSessionId !== createdId || !saved.effort) return;
        hydrateSessionEffort(createdId, saved.effort);
      }).catch((error) => {
        if (useUiStore.getState().selectedSessionId !== createdId) return;
        if (error instanceof ApiError && (error.status === 404 || error.code === "session_not_found")) return;
        setNotice(`对话已发送。档位尚未写入会话，不影响本次发送，也不会改成普通模式：${error instanceof Error ? error.message : "未知错误"}`);
      });
      window.dispatchEvent(new CustomEvent("hp-session-created", { detail: created }));
    } catch (error) {
      setPendingSend("");
      setNotice(sessionId ? (error instanceof Error ? error.message : "发送失败") : error instanceof ApiError ? `创建对话失败：${error.message}` : "创建对话失败");
      restore(text, attachments);
    } finally {
      setCreating(false);
    }
  }, [editingKey, serverBusy, preparing, draft, sessionId, displayModel, displayProvider, override, createSession, lockModel, setEffortFor, setSelectedSessionId, setDraft, restore]);

  const onSteer = useCallback(async () => {
    const text = draft.value.trim();
    const attachments = draft.attachments;
    if ((!text && attachments.length === 0) || !shown?.runId || shown.status !== "running") return;
    if (attachments.some((item) => item.kind === "image")) {
      setFlash("回合进行中图片需要重新添加（会改为上传发送），请先移除已有的图片");
      return;
    }
    setDraft({ value: "", attachments: [] });
    const ok = await liveRef.current.steer(sessionId, text, attachments);
    if (!ok) restore(text, attachments);
  }, [draft, shown, sessionId, setDraft, restore]);

  const onStop = useCallback(async () => {
    const target = runs.get(sessionId);
    try {
      await liveRef.current.stop(sessionId);
    } catch (error) {
      if (runs.get(sessionId) === target) setNotice(`停止失败：${error instanceof Error ? error.message : "未知错误，请重试"}`);
      return;
    }
    const held = runs.get(sessionId);
    if (useUiStore.getState().selectedSessionId !== sessionId || held !== target) return;
    if (!held?.unverified || held.runId || runs.isActive(sessionId)) return;
    const current = useUiStore.getState().composerDraft;
    if (current.value || current.attachments.length || (!held.userText && held.attachments.length === 0)) return;
    setDraft({ value: held.userText, attachments: held.attachments });
  }, [sessionId, setDraft]);

  const onModel = useCallback(async (model: string, provider: string) => {
    const prev = override;
    setOverride({ model, provider });
    if (!sessionId) {
      rememberChatModel("", model, provider);
      return;
    }
    try {
      await lockModel.mutateAsync({ sessionId, model, provider });
      rememberChatModel(sessionId, model, provider);
      window.dispatchEvent(new CustomEvent("hp-session-updated", { detail: sessionId }));
    } catch (error) {
      setOverride(prev);
      setNotice(error instanceof ApiError ? `切换模型失败：${error.message}` : "切换模型失败");
    }
  }, [override, sessionId, lockModel]);

  const editReason = useCallback((turn: UserTurn): string => {
    if (serverBusy && !shown) return "任务正在另一端执行，请先在那里停止并等待结束后再编辑";
    if (localActive || shown) return "请先停止当前回复，等待停止完成并加载历史后再编辑";
    if (history.isLoading || history.isError || loadingEarlier) return "请等待历史加载完成，失败时先重试加载";
    if (editingKey && editingKey !== String(turn.message?.id ?? "")) return "请先完成或取消正在编辑的消息";
    if (!turn.message) return "这条消息尚未保存到历史，暂不能编辑";
    if (turn.files.length || turn.uploads.length || turn.images.length || turn.droppedImages) return "附件的原始内容无法可靠重建，暂不支持编辑带附件的消息";
    try {
      editBoundary(messages, turn.message);
      return "";
    } catch (error) {
      return error instanceof Error ? error.message : "无法确认消息边界，不能编辑";
    }
  }, [serverBusy, shown, localActive, history.isLoading, history.isError, loadingEarlier, editingKey, messages]);

  const onEditSend = useCallback(async (turn: UserTurn, text: string) => {
    if (!turn.message || !sessionId) return;
    const boundary = editBoundary(messages, turn.message);
    const options = await api.modelOptions();
    const chosenModel = displayModel;
    const chosenProvider = override ? displayProvider : "";
    const providers = new Set(options.choices.filter((choice) =>
      canonicalModel(chosenModel, [choice], choice.provider) === canonicalModel(choice.model, [choice], choice.provider),
    ).map((choice) => choice.provider));
    const provider = chosenProvider || (chosenModel ? (providers.size === 1 ? [...providers][0] ?? "" : "") : "");
    const model = canonicalModel(chosenModel || "", options.choices, provider);
    if (!model || !provider) throw new Error("无法确认当前模型与提供方，请先重新选择模型");
    const key = `${sessionId}:${turn.message.id ?? turn.message.timestamp}`;
    let attempt = attempts.current.get(key);
    if (!attempt) {
      attempt = { requestId: crypto.randomUUID(), startAttempted: false };
      attempts.current.set(key, attempt);
    }
    const effort = currentEffort(sessionId);
    try {
      const result = await resendEdited({
        sessionId,
        boundary,
        originalText: turn.text,
        text,
        model,
        provider,
        effort,
      }, attempt, {
        branch: (body) => editBranch.mutateAsync(body),
        lock: (id, nextModel, nextProvider) => api.lockModel(id, nextModel, nextProvider),
        start: api.startRun,
      });
      setEffortFor(effort, result.session.id);
      try {
        const saved = await saveSessionEffort(result.session.id, effort);
        if (saved.effort) hydrateSessionEffort(result.session.id, saved.effort);
      } catch (error) {
        setNotice(`编辑已发出，但档位未能写入新会话：${error instanceof Error ? error.message : "未知错误"}`);
      }
      liveRef.current.start(result.session.id, text.trim(), [], text.trim(), model, provider, result.started);
      if (useUiStore.getState().selectedSessionId === sessionId) setSelectedSessionId(result.session.id);
      window.dispatchEvent(new CustomEvent("hp-session-created", { detail: result.session }));
      setEditingKey(null);
    } catch (error) {
      if (attempt.session) {
        setNotice(`编辑副本已保留：${attempt.session.title || attempt.session.id}。原会话未修改；发送结果不明确时，请先打开副本查看。`);
      }
      throw new Error(`编辑重发失败：${error instanceof Error ? error.message : "未知错误，请重试"}`);
    }
  }, [messages, sessionId, displayModel, displayProvider, override, editBranch, setEffortFor, setSelectedSessionId]);

  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const onEditSendRef = useRef(onEditSend);
  onEditSendRef.current = onEditSend;
  const startEdit = useCallback((key: string) => {
    setEditingKey(key);
    setStick(false);
  }, []);
  const endEdit = useCallback(() => setEditingKey(null), []);
  const sendEdit = useCallback(async (key: string, text: string) => {
    const turn = turnsRef.current.find((item) => item.role === "user" && String(item.message?.id ?? "") === key);
    if (turn?.role === "user") await onEditSendRef.current(turn, text);
  }, []);
  const ignoreEdit = useCallback((key: string) => { void key; }, []);
  const ignoreSend = useCallback(async (key: string, text: string) => { void key; void text; }, []);

  const loadEarlier = useCallback(async () => {
    if (!sessionId || loadingEarlier || !hasEarlier || history.isLoading) return;
    setLoadingEarlier(true);
    setEarlierError("");
    const epoch = reloadGen.current;
    const anchored = messagesRef.current;
    try {
      const offset = anchored.length;
      const page = await client.fetchQuery({
        queryKey: queryKeys.messages(sessionId, offset, PAGE),
        queryFn: ({ signal }) => api.messages(sessionId, { limit: PAGE, offset }, signal),
      });
      // 手动或自动换了最新页之后，旧 offset 页不能再接到新窗口前面。
      if (messagesSession.current !== sessionId || reloadGen.current !== epoch || messagesRef.current !== anchored || messagesRef.current.length < offset) return;
      const anchor = listRef.current?.capture() ?? null;
      const loaded = await loadCommentary(page.messages);
      if (messagesSession.current !== sessionId || reloadGen.current !== epoch || messagesRef.current !== anchored) return;
      setStick(false);
      setHold(anchor);
      setNotes((prev) => new Map([...prev, ...loaded]));
      setMessages((prev) => (prev !== anchored || reloadGen.current !== epoch ? prev : [...page.messages, ...prev]));
      setHasEarlier(page.hasMore);
      liveRef.current.restoreSteers(sessionId, [...page.messages, ...messagesRef.current]);
    } catch (error) {
      setEarlierError(error instanceof ApiError ? error.message : "加载失败");
      setHold(null);
    } finally {
      setLoadingEarlier(false);
    }
  }, [sessionId, loadingEarlier, hasEarlier, history.isLoading, client]);

  const onContentClick = (event: React.MouseEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    const path = event.nativeEvent.composedPath();
    if (disclosureLeavesBottom(path)) {
      setStick(false);
      setAtBottom(false);
    }
    const target = event.target;
    if (!(target instanceof Element)) return;
    const link = target.closest("a");
    if (link instanceof HTMLAnchorElement && link.href && !link.hasAttribute("download") && !link.href.startsWith("data:") && !link.href.startsWith("blob:")) {
      const nav = navigator as Navigator & { standalone?: boolean };
      const standalone = nav.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
      if (standalone) {
        event.preventDefault();
        window.open(link.href, "_blank", "noopener,noreferrer");
      }
    }
    const button = target.closest("[data-copy]");
    if (!(button instanceof HTMLButtonElement)) return;
    const code = button.closest(".code")?.querySelector("code")?.textContent ?? "";
    void navigator.clipboard.writeText(code).then(() => {
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = "复制"; }, 1200);
    }).catch(() => { button.textContent = "复制失败"; });
  };

  const abandonedUnconfirmed = unconfirmed && !localActive;
  const remoteHold = serverBusy && !localActive && !abandonedUnconfirmed;
  const typingLocked = creating || editingKey !== null;
  const canSteer = Boolean(active && shown?.runId && shown.status === "running" && !shown.stopping);
  const lastTurn = turns.at(-1);
  const browser = shown?.steps ?? (lastTurn?.role === "assistant" ? lastTurn.steps : []);

  const historyError = history.isError ? (history.error instanceof Error ? history.error.message : "加载失败") : "";
  const blank = turns.length === 0 && !showLive && !pendingVisible;
  const starts = (background.data ?? []).flatMap((item) => item.startedAt ? [item.startedAt] : []);
  const since = starts.length ? ` · 已运行 ${duration(Date.now() / 1000 - Math.min(...starts))}` : "";

  return (
    <section ref={setRoot} className={`page hp-chat${shown?.clarify ? " asking" : ""}`} aria-label="对话" onClick={onContentClick}>
      <ChatStyles />
      <MessageList
        keys={keys}
        stick={stick}
        hold={hold}
        listRef={listRef}
        onStick={(next, bottom) => { setStick(next); setAtBottom(bottom); }}
        onNearTop={() => { if (hasEarlier && !loadingEarlier) void loadEarlier(); }}
        onUserScroll={() => setHold(null)}
      >
        {historyError && blank ? (
          <div className="center">{historyError}<br /><button type="button" onClick={() => void history.refetch()}>重试</button></div>
        ) : history.isLoading && blank ? (
          <div className="center" aria-busy="true" />
        ) : blank ? (
          <div className="empty"><h1>有什么可以帮你？</h1></div>
        ) : (
          <div className="col">
            {pendingVisible ? <div className="turn user" data-pending-send=""><div className="bubble">{pendingSend}</div></div> : null}
            {hasEarlier ? (
              <div className="earlier-bar">
                {loadingEarlier ? (
                  <><span className="earlier-spinner" aria-hidden="true" /><span>正在加载历史记录…</span></>
                ) : earlierError ? (
                  <button type="button" className="earlier-btn retry" onClick={() => void loadEarlier()}>加载失败（{earlierError}），点击重试</button>
                ) : (
                  <button type="button" className="earlier-btn" onClick={() => void loadEarlier()}>加载更早的历史记录</button>
                )}
              </div>
            ) : null}
            {turns.map((turn, index) => {
              const source = turnSourceKey(turn) ?? `${turn.role}:${index}`;
              const last = index === turns.length - 1;
              const pending = turn.role === "assistant" && last && (ownsHistory || (keepRunningTools && turn.steps.some((step) => step.kind === "tool" && step.status === "running")));
              return (
                <div className="past" data-history-source={source} key={source}>
                  {turn.role === "user" ? (
                    <HistoryTurn
                      turn={turn}
                      pending={false}
                      outcome=""
                      editDisabled={editReason(turn)}
                      editing={editingKey === String(turn.message?.id ?? "") && String(turn.message?.id ?? "") !== ""}
                      messageKey={String(turn.message?.id ?? "")}
                      onEditStart={startEdit}
                      onEditEnd={endEdit}
                      onEditSend={sendEdit}
                    />
                  ) : (
                    <HistoryTurn
                      turn={turn}
                      pending={pending}
                      outcome={pending ? "" : "已完成"}
                      editDisabled=""
                      editing={false}
                      messageKey=""
                      onEditStart={ignoreEdit}
                      onEditEnd={endEdit}
                      onEditSend={ignoreSend}
                    />
                  )}
                </div>
              );
            })}
            {showLive && displayRun ? (
              <div className="mount-row">
                <LiveTail run={displayRun} userInHistory={userInHistory} skipSteerTexts={(() => {
                  let start = 0;
                  turns.forEach((turn, index) => { if (turn.role === "user" && !turn.steer) start = index; });
                  return turns.flatMap((turn, index) => index >= start && turn.role === "user" && turn.steer ? [turn.text] : []);
                })()} pace={stick && atBottom && visible && !slotHidden} onApprove={(choice: ApprovalChoice) => { void liveRef.current.respondApproval(sessionId, choice); }} />
              </div>
            ) : null}
            {serverBusy && !localActive ? (
              <div className="turn assistant" role="status">
                <div className="took">任务在另一端执行，等待实时连接</div>
                {serverExecution ? (
                  <div className="choices">
                    {executionConfirm ? (
                      <>
                        <p>这会结束该会话的独立 CLI 进程。</p>
                        <button type="button" className="deny" disabled={stopExecution.isPending} onClick={() => { const requested = sessionId; void stopExecution.mutateAsync({ sessionId: requested, executionId: serverExecution.id }).then(() => { if (messagesSession.current === requested) setServerExecution(null); }); }}>{stopExecution.isPending ? "正在停止…" : "确认停止"}</button>
                        <button type="button" disabled={stopExecution.isPending} onClick={() => setExecutionConfirm(false)}>取消</button>
                      </>
                    ) : <button type="button" className="deny" onClick={() => setExecutionConfirm(true)}>停止独立任务</button>}
                  </div>
                ) : null}
              </div>
            ) : null}
            {background.data && background.data.length > 0 && !shown ? (
              <details className="turn bg-procs" role="status">
                <summary>后台还有 {background.data.length} 个进程在运行{since}</summary>
                <ul>{background.data.map((item) => <li key={item.id}><code>{item.command || item.id}</code></li>)}</ul>
              </details>
            ) : null}
            {historyError && showLive ? <div className="notice err">{historyError} <button type="button" onClick={() => void history.refetch()}>重试</button></div> : null}
            {syncError ? <div className="notice err" role="status">{syncError}</div> : null}
            {effortNote ? <div className="notice" role="status">{effortNote}</div> : null}
            {notice ? <div className={`notice${notice === "已停止" ? "" : " err"}`}>{notice}</div> : null}
          </div>
        )}
      </MessageList>
      {atBottom ? null : (
        <button type="button" className="jump" aria-label="回到底部" onClick={() => { setStick(true); setAtBottom(true); }}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M7 2v10M3 8l4 4 4-4" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
      <div className="composer">
        <ComposerPanels sessionId={sessionId} active={Boolean(active)} tasks={taskItems} taskDockRef={taskDockRef}>
          <BrowserTask steps={browser ?? []} active={Boolean(active)} />
        </ComposerPanels>
        <Composer
          sessionId={sessionId}
          value={draft.value}
          attachments={draft.attachments}
          disabled={typingLocked}
          sendBlocked={remoteHold}
          running={Boolean(active)}
          canSteer={canSteer}
          preparing={preparing}
          stopping={Boolean(shown?.stopping)}
          unconfirmed={unconfirmed && localActive}
          flash={flash}
          onChange={(value, attachments) => setDraft({ value, attachments })}
          onPreparing={guardPreparing}
          onFlash={setFlash}
          onSend={() => void onSend()}
          onSteer={() => void onSteer()}
          onStop={() => void onStop()}
          tools={(
            <>
              <ModelPicker
                value={displayModel}
                provider={displayProvider}
                options={models.data}
                failed={models.isError}
                onOpen={() => { void models.refetch(); }}
                onSelect={(model, provider) => void onModel(model, provider)}
              />
              <EffortPicker
                model={displayModel || models.data?.current.model || ""}
                stored={shownEffort}
                sessionId={sessionId}
                onChange={(level) => {
                  if (!sessionId) {
                    setEffortFor(level, "");
                    setEffortNote("");
                    return;
                  }
                  const requested = sessionId;
                  const gen = ++effortGen.current;
                  void saveSessionEffort(requested, level).then((view) => {
                    if (effortGen.current !== gen || useUiStore.getState().selectedSessionId !== requested || !view.effort) return;
                    hydrateSessionEffort(requested, view.effort);
                    setRemoteEffort({ sessionId: requested, level: view.effort });
                    setEffortFor(view.effort, requested);
                    setEffortNote(view.applied === "next_turn" ? NEXT_TURN_EFFORT : "");
                  }).catch((error) => {
                    if (effortGen.current !== gen || useUiStore.getState().selectedSessionId !== requested) return;
                    setNotice(`档位未保存：${error instanceof Error ? error.message : "未知错误"}`);
                  });
                }}
              />
              <ContextRing sessionId={sessionId} usage={context.data} failed={context.isError} onOpen={() => { void context.refetch(); }} />
            </>
          )}
        />
      </div>
      {shown?.clarify ? (
        <ClarifySheet
          prompt={shown.clarify}
          onSubmit={(answers) => { void liveRef.current.respondClarify(sessionId, answers); }}
        />
      ) : null}
    </section>
  );
}
