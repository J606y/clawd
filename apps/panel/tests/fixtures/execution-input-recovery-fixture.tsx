import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { runs } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";

interface RunRecord {
  run_id: string;
  session_id: string;
  status: string;
  created_at: number;
  updated_at: number;
  execution_generation?: number;
  pending_controls?: Array<Record<string, unknown>>;
}

interface World {
  sid: string;
  busy: boolean;
  run: RunRecord | null;
  execution: { id: string; kind: "cli"; started_at: number } | null;
  messages: Array<{ id: number; role: string; content: string; timestamp: number }>;
  approval: { status: number; code?: string };
  stop: { hang: boolean; status: number; body: unknown };
  nextRunId: string;
  holdMessages?: boolean;
  holdSessionRun?: boolean;
}

declare global {
  interface Window {
    __world?: World;
    __requests?: Array<{ url: string; method: string; body: string; key: string }>;
    __streams?: Map<string, ReadableStreamDefaultController<Uint8Array>>;
    __held?: Record<string, (status: number, body: unknown) => void>;
    __messageHolds?: Array<(body?: unknown) => void>;
    __sessionRunHolds?: Array<(body?: unknown) => void>;
    __messagesInflight?: number;
    __unhandled?: string[];
    __loadedFrom?: string;
    __lastSessionRun?: { busy: boolean; runId: string };
    __emit?: (runId: string, event: Record<string, unknown>) => void;
    __closeStream?: (runId: string) => void;
    __runState?: () => { status: string; active: boolean; runId: string; approval: boolean; error: string } | null;
    fixtureReady?: boolean;
    fixtureError?: string;
  }
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
window.__requests = [];
window.__streams = new Map();
window.__held = {};
window.__messageHolds = [];
window.__sessionRunHolds = [];
window.__unhandled = [];
window.__loadedFrom = import.meta.url;
const encoder = new TextEncoder();

function headerValue(headers: HeadersInit | undefined, name: string): string {
  if (!headers) return "";
  if (headers instanceof Headers) return headers.get(name) ?? "";
  if (Array.isArray(headers)) return headers.find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? "";
  const found = Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase());
  return found ? String(found[1]) : "";
}

window.__emit = (runId, event) => {
  const stream = window.__streams?.get(runId);
  if (!stream) throw new Error(`no open stream for ${runId}`);
  stream.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
};
window.__closeStream = (runId) => {
  const stream = window.__streams?.get(runId);
  window.__streams?.delete(runId);
  stream?.close();
};
window.__runState = () => {
  const sid = window.__world?.sid ?? "";
  const run = runs.get(sid);
  return run ? { status: run.status, active: runs.isActive(sid), runId: run.runId, approval: Boolean(run.approval), error: run.error } : null;
};

