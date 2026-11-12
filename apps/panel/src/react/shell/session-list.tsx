import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { api, type SessionSummary } from "@/lib/api.js";
import { queryKeys, useBindSessionProject, useCreateProject, useDeleteSession, useForkSession, useLegacyFolders, usePatchProject, usePatchSession, useProjects } from "@/lib/queries";
import { LiveRetentionTracker } from "@/lib/session-live-retention.js";
import { sidecar } from "@/lib/sidecar.js";
import {
  archiveConfirmText,
  archivePatchBody,
  createNamedProjectBody,
  legacyMappingNeeds,
  projectActionError,
  rememberLegacyMappingNeeds,
  restorePatchBody,
} from "@/lib/project-model.js";
import { getOpenProjectId, getPendingNewChatProjectId, setOpenProjectId, setPendingNewChatProjectId, subscribeOpenProject } from "@/lib/project-send.js";
import { relativeTime, subscribeTick } from "@/lib/time.js";
import { useUiStore } from "@/stores/ui";
import { consumeSkipRenameBlur, setShellSession } from "./shell-session";
import { readId } from "./shell-events";
import { LegacyFolderMigration } from "./legacy-folder-migration";
import {
  HEAD_PAGE,
  HEAD_POLL_MS,
  LIVE_POLL_MS,
  PAGE,
  SEARCH_CAP,
  SOURCE_LABEL,
  VIEW_SOURCES,
  accepts,
  byRecent,
  dedupeSort,
  failMessage,
  folderNameError,
  folderSessionItems,
  mergeSessions,
  unfiledSessions,
  visibleSessions,
  type ListCursor,
} from "./session-logic";

export interface SessionListHandle {
  focusSearch: () => void;
  upsertSession: (session: SessionSummary) => void;
  refreshHead: () => Promise<void>;
}

interface RenameJob {
  token: symbol;
  promise: Promise<void>;
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

export const SessionList = forwardRef<SessionListHandle, {
  onSelect: (session: SessionSummary) => void;
  onDeleted: (sessionId: string) => void;
  onRenamed: (sessionId: string, title: string | null) => void;
}>(function SessionList(props, ref) {
  const filter = useUiStore((state) => state.sessionListFilter);
  const setFilter = useUiStore((state) => state.setSessionListFilter);
  const query = useUiStore((state) => state.sessionListQuery);
  const setQuery = useUiStore((state) => state.setSessionListQuery);
  const selectedId = useUiStore((state) => state.selectedSessionId);
  const projectsQuery = useProjects();
  const legacyQuery = useLegacyFolders();
  const patchSession = usePatchSession();
  const deleteSession = useDeleteSession();
  const forkSession = useForkSession();
  const createProject = useCreateProject();
  const patchProject = usePatchProject();
  const bindSession = useBindSessionProject();
  const queryClient = useQueryClient();
  let navigate: (to: string) => void;
  try {
    navigate = useNavigate();
  } catch {
    // Fixtures mount SessionList without a Router; navigating is a no-op there.
    navigate = () => {};
  }
  const openProjectId = useSyncExternalStore(subscribeOpenProject, getOpenProjectId, () => null);
  const [tracker] = useState(() => new LiveRetentionTracker());
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [menuId, setMenuId] = useState("");
  const [confirmId, setConfirmId] = useState("");
  const [renamingId, setRenamingId] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [busyId, setBusyId] = useState("");
  const [folderEditor, setFolderEditor] = useState("");
  const [folderName, setFolderName] = useState("");
  const [folderError, setFolderError] = useState("");
  const [folderDelete, setFolderDelete] = useState("");
  const [pendingProject, setPendingProject] = useState<string | null>(() => getPendingNewChatProjectId());
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const searchRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const sessionsRef = useRef(sessions);
  const filterRef = useRef(filter);
  const queryRef = useRef(query);
  const loadingRef = useRef(false);
  const hasMoreRef = useRef(false);
  const errorRef = useRef("");
  const inflight = useRef<AbortController | undefined>(undefined);
  const headRefresh = useRef<Promise<void> | undefined>(undefined);
  const cursors = useRef(new Map<string, ListCursor>());
  const editSeq = useRef(0);
  const editedAt = useRef(new Map<string, number>());
  const deleted = useRef(new Set<string>());
  const renameJobs = useRef(new Map<string, RenameJob>());
  const confirmedTitles = useRef(new Map<string, string | null>());
  const renameOrigin = useRef("");
  const projectBusy = useRef(false);
  const actions = useRef<Pick<SessionListHandle, "upsertSession" | "refreshHead">>({
    upsertSession: () => {},
    refreshHead: () => Promise.resolve(),
  });

  filterRef.current = filter;
  queryRef.current = query;
  sessionsRef.current = sessions;
  hasMoreRef.current = hasMore;
  errorRef.current = error;

  const index = projectsQuery.data;
  const projects = index?.projects ?? [];
  const activeProjects = projects.filter((project) => !project.archived);
  const archivedProjects = projects.filter((project) => project.archived);
  const assignments = index?.assignments ?? {};
  const assignedSessions = index?.sessions ?? [];
  const assignmentsRef = useRef(assignments);
  assignmentsRef.current = assignments;
  const visible = useMemo(() => visibleSessions(sessions, query), [sessions, query]);
  const unfiled = useMemo(
    () => unfiledSessions(visible, projects.map((project) => ({ id: project.id, name: project.name, collapsed: false })), assignments),
    [visible, projects, assignments],
  );

  const replace = useCallback((id: string, patch: Partial<SessionSummary>) => {
    editedAt.current.set(id, ++editSeq.current);
    setSessions((prev) => {
      const next = prev.map((item) => (item.id === id ? { ...item, ...patch } : item));
      sessionsRef.current = next;
      return next;
    });
  }, []);

  const renamePending = useCallback(() => new Set(renameJobs.current.keys()), []);

  const invalidateSessions = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
  }, [queryClient]);

