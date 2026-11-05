import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import { useNavigate } from "react-router";
import DOMPurify from "dompurify";
import { ApiError, api, type SessionSummary } from "@/lib/api";
import { artifactReference, parseAssistantContent, type Artifact, type AssistantContent as AssistantParts } from "@/lib/artifacts";
import { loadCommentary } from "@/lib/commentary";
import {
  createMarkdownStreamState,
  finalizeMarkdownBlocks,
  isGeneratedCodeBlock,
  isGeneratedTextBlock,
  renderMarkdown,
  renderMarkdownBlocks,
  type MarkdownBlock,
  type MarkdownStreamState,
} from "@/lib/markdown";
import { fetchAndSave } from "@/lib/save-file";
import { sidecar, type Subagent } from "@/lib/sidecar";
import { isBrowserStep, outputPreview, toolGroupSummary, toolLabel, toolSummary } from "@/lib/tools";
import { buildTurns, segments, type Step, type ToolStep, type Turn } from "@/lib/transcript";
import { useUiStore } from "@/stores/ui";
import "./docks.css";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "hp-assistant-content": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
      "hp-steps": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}

const POLL_MS = 3000;
const PAGE = 200;
const STEP_STATUS = { running: "Running", done: "Done", failed: "Failed" } as const;

const STATUS_LABEL: Record<Subagent["status"], string> = {
  running: "进行中",
  stalled: "疑似卡住",
  done: "已完成",
  ended: "已结束",
};

const proseSanitizeLRU = new Map<string, string>();
const PROSE_LRU_MAX = 100;

function sanitizeProse(source: string, useCache = true): string {
  if (useCache) {
    const hit = proseSanitizeLRU.get(source);
    if (hit !== undefined) {
      proseSanitizeLRU.delete(source);
      proseSanitizeLRU.set(source, hit);
      return hit;
    }
  }
  const sanitized = DOMPurify.sanitize(source, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "audio", "video", "source", "iframe", "style", "link"],
    FORBID_ATTR: ["style", "src", "srcset", "poster", "background"],
  });
  if (useCache) {
    if (proseSanitizeLRU.size >= PROSE_LRU_MAX) {
      const oldest = proseSanitizeLRU.keys().next().value;
      if (oldest !== undefined) proseSanitizeLRU.delete(oldest);
    }
    proseSanitizeLRU.set(source, sanitized);
  }
  return sanitized;
}

let activeScrollLocks = 0;
let originalBodyOverflow = "";

