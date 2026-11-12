import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { MessageList, type ListHandle } from "@/pages/chat/message-list";
import { runs, RUN_CHANGED, RUN_FINISHED } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";

declare global {
  interface Window {
    __gate?: Gate;
    __requests?: Array<{ url: string; method: string; at: number; signalId: number; aborted: boolean }>;
    __heldMessages?: Held | null;
    __holdMessages?: boolean;
    __failMessages?: boolean;
    __commentary?: Record<string, string[]>;
    fixtureReady?: boolean;
    fixtureError?: string;
    __select?: (id: string) => void;
    __finish?: (id: string) => void;
    __installRun?: (id: string, extra: Record<string, unknown>) => void;
    __patchRun?: (id: string, patch: Record<string, unknown>) => void;
    __capture?: () => { key: string; offset: number } | null;
    __setStick?: (stick: boolean) => void;
    __holdSessionRun?: boolean;
    __heldSessionRun?: Held | null;
    __sessionRunBody?: unknown;
    __holdRunStatus?: boolean;
    __heldRunStatus?: Held | null;
    __runStatusBody?: unknown;
    __holdEvents?: boolean;
    __heldQueue?: Held[];
    __holdOffsetZero?: boolean;
    __runState?: (id: string) => { runId: string; status: string; remote?: boolean; steps: number; text: string; userText: string; streamGone?: boolean; replayTruncated?: boolean; locallyReleased?: boolean; error?: string } | null;
    __executionBody?: unknown;
    __composerDraft?: () => { value: string; attachments: unknown[] };
    __settle?: (id: string, status?: string) => void;
  }
}

interface Held {
  url: string;
  signalId: number;
  resolve: (status: number, body: unknown) => void;
  reject: (error: Error) => void;
}

interface GateMessage {
  id: number;
  role: string;
  content: string;
  timestamp?: number;
  reasoning?: string;
  tool_calls?: unknown[];
  display_commentary?: string[];
}