  const load = useCallback(async (reset: boolean) => {
    inflight.current?.abort();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    loadingRef.current = true;
    setLoading(true);
    setError("");
    errorRef.current = "";
    const currentFilter = filterRef.current;
    if (reset) {
      cursors.current = new Map(VIEW_SOURCES[currentFilter].map((src) => [src ?? "*", { offset: 0, hasMore: true }]));
    }
    try {
      if (currentFilter === "live") {
        const since = editSeq.current;
        const raw = await sidecar.activeSessions(ctrl.signal);
        if (ctrl.signal.aborted) return;
        const active = raw.filter((item) => item && accepts("live", item.source) && !deleted.current.has(item.id));
        const retained = tracker.reconcile(active);
        const prevById = new Map(sessionsRef.current.map((item) => [item.id, item]));
        const pending = renamePending();
        const updated = retained.map((item) => {
          const old = prevById.get(item.id);
          if (old && (pending.has(item.id) || (editedAt.current.get(item.id) ?? -1) >= since)) return { ...item, ...old };
          return old ? { ...old, ...item } : item;
        });
        const next = [...updated].sort(byRecent);
        sessionsRef.current = next;
        setSessions(next);
        hasMoreRef.current = false;
        setHasMore(false);
        return;
      }
      const pending = [...cursors.current].filter(([, cursor]) => cursor.hasMore);
      const pages = await Promise.all(
        pending.map(([key, cursor]) =>
          api.listSessions(
            { limit: PAGE, offset: cursor.offset, source: key === "*" ? undefined : key },
            ctrl.signal,
          ),
        ),
      );
      if (ctrl.signal.aborted) return;
      pending.forEach(([key, cursor], index) => {
        const page = pages[index];
        if (!page) return;
        cursors.current.set(key, {
          offset: cursor.offset + page.data.filter((item) => !item.pinned).length,
          hasMore: page.has_more,
        });
      });
      const merged = [...(reset ? [] : sessionsRef.current), ...pages.flatMap((page) => page.data)];
      const next = dedupeSort(merged.filter((item) => !deleted.current.has(item.id)));
      sessionsRef.current = next;
      setSessions(next);
      const more = [...cursors.current.values()].some((cursor) => cursor.hasMore);
      hasMoreRef.current = more;
      setHasMore(more);
    } catch (caught) {
      if (ctrl.signal.aborted) return;
      const message = caught instanceof Error ? caught.message : "加载失败";
      errorRef.current = message;
      setError(message);
    } finally {
      if (inflight.current === ctrl) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [renamePending, tracker]);

  const loadForSearch = useCallback(async () => {
    while (queryRef.current.trim() && hasMoreRef.current && !loadingRef.current && sessionsRef.current.length < SEARCH_CAP) {
      await load(false);
      if (errorRef.current) return;
    }
  }, [load]);

  const refreshHead = useCallback((): Promise<void> => {
    if (loadingRef.current) return Promise.resolve();
    if (filterRef.current === "live") return load(true);
    const filterAtStart = filterRef.current;
    headRefresh.current ??= (async () => {
      try {
        const since = editSeq.current;
        const page = await api.listSessions({ limit: HEAD_PAGE });
        if (loadingRef.current || filterRef.current !== filterAtStart) return;
        const next = mergeSessions(
          sessionsRef.current,
          page.data,
          filterAtStart,
          deleted.current,
          since,
          editedAt.current,
          renamePending(),
        );
        sessionsRef.current = next;
        setSessions(next);
      } catch (caught) {
        console.warn("会话列表刷新失败", caught);
      } finally {
        headRefresh.current = undefined;
      }
    })();
    return headRefresh.current;
  }, [load, renamePending]);

  const upsertSession = useCallback((summary: SessionSummary) => {
    if (filterRef.current === "live") {
      void refreshHead();
      return;
    }
    const next = mergeSessions(
      sessionsRef.current,
      [summary],
      filterRef.current,
      deleted.current,
      Number.POSITIVE_INFINITY,
      editedAt.current,
      renamePending(),
    );
    sessionsRef.current = next;
    setSessions(next);
  }, [refreshHead, renamePending]);

  actions.current = { upsertSession, refreshHead };

  useImperativeHandle(ref, () => ({
    focusSearch: () => {
      searchRef.current?.focus();
      searchRef.current?.select();
    },
    upsertSession: (session) => actions.current.upsertSession(session),
    refreshHead: () => actions.current.refreshHead(),
  }), []);

  useEffect(() => subscribeTick(() => setNow(Date.now())), []);

  useEffect(() => {
    const onDeleted = (event: Event) => {
      const id = readId(event);
      if (!id) return;
      tracker.markDeleted(id);
      deleted.current.add(id);
      editedAt.current.set(id, ++editSeq.current);
      confirmedTitles.current.delete(id);
      const next = sessionsRef.current.filter((item) => item.id !== id);
      sessionsRef.current = next;
      setSessions(next);
      setMenuId((current) => current === id ? "" : current);
      setConfirmId((current) => current === id ? "" : current);
      setRenamingId((current) => current === id ? "" : current);
    };
    window.addEventListener("hp-session-deleted", onDeleted);
    return () => window.removeEventListener("hp-session-deleted", onDeleted);
  }, [tracker]);

  useEffect(() => {
    setSessions([]);
    sessionsRef.current = [];
    setHasMore(false);
    hasMoreRef.current = false;
    setError("");
    setMenuId("");
    setConfirmId("");
    let cancelled = false;
    void (async () => {
      await load(true);
      if (!cancelled) await loadForSearch();
    })();
    return () => {
      cancelled = true;
      inflight.current?.abort();
    };
  }, [filter, load, loadForSearch]);

  useEffect(() => {
    void loadForSearch();
  }, [query, loadForSearch]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshHead();
    };
    document.addEventListener("visibilitychange", onVisible);
    const liveTimer = window.setInterval(() => {
      if (document.visibilityState === "visible" && filterRef.current === "live") void refreshHead();
    }, LIVE_POLL_MS);
    const headTimer = window.setInterval(() => {
      if (document.visibilityState === "visible" && filterRef.current !== "live") void refreshHead();
    }, HEAD_POLL_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(liveTimer);
      window.clearInterval(headTimer);
    };
  }, [refreshHead]);