function acquireBodyScrollLock(): void {
  if (typeof document === "undefined") return;
  if (activeScrollLocks === 0) {
    originalBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  activeScrollLocks += 1;
}

function releaseBodyScrollLock(): void {
  if (typeof document === "undefined") return;
  if (activeScrollLocks > 0) {
    activeScrollLocks -= 1;
    if (activeScrollLocks === 0) {
      document.body.style.overflow = originalBodyOverflow;
      originalBodyOverflow = "";
    }
  }
}

function isSafeImageArtifact(artifact: Artifact): boolean {
  if (!artifact.image) return false;
  return artifact.preview.startsWith("data:image/") || artifact.preview.startsWith("/panel-sidecar/files/artifact?");
}

function safeBlock(block: MarkdownBlock, cache: WeakMap<MarkdownBlock, string>): string {
  if (isGeneratedCodeBlock(block) || isGeneratedTextBlock(block)) return block.html;
  let safe = cache.get(block);
  if (safe === undefined) {
    safe = sanitizeProse(block.html, false);
    cache.set(block, safe);
  }
  return safe;
}

function useAssistantModel(text: string, streaming: boolean): { parsed: AssistantParts; html: string } {
  const cache = useRef<{ text?: string; result?: AssistantParts }>({});
  const blocks = useRef<{
    source?: string;
    list: MarkdownBlock[];
    finalized: boolean;
    state: MarkdownStreamState;
    safe: WeakMap<MarkdownBlock, string>;
  }>({ list: [], finalized: false, state: createMarkdownStreamState(), safe: new WeakMap() });
  const prose = useRef<{ source?: string; html: string }>({ html: "" });

  let parsed: AssistantParts;
  const prior = cache.current;
  if (prior.text === text && prior.result) {
    parsed = prior.result;
  } else {
    const streamingAppend = streaming && !!prior.result && prior.text !== undefined && text.startsWith(prior.text);
    parsed = parseAssistantContent(text, streamingAppend
      ? { cache: false, priorSource: prior.text, priorResult: prior.result }
      : { cache: !streaming });
    cache.current = { text, result: parsed };
  }

  const slot = blocks.current;
  const useBlocks = streaming || slot.source === parsed.text;
  if (useBlocks) {
    if (slot.source !== parsed.text) {
      slot.list = renderMarkdownBlocks(parsed.text, slot.list, slot.state);
      slot.source = parsed.text;
    }
    if (streaming) slot.finalized = false;
    else if (!slot.finalized) {
      slot.list = finalizeMarkdownBlocks(slot.list, slot.state);
      slot.finalized = true;
    }
    return { parsed, html: slot.list.map((block) => safeBlock(block, slot.safe)).join("") };
  }
  if (prose.current.source !== parsed.text) {
    prose.current = { source: parsed.text, html: sanitizeProse(renderMarkdown(parsed.text, true)) };
  }
  slot.list = [];
  slot.source = undefined;
  slot.state = createMarkdownStreamState();
  slot.safe = new WeakMap();
  slot.finalized = false;
  return { parsed, html: prose.current.html };
}

function enlargeKey(event: ReactKeyboardEvent<HTMLAnchorElement>): void {
  if (event.key !== "Enter" || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (!event.currentTarget.href) return;
  event.preventDefault();
  window.open(event.currentTarget.href, "_blank", "noopener,noreferrer");
}

function AssistantContent(props: { text: string; images: string[]; streaming: boolean }) {
  const { parsed, html } = useAssistantModel(props.text, props.streaming);
  const artifacts = [...parsed.artifacts];
  for (const image of props.images.slice(0, 32)) {
    const artifact = artifactReference(image);
    if (artifact?.image && artifacts.length < 32 && !artifacts.some((item) => item.source === image)) artifacts.push(artifact);
  }
  const [failed, setFailed] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState<Artifact | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const dialogNode = useRef<HTMLDialogElement | null>(null);
  const lastFocused = useRef<HTMLElement | null>(null);
  const leaveTimer = useRef(0);
  const hasLock = useRef(false);
  const alive = useRef(true);
  const savingRef = useRef(false);
  const leavingRef = useRef(false);

  const unlock = () => {
    if (!hasLock.current) return;
    hasLock.current = false;
    releaseBodyScrollLock();
  };

  const finishClose = () => {
    const dialog = dialogRef.current ?? dialogNode.current;
    if (dialog?.open) dialog.close();
    leavingRef.current = false;
    setLeaving(false);
    setExpanded(null);
    setPreviewFailed(false);
    unlock();
    const focus = lastFocused.current;
    lastFocused.current = null;
    if (alive.current && focus && typeof focus.focus === "function") focus.focus();
  };

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (dialog) dialogNode.current = dialog;
    if (!expanded || !dialog || dialog.open) return;
    dialog.showModal();
    if (!hasLock.current) {
      hasLock.current = true;
      acquireBodyScrollLock();
    }
  }, [expanded]);

  useLayoutEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      window.clearTimeout(leaveTimer.current);
      const dialog = dialogNode.current;
      if (dialog?.open) dialog.close();
      if (hasLock.current) {
        hasLock.current = false;
        releaseBodyScrollLock();
      }
    };
  }, []);

  const preview = (event: ReactMouseEvent<HTMLAnchorElement>, artifact: Artifact) => {
    if (!isSafeImageArtifact(artifact)) return;
    event.preventDefault();
    event.stopPropagation();
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = 0;
    leavingRef.current = false;
    setLeaving(false);
    dialogRef.current?.removeAttribute("data-leave");
    lastFocused.current = event.currentTarget;
    setPreviewFailed(false);
    setExpanded(artifact);
  };

  const closePreview = () => {
    const dialog = dialogRef.current;
    if (leavingRef.current || dialog?.hasAttribute("data-leave")) return;
    if (dialog?.open) {
      leavingRef.current = true;
      dialog.setAttribute("data-leave", "");
      setLeaving(true);
      leaveTimer.current = window.setTimeout(() => {
        leaveTimer.current = 0;
        finishClose();
      }, 280);
      return;
    }
    finishClose();
  };

  const save = (event: ReactMouseEvent<HTMLButtonElement>, artifact: Artifact) => {
    event.preventDefault();
    event.stopPropagation();
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setDownloadError("");
    void fetchAndSave(artifact.download, artifact.name).then(() => {
      if (!alive.current) return;
      savingRef.current = false;
      setSaving(false);
    }, (error: unknown) => {
      if (!alive.current) return;
      setDownloadError(error instanceof Error ? error.message : "下载失败，请重试");
      savingRef.current = false;
      setSaving(false);
    });
  };

  return (
    <hp-assistant-content>
      <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
      <div className="artifacts">
        {artifacts.map((artifact) => (
          <section key={artifact.source} className="artifact" aria-label={artifact.image ? "生成图片" : "生成文件"}>
            <span className="name">{artifact.name}</span>
            {artifact.image ? (
              failed.has(artifact.source) ? <p className="error" role="status">图片无法预览，仍可尝试下载</p> : (
                <a href={artifact.preview} target="_blank" rel="noopener noreferrer" aria-label={`放大查看 ${artifact.name}`} onClick={(event) => preview(event, artifact)} onKeyDown={enlargeKey}>
                  <img src={artifact.preview} alt={artifact.name} onError={() => setFailed((prev) => new Set([...prev].slice(-31).concat(artifact.source)))} />
                </a>
              )
            ) : null}
            <div className="actions">
              {artifact.image ? <a href={artifact.preview} target="_blank" rel="noopener noreferrer" onClick={(event) => preview(event, artifact)} onKeyDown={enlargeKey}>放大查看</a> : null}
              <button type="button" className="save" data-save-name={artifact.name} data-save-url={artifact.download.startsWith("data:") ? "" : artifact.download} disabled={saving} onClick={(event) => save(event, artifact)}>{artifact.image ? "下载图片" : "下载文件"}</button>
            </div>
          </section>
        ))}
      </div>
      {downloadError ? <p className="error" role="alert">{downloadError}</p> : null}
      {expanded ? (
        <dialog
          ref={dialogRef}
          data-leave={leaving ? "" : undefined}
          aria-label={`放大查看 ${expanded.name}`}
          onCancel={(event) => { event.preventDefault(); closePreview(); }}
          onClick={(event) => {
            const dialog = event.currentTarget;
            const rect = dialog.getBoundingClientRect();
            if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) closePreview();
          }}
        >
          <img src={expanded.preview} alt={expanded.name} hidden={previewFailed} onError={() => setPreviewFailed(true)} />
          <p className="preview-error" role="status" hidden={!previewFailed}>图片无法预览，仍可尝试下载</p>
          <div className="actions">
            <button type="button" className="download-btn" data-save-name={expanded.name} disabled={saving} onClick={(event) => save(event, expanded)}>下载图片</button>
            <button type="button" data-close onClick={() => closePreview()}>关闭预览</button>
          </div>
        </dialog>
      ) : null}
    </hp-assistant-content>
  );
}

