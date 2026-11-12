import { useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEvent, type JSX, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  acceptScopedResult,
  createOperationGuard,
  downloadProjectFile,
  formatFileSize,
  formatResourceUpdated,
  instructionsBlockedReason,
  readProjectResources,
  saveProjectInstructions,
  uploadBlockedReason,
  uploadProjectFile,
  type ProjectFileResource,
  type ProjectMemoryResource,
  type ProjectResourcesSnapshot,
} from "@/lib/project-resources";
import { pageSave } from "@/lib/save-file";
import { subscribeTick } from "@/lib/time";
import "./project-resources.css";

const PLACEHOLDER = "使用专业的语气、使用简洁易懂的措辞、您是天体物理学专家等";

type SheetKind = "archive" | "instructions" | "memory";
type Phase = "idle" | "loading" | "ready" | "error";

interface ScopedText {
  projectId: string;
  text: string;
}

interface UploadView {
  projectId: string;
  name: string;
  ratio: number;
}

export function ProjectResources(props: {
  projectId: string;
  projectName: string;
  archived: boolean;
}): JSX.Element {
  const titleId = useId();
  const helperId = useId();
  const [sheet, setSheet] = useState<SheetKind | "">("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<ScopedText | null>(null);
  const [snapshot, setSnapshot] = useState<ProjectResourcesSnapshot | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [pendingSheet, setPendingSheet] = useState<SheetKind | "">("");
  const [saveIssue, setSaveIssue] = useState<ScopedText | null>(null);
  const [saving, setSaving] = useState(false);
  const [fileIssue, setFileIssue] = useState<ScopedText | null>(null);
  const [fileIssueKind, setFileIssueKind] = useState<"" | "upload" | "download">("");
  const [upload, setUpload] = useState<UploadView | null>(null);
  const [memoryId, setMemoryId] = useState<string | null>(null);
  const [notice, setNotice] = useState<ScopedText | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [reloadKey, setReloadKey] = useState(0);
  const reads = useRef(createOperationGuard());
  const uploads = useRef(createOperationGuard());
  const loadAbort = useRef<AbortController | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const saveAbort = useRef<AbortController | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const retryFile = useRef<{ projectId: string; file: File } | null>(null);
  const retryDownload = useRef<ProjectFileResource | null>(null);
  const draftDirty = useRef(false);
  const mounted = useRef(true);
  const projectRef = useRef(props.projectId);
  const snapshotRef = useRef<ProjectResourcesSnapshot | null>(null);
  const instructionRef = useRef<HTMLTextAreaElement | null>(null);
  const saveGen = useRef(0);
  const downloadGen = useRef(0);
  const uiEpoch = useRef(0);
  const seenScope = useRef({ sheet, projectId: props.projectId, reloadKey });
  const sheetRef = useRef<SheetKind | "">(sheet);
  const draftRef = useRef<string | null>(draft);
  const phaseRef = useRef<Phase>(phase);
  // 关弹层、换弹层、换项目或重试后，旧保存回执不能关新层或覆盖新草稿。
  if (
    seenScope.current.sheet !== sheet
    || seenScope.current.projectId !== props.projectId
    || seenScope.current.reloadKey !== reloadKey
  ) {
    seenScope.current = { sheet, projectId: props.projectId, reloadKey };
    uiEpoch.current += 1;
  }
  projectRef.current = props.projectId;
  snapshotRef.current = snapshot;
  sheetRef.current = sheet;
  draftRef.current = draft;
  phaseRef.current = phase;

  const view = snapshot && snapshot.project_id === props.projectId ? snapshot : null;
  const archived = props.archived || view?.archived === true;
  const dirty = sheet === "instructions" && baseline != null && draft != null && draft !== baseline && !archived;
  const uploadView = upload && upload.projectId === props.projectId ? upload : null;
  const fileIssueText = fileIssue?.projectId === props.projectId ? fileIssue.text : "";
  const saveIssueText = saveIssue?.projectId === props.projectId ? saveIssue.text : "";
  const noticeText = notice?.projectId === props.projectId ? notice.text : "";
  const errorTextForProject = error?.projectId === props.projectId ? error.text : "";
  const selected = memoryId == null ? null : view?.memories.find((item) => item.id === memoryId) ?? null;
  const panelState = sheetState(sheet, phase, view, errorTextForProject);

  useEffect(() => subscribeTick(() => setNow(Date.now())), []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      reads.current.begin("");
      uploads.current.begin("");
      loadAbort.current?.abort();
      uploadAbort.current?.abort();
      saveAbort.current?.abort();
    };
  }, []);

  useEffect(() => {
    uploads.current.begin(props.projectId);
    uploadAbort.current?.abort();
    setUpload(null);
    setFileIssue(null);
    setFileIssueKind("");
    setMemoryId(null);
    setNotice(null);
    retryFile.current = null;
    retryDownload.current = null;
  }, [props.projectId]);

  useEffect(() => {
    if (!sheet) return;
    const ticket = reads.current.begin(props.projectId);
    const ctrl = new AbortController();
    loadAbort.current = ctrl;
    draftDirty.current = false;
    setDraft(null);
    setBaseline(null);
    setPhase("loading");
    setError(null);
    setSaveIssue(null);
    setConfirmDiscard(false);
    setSnapshot((current) => (current && current.project_id === props.projectId ? current : null));
    let alive = true;
    readProjectResources(props.projectId, ctrl.signal).then((data) => {
      if (!alive || !reads.current.matches(ticket) || data.project_id !== props.projectId) return;
      setSnapshot(data);
      setPhase("ready");
      setBaseline(data.instructions);
      setDraft((current) => (draftDirty.current && current != null ? current : data.instructions));
    }).catch((caught: unknown) => {
      if (!alive || !reads.current.matches(ticket) || isAbort(caught)) return;
      setSnapshot(null);
      setDraft(null);
      setBaseline(null);
      setPhase("error");
      setError({ projectId: props.projectId, text: messageOf(caught) || "读取失败，没有当成空内容" });
    });
    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [sheet, props.projectId, reloadKey]);

  useLayoutEffect(() => {
    if (sheet === "instructions" && panelState === "ready") instructionRef.current?.focus();
  }, [sheet, panelState]);

  useEffect(() => {
    if (sheet) return;
    const button = opener.current;
    opener.current = null;
    if (button?.isConnected) button.focus();
  }, [sheet]);

  const requestClose = () => {
    if (confirmDiscard) {
      setConfirmDiscard(false);
      setPendingSheet("");
      return;
    }
    if (dirty) {
      setConfirmDiscard(true);
      return;
    }
    setSheet("");
  };

  const openSheet = (kind: SheetKind, button: HTMLButtonElement) => {
    if (sheet === kind) {
      requestClose();
      return;
    }
    if (dirty) {
      opener.current = button;
      setPendingSheet(kind);
      setConfirmDiscard(true);
      return;
    }
    opener.current = button;
    setMemoryId(null);
    setSheet(kind);
  };

  const discard = () => {
    draftDirty.current = false;
    setDraft(baseline);
    setConfirmDiscard(false);
    const next = pendingSheet;
    setPendingSheet("");
    setMemoryId(null);
    setSheet(next);
  };

  const save = async () => {
    if (
      saving
      || sheet !== "instructions"
      || phase !== "ready"
      || !view
      || view.project_id !== props.projectId
      || draft == null
      || baseline == null
    ) return;
    const blocked = instructionsBlockedReason(draft, view.limits.instructions_chars, archived, true);
    if (blocked || draft === baseline) {
      if (blocked) setSaveIssue({ projectId: props.projectId, text: blocked });
      return;
    }
    const projectId = props.projectId;
    const content = draft;
    const epoch = uiEpoch.current;
    const gen = ++saveGen.current;
    const ctrl = new AbortController();
    saveAbort.current?.abort();
    saveAbort.current = ctrl;
    setSaving(true);
    setSaveIssue(null);
    const sameSave = () => mounted.current
      && gen === saveGen.current
      && projectRef.current === projectId
      && uiEpoch.current === epoch
      && sheetRef.current === "instructions"
      && phaseRef.current === "ready";
    try {
      const result = await saveProjectInstructions(projectId, content, ctrl.signal, view.limits.instructions_chars);
      if (!sameSave()) return;
      if (!result.confirmed || result.instructions !== content) {
        setSaveIssue({ projectId, text: "回读与草稿不一致，没有关闭。" });
        return;
      }
      if (draftRef.current !== content) {
        setBaseline(result.instructions);
        setSaveIssue({ projectId, text: "已保存发送时的指示，当前草稿已变更，没有关闭。" });
        return;
      }
      draftDirty.current = false;
      setBaseline(result.instructions);
      setDraft(result.instructions);
      setNotice({ projectId, text: "已保存这个项目的指示。" });
      setSheet("");
    } catch (caught) {
      if (!sameSave() || isAbort(caught)) return;
      setSaveIssue({ projectId, text: messageOf(caught) || "没有保存，草稿还在" });
    } finally {
      if (mounted.current && gen === saveGen.current) setSaving(false);
    }
  };

  const startUpload = async (file: File) => {
    const projectId = props.projectId;
    const current = snapshotRef.current;
    if (!current || current.project_id !== projectId || phase !== "ready") {
      setFileIssue({ projectId, text: "档案列表还不是这个项目的，没有上传" });
      setFileIssueKind("upload");
      return;
    }
    const blocked = uploadBlockedReason(current.files, file, current.limits.file_bytes, archived);
    if (blocked) {
      retryFile.current = null;
      setFileIssue({ projectId, text: blocked });
      setFileIssueKind("upload");
      return;
    }
    const ticket = uploads.current.begin(projectId);
    const ctrl = new AbortController();
    uploadAbort.current?.abort();
    uploadAbort.current = ctrl;
    retryFile.current = { projectId, file };
    setFileIssue(null);
    setFileIssueKind("");
    setUpload({ projectId, name: file.name, ratio: 0 });
    try {
      const result = await uploadProjectFile(projectId, file, ctrl.signal, (ratio) => {
        if (!mounted.current || !acceptScopedResult(ticket, uploads.current.current(), projectId)) return;
        setUpload({ projectId, name: file.name, ratio });
      });
      if (!mounted.current || !acceptScopedResult(ticket, uploads.current.current(), result.project_id)) return;
      const latest = snapshotRef.current;
      if (latest && latest.project_id === result.project_id && latest.files.some((item) => item.name === result.file.name)) {
        setFileIssue({ projectId, text: `已有同名档案「${result.file.name}」，没有覆盖` });
        setFileIssueKind("upload");
        retryFile.current = null;
        setUpload(null);
        return;
      }
      setSnapshot((prev) => {
        if (!prev || prev.project_id !== result.project_id) return prev;
        if (prev.files.some((item) => item.name === result.file.name)) return prev;
        return { ...prev, files: [...prev.files, result.file] };
      });
      setUpload(null);
      retryFile.current = null;
      setNotice({ projectId, text: `已上传「${result.file.name}」` });
    } catch (caught) {
      if (!mounted.current || !uploads.current.matches(ticket) || isAbort(caught)) return;
      setUpload(null);
      setFileIssue({ projectId, text: messageOf(caught) || "上传失败，没有覆盖已有档案" });
      setFileIssueKind("upload");
    }
  };

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file) void startUpload(file);
  };

  const download = async (file: ProjectFileResource) => {
    const projectId = props.projectId;
    const gen = ++downloadGen.current;
    retryDownload.current = file;
    setFileIssue(null);
    setFileIssueKind("");
    try {
      const blob = await downloadProjectFile(projectId, file.name);
      if (!mounted.current || gen !== downloadGen.current || projectRef.current !== projectId) return;
      const typed = blob.type ? blob : new Blob([blob], { type: file.content_type || "application/octet-stream" });
      const saved = await pageSave(typed, file.name);
      if (!mounted.current || projectRef.current !== projectId) return;
      setNotice({
        projectId,
        text: saved === "shared" ? `已交给系统打开「${file.name}」` : `已开始下载「${file.name}」`,
      });
    } catch (caught) {
      if (!mounted.current || gen !== downloadGen.current || projectRef.current !== projectId || isAbort(caught)) return;
      setFileIssue({ projectId, text: messageOf(caught) || "下载失败" });
      setFileIssueKind("download");
    }
  };

  const canUpload = panelState === "ready" || panelState === "empty";
  const saveBlocked = instructionsBlockedReason(
    phase === "ready" && view && draft != null ? draft : null,
    view?.limits.instructions_chars ?? null,
    archived,
    phase === "ready" && view != null && draft != null,
  );
  const canSave = saveBlocked === "" && dirty && !saving;

  return (
    <div className="project-resources" data-project-id={props.projectId}>
      <div className="project-resources-pills" role="group" aria-label="项目入口">
        <button type="button" aria-pressed={sheet === "archive"} onClick={(event) => openSheet("archive", event.currentTarget)}>新增档案</button>
        <button type="button" aria-pressed={sheet === "instructions"} onClick={(event) => openSheet("instructions", event.currentTarget)}>新增指示</button>
        <button type="button" aria-pressed={sheet === "memory"} onClick={(event) => openSheet("memory", event.currentTarget)}>查看记忆</button>
      </div>
      {noticeText ? <p className="project-resources-status" role="status">{noticeText}</p> : null}
      <ResourceDialog
        open={sheet !== ""}
        labelledBy={titleId}
        onDismiss={requestClose}
      >
        {sheet ? (
          <div className="project-resources-panel" data-sheet={sheet} data-state={panelState} aria-busy={panelState === "loading"}>
            <div className="project-resources-grab" aria-hidden="true" />
            <header className="project-resources-header">
              <button type="button" className="project-resources-close" aria-label="关闭" data-autofocus={sheet === "instructions" && panelState === "ready" ? undefined : "true"} onClick={requestClose}>×</button>
              <h2 id={titleId} className="project-resources-title">{sheet === "archive" ? "项目档案" : sheet === "instructions" ? "指示" : "记忆"}</h2>
              {sheet === "instructions" ? (
                <button type="button" className="project-resources-save" aria-label="保存指示" disabled={!canSave} onClick={() => void save()}>✓</button>
              ) : <span />}
            </header>
            <div className="project-resources-body">
              {archived ? <p className="project-resources-banner">这个项目已归档，只能查看。</p> : null}
              {panelState === "loading" ? <p className="project-resources-banner" role="status">正在读取</p> : null}
              {panelState === "error" ? (
                <div className="project-resources-alert" role="alert">
                  <p>{errorTextForProject || "读取失败，没有当成空内容"}</p>
                  <button type="button" className="project-resources-retry" onClick={() => setReloadKey((value) => value + 1)}>重试</button>
                </div>
              ) : null}
              {sheet === "archive" && (panelState === "empty" || panelState === "ready") ? (
                panelState === "empty" ? (
                  <div className="project-resources-empty">
                    <div className="project-resources-empty-icon"><FilePlusIcon /></div>
                    <h3>尚无档案</h3>
                    <p>新增档案，供 clawd 在「{props.projectName}」中使用。</p>
                  </div>
                ) : (
                  <ul className="project-resources-group" aria-label="已有档案">
                    {view?.files.map((file, index) => (
                      <li key={`${file.name}:${index}`}>
                        <button
                          type="button"
                          className="project-resources-file"
                          aria-label={`下载或打开 ${file.name}`}
                          onClick={() => void download(file)}
                        >
                          <span className="project-resources-file-name">{file.name}</span>
                          <span className="project-resources-file-meta">{fileMeta(file, now)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )
              ) : null}
              {sheet === "instructions" && panelState === "ready" && draft != null ? (
                <>
                  <textarea
                    className="project-resources-instructions"
                    aria-label="项目指示"
                    aria-describedby={helperId}
                    data-autofocus="true"
                    placeholder={PLACEHOLDER}
                    ref={instructionRef}
                    value={draft}
                    readOnly={archived}
                    rows={6}
                    onChange={(event) => {
                      draftDirty.current = true;
                      setDraft(event.currentTarget.value);
                    }}
                    onKeyDown={(event) => {
                      if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && canSave) {
                        event.preventDefault();
                        void save();
                      }
                    }}
                  />
                  <p id={helperId} className="project-resources-help">指示 clawd 在「{props.projectName}」中的行为与回应方式。</p>
                  {view ? <p className="project-resources-count">{draft.length} / {view.limits.instructions_chars}</p> : null}
                </>
              ) : null}
              {sheet === "memory" && panelState === "empty" ? (
                <div className="project-resources-empty">
                  <h3>尚无记忆</h3>
                  <p>这里只显示这个项目的记忆。</p>
                </div>
              ) : null}
              {sheet === "memory" && panelState === "ready" && memoryId == null ? (
                <ul className="project-resources-group" aria-label="项目记忆">
                  {view?.memories.map((item, index) => (
                    <li key={`${item.id}:${index}`}>
                      <button
                        type="button"
                        className="project-resources-memory"
                        onClick={() => setMemoryId(item.id)}
                      >
                        <span className="project-resources-memory-title">{item.title || "未命名记忆"}</span>
                        <span className="project-resources-memory-time">{formatResourceUpdated(item.updated_at, now)}</span>
                        <ChevronIcon />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              {sheet === "memory" && panelState === "ready" && memoryId != null ? (
                <MemoryDetail item={selected} now={now} onBack={() => setMemoryId(null)} />
              ) : null}
              {saveIssueText ? <div className="project-resources-alert" role="alert">{saveIssueText}</div> : null}
              {fileIssueText ? (
                <div className="project-resources-alert" role="alert">
                  <p>{fileIssueText}</p>
                  {fileIssueKind === "upload" && retryFile.current?.projectId === props.projectId ? (
                    <button type="button" className="project-resources-retry" onClick={() => {
                      const pending = retryFile.current;
                      if (pending && pending.projectId === props.projectId) void startUpload(pending.file);
                    }}>重试</button>
                  ) : null}
                  {fileIssueKind === "download" && retryDownload.current ? (
                    <button type="button" className="project-resources-retry" onClick={() => {
                      const pending = retryDownload.current;
                      if (pending) void download(pending);
                    }}>重试下载</button>
                  ) : null}
                </div>
              ) : null}
              {confirmDiscard ? (
                <div className="project-resources-confirm" role="group" aria-label="未保存的指示">
                  <p>指示还没保存。放弃这些更改？</p>
                  <div className="project-resources-confirm-actions">
                    <button type="button" className="stay" onClick={() => { setConfirmDiscard(false); setPendingSheet(""); }}>继续编辑</button>
                    <button type="button" className="discard" onClick={discard}>放弃更改</button>
                  </div>
                </div>
              ) : null}
            </div>
            {sheet === "archive" ? (
              <div className="project-resources-footer">
                {uploadView ? (
                  <div className="project-resources-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(uploadView.ratio * 100)} aria-label={`正在上传 ${uploadView.name}`}>
                    <span style={{ width: `${Math.round(uploadView.ratio * 100)}%` }} />
                  </div>
                ) : null}
                <input
                  ref={fileInput}
                  className="project-resources-file-input"
                  type="file"
                  aria-hidden="true"
                  tabIndex={-1}
                  onChange={onPick}
                />
                <button
                  type="button"
                  className="project-resources-add"
                  disabled={!canUpload || archived || uploadView != null}
                  onClick={() => fileInput.current?.click()}
                >
                  <PaperclipIcon />
                  新增档案
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </ResourceDialog>
    </div>
  );
}

function MemoryDetail(props: {
  item: ProjectMemoryResource | null;
  now: number;
  onBack: () => void;
}) {
  return (
    <div className="project-resources-detail" data-memory-detail="true">
      <button type="button" className="project-resources-back" onClick={props.onBack}>‹ 返回列表</button>
      {props.item ? (
        <>
          <h3 className="project-resources-detail-title">{props.item.title || "未命名记忆"}</h3>
          <p className="project-resources-meta">{formatResourceUpdated(props.item.updated_at, props.now)}</p>
          <p className="project-resources-detail-body">{props.item.content}</p>
        </>
      ) : <p className="project-resources-banner">这条记忆已经不在列表里。</p>}
    </div>
  );
}

function ResourceDialog(props: {
  open: boolean;
  labelledBy: string;
  onDismiss: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!props.open || !dialog) return;
    if (!dialog.open) dialog.showModal();
    const previousBody = document.body.style.overflow;
    const previousHtml = document.documentElement.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    const preferred = dialog.querySelector<HTMLElement>("[data-autofocus]") ?? dialog.querySelector<HTMLElement>("button");
    preferred?.focus();
    return () => {
      document.body.style.overflow = previousBody;
      document.documentElement.style.overflow = previousHtml;
      if (dialog.open) dialog.close();
    };
  }, [props.open]);

  if (!props.open || typeof document === "undefined") return null;
  return createPortal(
    <dialog
      ref={dialogRef}
      className="project-resources-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby={props.labelledBy}
      onCancel={(event) => {
        event.preventDefault();
        props.onDismiss();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) props.onDismiss();
      }}
    >
      {props.children}
    </dialog>,
    document.body,
  );
}

function sheetState(
  sheet: SheetKind | "",
  phase: Phase,
  view: ProjectResourcesSnapshot | null,
  errorText: string,
): "loading" | "error" | "empty" | "ready" {
  if (phase === "error" && errorText) return "error";
  if (phase !== "ready" || !view) return "loading";
  if (sheet === "archive" && view.files.length === 0) return "empty";
  if (sheet === "memory" && view.memories.length === 0) return "empty";
  return "ready";
}

function fileMeta(file: ProjectFileResource, now: number): string {
  const size = formatFileSize(file.size);
  const time = formatResourceUpdated(file.updated_at, now);
  return size ? `${size} · ${time}` : time;
}

function isAbort(error: unknown): boolean {
  return !!error && typeof error === "object" && "name" in error && (error as { name?: string }).name === "AbortError";
}

function messageOf(error: unknown): string {
  if (isAbort(error)) return "";
  return error instanceof Error ? error.message : "";
}

function FilePlusIcon() {
  return (
    <svg className="project-resources-glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7.2 3.8h6.4L18.8 9v10.2a1.4 1.4 0 0 1-1.4 1.4H7.2a1.4 1.4 0 0 1-1.4-1.4V5.2a1.4 1.4 0 0 1 1.4-1.4z" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M13.4 3.9V9h5.1" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 12.2v5.2M9.4 14.8h5.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function PaperclipIcon() {
  return (
    <svg className="project-resources-glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8.2 12.7l6.2-6.2a2.8 2.8 0 0 1 4 4l-7.5 7.5a4 4 0 0 1-5.7-5.7l7-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg className="project-resources-chevron" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
