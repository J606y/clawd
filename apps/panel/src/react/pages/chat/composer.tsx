import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import {
  AttachmentError,
  MAX_IMAGES,
  MAX_TOTAL_BYTES,
  formatBytes,
  isInline,
  prepare,
  uploadedAttachment,
  type Attachment,
} from "@/lib/attachments";
import { MAX_UPLOAD_BYTES, uploadFile } from "@/lib/sidecar";
import { composerFieldHeight } from "@/lib/composer-size";

const COARSE = window.matchMedia("(pointer: coarse)");
const ARCHIVE_MS = 60_000;

export interface ComposerProps {
  sessionId: string;
  value: string;
  attachments: Attachment[];
  disabled: boolean;
  /** 拦住新的准入，但不禁用草稿输入。 */
  sendBlocked?: boolean;
  running: boolean;
  canSteer: boolean;
  preparing: boolean;
  stopping: boolean;
  /** 创建结果尚未确认：占位符改短句，其它占位符不动。 */
  unconfirmed?: boolean;
  flash: string;
  onChange: (value: string, attachments: Attachment[]) => void;
  onPreparing: (preparing: boolean) => void;
  onFlash: (message: string) => void;
  onSend: () => void;
  onSteer: () => void;
  onStop: () => void;
  tools?: ReactNode;
}

function autosize(box: HTMLTextAreaElement, mirror: HTMLTextAreaElement): void {
  mirror.style.width = `${box.clientWidth}px`;
  mirror.value = box.value.length > 0 ? box.value : " ";
  const style = getComputedStyle(box);
  const pad = (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0);
  const want = composerFieldHeight(mirror.scrollHeight, pad, 24, Number.POSITIVE_INFINITY);
  const next = Math.min(240, want);
  if (box.style.height !== `${next}px`) box.style.height = `${next}px`;
  // Short fields stay hidden so they do not show a scrollbar. Only the 240px cap scrolls.
  box.style.overflowY = want > 240 ? "auto" : "hidden";
}