  useEffect(() => {
    if (!selectedId || getOpenProjectId()) return;
    const found = sessions.find((session) => session.id === selectedId);
    if (found) setShellSession(found);
  }, [sessions, selectedId]);

  useEffect(() => {
    if (!folderEditor) return;
    folderInputRef.current?.focus();
  }, [folderEditor]);

  useEffect(() => {
    if (!renamingId) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [renamingId]);

  useEffect(() => {
    if (!legacyQuery.isSuccess) return;
    try {
      rememberLegacyMappingNeeds(legacyMappingNeeds(legacyQuery.data.folders), localStorage);
    } catch {
      // 记录失败仍显示本次核对结果，不隐藏未迁移提示。
    }
  }, [legacyQuery.isSuccess, legacyQuery.data]);

  useEffect(() => {
    if (!projectsQuery.isSuccess || !pendingProject) return;
    const found = projectsQuery.data.projects.find((project) => project.id === pendingProject);
    if (!found || found.archived || !found.primary_path) {
      setPendingNewChatProjectId(null);
      setPendingProject(null);
    }
  }, [projectsQuery.isSuccess, projectsQuery.data, pendingProject]);

  const runProject = useCallback(async (work: () => Promise<void>, fallback: string): Promise<boolean> => {
    if (projectBusy.current) return false;
    projectBusy.current = true;
    try {
      await work();
      setFolderError("");
      return true;
    } catch (caught) {
      setFolderError(projectActionError(caught, fallback));
      return false;
    } finally {
      projectBusy.current = false;
    }
  }, []);

  const choosePending = (id: string | null) => {
    setPendingNewChatProjectId(id);
    setPendingProject(id);
  };

  const openProject = (id: string) => {
    setOpenProjectId(id);
    choosePending(id);
    setMenuId("");
    setFolderDelete("");
    setFolderEditor("");
    const selected = useUiStore.getState().selectedSessionId;
    if (selected && assignmentsRef.current[selected] !== id) {
      useUiStore.getState().setSelectedSessionId(null);
      setShellSession(null);
    }
    if (window.matchMedia("(max-width: 768px)").matches) useUiStore.getState().setDrawerOpen(false);
    if (window.location.pathname !== "/") navigate("/");
  };

  const commitFolder = async (event: FormEvent) => {
    event.preventDefault();
    const message = folderNameError(folderName, projects.map((project) => ({ id: project.id, name: project.name, collapsed: false })), folderEditor);
    if (message) {
      setFolderError(message);
      return;
    }
    if (folderEditor === "new") {
      let createdId = "";
      const ok = await runProject(
        () => createProject.mutateAsync(createNamedProjectBody(folderName)).then((created) => {
          createdId = created.id;
        }),
        "文件夹保存失败",
      );
      if (ok && createdId) openProject(createdId);
      if (ok) setFolderEditor("");
      return;
    }
    const ok = await runProject(
      () => patchProject.mutateAsync({ id: folderEditor, patch: { name: folderName.trim() } }).then(() => undefined),
      "文件夹保存失败",
    );
    if (ok) setFolderEditor("");
  };

  const togglePin = async (session: SessionSummary) => {
    setMenuId("");
    setConfirmId("");
    const prev = session.pinned;
    replace(session.id, { pinned: !prev });
    try {
      await patchSession.mutateAsync({ sessionId: session.id, patch: { pinned: !prev } });
      invalidateSessions();
    } catch (caught) {
      replace(session.id, { pinned: prev });
      setError(failMessage(caught, "置顶失败"));
    }
  };

  const commitRename = async (sessionId: string, value: string) => {
    if (renamingId !== sessionId) return;
    setRenamingId("");
    const title = value.trim();
    if (!title || title === renameOrigin.current) return;
    const previous = renameJobs.current.get(sessionId);
    const token = Symbol();
    if (!previous) confirmedTitles.current.set(sessionId, renameOrigin.current || null);
    replace(sessionId, { title });
    const job = (async () => {
      await previous?.promise;
      try {
        await patchSession.mutateAsync({ sessionId, patch: { title } });
        confirmedTitles.current.set(sessionId, title);
        if (renameJobs.current.get(sessionId)?.token !== token) return;
        renameJobs.current.delete(sessionId);
        replace(sessionId, { title });
        props.onRenamed(sessionId, title);
        invalidateSessions();
      } catch (caught) {
        if (renameJobs.current.get(sessionId)?.token !== token) return;
        const confirmed = confirmedTitles.current.get(sessionId) ?? null;
        renameJobs.current.delete(sessionId);
        replace(sessionId, { title: confirmed });
        props.onRenamed(sessionId, confirmed);
        setError(failMessage(caught, "改名失败"));
      } finally {
        if (!renameJobs.current.has(sessionId)) confirmedTitles.current.delete(sessionId);
      }
    })();
    renameJobs.current.set(sessionId, { token, promise: job });
    await job;
  };

  const forkOne = async (session: SessionSummary) => {
    setMenuId("");
    setConfirmId("");
    setBusyId(session.id);
    try {
      const copy = await forkSession.mutateAsync(session.id);
      const next = [copy, ...sessionsRef.current.filter((item) => item.id !== copy.id)];
      sessionsRef.current = next;
      setSessions(next);
      props.onSelect(copy);
      invalidateSessions();
    } catch (caught) {
      setError(failMessage(caught, "分叉失败"));
    } finally {
      setBusyId("");
    }
  };

  const deleteOne = async (session: SessionSummary) => {
    setMenuId("");
    setConfirmId("");
    setBusyId(session.id);
    try {
      const result = await deleteSession.mutateAsync(session.id);
      if (!result || typeof result !== "object") throw new Error("服务器未确认删除，对话仍保留");
      const receipt = result as { deleted?: unknown; id?: unknown };
      if (receipt.deleted !== true || receipt.id !== session.id) throw new Error("服务器未确认该对话已删除，对话仍保留");
      window.dispatchEvent(new CustomEvent("hp-session-deleted", { detail: session.id }));
      invalidateSessions();
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
    } catch (caught) {
      setError(failMessage(caught, "删除失败"));
    } finally {
      setBusyId("");
    }
  };

  const moveSession = (sessionId: string, projectId: string | null) => {
    setMenuId("");
    setConfirmId("");
    void runProject(
      () => bindSession.mutateAsync({ sessionId, projectId }).then(() => undefined),
      "移动失败",
    );
  };

  const startRename = (session: SessionSummary) => {
    setMenuId("");
    setConfirmId("");
    setRenamingId(session.id);
    setRenameValue(session.title ?? "");
    renameOrigin.current = session.title ?? "";
  };

  const onRenameKey = (sessionId: string, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      void commitRename(sessionId, event.currentTarget.value);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setRenamingId("");
    }
  };

