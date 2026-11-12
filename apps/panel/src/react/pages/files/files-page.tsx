import { useEffect, useRef, useState, Fragment } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { queryKeys, useDeleteFile, useDir, useFileRoots } from "@/lib/queries";
import { fetchAndSave } from "@/lib/save-file";
import {
  MAX_UPLOAD_BYTES,
  downloadUrl,
  uploadFile,
  type DirListing,
  type FileEntry,
  type FileRoot,
} from "@/lib/sidecar";
import { relativeTime, subscribeTick } from "@/lib/time";
import "./files.css";

interface UploadRow {
  id: number;
  name: string;
  progress: number;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

function bytes(n: number | null | undefined): string {
  if (n == null) return "—";
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`;
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

function hasFiles(transfer: DataTransfer | null): boolean {
  if (!transfer) return false;
  return Array.from(transfer.types).includes("Files");
}

export function FilesPage() {
  const queryClient = useQueryClient();
  const rootsQuery = useFileRoots();
  const deleteFile = useDeleteFile();
  const [root, setRoot] = useState("uploads");
  const [path, setPath] = useState("");
  const [shown, setShown] = useState<DirListing | null>(null);
  const [confirm, setConfirm] = useState("");
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [saving, setSaving] = useState("");
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [forceDir, setForceDir] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const uploadSeq = useRef(0);
  const uploadsRef = useRef<UploadRow[]>([]);
  const rootRef = useRef(root);
  const pathRef = useRef(path);
  const savingRef = useRef("");

  const roots = rootsQuery.data;
  const activeRoot = !roots
    ? root
    : roots.some((item) => item.id === root)
      ? root
      : (roots[0]?.id ?? "");
  const dirEnabled = rootsQuery.isSuccess || forceDir;
  const dirQuery = useDir(activeRoot, path, dirEnabled);

  rootRef.current = activeRoot;
  pathRef.current = path;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => subscribeTick(() => setNow(Date.now())), []);

  useEffect(() => {
    if (!dirQuery.data) return;
    setShown(dirQuery.data);
    pathRef.current = dirQuery.data.path;
    if (dirQuery.data.path !== path) setPath(dirQuery.data.path);
  }, [dirQuery.data, path]);

  const open = (nextRoot: string, nextPath: string) => {
    setConfirm("");
    setDragging(false);
    setRoot(nextRoot);
    setPath(nextPath);
    pathRef.current = nextPath;
    rootRef.current = nextRoot;
    if (!rootsQuery.isSuccess) setForceDir(true);
  };

  const publishUploads = (rows: UploadRow[]) => {
    uploadsRef.current = rows;
    if (mounted.current) setUploads(rows.map((row) => ({ ...row })));
  };

  const upload = async (files: File[]) => {
    if (!shown?.writable) return;
    const startedRoot = rootRef.current;
    const startedPath = pathRef.current;
    const stillThere = () => rootRef.current === startedRoot && pathRef.current === startedPath;
    for (const file of files) {
      if (file.size > MAX_UPLOAD_BYTES) {
        if (mounted.current && stillThere()) setNotice(`${file.name} 超过 500MB，未上传`);
        continue;
      }
      const entry: UploadRow = { id: ++uploadSeq.current, name: file.name, progress: 0 };
      publishUploads([...uploadsRef.current, entry]);
      try {
        const result = await uploadFile(file, startedRoot, (fraction) => {
          entry.progress = fraction;
          publishUploads(uploadsRef.current);
        }, 0, startedPath);
        if (mounted.current && stillThere()) setNotice(`已上传 ${file.name} → ${result.absPath}`);
      } catch (error) {
        if (mounted.current && stillThere()) {
          setNotice(error instanceof ApiError ? `${file.name} 上传失败：${error.message}` : `${file.name} 上传失败`);
        }
      } finally {
        publishUploads(uploadsRef.current.filter((row) => row.id !== entry.id));
      }
    }
    if (!mounted.current) return;
    await queryClient.invalidateQueries({ queryKey: queryKeys.dir(startedRoot, startedPath) });
  };

  const remove = async (entry: FileEntry) => {
    const rel = joinPath(pathRef.current, entry.name);
    const atRoot = rootRef.current;
    setConfirm("");
    try {
      await deleteFile.mutateAsync({ root: atRoot, path: rel });
      if (!mounted.current) return;
      setNotice(`已删除 ${entry.name}`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.dir(rootRef.current, pathRef.current) });
    } catch (error) {
      if (mounted.current) setNotice(error instanceof ApiError ? `删除失败：${error.message}` : "删除失败");
    }
  };

  const copyPath = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice(`已复制路径：${text}`);
    } catch {
      setNotice(text);
    }
  };

  const save = async (name: string, rel: string) => {
    if (savingRef.current) return;
    savingRef.current = name;
    setSaving(name);
    setNotice("正在下载…");
    try {
      await fetchAndSave(downloadUrl(rootRef.current, rel), name);
      if (mounted.current && savingRef.current === name) setNotice("");
    } catch (error) {
      if (mounted.current && savingRef.current === name) {
        setNotice(error instanceof Error ? error.message : "下载失败，请重试");
      }
    } finally {
      if (savingRef.current === name) {
        savingRef.current = "";
        if (mounted.current) setSaving("");
      }
    }
  };

  const dirError = dirQuery.isError ? errorText(dirQuery.error, "加载失败") : "";
  const rootsError = rootsQuery.isError && !shown ? errorText(rootsQuery.error, "加载失败") : "";
  const error = dirError || rootsError;
  const loading = !shown && !error && (rootsQuery.isPending || dirQuery.isPending || dirQuery.isFetching);
  const parts = shown?.path ? shown.path.split("/") : [];
  const rootLabel = roots?.find((item) => item.id === activeRoot)?.label ?? activeRoot;

  return (
    <section className="files-page" aria-label="文件">
      <div className="page">
        <div className="head">
          <h1>文件</h1>
          {shown?.writable ? (
            <>
              <button type="button" className="btn primary" onClick={() => inputRef.current?.click()}>
                上传
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                onChange={(event) => {
                  const picked = event.target.files;
                  if (picked) void upload([...picked]);
                  event.target.value = "";
                }}
              />
            </>
          ) : null}
        </div>
        <p className="sub">只开放下面这几个目录。上传的文件可以让 clawd 直接读取：点「复制路径」发给它即可。单个文件最大 50MB。</p>
        <div className="roots" role="group" aria-label="目录">
          {(roots ?? []).map((item: FileRoot) => (
            <button
              key={item.id}
              type="button"
              className="btn"
              aria-pressed={item.id === activeRoot}
              onClick={() => open(item.id, "")}
            >
              {item.label}{item.writable ? "" : "（只读）"}
            </button>
          ))}
        </div>
        {notice ? <div className={`notice ${notice.includes("失败") ? "error" : "ok"}`}>{notice}</div> : null}
        {uploads.length ? (
          <div className="uploads">
            {uploads.map((row) => (
              <div key={row.id}>
                {row.name} · {Math.round(row.progress * 100)}%
                <div className="bar"><i style={{ width: `${row.progress * 100}%` }} /></div>
              </div>
            ))}
          </div>
        ) : null}
        {shown ? (
          <div className="crumbs">
            <button type="button" onClick={() => open(activeRoot, "")}>{rootLabel}</button>
            {parts.map((part, index) => (
              <Fragment key={`${part}-${index}`}>
                <span>/</span>
                <button type="button" onClick={() => open(activeRoot, parts.slice(0, index + 1).join("/"))}>{part}</button>
              </Fragment>
            ))}
          </div>
        ) : null}
        <div
          className="drop"
          {...(dragging ? { "data-drag": "" } : {})}
          onDragOver={(event) => {
            if (!shown?.writable || !hasFiles(event.dataTransfer)) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (event.dataTransfer?.files.length) void upload([...event.dataTransfer.files]);
          }}
        >
          {error ? (
            <div className="empty">
              {error}
              <br />
              <button type="button" className="link" onClick={() => open(activeRoot, pathRef.current)}>重试</button>
            </div>
          ) : loading ? (
            <div className="loading">加载中…</div>
          ) : shown && shown.entries.length ? (
            <div className="card list">
              {shown.entries.map((entry) => {
                const rel = joinPath(shown.path, entry.name);
                return (
                  <div className="row" key={entry.name}>
                    <span className="icon" aria-hidden="true">{entry.dir ? "📁" : "📄"}</span>
                    {entry.dir ? (
                      <button type="button" className="name" onClick={() => open(activeRoot, rel)}>{entry.name}</button>
                    ) : (
                      <button
                        type="button"
                        className="name"
                        data-save-file=""
                        title={`下载 ${entry.name}`}
                        disabled={saving === entry.name}
                        onClick={() => void save(entry.name, rel)}
                      >
                        {entry.name}
                      </button>
                    )}
                    <span className="meta">{entry.dir ? "" : bytes(entry.size)} · {relativeTime(entry.modifiedAt, now)}</span>
                    <span className="acts">
                      {entry.dir ? null : (
                        <button
                          type="button"
                          className="btn"
                          title="复制服务器路径，可以直接发给 clawd"
                          onClick={() => void copyPath(`${shown.absPath}/${entry.name}`)}
                        >
                          复制路径
                        </button>
                      )}
                      {shown.writable && !entry.dir ? (
                        confirm === entry.name ? (
                          <button type="button" className="btn danger" onClick={() => void remove(entry)}>确认删除</button>
                        ) : (
                          <button type="button" className="btn danger" onClick={() => setConfirm(entry.name)}>删除</button>
                        )
                      ) : null}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="empty">{shown?.writable ? "这里还没有文件，可以把文件拖进来" : "这里还没有文件"}</div>
          )}
        </div>
      </div>
    </section>
  );
}