function StepDetail(props: { step: ToolStep; index: number; full: boolean; onMore: (index: number) => void }) {
  const step = props.step;
  const out = step.output ?? "";
  const preview = outputPreview(out);
  const args = step.args ? JSON.stringify(step.args, null, 2) : null;
  return (
    <>
      <div className="preview" data-s={step.status}>
        <span className="branch" aria-hidden="true">⎿</span>
        <pre>{step.output === null ? (step.status === "running" ? "Running…" : "No output captured") : (props.full ? out : preview.text) || "(No output)"}</pre>
      </div>
      {preview.truncated && !props.full ? <button type="button" className="more" onClick={() => props.onMore(props.index)}>Show more</button> : null}
      {step.outputPartial ? <div className="partial">Partial output · full result available after completion</div> : null}
      {args || out ? (
        <details className="detail">
          <summary>Input</summary>
          {args ? <pre>{args}</pre> : null}
          {out ? <><h4>输出</h4><pre>{props.full ? out : preview.text}</pre></> : null}
        </details>
      ) : null}
    </>
  );
}

function StepsView(props: { steps: Step[]; live: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  const [fullOutput, setFullOutput] = useState<Set<number>>(() => new Set());
  if (!props.steps.length) return null;
  const tools = props.steps.filter((step): step is ToolStep => step.kind === "tool");
  const failed = tools.filter((step) => step.status === "failed").length;
  const shown = open ?? false;
  const running = props.live && tools.some((step) => step.status === "running");
  const watch = (event: ReactMouseEvent<HTMLButtonElement>) => {
    const host = event.currentTarget.closest("hp-steps") ?? event.currentTarget;
    host.dispatchEvent(new CustomEvent("hp-live-open", {
      bubbles: true,
      composed: true,
      detail: {
        scope: "task",
        active: props.live,
        token: `task-open-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      },
    }));
  };
  return (
    <hp-steps>
      <button type="button" className="summary" aria-expanded={shown} onClick={() => setOpen(!shown)}>
        <span className="dot" data-s={running ? "running" : failed ? "failed" : "done"} aria-hidden="true" />
        <span className="now">{toolGroupSummary(tools)}</span>
        {failed ? <span className="fail">· {failed} failed</span> : null}
        <span className="hint">{shown ? "collapse" : "expand"}</span>
      </button>
      {tools.some(isBrowserStep) ? <button type="button" className="watch" onClick={watch}>观看</button> : null}
      <div className="fold" data-open={shown ? "" : undefined}>
        <div>
          <ol>
            {props.steps.map((step, index) => step.kind === "note" ? (
              <li key={index} className="note">{step.text}</li>
            ) : (
              <li key={index}>
                <button type="button" className="row" aria-expanded={expanded.has(index)} onClick={() => {
                  const next = new Set(expanded);
                  if (next.has(index)) next.delete(index);
                  else next.add(index);
                  setExpanded(next);
                }}>
                  <span className="dot" data-s={step.status} aria-label={STEP_STATUS[step.status]} />
                  <span className="name" title={toolLabel(step.name)}>{toolLabel(step.name)}</span>
                  <span className="arg">{toolSummary(step)}</span>
                </button>
                <div className="fold" data-open={expanded.has(index) ? "" : undefined}>
                  <div>
                    {expanded.has(index) ? (
                      <StepDetail step={step} index={index} full={fullOutput.has(index)} onMore={(i) => setFullOutput((prev) => new Set(prev).add(i))} />
                    ) : null}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </hp-steps>
  );
}

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

function cleanTitle(title: string | null): string {
  return (title ?? "").replace(/^Subagent:\s*/i, "").trim() || "（未命名子任务）";
}

function TurnView(props: { turn: Turn; index: number; last: boolean; running: boolean }) {
  const turn = props.turn;
  if (turn.role === "notice") return <div className="note">{turn.title}</div>;
  if (turn.role === "user") {
    if (props.index === 0) {
      return (
        <details className="task">
          <summary>任务说明</summary>
          <pre>{turn.text}</pre>
        </details>
      );
    }
    return <div className="bubble">{turn.text}</div>;
  }
  const live = props.running && props.last;
  const parts = segments(turn.steps);
  return (
    <div className="assistant" data-live={live ? "true" : "false"}>
      {turn.reasoning ? (
        <details className="reasoning">
          <summary>思考过程</summary>
          <div>{turn.reasoning}</div>
        </details>
      ) : null}
      {parts.map((part, index) => part.kind === "note"
        ? <AssistantContent key={`n${index}`} text={part.text} images={part.images ?? []} streaming={live && index === parts.length - 1} />
        : <StepsView key={`t${part.start}`} steps={part.steps} live={live && index === parts.length - 1} />)}
      {turn.text || turn.images?.length ? <AssistantContent text={turn.text} images={turn.images ?? []} streaming={live} /> : null}
    </div>
  );
}

export function AgentDock(props: { sessionId: string; agentId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const setSelectedSessionId = useUiStore((state) => state.setSelectedSessionId);
  const [agent, setAgent] = useState<Subagent | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const rootRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  const skew = useRef(0);
  const timer = useRef(0);
  const stickRef = useRef(false);
  const agentRef = useRef(agent);
  const sessionRef = useRef(props.sessionId);
  const agentIdRef = useRef(props.agentId);
  agentRef.current = agent;
  sessionRef.current = props.sessionId;
  agentIdRef.current = props.agentId;

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    const agentId = agentIdRef.current;
    const sessionId = sessionRef.current;
    if (!agentId || document.visibilityState !== "visible") return;
    const token = ++seq.current;
    const scroller = bodyRef.current;
    stickRef.current = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
    let running = agentRef.current?.status === "running";
    try {
      const [page, list] = await Promise.all([
        api.messages(agentId, { limit: PAGE, offset: 0 }),
        sidecar.subagents(sessionId, 30).catch(() => null),
      ]);
      if (token !== seq.current || agentIdRef.current !== agentId) return;
      const notes = await loadCommentary(page.messages);
      if (token !== seq.current || agentIdRef.current !== agentId) return;
      const fresh = list?.data.find((item) => item.id === agentId) ?? null;
      const status = fresh?.status ?? agentRef.current?.status;
      running = status === "running";
      setTurns(buildTurns(page.messages, notes, { inFlight: running }));
      setHasEarlier(page.hasMore);
      setError("");
      if (list) skew.current = list.now - Date.now() / 1000;
      if (fresh) setAgent(fresh);
      else if (!agentRef.current) {
        setAgent({
          id: agentId,
          parentId: sessionId,
          parentTitle: null,
          title: null,
          model: null,
          startedAt: Date.now() / 1000,
          endedAt: null,
          endReason: null,
          status: "ended",
          messages: page.messages.length,
          toolCalls: 0,
          apiCalls: 0,
          tokens: 0,
          lastActivityAt: Date.now() / 1000,
          lastTool: null,
          snippet: "",
        });
      }
    } catch (caught) {
      if (token !== seq.current) return;
      setError(caught instanceof ApiError ? caught.message : "加载失败");
    } finally {
      if (token === seq.current) {
        setLoading(false);
        if (running) timer.current = window.setTimeout(() => void refresh(), POLL_MS);
      }
    }
  }, []);

  useEffect(() => {
    setTurns([]);
    setHasEarlier(false);
    setError("");
    setLoading(true);
    setAgent(null);
    skew.current = 0;
    seq.current += 1;
    void refresh();
  }, [props.sessionId, props.agentId, refresh]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearTimeout(timer.current);
      seq.current += 1;
    };
  }, [refresh]);

  const running = agent?.status === "running";
  useEffect(() => {
    if (!running) return;
    const clock = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(clock);
  }, [running]);

  useLayoutEffect(() => {
    if (!stickRef.current) return;
    stickRef.current = false;
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const close = () => {
    rootRef.current?.dispatchEvent(new CustomEvent("hp-agent-close", { bubbles: true, composed: true }));
    props.onClose();
  };

  const openInChat = () => {
    if (!agent) return;
    const summary: SessionSummary = {
      id: agent.id,
      source: "subagent",
      title: agent.title,
      model: agent.model,
      started_at: agent.startedAt,
      last_active: agent.lastActivityAt,
      message_count: agent.messages,
      preview: null,
      pinned: false,
      archived: false,
    };
    rootRef.current?.dispatchEvent(new CustomEvent("hp-open-session", { detail: summary, bubbles: true, composed: true }));
    setSelectedSessionId(agent.id);
    navigate("/");
    props.onClose();
  };

  const now = Date.now() / 1000 + skew.current;
  const end = agent ? (agent.endedAt ?? (agent.status === "running" ? now : agent.lastActivityAt)) : now;
  void tick;

  return (
    <section ref={rootRef} className="dock-agent" aria-label="子代理">
      <header>
        <div className="head">
          <h2 title={agent ? cleanTitle(agent.title) : "子代理"}>{agent ? cleanTitle(agent.title) : "子代理"}</h2>
          {agent ? (
            <div className="sub">
              <span className="st" data-s={agent.status}>{STATUS_LABEL[agent.status]}</span>
              <span>{agent.status === "running" ? "已运行 " : "用时 "}{duration(end - agent.startedAt)}</span>
              <span>工具调用 {agent.toolCalls} 次</span>
            </div>
          ) : (
            <div className="sub"><span>{loading ? "加载中…" : "未找到子代理"}</span></div>
          )}
        </div>
        <button type="button" className="btn" title="在主区域打开这个子代理的对话" disabled={!agent} onClick={openInChat}>在对话中打开</button>
        <button type="button" className="btn" aria-label="关闭" onClick={close}>
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <div ref={bodyRef} className="body">
        {error ? <div className="error" role="alert">{error}</div> : null}
        {loading && turns.length === 0 ? (
          <div className="center">加载中…</div>
        ) : turns.length ? (
          <>
            {hasEarlier ? <div className="note">仅显示最近 {PAGE} 条消息</div> : null}
            {turns.map((turn, index) => (
              <TurnView key={`${turn.role}-${index}`} turn={turn} index={index} last={index === turns.length - 1} running={!!running} />
            ))}
          </>
        ) : error ? null : (
          <div className="center">还没有消息</div>
        )}
      </div>
    </section>
  );
}
