import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { runs } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";

declare global {
  interface Window {
    __gate?: Gate;
    __requests?: Array<{ url: string; method: string; body: string }>;
    __held?: Record<string, (status: number, body: unknown) => void>;
    fixtureReady?: boolean;
    fixtureError?: string;
    __select?: (id: string) => void;
    __observe?: (id: string) => void;
    __refetchBackground?: (id: string) => Promise<unknown>;
    __release?: (key: string, status: number, body: unknown) => void;
  }
}

interface Gate {
  sessionId: string;
  sessions: Array<{ id: string; title: string }>;
  messages: Record<string, Array<{ id: number; role: string; content: string; timestamp: number }>>;
  background: Record<string, { status: number; processes?: unknown; hang?: boolean }>;
  run: Record<string, unknown>;
  execution: Record<string, unknown>;
  stop: { hang: boolean; status: number; body: unknown };
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
window.__requests = [];
window.__held = {};
window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});

function sessionOf(url: URL): string {
  return url.searchParams.get("session_id") || url.searchParams.get("session") || "";
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

window.fetch = async (input, init) => {
  const url = new URL(String(input instanceof Request ? input.url : input), location.href);
  const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const body = typeof init?.body === "string" ? init.body : "";
  window.__requests?.push({ url: `${url.pathname}${url.search}`, method, body });
  const path = url.pathname;
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  const gate = window.__gate;
  if (!gate) return json({ error: { message: "夹具尚未就绪", code: "fixture_not_ready" } }, 503);
  const runtime = runtimeSession(path);
  const sid = runtime?.id || sessionOf(url);
  if (runtime?.kind === "background" || path.endsWith("/sessions/background")) {
    const spec = gate.background[sid] ?? { status: 200, processes: [] };
    if (spec.hang) {
      return new Promise((resolve) => {
        window.__held![`bg:${sid}`] = (status, payload) => resolve(json(payload, status));
      });
    }
    if (spec.status !== 200) return json({ error: { message: "后台进程登记暂不可用", code: "process_registry_unavailable" } }, spec.status);
    return json({ processes: spec.processes ?? [] });
  }
  if (runtime?.kind === "execution/stop" || path.endsWith("/sessions/execution/stop")) return new Promise((resolve) => {
    if (!gate.stop.hang) {
      resolve(json(gate.stop.body, gate.stop.status));
      return;
    }
    window.__held!["stop"] = (status, payload) => resolve(json(payload, status));
  });
  if (runtime?.kind === "execution" || path.endsWith("/sessions/execution")) return json(gate.execution[sid] ?? { busy: false, execution: null });
  if (runtime?.kind === "run" || path.endsWith("/sessions/run")) {
    const key = url.searchParams.get("idempotency_key");
    const body = gate.run[sid] ?? { busy: false, run: null };
    if (!key) return json(body);
    const found = body && typeof body === "object" && "run" in body && (body as { run?: { run_id?: string } | null }).run;
    return json({ ...body, resolution: { idempotency_key: key, state: found ? "found" : "not_found" } });
  }
  if (path.endsWith("/sessions/run/resolve")) return json({ busy: false, run: null, resolution: { idempotency_key: url.searchParams.get("idempotency_key") ?? "", state: "not_found" } });
  if (runtime?.kind === "context") return json({ used: null, window: null, source: "no-data", session_id: sid });
  if (path.includes("/sessions/folders")) return json({ folders: [], assignments: {} });
  if (path.includes("/api/runtime/subagents") || path.includes("/subagents")) return json(subagentPage(url));
  if (path.includes("/context")) return json({ used: null, window: null, source: "no-data" });
  if (/\/api\/sessions\/[^/]+\/effort$/.test(path)) {
    const id = decodeURIComponent(path.split("/sessions/")[1]?.split("/")[0] ?? sid);
    return json(effortView(id));
  }
  if (path.endsWith("/health") || path.endsWith("/health/")) return json({ status: "ok" });
  if (path.includes("/api/model/options")) {
    return json({ provider: "fixture", model: "fixture-model", providers: [{ slug: "fixture", name: "Fixture", authenticated: true, models: ["fixture-model"] }] });
  }
  if (path.includes("/messages")) {
    const id = path.split("/sessions/")[1]?.split("/")[0] ?? sid;
    return json({ data: gate.messages[id] ?? [], pagination: { returned: (gate.messages[id] ?? []).length } });
  }
  if (path.includes("/api/sessions")) {
    return json({
      data: gate.sessions.map((item) => ({
        id: item.id, source: "api_server", title: item.title, model: null, started_at: 1, last_active: 1, message_count: 1, preview: null, pinned: false, archived: false,
      })),
      has_more: false,
    });
  }
  if (path.includes("/sessions/commentary")) return json({ items: {} });
  if (path.includes("/sessions/message-origins")) return json({ items: {} });
  return json({ error: { message: "夹具没有这条路由", code: "fixture_not_found" } }, 404);
};

window.__select = (id) => useUiStore.getState().setSelectedSessionId(id);
window.__observe = (id) => { runs.observe(id, `run-${id}`, Date.now(), false); };
window.__refetchBackground = (id) => client.refetchQueries({ queryKey: ["session-background", id] });
window.__release = (key, status, body) => {
  const finish = window.__held?.[key];
  if (!finish) throw new Error(`nothing held for ${key}`);
  delete window.__held?.[key];
  finish(status, body);
};

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
const boot = () => {
  const gate = window.__gate;
  if (!gate) {
    window.setTimeout(boot, 20);
    return;
  }
  useUiStore.setState({ selectedSessionId: gate.sessionId });
  createRoot(root).render(
    <QueryClientProvider client={client}>
      <ChatPage />
    </QueryClientProvider>,
  );
  window.fixtureReady = true;
};
boot();
