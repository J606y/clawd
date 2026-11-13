import { memo, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { fetchAndSave } from "@/lib/save-file";
import { downloadUrl, MAX_UPLOAD_BYTES } from "@/lib/sidecar";
import { artifactReference, artifactRoots, parseAssistantContent } from "@/lib/artifacts";
import { formatElapsed, completionVerb, spinnerVerb } from "@/lib/spinner-verbs";
import { retryLine, retryReason, silentLine, SILENT_QUERY_MS } from "@/lib/run-status";
import { api } from "@/lib/api";
import { sidecar, type RunWait } from "@/lib/sidecar";
import { isBrowserStep, outputPreview, toolGroupSummary, toolLabel, toolSummary } from "@/lib/tools";
import { isTodoStep } from "@/lib/task-list";
import { segments, type AssistantTurn, type NoticeTurn, type Step, type ToolStep, type UserTurn } from "@/lib/transcript";
import { renderMarkdown } from "@/lib/markdown";
import { splitLeakedThinking } from "@/lib/transcript";
import { projectLeakedTail } from "./leaked-tail";
import { RevealedBody, StaticMarkdown } from "./revealed";
import { WidgetCard, isShowWidgetStep } from "./widget-card";

function uploadRef(absPath: string): { download: string; preview: string } | null {
  const root = artifactRoots().filter((item) => item.endsWith("/uploads/")).find((item) => absPath.startsWith(item));
  if (!root) return null;
  const relative = absPath.slice(root.length);
  if (!relative || relative.split("/").some((part) => !part || part === "." || part === "..") || /[\u0000-\u001f\u007f\\]/.test(relative)) return null;
  return {
    download: downloadUrl("uploads", relative),
    preview: `/panel-sidecar/files/artifact?${new URLSearchParams({ path: absPath, preview: "1" })}`,
  };
}

const IMAGE_DATA = /^data:image\/(?:png|jpeg|webp|gif|bmp);base64,/i;

function PreviewDialog({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog || dialog.open) return;
    dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => {
        const dialog = event.currentTarget;
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

export function UserAttachments({
  images,
  files,
  uploads,
  droppedImages,
}: {
  images: string[];
  files: { name: string; text: string }[];
  uploads: { name: string; absPath: string; size: string; image?: boolean }[];
  droppedImages: number;
}) {
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [failed, setFailed] = useState<string[]>([]);
  const busy = useRef(false);
  if (!images.length && !files.length && !uploads.length && !droppedImages) return null;
  const download = async (url: string, name: string) => {
    if (busy.current) return;
    busy.current = true;
    setError("");
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 90_000);
    try {
      await fetchAndSave(url, name, ctrl.signal);
    } catch (err) {
      setError(err instanceof Error ? err.message : "下载失败");
    } finally {
      window.clearTimeout(timer);
      busy.current = false;
    }
  };
  const pictures = images.filter((src) => IMAGE_DATA.test(src));
  const archived = uploads.filter((file) => file.image && uploadRef(file.absPath));
  const shownImage = (file: { absPath: string; image?: boolean }) => {
    if (!file.image || !uploadRef(file.absPath)) return false;
    const shown = images.length ? archived.slice(0, images.length) : archived;
    return shown.some((item) => item.absPath === file.absPath);
  };
  return (
    <div className="user-atts">
      {pictures.length || archived.length ? (
        <div className="imgs">
          {pictures.length
            ? pictures.map((src, index) => {
              const file = archived[index];
              const extension = src.slice(11, src.indexOf(";")).replace("jpeg", "jpg");
              const name = file?.name ?? `图片 ${index + 1}.${extension}`;
              return (
                <button
                  key={`${index}:${src.slice(0, 32)}`}
                  className="thumbnail"
                  type="button"
                  aria-label={`查看 ${name}`}
                  onClick={() => {
                    setFailed((list) => list.filter((item) => item !== src));
                    setPreview(src);
                  }}
                >
                  {failed.includes(src) ? <span>图片无法预览，点击重试</span> : <img src={src} alt={name} loading="lazy" onError={() => setFailed((list) => list.includes(src) ? list : [...list, src].slice(-32))} />}
                </button>
              );
            })
            : archived.map((file) => {
              const ref = uploadRef(file.absPath);
              if (!ref) return null;
              return (
                <button key={file.absPath} className="thumbnail" type="button" aria-label={`查看 ${file.name}`} onClick={() => setPreview(ref.preview)}>
                  <img src={ref.preview} alt={file.name} loading="lazy" />
                </button>
              );
            })}
        </div>
      ) : null}
      <div className="files">
        {uploads.filter((file) => !shownImage(file)).map((file) => {
          const ref = uploadRef(file.absPath);
          return (
            <div className="file upload" key={file.absPath}>
              <button type="button" disabled={busy.current || !ref} title={ref ? `下载 ${file.name}` : "此文件不在允许下载的上传目录中"} onClick={() => { if (ref) void download(ref.download, file.name); }}>
                📎 {file.name} · {file.size}
              </button>
            </div>
          );
        })}
        {files.map((file) => (
          <details className="file" key={file.name}>
            <summary>{file.name}</summary>
            <pre>{file.text}</pre>
            <button className="action" type="button" title="导出历史中保存的文本内容" onClick={() => void download(URL.createObjectURL(new Blob([file.text], { type: "text/plain" })), file.name)}>下载文本</button>
          </details>
        ))}
        {droppedImages ? <span className="file dropped" title="这张图片的原图未保存在历史中，无法恢复">{droppedImages} 张图片（未存档）</span> : null}
      </div>
      {error ? <div className="error" role="alert">{error.includes("50MB") || error.includes(String(MAX_UPLOAD_BYTES)) ? "文件超过 50MB，请到文件管理下载" : error}</div> : null}
      {preview ? (
        <PreviewDialog label="查看图片" onClose={() => setPreview("")}>
          <img src={preview} alt="" />
          <div className="actions">
            <button type="button" data-close onClick={() => setPreview("")}>关闭预览</button>
          </div>
        </PreviewDialog>
      ) : null}
    </div>
  );
}

function enlargeKey(event: ReactKeyboardEvent<HTMLAnchorElement>): void {
  if (event.key !== "Enter" || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (!event.currentTarget.href) return;
  event.preventDefault();
  window.open(event.currentTarget.href, "_blank", "noopener,noreferrer");
}

export function AssistantBody({ text, images, streaming, pace, ownerKey, releaseHeld = false }: { text: string; images: string[]; streaming: boolean; pace: boolean; ownerKey: string; releaseHeld?: boolean }) {
  // 流式只投影活动尾部。闭合帧仍用既有分类；未证实的协议前缀不进入揭示，节奏不变。
  const source = streaming ? projectLeakedTail(text, releaseHeld).text : text;
  const prior = useRef<{ ownerKey?: string; source?: string; result?: ReturnType<typeof parseAssistantContent> }>({});
  if (prior.current.ownerKey !== ownerKey) prior.current = { ownerKey };
  let parsed: ReturnType<typeof parseAssistantContent>;
  if (!source) {
    parsed = { text: "", artifacts: [] };
  } else if (prior.current.source === source && prior.current.result) {
    parsed = prior.current.result;
  } else {
    const previous = prior.current;
    const append = streaming && previous.result && typeof previous.source === "string" && source.startsWith(previous.source);
    parsed = parseAssistantContent(source, append
      ? { cache: false, priorSource: previous.source, priorResult: previous.result }
      : { cache: !streaming });
    prior.current = { ownerKey, source, result: parsed };
  }
  const [failed, setFailed] = useState<string[]>([]);
  const [preview, setPreview] = useState<{ name: string; preview: string; download: string } | null>(null);
  const [downloadError, setDownloadError] = useState("");
  const extras = images.slice(0, 32).flatMap((src) => {
    const artifact = artifactReference(src);
    return artifact?.image ? [artifact] : [];
  });
  const artifacts = [...parsed.artifacts];
  for (const artifact of extras) {
    if (artifacts.length >= 32 || artifacts.some((item) => item.source === artifact.source)) continue;
    artifacts.push(artifact);
  }
  const save = (url: string, name: string) => {
    setDownloadError("");
    void fetchAndSave(url, name).catch((error: unknown) => setDownloadError(error instanceof Error ? error.message : "下载失败"));
  };
  return (
    <div className="assistant-body">
      {streaming ? <RevealedBody text={parsed.text} active={streaming} pace={pace} ownerKey={ownerKey} /> : <StaticMarkdown text={parsed.text} />}
      {artifacts.length ? (
        <div className="artifacts">
          {artifacts.map((item) => (
            <section className="artifact" key={item.source} aria-label={item.image ? "生成图片" : "生成文件"}>
              <span className="name">{item.name}</span>
              {item.image ? (
                failed.includes(item.source) ? <p className="error" role="status">图片无法预览，仍可尝试下载</p> : (
                  <a href={item.preview} target="_blank" rel="noopener noreferrer" aria-label={`放大查看 ${item.name}`} onClick={(event) => { event.preventDefault(); setPreview(item); }} onKeyDown={enlargeKey}>
                    <img src={item.preview} alt={item.name} onError={() => setFailed((list) => list.includes(item.source) ? list : [...list, item.source].slice(-32))} />
                  </a>
                )
              ) : null}
              <div className="actions">
                {item.image ? <a href={item.preview} target="_blank" rel="noopener noreferrer" onClick={(event) => { event.preventDefault(); setPreview(item); }} onKeyDown={enlargeKey}>放大查看</a> : null}
                <button type="button" className="save" onClick={() => save(item.download, item.name)}>{item.image ? "下载图片" : "下载文件"}</button>
              </div>
            </section>
          ))}
        </div>
      ) : null}
      {downloadError ? <p className="error" role="alert">{downloadError}</p> : null}
      {preview ? (
        <PreviewDialog label={`放大查看 ${preview.name}`} onClose={() => setPreview(null)}>
          <img src={preview.preview} alt={preview.name} />
          <div className="actions">
            <button type="button" className="save" onClick={() => save(preview.download, preview.name)}>下载图片</button>
            <button type="button" data-close onClick={() => setPreview(null)}>关闭预览</button>
          </div>
        </PreviewDialog>
      ) : null}
    </div>
  );
}

function StepList({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [more, setMore] = useState<Record<string, boolean>>({});
  const failed = steps.filter((step) => step.status === "failed").length;
  const summary = toolGroupSummary(steps);
  const openGroup = open;
  const running = live && steps.some((step) => step.status === "running");
  return (
    <div className="tool-steps">
      <button type="button" className="summary" aria-expanded={openGroup} onClick={() => setOpen((value) => !value)}>
        <span className="dot" data-s={running ? "running" : failed ? "failed" : "done"} aria-hidden="true" />
        <span className="now">{summary}</span>
        {failed ? <span className="fail">· {failed} failed</span> : null}
        <span className="hint">{openGroup ? "collapse" : "expand"}</span>
      </button>
      {steps.some(isBrowserStep) ? (
        <button type="button" className="watch" onClick={() => window.dispatchEvent(new CustomEvent("hp-live-open", { detail: { scope: "task", active: live, token: `task-open-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` } }))}>观看</button>
      ) : null}
      <div className="fold" data-open={openGroup ? "" : undefined}>
        <div>
          <ol>
            {steps.map((step) => {
              const preview = step.output ? outputPreview(step.output) : null;
              const shown = step.output ? (more[step.id] ? step.output : outputPreview(step.output).text) : "";
              const status = step.status === "running" ? "Running" : step.status === "failed" ? "Failed" : "Done";
              return (
                <li key={step.id}>
                  <button type="button" className="row" aria-expanded={opened[step.id] ? "true" : "false"} onClick={() => setOpened((state) => ({ ...state, [step.id]: !state[step.id] }))}>
                    <span className="dot" data-s={step.status} aria-label={status} />
                    <span className="name" title={toolLabel(step.name)}>{toolLabel(step.name)}</span>
                    <span className="arg">{toolSummary(step)}</span>
                  </button>
                  <div className="fold" data-open={opened[step.id] ? "" : undefined}>
                    <div>
                      {opened[step.id] ? (
                        <>
                          <div className="preview" data-s={step.status}>
                            <span className="branch" aria-hidden="true">⎿</span>
                            <pre>{step.output === null ? (step.status === "running" ? "Running…" : "No output captured") : (shown || "(No output)")}</pre>
                          </div>
                          {preview?.truncated && !more[step.id] ? <button type="button" className="more" onClick={() => setMore((state) => ({ ...state, [step.id]: true }))}>Show more</button> : null}
                          {step.outputPartial ? <div className="partial">Partial output · full result available after completion</div> : null}
                          {step.args || step.output ? (
                            <details className="detail">
                              <summary>Input</summary>
                              {step.args ? <pre>{JSON.stringify(step.args, null, 2)}</pre> : null}
                              {step.output ? <><h4>输出</h4><pre>{shown}</pre></> : null}
                            </details>
                          ) : null}
                        </>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
      {steps.map((step) => isShowWidgetStep(step) ? <WidgetCard key={step.id} step={step} /> : null)}
    </div>
  );
}

export function StepGroups({ steps, live }: { steps: Step[]; live: boolean }) {
  const visible: Step[] = [];
  for (const step of steps) {
    if (!isTodoStep(step)) visible.push(step);
  }
  const groups = segments(visible);
  return (
    <>
      {groups.map((group, index) => group.kind === "note"
        ? <AssistantBody key={`n${index}`} text={group.text} images={group.images ?? []} streaming={false} pace={false} ownerKey="" />
        : <StepList key={`t${group.start}`} steps={group.steps} live={live && index === groups.length - 1} />)}
    </>
  );
}

export function TurnSummary({ startedAt, endedAt, outcome }: { startedAt?: number; endedAt?: number; outcome: string }) {
  if (startedAt === undefined || endedAt === undefined || endedAt < startedAt) return null;
  const failed = outcome === "failed" || outcome === "执行失败";
  const interrupted = outcome === "cancelled" || outcome === "interrupted" || outcome === "已停止" || outcome === "已中断";
  const word = failed ? "Failed" : interrupted ? "Interrupted" : completionVerb(`${startedAt}:${endedAt}`);
  return <span className="turn-summary"><span className={word === "Failed" ? "failed" : undefined}>✻ {word} for {formatElapsed((endedAt - startedAt) * 1000)}</span></span>;
}

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

export function NoticeRow({ turn }: { turn: NoticeTurn }) {
  return (
    <details className={`turn sys-notice${turn.failed ? " failed" : ""}`}>
      <summary>{turn.title}{turn.seconds !== undefined ? ` · ${duration(turn.seconds)}` : ""}</summary>
      <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(turn.detail) }} />
    </details>
  );
}

export const HistoryTurn = memo(function HistoryTurn({
  turn,
  pending,
  outcome,
  editDisabled,
  editing,
  messageKey,
  onEditStart,
  onEditEnd,
  onEditSend,
}: {
  turn: UserTurn | AssistantTurn | NoticeTurn;
  pending: boolean;
  outcome: string;
  editDisabled: string;
  editing: boolean;
  messageKey: string;
  onEditStart: (key: string) => void;
  onEditEnd: () => void;
  onEditSend: (key: string, text: string) => Promise<void>;
}) {
  if (turn.role === "notice") return <NoticeRow turn={turn} />;
  if (turn.role === "user") {
    return (
      <div className={`turn user${turn.steer ? " steer" : ""}`}>
        {turn.steer ? <span className="steer-tag">插话</span> : null}
        <UserAttachments images={turn.images} files={turn.files} uploads={turn.uploads} droppedImages={turn.droppedImages} />
        {turn.message ? (
          <MessageMenu text={turn.text} disabledReason={editDisabled} editing={editing} onEditStart={() => onEditStart(messageKey)} onEditEnd={onEditEnd} onSend={(text) => onEditSend(messageKey, text)} />
        ) : turn.text ? <div className="bubble">{turn.text}</div> : null}
      </div>
    );
  }
  const showSummary = !pending && turn.steps.some((step) => step.kind === "tool");
  return (
    <div className="turn assistant">
      {turn.reasoning ? <details className="reasoning"><summary>Thought</summary><div>{turn.reasoning}</div></details> : null}
      <StepGroups steps={turn.steps} live={pending} />
      <AssistantBody text={turn.text} images={turn.images ?? []} streaming={false} pace={false} ownerKey="" />
      {showSummary ? <TurnSummary startedAt={turn.startedAt} endedAt={turn.endedAt} outcome={outcome} /> : null}
    </div>
  );
});

function MessageMenu({
  text,
  disabledReason,
  editing,
  onEditStart,
  onEditEnd,
  onSend,
}: {
  text: string;
  disabledReason: string;
  editing: boolean;
  onEditStart: () => void;
  onEditEnd: () => void;
  onSend: (text: string) => Promise<void>;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(text);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [place, setPlace] = useState<{ top: number | null; bottom: number | null; left: number }>({ top: null, bottom: null, left: 0 });
  const id = useId();
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const press = useRef<{ x: number; y: number; timer: number } | null>(null);

  useEffect(() => () => { if (editing) onEditEnd(); }, [editing, onEditEnd]);

  useEffect(() => {
    if (!open && !editing) return;
    const closeOutside = (event: MouseEvent) => {
      const path = event.composedPath();
      if (rootRef.current && !path.includes(rootRef.current)) setOpen(false);
    };
    const onScroll = () => setOpen(false);
    const onKey = (event: KeyboardEvent) => {
      if (!rootRef.current || !event.composedPath().includes(rootRef.current)) return;
      if (event.key === "Tab") {
        setOpen(false);
        return;
      }
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (open) setOpen(false);
      else if (editing) onEditEnd();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("scroll", onScroll, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("scroll", onScroll, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, editing, onEditEnd]);

  const openMenu = () => {
    if (!rootRef.current) return;
    setOpen(true);
  };

  useLayoutEffect(() => {
    if (!open) return;
    const root = rootRef.current;
    const menu = menuRef.current;
    if (!root || !menu) return;
    const rect = root.getBoundingClientRect();
    const menuBox = menu.getBoundingClientRect();
    const vv = window.visualViewport;
    const viewTop = vv ? vv.offsetTop : 0;
    const viewLeft = vv ? vv.offsetLeft : 0;
    const viewRight = viewLeft + (vv ? vv.width : window.innerWidth);
    const viewBottom = viewTop + (vv ? vv.height : window.innerHeight);
    const gap = 6;
    const height = menuBox.height;
    const width = menuBox.width;
    const spaceAbove = rect.top - viewTop;
    const spaceBelow = viewBottom - rect.bottom;
    const fitsBelow = spaceBelow >= height + gap;
    const fitsAbove = spaceAbove >= height + gap;
    const placeBelow = fitsBelow || (!fitsAbove && spaceBelow >= spaceAbove);
    let absTop = placeBelow ? rect.bottom + gap : rect.top - gap - height;
    const minTop = viewTop;
    const maxTop = Math.max(minTop, viewBottom - height);
    absTop = Math.min(Math.max(absTop, minTop), maxTop);
    let left = rect.width - width;
    const minLeft = viewLeft - rect.left;
    const maxLeft = viewRight - width - rect.left;
    left = minLeft <= maxLeft ? Math.min(Math.max(left, minLeft), maxLeft) : minLeft;
    if (placeBelow) setPlace({ top: absTop - rect.top, bottom: null, left });
    else setPlace({ top: null, bottom: rect.bottom - (absTop + height), left });
  }, [open]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!coarse || editing || event.pointerType === "mouse") return;
    const point = { x: event.clientX, y: event.clientY, timer: window.setTimeout(() => openMenu(), 500) };
    press.current = point;
  };
  const cancelPress = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = press.current;
    if (!current) return;
    if (Math.hypot(event.clientX - current.x, event.clientY - current.y) > 10 || event.type !== "pointermove") {
      window.clearTimeout(current.timer);
      press.current = null;
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setOpen(false);
    } catch {
      setError("复制失败，请允许剪贴板访问后重试。");
    }
  };

  const send = async () => {
    if (pending || disabledReason || !draft.trim() || draft.trim() === text.trim()) return;
    setPending(true);
    setError("");
    try {
      await onSend(draft);
    } catch (err) {
      setError(err instanceof Error ? err.message : "编辑重发失败");
    } finally {
      setPending(false);
    }
  };

  const onEditKey = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || coarse || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    event.preventDefault();
    void send();
  };

  return (
    <div ref={rootRef} className="bubble msg-editor" onContextMenu={(event) => { if (editing) return; event.preventDefault(); openMenu(); }} onPointerDown={onPointerDown} onPointerMove={cancelPress} onPointerUp={cancelPress} onPointerCancel={cancelPress}>
      <div className="root">
        {editing ? (
          <>
            <textarea aria-label="编辑已发送消息" value={draft} disabled={pending} maxLength={20000} onChange={(event) => setDraft(event.target.value)} onKeyDown={onEditKey} />
            <p className="note" id="edit-note">发送会建立副本，保留原历史。</p>
            {disabledReason ? <p className="note disabled-reason" id="editing-reason">{disabledReason}</p> : null}
            {error ? <p className="error" role="alert">{error}</p> : null}
            <button type="button" className="cancel" aria-label="取消编辑消息" disabled={pending} onClick={() => { setDraft(text); onEditEnd(); }}>取消</button>
            <button type="button" className="send" aria-label="发送编辑后的消息" disabled={pending || !draft.trim() || draft.trim() === text.trim()} onClick={() => void send()}>{pending ? "发送中…" : "发送副本"}</button>
          </>
        ) : (
          <>
            <div className="message-text">{text}</div>
            <button type="button" className="trigger" aria-label={disabledReason ? `消息操作：${disabledReason}` : "消息操作"} title={disabledReason || (pending ? "正在发送" : "消息操作")} aria-haspopup="menu" aria-expanded={open} aria-controls={id} onClick={() => openMenu()}>⋯</button>
          </>
        )}
        {open ? (
          <div id={id} ref={menuRef} role="menu" aria-label="消息操作" className="menu" style={{ left: place.left, top: place.top ?? "auto", bottom: place.bottom ?? "auto" }}>
            <button type="button" role="menuitem" data-action="edit" aria-disabled={pending || disabledReason ? "true" : "false"} onClick={() => { setOpen(false); if (!disabledReason && !pending) { setDraft(text); onEditStart(); } }}>编辑</button>
            {pending || disabledReason ? <p id="edit-reason" className="note">{disabledReason || "正在发送"}</p> : null}
            <button type="button" role="menuitem" data-action="copy" onClick={() => void copy()}>复制</button>
            {error ? <p className="error" role="alert">{error}</p> : null}
            <button type="button" role="menuitem" data-action="close" onClick={() => setOpen(false)}>关闭</button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function RunStatusLine({
  sessionId,
  runId,
  startedAt,
  endedAt,
  lastEventAt,
  stopping,
  streaming,
  busy,
  tokenLabel,
  settled,
}: {
  sessionId: string;
  runId: string;
  startedAt: number;
  endedAt?: number;
  lastEventAt: number;
  stopping: boolean;
  streaming: boolean;
  busy: boolean;
  tokenLabel: string;
  settled?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [wait, setWait] = useState<RunWait | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [pollError, setPollError] = useState("");
  const verb = useRef(spinnerVerb());
  useEffect(() => {
    if (settled) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden" || stopping) return;
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [stopping, settled]);
  useEffect(() => {
    if (settled || !sessionId || !runId || stopping || document.visibilityState === "hidden") return;
    const ctrl = new AbortController();
    const poll = async () => {
      try {
        const status = await api.runStatus(runId, ctrl.signal);
        if (status.run_id !== runId || status.session_id !== sessionId || status.status !== "running" || typeof status.created_at !== "number") {
          setWait(null);
          setPollError("Authoritative run boundary unavailable");
          return;
        }
        const started = status.created_at * 1000;
        const next = await sidecar.runWait(sessionId, started, ctrl.signal);
        setWait(next.state === "retrying" && next.available === true && next.turnStartedAtMs === started ? next : null);
        setFetchedAt(Date.now());
        setPollError(next.turnStartedAtMs === started && next.available ? "" : "Turn-scoped retry evidence unavailable");
      } catch {
        if (!ctrl.signal.aborted) {
          setWait(null);
          setPollError("Retry status unavailable");
        }
      }
    };
    const silent = Date.now() - lastEventAt >= SILENT_QUERY_MS;
    if (silent) void poll();
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void poll(); }, 5000);
    return () => {
      ctrl.abort();
      window.clearInterval(timer);
    };
  }, [sessionId, runId, stopping, lastEventAt, settled]);
  if (wait && Date.now() - fetchedAt > 13_000) return null;
  const elapsed = settled
    ? startedAt && endedAt
      ? formatElapsed(endedAt - startedAt)
      : ""
    : startedAt
      ? formatElapsed(now - startedAt)
      : "";
  const retry = !stopping && wait?.state === "retrying" ? wait : null;
  const tokenTitle = settled ? "服务端真实用量：本轮输出 token（含隐藏思考；思考 token 未单独上报）" : "Approximate observable output this turn; excludes unavailable hidden thinking and tool inputs";
  if (retry) {
    const left = typeof retry.remainingSeconds === "number" ? retry.remainingSeconds - (now - fetchedAt) / 1000 : null;
    const label = retryReason(retry.summary, retry.httpStatus);
    const token = tokenLabel ? <span className="tokens" title={tokenTitle}>{tokenLabel}</span> : null;
    const meta = elapsed || tokenLabel ? <span className="elapsed" title={pollError}>({elapsed}{elapsed && tokenLabel ? " · " : null}{token})</span> : null;
    return (
      <div className="run-status">
        <div className="line retry" role="status">
          <span className="spinner" aria-hidden="true">✻</span>
          <span className="main">{label}</span>
          <span className="detail" aria-live="off" title={retry.summary ?? ""}>{retryLine(retry, left).slice(label.length + 3)}</span>
          {meta}
        </div>
      </div>
    );
  }
  const showThinking = busy || !streaming || now - lastEventAt >= 2000;
  const label = settled ? "" : stopping ? "Stopping…" : showThinking ? `${verb.current}…` : "";
  const note = settled || stopping ? "" : silentLine(now - lastEventAt);
  const token = tokenLabel ? <span className="tokens" title={tokenTitle}>{tokenLabel}</span> : null;
  const meta = elapsed || tokenLabel ? <span className="elapsed" title={pollError}>({elapsed}{elapsed && tokenLabel ? " · " : null}{token})</span> : null;
  return (
    <div className="run-status">
      <div className="line">
        {label ? <><span className="spinner" aria-hidden="true">✻</span><span className="main">{label}{!stopping ? <span className="glow" aria-hidden="true">{label}</span> : null}</span></> : null}
        {meta}
      </div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

export function ApprovalCard({
  description,
  command,
  choices,
  pending,
  onChoose,
}: {
  description: string;
  command: string;
  choices: Array<"once" | "session" | "always" | "deny">;
  pending: boolean;
  onChoose: (choice: "once" | "session" | "always" | "deny") => void;
}) {
  const labels = { once: "Yes", session: "Yes, allow for this session", always: "Yes, always allow", deny: "No" } as const;
  return (
    <div className="approval" role="alertdialog" aria-label="需要你的确认">
      <h3>Do you want to proceed?</h3>
      {description ? <p>{description}</p> : null}
      {command ? <pre>{command}</pre> : null}
      <div className="choices">
        {choices.map((choice, index) => (
          <button key={choice} type="button" className={choice === "deny" ? "deny" : index === 0 ? "primary" : undefined} disabled={pending} onClick={() => onChoose(choice)}>
            {labels[choice]}
          </button>
        ))}
      </div>
    </div>
  );
}

export function BrowserTask({ steps, active }: { steps: Step[]; active: boolean }) {
  if (!steps.some(isBrowserStep)) return null;
  return (
    <div className="browser-task">
      <button
        type="button"
        aria-label="查看 AI 当前页面"
        onClick={() => {
          window.dispatchEvent(new CustomEvent("hp-live-open", {
            detail: { scope: "task", active, token: `task-open-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` },
          }));
        }}
      >
        <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M7 17h6M10 14v3" stroke="currentColor" strokeWidth="1.4" /></svg>
        <span className="copy"><b>{active ? "浏览器任务执行中" : "浏览器任务已结束"}</b><small>AI 当前页面 · {active ? "可观看实时画面" : "任务已结束，画面已关闭"}</small></span>
        <span className="action">查看画面</span>
      </button>
    </div>
  );
}

export function leakedText(raw: string): { reasoning: string; text: string } {
  return splitLeakedThinking(raw);
}

export function markdownDetail(detail: string): string {
  return renderMarkdown(detail);
}
