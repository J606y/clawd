import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { ChatPage } from "@/pages/chat/chat-page";
import { api, type SessionSummary } from "@/lib/api.js";
import { clearCredential } from "@/lib/auth.js";
import { saveLiveView, type LiveView } from "@/lib/live-view.js";
import { useHealth, useProjects } from "@/lib/queries";
import { getOpenProjectId, subscribeOpenProject } from "@/lib/project-send";
import { NAV_MS, reducedMotion } from "@/lib/route-motion.js";
import { useResumeRuns } from "@/lib/stream";
import { nextThemePref } from "@/lib/theme.js";
import { useUiStore } from "@/stores/ui";
import { DockHost } from "./dock-host";
import { EMPTY_DOCK, finishDockLeave, reduceDock, type DockState, type DockView } from "./dock-state";
import { SessionList, type SessionListHandle } from "./session-list";
import { resumeMainSession, resumeSelectsSubagent } from "./session-logic";
import { readAgent, readId, readLiveOpen, readRename, readSession } from "./shell-events";
import { consumeSkipRenameBlur, markSkipRenameBlur, setShellSession, useShellSession } from "./shell-session";
import { useDocumentVisible, useNarrow } from "./viewport";
import "./shell.css";

function NavGlyph({ children }: { children: ReactNode }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

const NAV: { to: string; label: string; end: boolean; icon: ReactNode }[] = [
  { to: "/", label: "对话", end: true, icon: <NavGlyph><path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z" /></NavGlyph> },
  {
    to: "/image",
    label: "生图",
    end: false,
    icon: (
      <NavGlyph>
        <rect x="1.6" y="2.4" width="12.8" height="11.2" rx="1.6" />
        <circle cx="5.2" cy="5.6" r="1" fill="currentColor" stroke="none" />
        <path d="M1.8 11.4 5.4 8.2l2.1 1.7 2.4-2.2 4.3 3.7" />
      </NavGlyph>
    ),
  },
  { to: "/files", label: "文件", end: false, icon: <NavGlyph><path d="M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" /></NavGlyph> },
  {
    to: "/activity",
    label: "活动",
    end: false,
    icon: (
      <NavGlyph>
        <path d="M3 4h10M3 8h7M3 12h4" />
        <circle cx="12.5" cy="11.5" r="1.5" />
      </NavGlyph>
    ),
  },
  { to: "/status", label: "状态", end: false, icon: <NavGlyph><path d="M2 9h2.5l1.5-4 2.5 7 1.5-4.5 1 1.5H14" /></NavGlyph> },
  {
    to: "/settings",
    label: "设置",
    end: false,
    icon: (
      <NavGlyph>
        <circle cx="8" cy="8" r="2" />
        <path d="M6.5 1.5h3l.4 1.8 1.2.7 1.8-.6 1.5 2.6L13 7.2v1.6l1.4 1.2-1.5 2.6-1.8-.6-1.2.7-.4 1.8h-3l-.4-1.8-1.2-.7-1.8.6L1.6 10 3 8.8V7.2L1.6 6l1.5-2.6 1.8.6 1.2-.7z" />
      </NavGlyph>
    ),
  },
];

function navCurrent(pathname: string, to: string, end: boolean): "page" | "false" {
  const on = end ? pathname === to : pathname === to || pathname.startsWith(`${to}/`);
  return on ? "page" : "false";
}

function pageTitle(pathname: string): string {
  if (pathname === "/") return "对话";
  if (pathname.startsWith("/image")) return "生图";
  if (pathname.startsWith("/files")) return "文件";
  if (pathname.startsWith("/activity")) return "活动";
  if (pathname.startsWith("/status")) return "状态";
  if (pathname.startsWith("/settings")) return "设置";
  return "clawd";
}

function themeLabel(pref: "system" | "light" | "dark"): string {
  if (pref === "light") return "浅色";
  if (pref === "dark") return "深色";
  return "跟随系统";
}

function placeholderSession(id: string, title = "", source = "api_server"): SessionSummary {
  const now = Date.now() / 1000;
  return {
    id,
    source: source || "api_server",
    title: title || null,
    model: null,
    started_at: now,
    last_active: now,
    message_count: 0,
    preview: null,
    pinned: false,
    archived: false,
  };
}

function livePayload(session: SessionSummary | null, selectedId: string | null): LiveView {
  return {
    sessionId: session?.id || selectedId || "",
    sessionTitle: (session?.title ?? "").slice(0, 300),
    sessionSource: (session?.source ?? "api_server").slice(0, 100),
  };
}

export function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const narrow = useNarrow();
  const visible = useDocumentVisible();
  const health = useHealth(visible);
  const resumed = useResumeRuns();
  const listRef = useRef<SessionListHandle>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const scrollMemory = useRef(new Map<string, number>());
  const pathRef = useRef(location.pathname);
  const routeRef = useRef(location.pathname);
  const navigateRef = useRef(navigate);
  routeRef.current = location.pathname;
  navigateRef.current = navigate;
  const wasLive = useRef(useUiStore.getState().liveView !== null);
  const onChat = location.pathname === "/";
  const drawerOpen = useUiStore((state) => state.drawerOpen);
  const setDrawerOpen = useUiStore((state) => state.setDrawerOpen);
  const themePref = useUiStore((state) => state.themePref);
  const setThemePref = useUiStore((state) => state.setThemePref);
  const selectedId = useUiStore((state) => state.selectedSessionId);
  const setSelectedSessionId = useUiStore((state) => state.setSelectedSessionId);
  const setLiveView = useUiStore((state) => state.setLiveView);
  const shellSession = useShellSession();
  const [linkDown, setLinkDown] = useState(false);
  const [dock, setDock] = useState<DockState>(() => {
    const saved = useUiStore.getState().liveView;
    if (!saved) return EMPTY_DOCK;
    return {
      current: { kind: "live", sessionId: saved.sessionId, agentId: "", key: "live" },
      leaving: null,
      pending: null,
    };
  });
  const dockRef = useRef(dock);
  dockRef.current = dock;

  const showDock = (next: DockView | null) => {
    setDock((state) => reduceDock(state, next, reducedMotion()));
  };

  const closeDrawerIfNarrow = () => {
    if (window.matchMedia("(max-width: 768px)").matches) setDrawerOpen(false);
  };

  const newChat = () => {
    setSelectedSessionId(null);
    setShellSession(null);
    if (location.pathname !== "/") navigate("/");
    closeDrawerIfNarrow();
  };

  const selectSession = (session: SessionSummary) => {
    if (selectedId && selectedId !== session.id && (dock.current?.kind === "task" || dock.leaving?.kind === "task")) {
      showDock(null);
    }
    setSelectedSessionId(session.id);
    setShellSession(session);
    if (location.pathname !== "/") navigate("/");
    closeDrawerIfNarrow();
  };

  const openLive = () => {
    closeDrawerIfNarrow();
    showDock({ kind: "live", sessionId: selectedId ?? "", agentId: "", key: "live" });
  };

  const persistLive = (view: LiveView | null) => {
    const saved = saveLiveView(view);
    setLiveView(view);
    if (!saved) {
      console.warn(view
        ? "无法保存实时画面入口；刷新后请重新打开，聊天不受影响。"
        : "无法清除实时画面的只读恢复记录。");
    }
  };

  useEffect(() => {
    const saved = useUiStore.getState().liveView;
    if (saved?.sessionId && !useUiStore.getState().selectedSessionId && !resumeSelectsSubagent(saved.sessionSource)) {
      setSelectedSessionId(saved.sessionId);
      setShellSession(placeholderSession(saved.sessionId, saved.sessionTitle, saved.sessionSource));
    }
  }, [setSelectedSessionId]);

  useEffect(() => {
    const saved = useUiStore.getState().liveView;
    if (useUiStore.getState().selectedSessionId) return;
    if (saved?.sessionId && !resumeSelectsSubagent(saved.sessionSource)) return;
    const ids = resumed.filter((id) => id);
    if (!ids.length) return;
    let cancelled = false;
    void api.listSessions({ limit: 100 }).then((page) => {
      if (cancelled || useUiStore.getState().selectedSessionId) return;
      const found = resumeMainSession(ids, page.data);
      if (!found) return;
      setSelectedSessionId(found.id);
      setShellSession(found);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [resumed, setSelectedSessionId]);

  useEffect(() => {
    const live = dock.current?.kind === "live";
    if (live) {
      wasLive.current = true;
      const payload = livePayload(shellSession, selectedId);
      const saved = useUiStore.getState().liveView;
      if (!payload.sessionId && saved?.sessionId) return;
      persistLive(payload);
      return;
    }
    if (wasLive.current) {
      wasLive.current = false;
      persistLive(null);
    }
  }, [dock.current, selectedId, shellSession, setLiveView]);

  useEffect(() => {
    if (!dock.leaving) return;
    if (reducedMotion()) {
      setDock((state) => (state.leaving ? finishDockLeave(state) : state));
      return;
    }
    // 移动端子代理缩回走 WAAPI，结束时由 DockHost 卸载。不能另挂 NAV_MS 定时器：
    // 动画 280ms 结束后即卸载，避免停在缩略状态等待侧栏的 350ms 计时。
    if (dock.leaving.kind === "agent" && narrow) return;
    const timer = window.setTimeout(() => {
      setDock((state) => (state.leaving ? finishDockLeave(state) : state));
    }, NAV_MS);
    return () => window.clearTimeout(timer);
  }, [dock.leaving, narrow]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (window.matchMedia("(max-width: 768px)").matches) setDrawerOpen(true);
        if (location.pathname !== "/") navigate("/");
        window.setTimeout(() => listRef.current?.focusSearch(), 0);
        return;
      }
      if (mod && event.shiftKey && !event.altKey && event.key.toLowerCase() === "o") {
        event.preventDefault();
        newChat();
        return;
      }
      if (event.key !== "Escape") return;
      if (useUiStore.getState().drawerOpen) {
        event.preventDefault();
        event.stopPropagation();
        markSkipRenameBlur();
        setDrawerOpen(false);
        window.setTimeout(() => consumeSkipRenameBlur(), 0);
        return;
      }
      if (!window.matchMedia("(max-width: 768px)").matches) return;
      const state = dockRef.current;
      if (state.current?.kind === "agent" || state.leaving?.kind === "agent") showDock(null);
      else if (state.current?.kind === "live" || state.current?.kind === "task" || state.leaving) showDock(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [location.pathname, navigate, setDrawerOpen]);

  useEffect(() => {
    const onOpen = (event: Event) => {
      const session = readSession(event);
      if (!session) return;
      const selected = useUiStore.getState().selectedSessionId;
      const state = dockRef.current;
      if (selected && selected !== session.id && (state.current?.kind === "task" || state.leaving?.kind === "task")) {
        showDock(null);
      }
      setSelectedSessionId(session.id);
      setShellSession(session);
      if (routeRef.current !== "/") navigateRef.current("/");
      if (window.matchMedia("(max-width: 768px)").matches) setDrawerOpen(false);
    };
    const onCreated = (event: Event) => {
      const session = readSession(event);
      if (!session) return;
      listRef.current?.upsertSession(session);
      setSelectedSessionId(session.id);
      setShellSession(session);
    };
    const onUpdated = (event: Event) => {
      if (readId(event)) void listRef.current?.refreshHead();
    };
    const onDeleted = (event: Event) => {
      const id = readId(event);
      if (id && useUiStore.getState().selectedSessionId === id) {
        setSelectedSessionId(null);
        setShellSession(null);
      }
    };
    const onRenamed = (event: Event) => {
      const detail = readRename(event);
      if (!detail || useUiStore.getState().selectedSessionId !== detail.id) return;
      const current = useUiStore.getState().selectedSessionId;
      if (!current) return;
      setShellSession({ ...(placeholderSession(detail.id)), title: detail.title });
    };
    const onLive = (event: Event) => {
      const detail = readLiveOpen(event);
      closeDrawerIfNarrow();
      if (detail.scope === "task") {
        showDock({
          kind: "task",
          sessionId: detail.sessionId || useUiStore.getState().selectedSessionId || "",
          agentId: "",
          key: detail.token,
        });
        return;
      }
      openLive();
    };
    const onAgent = (event: Event) => {
      const agent = readAgent(event);
      if (!agent) return;
      closeDrawerIfNarrow();
      showDock({
        kind: "agent",
        sessionId: agent.parentId || useUiStore.getState().selectedSessionId || "",
        agentId: agent.id,
        key: agent.id,
        origin: agent.origin,
      });
    };
    const onLiveClose = () => {
      if (dockRef.current.current?.kind === "live" || dockRef.current.leaving?.kind === "live") showDock(null);
    };
    const onTaskClose = () => {
      if (dockRef.current.current?.kind === "task" || dockRef.current.leaving?.kind === "task") showDock(null);
    };
    window.addEventListener("hp-open-session", onOpen);
    window.addEventListener("hp-select-session", onOpen);
    window.addEventListener("hp-session-created", onCreated);
    window.addEventListener("hp-session-updated", onUpdated);
    window.addEventListener("hp-session-deleted", onDeleted);
    window.addEventListener("hp-session-renamed", onRenamed);
    window.addEventListener("hp-live-open", onLive);
    window.addEventListener("hp-view-subagent", onAgent);
    window.addEventListener("hp-live-close", onLiveClose);
    window.addEventListener("hp-task-live-close", onTaskClose);
    const onAgentClose = () => showDock(null);
    window.addEventListener("hp-agent-close", onAgentClose);
    return () => {
      window.removeEventListener("hp-open-session", onOpen);
      window.removeEventListener("hp-select-session", onOpen);
      window.removeEventListener("hp-session-created", onCreated);
      window.removeEventListener("hp-session-updated", onUpdated);
      window.removeEventListener("hp-session-deleted", onDeleted);
      window.removeEventListener("hp-session-renamed", onRenamed);
      window.removeEventListener("hp-live-open", onLive);
      window.removeEventListener("hp-view-subagent", onAgent);
      window.removeEventListener("hp-live-close", onLiveClose);
      window.removeEventListener("hp-task-live-close", onTaskClose);
      window.removeEventListener("hp-agent-close", onAgentClose);
    };
  }, [setSelectedSessionId]);

  useEffect(() => {
    const down = () => setLinkDown(true);
    const up = () => {
      setLinkDown(false);
      void health.refetch();
    };
    window.addEventListener("offline", down);
    window.addEventListener("online", up);
    return () => {
      window.removeEventListener("offline", down);
      window.removeEventListener("online", up);
    };
  }, [health.refetch]);

  useEffect(() => {
    if (health.data) setLinkDown(false);
  }, [health.data]);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage || pathRef.current === location.pathname) return;
    scrollMemory.current.set(pathRef.current, stage.scrollTop);
    pathRef.current = location.pathname;
    stage.scrollTop = scrollMemory.current.get(location.pathname) ?? 0;
  }, [location.pathname]);

  const offline = linkDown || health.isError;
  const version = health.data?.version ?? "";
  const openProjectId = useSyncExternalStore(subscribeOpenProject, getOpenProjectId, () => null);
  const projects = useProjects();
  const openProject = openProjectId
    ? projects.data?.projects.find((item) => item.id === openProjectId) ?? null
    : null;
  const title = openProject
    ? openProject.name
    : onChat
      ? (selectedId || shellSession ? shellSession?.title || "未命名对话" : "新对话")
      : pageTitle(location.pathname);
  const livePressed = dock.current?.kind === "live";
  const liveColumn = dock.current?.kind === "live" || dock.current?.kind === "task" || dock.leaving?.kind === "live" || dock.leaving?.kind === "task";
  const agentColumn = dock.current?.kind === "agent" || dock.leaving?.kind === "agent";

  const logout = async () => {
    try {
      const response = await fetch(new URL("panel-auth/logout", document.baseURI), {
        method: "POST", credentials: "same-origin", cache: "no-store",
      });
      if (response.status !== 204) throw new Error(`退出失败：HTTP ${response.status}`);
      clearCredential();
      persistLive(null);
      window.location.reload();
    } catch (error) {
      console.error("退出失败", error);
      window.alert("退出失败，请重试。当前登录尚未注销。");
    }
  };

  return (
    <div
      className="shell"
      data-project={openProject ? "" : undefined}
      data-drawer={drawerOpen ? "" : undefined}
      data-live={liveColumn ? "" : undefined}
      data-agent={agentColumn ? "" : undefined}
      data-dock-leave={dock.leaving ? "" : undefined}
      data-live-mode={dock.current?.kind === "task" || dock.leaving?.kind === "task" ? "task" : "global"}
    >
      <aside aria-label="会话">
        <div className="brand">
          <span className="dot" aria-hidden="true">
            <svg width="22" height="22" viewBox="40 15 300 300" shapeRendering="crispEdges">
              <rect x="80" y="120" width="220" height="120" fill="#D97757" />
              <rect x="40" y="160" width="40" height="40" fill="#D97757" />
              <rect x="300" y="160" width="40" height="40" fill="#D97757" />
              <rect x="120" y="170" width="20" height="10" fill="#1F1E1D" />
              <rect x="110" y="180" width="10" height="10" fill="#1F1E1D" />
              <rect x="140" y="180" width="10" height="10" fill="#1F1E1D" />
              <rect x="240" y="170" width="20" height="10" fill="#1F1E1D" />
              <rect x="230" y="180" width="10" height="10" fill="#1F1E1D" />
              <rect x="260" y="180" width="10" height="10" fill="#1F1E1D" />
              <rect x="100" y="240" width="20" height="40" fill="#D97757" />
              <rect x="140" y="240" width="20" height="40" fill="#D97757" />
              <rect x="220" y="240" width="20" height="40" fill="#D97757" />
              <rect x="260" y="240" width="20" height="40" fill="#D97757" />
              <rect x="120" y="60" width="140" height="40" fill="#8B5A2B" />
              <rect x="140" y="50" width="40" height="10" fill="#8B5A2B" />
              <rect x="200" y="50" width="40" height="10" fill="#8B5A2B" />
              <rect x="60" y="100" width="260" height="20" fill="#8B5A2B" />
              <rect x="40" y="80" width="20" height="20" fill="#8B5A2B" />
              <rect x="320" y="80" width="20" height="20" fill="#8B5A2B" />
              <rect x="120" y="85" width="140" height="15" fill="#5A3A1A" />
            </svg>
          </span>
          clawd
          <span className="spacer" />
          <button type="button" className="icon-btn" title="新对话" aria-label="新对话" onClick={newChat}>
            <PlusIcon />
          </button>
        </div>
        <nav aria-label="页面">
          {NAV.map((item) => (
            <button
              key={item.to}
              type="button"
              aria-label={item.label}
              title={item.label}
              aria-current={navCurrent(location.pathname, item.to, item.end)}
              onClick={() => {
                navigate(item.to);
                closeDrawerIfNarrow();
              }}
            >
              {item.icon}
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>
        <SessionList
          ref={listRef}
          onSelect={selectSession}
          onDeleted={(id) => {
            if (selectedId === id) {
              setSelectedSessionId(null);
              setShellSession(null);
            }
          }}
          onRenamed={(id, nextTitle) => {
            if (selectedId === id) setShellSession(placeholderSession(id, nextTitle ?? "", shellSession?.source));
          }}
        />
        <footer>
          <span className="link" data-state={offline ? "offline" : "online"} title={offline ? "连接中断" : "已连接"}>
            <i aria-hidden="true" />
            {offline ? "连接中断" : `clawd ${version}`}
          </span>
          <button type="button" title="切换外观" onClick={() => setThemePref(nextThemePref(themePref))}>
            {themeLabel(themePref)}
          </button>
          <button type="button" onClick={logout}>退出</button>
        </footer>
      </aside>
      <div className="scrim" onClick={() => setDrawerOpen(false)} />
      <main>
        {offline ? (
          <div className="offline" role="status">
            连接中断，正在自动重连…
            <button type="button" onClick={() => void health.refetch()}>立即重试</button>
          </div>
        ) : null}
        <div className="topbar">
          <button type="button" className="icon-btn menu" aria-label="打开会话列表" onClick={() => setDrawerOpen(true)}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
          <h2>{title}</h2>
          {openProject ? null : <button type="button" className="icon-btn" title="新对话" aria-label="新对话" onClick={newChat}>
            <PlusIcon />
          </button>}
          {openProject ? null : <button
            type="button"
            className="icon-btn"
            title="实时画面：查看 clawd 正在操作的浏览器"
            aria-label="实时画面"
            aria-pressed={livePressed}
            onClick={() => {
              if (dock.current?.kind === "task") openLive();
              else if (dock.current?.kind === "live") showDock(null);
              else openLive();
            }}
          >
            <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
              <rect x="2.5" y="3.5" width="15" height="10.5" rx="2" stroke="currentColor" strokeWidth="1.5" fill="none" />
              <path d="M7 17h6M10 14v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <circle cx="10" cy="8.75" r="1.6" fill="currentColor" />
            </svg>
          </button>}
        </div>
        <div className="stage nav-stage" ref={stageRef}>
          {/* 现版出字仍由对话页负责。离开对话立即 hidden，不在这里换旧出字。 */}
          <div className="pane chat-pane chat-slot" hidden={!onChat}>
            <ChatPage />
          </div>
          {onChat ? null : <div className="pane"><Outlet /></div>}
        </div>
      </main>
      <DockHost
        state={dock}
        narrow={narrow}
        onClose={() => showDock(null)}
        onLeaveSettled={() => setDock((state) => (state.leaving ? finishDockLeave(state) : state))}
      />
    </div>
  );
}