  const onRenameBlur = (sessionId: string, value: string) => {
    if (consumeSkipRenameBlur()) {
      setRenamingId("");
      return;
    }
    void commitRename(sessionId, value);
  };

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (!menuId) return;
      if (event.composedPath().some((node) => node instanceof HTMLElement && (node.classList.contains("menu") || node.classList.contains("more-btn")))) return;
      setMenuId("");
      setConfirmId("");
    };
    const onDocKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && menuId) {
        setMenuId("");
        setConfirmId("");
      }
    };
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onDocKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onDocKey);
    };
  }, [menuId]);

  const searching = query.trim().length > 0;
  const projectBusyNow = createProject.isPending || patchProject.isPending || bindSession.isPending;
  const shownFolderError = folderError || (projectsQuery.isError ? "文件夹加载失败，请重试；对话仍可正常访问" : "");
  const legacyGroups = (() => {
    const data = legacyQuery.data;
    if (!data) return [] as { id: string; name: string; items: SessionSummary[] }[];
    const taken = new Set(Object.keys(assignments));
    return data.folders.flatMap((folder) => {
      const items = folderSessionItems(
        sessions,
        data.sessions ?? [],
        folder.id,
        data.assignments ?? {},
        query,
        deleted.current,
      ).filter((session) => !taken.has(session.id));
      if (!items.length) return [];
      return [{ id: folder.id, name: folder.name.trim() || "未命名文件夹", items }];
    });
  })();
  const archiveProject = async (id: string) => {
    const ok = await runProject(
      () => patchProject.mutateAsync({ id, patch: archivePatchBody() }).then(() => undefined),
      "项目归档失败",
    );
    if (!ok) return;
    setFolderDelete("");
    if (pendingProject === id) choosePending(null);
    if (getOpenProjectId() === id) {
      setOpenProjectId(null);
      const selected = useUiStore.getState().selectedSessionId;
      setShellSession(selected ? sessionsRef.current.find((session) => session.id === selected) ?? null : null);
    }
  };

  const restoreProject = (id: string) => {
    void runProject(
      () => patchProject.mutateAsync({ id, patch: restorePatchBody() }).then(() => undefined),
      "项目恢复失败",
    );
  };


  const renderSession = (session: SessionSummary) => {
    if (renamingId === session.id) {
      return (
        <li key={session.id}>
          <input
            ref={renameInputRef}
            className="rename"
            maxLength={120}
            aria-label="对话名称"
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={(event) => onRenameKey(session.id, event)}
            onBlur={(event) => onRenameBlur(session.id, event.target.value)}
          />
        </li>
      );
    }
    const open = menuId === session.id;
    const assigned = assignments[session.id];
    return (
      <li key={session.id}>
        <button
          className="item"
          disabled={busyId === session.id}
          aria-current={session.id === selectedId}
          onClick={() => {
            const assigned = assignments[session.id];
            choosePending(assigned ?? null);
            setOpenProjectId(null);
            props.onSelect(session);
          }}
        >
          <span className="title">
            {session.pinned ? <span className="pin" aria-label="已置顶">●</span> : null}
            {session.title || "未命名对话"}
          </span>
          <span className="time">{relativeTime(session.last_active ?? session.started_at, now)}</span>
          <span className="meta">
            <span className="badge">{sourceLabel(session.source)}</span>
            {session.preview ?? `${session.message_count} 条消息`}
          </span>
        </button>
        <button
          className="more-btn"
          aria-label="更多操作"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => {
            setConfirmId("");
            setMenuId(open ? "" : session.id);
          }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
            <circle cx="3" cy="8" r="1.4" />
            <circle cx="8" cy="8" r="1.4" />
            <circle cx="13" cy="8" r="1.4" />
          </svg>
        </button>
        {open ? (
          <div className="presence" data-kind="drop" data-state="open">
            <div className="menu" role="menu">
              <div className="sheet">
              <button role="menuitem" onClick={() => void togglePin(session)}>{session.pinned ? "取消置顶" : "置顶"}</button>
              <button role="menuitem" onClick={() => startRename(session)}>重命名</button>
              {assigned ? (
                <button role="menuitem" disabled={projectBusyNow} onClick={() => moveSession(session.id, null)}>移出文件夹</button>
              ) : null}
              <button role="menuitem" onClick={() => void forkOne(session)}>分叉为新对话</button>
              {confirmId === session.id ? (
                <button role="menuitem" className="danger" onClick={() => void deleteOne(session)}>确认删除</button>
              ) : (
                <button role="menuitem" className="danger" onClick={() => setConfirmId(session.id)}>删除</button>
              )}
              </div>
            </div>
          </div>
        ) : null}
      </li>
    );
  };

  const renderProject = (project: (typeof projects)[number], archived: boolean) => {
    const items = folderSessionItems(sessions, assignedSessions, project.id, assignments, query, deleted.current);
    const sessionsFailed = Boolean(index?.sessionErrors[project.id]);
    return (
      <div className="folder" data-folder-id={project.id} key={project.id}>
        <button
          className="folder-toggle"
          aria-current={openProjectId === project.id ? "page" : undefined}
          disabled={projectBusyNow}
          onClick={() => openProject(project.id)}
        >
          {project.name} <span>{sessionsFailed ? "(归属未加载)" : `(${items.length})`}</span>
        </button>
        <button
          className="folder-edit"
          aria-label={`重命名文件夹：${project.name}`}
          disabled={projectBusyNow}
          onClick={() => {
            setFolderEditor(project.id);
            setFolderName(project.name);
            setFolderError("");
          }}
        >改名</button>
        {archived ? (
          <button className="folder-delete" aria-label={`恢复文件夹：${project.name}`} disabled={projectBusyNow} onClick={() => restoreProject(project.id)}>恢复</button>
        ) : (
          <button className="folder-delete" aria-label={`归档文件夹：${project.name}`} disabled={projectBusyNow} onClick={() => setFolderDelete(project.id)}>归档</button>
        )}
        {folderDelete === project.id ? (
          <div className="folder-confirm" role="alertdialog" aria-label="归档文件夹确认">
            <p>{archiveConfirmText(project.name)}</p>
            <button disabled={projectBusyNow} onClick={() => void archiveProject(project.id)}>确认归档</button>
            <button disabled={projectBusyNow} onClick={() => setFolderDelete("")}>取消归档</button>
          </div>
        ) : null}
        {searching ? (
          sessionsFailed ? (
            <div className="folder-confirm" role="alert">
              此文件夹的对话归属加载失败，未当作空列表。
              <button disabled={projectBusyNow} onClick={() => void projectsQuery.refetch()}>重试加载文件夹</button>
            </div>
          ) : (
            <ul aria-label={project.name}>
              {items.map((session) => renderSession(session))}
              {items.length ? null : <li className="folder-empty">没有匹配的对话</li>}
            </ul>
          )
        ) : null}
      </div>
    );
  };

  return (
    <div className="session-list">
      <div className="search">
        <input
          ref={searchRef}
          type="search"
          placeholder="搜索对话"
          aria-label="搜索对话"
          value={query}
          onChange={(event) => {
            setMenuId("");
            setConfirmId("");
            setQuery(event.target.value);
          }}
        />
      </div>
      <section className="folders" aria-label="会话项目">
        <button disabled={projectBusyNow} onClick={() => {
          setFolderEditor("new");
          setFolderName("");
          setFolderError("");
        }}>新建项目</button>
        {folderEditor ? (
          <form onSubmit={(event) => void commitFolder(event)}>
            <input
              ref={folderInputRef}
              aria-label="项目名称"
              value={folderName}
              onChange={(event) => setFolderName(event.target.value)}
            />
            <button disabled={projectBusyNow}>保存</button>
            <button type="button" disabled={projectBusyNow} onClick={() => setFolderEditor("")}>取消</button>
          </form>
        ) : null}
        {index ? activeProjects.map((project) => renderProject(project, false)) : projectsQuery.isPending ? <div role="status">正在加载文件夹</div> : null}
        {legacyGroups.map((group) => (
          <div className="folder" key={`legacy-${group.id}`}>
            <div className="folder-toggle">{group.name}</div>
            <p>尚未成为项目，对话仍在下方列表。</p>
          </div>
        ))}
        {archivedProjects.length ? (
          <>
            <button type="button" aria-expanded={archivedOpen || searching} disabled={projectBusyNow} onClick={() => setArchivedOpen((open) => !open)}>
              已归档（{archivedProjects.length}）
            </button>
            {archivedOpen || searching ? archivedProjects.map((project) => renderProject(project, true)) : null}
          </>
        ) : null}
        {legacyQuery.isError ? (
          <div role="alert">无法核对旧文件夹。不能把这次失败当成没有旧数据，对话仍可在下方列表访问。<button disabled={projectBusyNow} onClick={() => void legacyQuery.refetch()}>重试</button></div>
        ) : null}
        <LegacyFolderMigration
          active={projectsQuery.isSuccess}
          disabled={projectBusyNow}
          onMigrated={() => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
            void queryClient.invalidateQueries({ queryKey: queryKeys.legacyFolders });
          }}
        />
        {index?.conflicts.length ? <div role="alert">有对话同时出现在多个文件夹，已保留先出现的归属</div> : null}
        {shownFolderError ? (
          <div role="alert">{shownFolderError}<button disabled={projectBusyNow} onClick={() => void projectsQuery.refetch()}>重试加载文件夹</button></div>
        ) : null}
      </section>
      <div className="seg" role="group" aria-label="会话筛选">
        <button aria-pressed={filter === "live"} onClick={() => setFilter("live")}>实时</button>
        <button aria-pressed={filter === "chats"} onClick={() => setFilter("chats")}>对话</button>
        <button aria-pressed={filter === "auto"} onClick={() => setFilter("auto")}>自动</button>
      </div>
      <ul>
        {unfiled.map((session) => renderSession(session))}
        {hasMore && !loading && !query.trim() ? (
          <li><button className="more" onClick={() => void load(false)}>加载更多</button></li>
        ) : null}
      </ul>
      {error ? (
        <div className="status">
          {error}
          <br />
          <button onClick={() => void load(true)}>重试</button>
        </div>
      ) : !loading && visible.length === 0 ? (
        <div className="status">还没有对话</div>
      ) : null}
    </div>
  );
});