export function Composer({
  sessionId,
  value,
  attachments,
  disabled,
  sendBlocked = false,
  running,
  canSteer,
  preparing,
  stopping,
  unconfirmed = false,
  flash,
  onChange,
  onPreparing,
  onFlash,
  onSend,
  onSteer,
  onStop,
  tools,
}: ComposerProps) {
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLTextAreaElement>(null);
  const [dragging, setDragging] = useState(false);
  const [coarse, setCoarse] = useState(COARSE.matches);
  const liveDraft = useRef({ value, attachments });
  const draftSeen = useRef<{ value: string; attachments: Attachment[] } | null>(null);
  const draftEcho = useRef(false);
  const sessionRef = useRef(sessionId);
  const generation = useRef(0);
  const inflight = useRef(0);
  const onPreparingRef = useRef(onPreparing);
  const mounted = useRef(true);

  useEffect(() => {
    const sync = () => setCoarse(COARSE.matches);
    COARSE.addEventListener("change", sync);
    return () => COARSE.removeEventListener("change", sync);
  }, []);

  useLayoutEffect(() => {
    onPreparingRef.current = onPreparing;
  });

  useLayoutEffect(() => {
    const sessionChanged = sessionRef.current !== sessionId;
    const draftReplaced = Boolean(
      draftSeen.current
      && !draftEcho.current
      && (draftSeen.current.value !== value || draftSeen.current.attachments !== attachments),
    );
    if (sessionChanged || draftReplaced) {
      generation.current += 1;
      inflight.current = 0;
      onPreparingRef.current(false);
    }
    sessionRef.current = sessionId;
    draftEcho.current = false;
    draftSeen.current = { value, attachments };
    liveDraft.current = { value, attachments };
  }, [sessionId, value, attachments]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
      inflight.current = 0;
      onPreparingRef.current(false);
    };
  }, []);

  useEffect(() => {
    const box = boxRef.current;
    const mirror = mirrorRef.current;
    if (!box || !mirror) return;
    autosize(box, mirror);
    const id = requestAnimationFrame(() => autosize(box, mirror));
    return () => cancelAnimationFrame(id);
  }, [value]);

  const commitDraft = (nextValue: string, nextAttachments: Attachment[]) => {
    if (!mounted.current) return;
    draftEcho.current = true;
    const next = { value: nextValue, attachments: nextAttachments };
    draftSeen.current = next;
    liveDraft.current = next;
    onChange(nextValue, nextAttachments);
  };

  const addFiles = async (files: File[]) => {
    const ticket = generation.current;
    const owned = () => mounted.current && generation.current === ticket;
    const finish = () => {
      if (!owned()) return;
      inflight.current -= 1;
      if (inflight.current <= 0) {
        inflight.current = 0;
        onPreparingRef.current(false);
      }
    };
    for (const file of files) {
      if (!owned()) return;
      const image = file.type.startsWith("image/");
      const uploadImage = running && image;
      if (image && !uploadImage && liveDraft.current.attachments.filter((item) => item.kind === "image").length >= MAX_IMAGES) {
        onFlash(`一条消息最多 ${MAX_IMAGES} 张图片`);
        break;
      }
      inflight.current += 1;
      onPreparingRef.current(true);
      try {
        if (uploadImage || !isInline(file)) {
          if (file.size > MAX_UPLOAD_BYTES) throw new AttachmentError(`${file.name} 超过 500MB`);
          const uploaded = await uploadFile(file, "uploads");
          if (!owned()) return;
          const now = liveDraft.current;
          commitDraft(now.value, [...now.attachments, uploadedAttachment(file.name, uploaded.absPath, uploaded.size, uploadImage)]);
          continue;
        }
        const prepared = await prepare(file);
        if (!owned()) return;
        const inlineBytes = liveDraft.current.attachments.filter((item) => item.kind !== "file").reduce((sum, item) => sum + item.bytes, 0);
        if (inlineBytes + prepared.bytes > MAX_TOTAL_BYTES) {
          onFlash(`图片和文本附件合计超过 ${formatBytes(MAX_TOTAL_BYTES)}，请分开发送`);
          break;
        }
        if (prepared.kind === "image") {
          try {
            if (file.size > MAX_UPLOAD_BYTES) throw new AttachmentError("原图超过 500MB");
            const archived = await uploadFile(file, "uploads", undefined, ARCHIVE_MS);
            if (!owned()) return;
            prepared.absPath = archived.absPath;
            prepared.originalBytes = file.size;
          } catch (error) {
            if (!owned()) return;
            const reason = error instanceof ApiError || error instanceof AttachmentError ? error.message : "上传失败";
            onFlash(`${file.name || "图片"} 原图未能存档（${reason}），将只发送压缩图，历史里无法下载原图`);
          }
        }
        if (!owned()) return;
        const now = liveDraft.current;
        commitDraft(now.value, [...now.attachments, prepared]);
      } catch (error) {
        if (owned()) onFlash(error instanceof AttachmentError || error instanceof ApiError ? error.message : `无法添加 ${file.name}`);
      } finally {
        finish();
      }
    }
  };

  const submit = () => {
    if (preparing) return;
    if (running) {
      if (!canSteer) return;
      onSteer();
      return;
    }
    if (disabled || sendBlocked) return;
    onSend();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || coarse) return;
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    event.preventDefault();
    submit();
  };

  const onPick = (files: FileList | null, input: HTMLInputElement) => {
    if (files && files.length) void addFiles([...files]);
    input.value = "";
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...(event.clipboardData.files ?? [])];
    if (!files.length || event.clipboardData.getData("text/plain")) return;
    event.preventDefault();
    void addFiles(files);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) void addFiles([...event.dataTransfer.files]);
  };

  const placeholder = unconfirmed
    ? "这次发送尚未确认，点停止可放弃等待"
    : running
      ? (canSteer ? "补充指令，clawd 会在下一步看到" : "clawd 正在启动…")
      : "给 clawd 发消息";
  const hasContent = value.trim().length > 0 || attachments.length > 0;

  return (
    <>
      <textarea ref={mirrorRef} className="measure" rows={1} tabIndex={-1} aria-hidden="true" readOnly />
      <div
        className="box"
        data-drag={dragging ? "" : undefined}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        {attachments.length || preparing ? (
          <div className="atts">
            {attachments.map((item) => (
              <div className="att" key={item.id} title={item.name}>
                {item.kind === "image"
                  ? <img src={item.dataUrl} alt={item.name} />
                  : <div className="file"><b>{item.name}</b><span>{formatBytes(item.bytes)}</span></div>}
                <button type="button" className="x" aria-label={`移除 ${item.name}`} onClick={() => commitDraft(liveDraft.current.value, liveDraft.current.attachments.filter((entry) => entry.id !== item.id))}>
                  <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                    <path d="M1 1l6 6M7 1 1 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
            ))}
            {preparing ? <div className="att busy">处理中</div> : null}
          </div>
        ) : null}
        <div className="row">
          <label className="btn clip" title="添加图片或文件" data-disabled={disabled ? "" : undefined}>
            <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M13.5 6.5 7.6 12.4a1.5 1.5 0 0 0 2.1 2.1l6.2-6.2a3 3 0 0 0-4.2-4.2L5.3 10.5a4.5 4.5 0 0 0 6.4 6.4l5.3-5.3" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <input
              type="file"
              multiple
              aria-label="添加图片或文件"
              disabled={disabled}
              onChange={(event) => onPick(event.target.files, event.target)}
            />
          </label>
          <textarea
            ref={boxRef}
            rows={1}
            value={value}
            disabled={disabled}
            placeholder={placeholder}
            aria-label="消息"
            enterKeyHint={coarse ? "enter" : "send"}
            onChange={(event) => commitDraft(event.target.value, liveDraft.current.attachments)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
          />
          {running ? (
            <>
              {hasContent ? (
                <button type="button" className="btn send" aria-label="插话" title="插话" disabled={!canSteer || preparing} onClick={submit}>
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              ) : null}
              <button type="button" className="btn stop" aria-label="停止" title="停止" disabled={stopping} onClick={onStop}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="2" fill="currentColor" /></svg>
              </button>
            </>
          ) : (
            <button type="button" className="btn send" aria-label="发送" title="发送" disabled={!hasContent || disabled || sendBlocked || preparing} onClick={submit}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>
      </div>
      <div className="foot">
        {flash ? <span className="err" role="alert">{flash}</span> : <span className="hint">{running ? "Enter 插话 · " : "Enter 发送 · "}Shift + Enter 换行</span>}
        <div className="tools">{tools}</div>
      </div>
    </>
  );
}