interface Gate {
  mode?: "chat" | "list";
  sessionId?: string;
  sessions?: Array<{ id: string; title: string; source?: string; parent_session_id?: string }>;
  messages?: Record<string, GateMessage[]>;
  count?: number;
  itemHeight?: number;
  itemGap?: number;
  stick?: boolean;
  special?: string;
  installRun?: Record<string, unknown>;
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
window.__requests = [];
window.__heldMessages = null;
window.__heldQueue = [];
window.__commentary = window.__commentary ?? {};

let signalSeq = 0;
const signalIds = new WeakMap<AbortSignal, number>();
function signalId(signal: AbortSignal | null | undefined): number {
  if (!signal) return 0;
  let id = signalIds.get(signal);
  if (!id) {
    id = ++signalSeq;
    signalIds.set(signal, id);
  }
  return id;
}

function sessionOf(url: URL): string {
  return url.searchParams.get("session_id") || url.searchParams.get("session") || "";
}

function messageId(url: URL): string {
  return url.pathname.split("/sessions/")[1]?.split("/")[0] ?? sessionOf(url);
}

function runtimeSession(pathname: string): { id: string; kind: string } | null {
  const match = pathname.match(/\/api\/runtime\/sessions\/([^/]+)\/(background|execution\/stop|execution|run|context)$/);
  if (!match?.[1] || !match[2]) return null;
  return { id: decodeURIComponent(match[1]), kind: match[2] };
}

function effortView(sessionId: string) {
  return { session_id: sessionId, effort: null, source: "unset", applied: "unset", running: null };
}

function subagentPage(url: URL) {
  const limit = Number(url.searchParams.get("limit") || 50);
  const offset = Number(url.searchParams.get("offset") || 0);
  return { data: [], running: 0, stalled: 0, total: 0, now: Date.now() / 1000, limit, offset };
}

function withCommentary(rows: GateMessage[]): GateMessage[] {
  const notes = window.__commentary ?? {};
  return rows.map((row) => {
    const extra = notes[String(row.id)];
    if (!extra || row.role !== "assistant") return row;
    return { ...row, display_commentary: extra };
  });
}

function pageOf(rows: GateMessage[], url: URL) {
  const limit = Number(url.searchParams.get("limit") || rows.length || 0);
  const offset = Number(url.searchParams.get("offset") || 0);
  const end = Math.max(0, rows.length - offset);
  const start = Math.max(0, end - limit);
  const messages = withCommentary(rows.slice(start, end));
  return { data: messages, pagination: { returned: messages.length, limit, offset } };
}

window.fetch = async (input, init) => {
  const url = new URL(String(input instanceof Request ? input.url : input), location.href);
  const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
  const id = signalId(signal);
  const record = { url: `${url.pathname}${url.search}`, method, at: Date.now(), signalId: id, aborted: Boolean(signal?.aborted) };
  if (signal && !signal.aborted) signal.addEventListener("abort", () => { record.aborted = true; });
  window.__requests?.push(record);
  const path = url.pathname;
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  const gate = window.__gate;
  const hold = (bucket: "__heldMessages" | "__heldSessionRun" | "__heldRunStatus") => new Promise<Response>((resolve, reject) => {
    const held: Held = {
      url: record.url,
      signalId: id,
      resolve: (status, body) => resolve(json(body, status)),
      reject,
    };
    window[bucket] = held;
    window.__heldQueue?.push(held);
  });
  if (path.includes("/messages") && !path.includes("message-origins")) {
    if (window.__failMessages) return json({ error: "synthetic unavailable" }, 500);
    const offset = Number(url.searchParams.get("offset") || 0);
    if (window.__holdMessages || (window.__holdOffsetZero && offset === 0)) return hold("__heldMessages");
    const session = messageId(url);
    return json({ ...pageOf(gate?.messages?.[session] ?? [], url), session_id: session });
  }
  if (path.includes("/sessions/commentary")) return json({ items: window.__commentary ?? {} });
  if (path.includes("/sessions/message-origins")) return json({ items: {} });
  const runtime = runtimeSession(path);
  if (runtime?.kind === "background") return json({ processes: [] });
  if (runtime?.kind === "execution" || path.endsWith("/sessions/execution")) {
    return json(window.__executionBody ?? { busy: false, execution: null });
  }
  if (runtime?.kind === "run" || path.endsWith("/sessions/run") || path.includes("/sessions/run/resolve")) {
    if (window.__holdSessionRun) return hold("__heldSessionRun");
    if (window.__sessionRunBody) return json(window.__sessionRunBody);
    const key = url.searchParams.get("idempotency_key") ?? "";
    return json({ busy: false, run: null, ...(key ? { resolution: { idempotency_key: key, state: "not_found" } } : {}) });
  }
  if (runtime?.kind === "context" || path.endsWith("/context")) {
    return json({ used: null, window: null, source: "no-data", session_id: runtime?.id ?? "" });
  }
  if (path.includes("/sessions/folders")) return json({ folders: [], assignments: {} });
  if (path.includes("/api/runtime/subagents") || path.includes("/subagents")) return json(subagentPage(url));
  if (/\/api\/sessions\/[^/]+\/effort$/.test(path)) {
    const id = decodeURIComponent(path.split("/sessions/")[1]?.split("/")[0] ?? "");
    if (method !== "GET") {
      return json({ error: { message: "此夹具不保存档位", code: "effort_readonly" } }, 405);
    }
    return json(effortView(id));
  }
  if (path.endsWith("/health") || path.endsWith("/health/")) return json({ status: "ok" });
  if (path.includes("/api/model/options")) {
    return json({ provider: "fixture", model: "fixture-model", providers: [{ slug: "fixture", name: "Fixture", authenticated: true, models: ["fixture-model"] }] });
  }
  if (path.includes("/api/sessions")) {
    const sessions = gate?.sessions ?? [{ id: gate?.sessionId || "sess", title: "synthetic" }];
    return json({
      data: sessions.map((item) => ({
        id: item.id, source: item.source || "api_server", parent_session_id: item.parent_session_id, title: item.title, model: null, started_at: 1, last_active: 1, message_count: 1, preview: null, pinned: false, archived: false,
      })),
      has_more: false,
    });
  }
  if (path.includes("/events")) {
    if (window.__holdEvents) return new Promise<Response>(() => undefined);
  }
  if (path.includes("/runs/") && !path.endsWith("/stop")) {
    if (window.__holdRunStatus) return hold("__heldRunStatus");
    if (window.__runStatusBody) return json(window.__runStatusBody);
    return json({ status: "running", run_id: "run-sess", session_id: gate?.sessionId || "sess", created_at: 100 });
  }
  return json({ error: { message: "夹具没有这条路由", code: "fixture_not_found" } }, 404);
};

function blankRun(id: string, extra: Record<string, unknown>) {
  return {
    sessionId: id,
    runId: `run-${id}`,
    status: "running",
    userText: "",
    attachments: [],
    steers: [],
    thinking: "",
    steps: [],
    text: "",
    approval: null,
    clarify: null,
    error: "",
    stopping: false,
    startedAt: 100000,
    lastEventAt: Date.now(),
    model: "",
    provider: "",
    unverified: false,
    remote: false,
    ...extra,
  };
}

window.__select = (id) => { history.pushState({ sessionId: id }, "", `#${encodeURIComponent(id)}`); useUiStore.getState().setSelectedSessionId(id); };
window.addEventListener("popstate", () => useUiStore.getState().setSelectedSessionId(decodeURIComponent(location.hash.slice(1))));
Object.assign(window, {
  __selected: () => useUiStore.getState().selectedSessionId,
  __reload: async () => {
    for (const query of client.getQueryCache().getAll()) {
      if (query.queryKey.includes("main") && JSON.stringify(query.queryKey).toLowerCase().includes("message")) {
        await client.refetchQueries({ queryKey: query.queryKey, exact: true });
      }
    }
  },
  __fixtureEpoch: crypto.randomUUID(),
});
window.__finish = (id) => runs.dispatchEvent(new CustomEvent(RUN_FINISHED, { detail: id }));
window.__settle = (id, status = "completed") => {
  const store = runs as unknown as { runs: Map<string, Record<string, unknown>> };
  const current = store.runs.get(id);
  if (!current) throw new Error(`no run ${id}`);
  store.runs.set(id, { ...current, status, stopping: false });
  runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
  runs.dispatchEvent(new CustomEvent(RUN_FINISHED, { detail: id }));
};
window.__runState = (id) => {
  const current = (runs as unknown as { runs: Map<string, Record<string, unknown>> }).runs.get(id);
  if (!current) return null;
  return {
    runId: String(current.runId ?? ""),
    status: String(current.status ?? ""),
    remote: Boolean(current.remote),
    steps: Array.isArray(current.steps) ? current.steps.length : 0,
    text: String(current.text ?? ""),
    userText: String(current.userText ?? ""),
    streamGone: Boolean(current.streamGone),
    replayTruncated: Boolean(current.replayTruncated),
    locallyReleased: Boolean(current.locallyReleased),
    error: String(current.error ?? ""),
  };
};
window.__composerDraft = () => useUiStore.getState().composerDraft;
window.__installRun = (id, extra) => {
  const store = runs as unknown as { runs: Map<string, ReturnType<typeof blankRun>> };
  store.runs.set(id, blankRun(id, extra));
  runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
};
window.__patchRun = (id, patch) => {
  const store = runs as unknown as { runs: Map<string, Record<string, unknown>> };
  const current = store.runs.get(id);
  if (!current) throw new Error(`no run ${id}`);
  store.runs.set(id, { ...current, ...patch });
  runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
};

function ListProbe({ count, itemHeight, itemGap, stick, special }: Required<Pick<Gate, "count" | "itemHeight" | "itemGap" | "stick">> & { special: string }) {
  const ref = useRef<ListHandle>(null);
  const [following, setFollowing] = useState(stick);
  const keys = Array.from({ length: count }, (_, index) => index === 3 && special ? special : `src-${index}`);
  useEffect(() => {
    window.__capture = () => ref.current?.capture() ?? null;
    window.__setStick = setFollowing;
  });
  return (
    <div style={{ height: 500 }}>
      <style>{`.scroll{height:500px;overflow:auto}.row{box-sizing:border-box}`}</style>
      <MessageList
        keys={keys}
        stick={following}
        hold={null}
        listRef={ref}
        onStick={() => undefined}
        onNearTop={() => undefined}
        onUserScroll={() => undefined}
      >
        {keys.map((key) => (
          <div key={key} className="past row" data-history-source={key} style={{ height: itemHeight, marginBottom: itemGap }}>{key}</div>
        ))}
      </MessageList>
    </div>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");

const boot = () => {
  const gate = window.__gate;
  if (!gate) {
    window.setTimeout(boot, 20);
    return;
  }
  if (gate.mode === "list") {
    createRoot(root).render(
      <ListProbe
        count={gate.count ?? 10}
        itemHeight={gate.itemHeight ?? 50}
        itemGap={gate.itemGap ?? 10}
        stick={gate.stick ?? false}
        special={gate.special ?? ""}
      />,
    );
    window.fixtureReady = true;
    return;
  }
  const sessionId = decodeURIComponent(location.hash.slice(1)) || gate.sessionId || "sess";
  history.replaceState({ sessionId }, "", `#${encodeURIComponent(sessionId)}`);
  if (gate.installRun) window.__installRun?.(sessionId, gate.installRun);
  useUiStore.setState({ selectedSessionId: sessionId });
  createRoot(root).render(
    <QueryClientProvider client={client}>
      <nav aria-label="隔离测试会话切换" style={{ display: "flex", gap: 8, padding: 8 }}>
        <button data-test-session="main" style={{ minHeight: 44 }} onClick={() => window.__select?.("main")}>主会话</button>
        <button data-test-session="child" style={{ minHeight: 44 }} onClick={() => window.__select?.("child")}>子会话</button>
      </nav>
      <div className="chat-slot" id="chat-slot">
        <ChatPage />
      </div>
    </QueryClientProvider>,
  );
  window.fixtureReady = true;
};
boot();