window.fetch = async (input, init) => {
  const url = new URL(String(input instanceof Request ? input.url : input), location.href);
  const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const headers = init?.headers || (input instanceof Request ? input.headers : undefined);
  const body = typeof init?.body === "string" ? init.body : "";
  window.__requests?.push({ url: `${url.pathname}${url.search}`, method, body, key: headerValue(headers, "Idempotency-Key") });
  const path = url.pathname;
  const world = window.__world;
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  if (!world) return json({});
  if (path.endsWith("/sessions/background")) return json({ processes: [] });
  if (path.endsWith("/sessions/execution/stop")) return json({ status: "cancelled", busy: false });
  if (path.endsWith("/sessions/execution")) return json({ busy: Boolean(world.execution), execution: world.execution });
  if (path.endsWith("/sessions/run/resolve")) return json({ busy: false, run: null, resolution: { idempotency_key: url.searchParams.get("idempotency_key") ?? "", state: "not_found" } });
  if (path.endsWith("/sessions/run")) {
    window.__lastSessionRun = { busy: world.busy, runId: world.run?.run_id ?? "" };
    const snap = {
      busy: world.busy,
      run: world.run ? { ...world.run, pending_controls: [...(world.run.pending_controls ?? [])] } : null,
      acquired_at: world.busy ? 100 : undefined,
    };
    if (world.holdSessionRun) {
      return new Promise((resolve) => {
        window.__sessionRunHolds?.push((override) => resolve(json(override ?? snap)));
      });
    }
    return json(snap);
  }
  if (path.includes("/sessions/folders")) return json({ folders: [], assignments: {} });
  if (path.includes("/subagents")) return json({ data: [], running: 0, now: Date.now() });
  if (path.includes("/context")) return json({ used: null, window: null, source: "no-data" });
  if (path.includes("/sessions/commentary")) return json({ items: {} });
  if (path.includes("/sessions/message-origins")) return json({ items: {} });
  if (path.endsWith("/health") || path.endsWith("/health/")) return json({ status: "ok" });
  if (path.includes("/api/model/options")) {
    return json({ provider: "fixture", model: "fixture-model", providers: [{ slug: "fixture", name: "Fixture", authenticated: true, models: ["fixture-model"] }] });
  }
  if (path.includes("/messages")) {
    const payload = { data: world.messages, pagination: { returned: world.messages.length } };
    if (world.holdMessages) {
      return new Promise((resolve) => {
        window.__messageHolds?.push((override) => resolve(json(override ?? payload)));
      });
    }
    window.__messagesInflight = (window.__messagesInflight ?? 0) + 1;
    setTimeout(() => { window.__messagesInflight = Math.max(0, (window.__messagesInflight ?? 1) - 1); }, 0);
    return json(payload);
  }
  if (path.endsWith("/steer") && method === "POST") return json({ ok: true });
  if (path.includes("/api/sessions")) {
    return json({
      data: [{ id: world.sid, source: "api_server", title: "合成会话", model: null, started_at: 1, last_active: 1, message_count: world.messages.length, preview: null, pinned: false, archived: false }],
      has_more: false,
    });
  }
  if (path.endsWith("/v1/runs") && method === "POST") {
    const runId = world.nextRunId;
    world.run = {
      run_id: runId,
      session_id: world.sid,
      status: "running",
      created_at: 100,
      updated_at: 100,
      execution_generation: world.run?.execution_generation ?? 4,
      pending_controls: world.run?.pending_controls ?? [],
    };
    world.busy = true;
    return json({ run_id: runId, status: "queued" }, 202);
  }
  const approval = path.match(/^\/panel-api\/v1\/runs\/([^/]+)\/approval$/);
  if (approval && method === "POST") {
    const spec = world.approval;
    if (spec.status !== 200) return json({ error: { message: spec.code ?? "invalid_params", code: spec.code ?? "invalid_params" } }, spec.status);
    return json({ object: "hermes.run.approval_response", run_id: decodeURIComponent(approval[1]), resolved: 1 });
  }
  const stop = path.match(/^\/panel-api\/v1\/runs\/([^/]+)\/stop$/);
  if (stop && method === "POST") {
    if (world.stop.hang) {
      return new Promise((resolve) => {
        window.__held!.stop = (status, payload) => resolve(json(payload, status));
      });
    }
    if (world.stop.status === 200 && world.run) {
      world.busy = false;
      world.run = { ...world.run, status: "cancelled" };
    }
    return json(world.stop.body, world.stop.status);
  }
  const events = path.match(/^\/panel-api\/v1\/runs\/([^/]+)\/events$/);
  if (events) {
    const runId = decodeURIComponent(events[1]);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        window.__streams?.set(runId, controller);
      },
      cancel() {
        window.__streams?.delete(runId);
      },
    });
    return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
  }
  const status = path.match(/^\/panel-api\/v1\/runs\/([^/]+)$/);
  if (status && method === "GET") {
    const run = world.run;
    if (!run || run.run_id !== decodeURIComponent(status[1])) return json({ error: { message: "not found", code: "not_found" } }, 404);
    return json({
      run_id: run.run_id,
      session_id: run.session_id,
      status: run.status,
      execution_generation: run.execution_generation ?? 4,
      pending_controls: run.pending_controls ?? [],
      created_at: run.created_at,
    });
  }
  window.__unhandled?.push(`${method} ${path}`);
  return json({});
};

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
const boot = () => {
  const world = window.__world;
  if (!world) {
    window.setTimeout(boot, 20);
    return;
  }
  useUiStore.setState({ selectedSessionId: world.sid, composerDraft: { value: "", attachments: [] } });
  createRoot(root).render(
    <QueryClientProvider client={client}>
      <ChatPage />
    </QueryClientProvider>,
  );
  window.fixtureReady = true;
};
boot();
