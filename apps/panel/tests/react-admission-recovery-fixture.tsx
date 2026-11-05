import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { useLiveRun, useResumeRuns } from "@/lib/stream";
import { runs } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";

declare global {
  interface Window {
    __generation?: number;
    __requests?: Array<{ url: string; method: string; key: string; body: string }>;
    fixtureReady?: boolean;
    __selectSession?: (id: string) => void;
    __uiState?: () => { sessionId: string | null; value: string };
    __runState?: () => { error: string; status: string; unverified: boolean; locallyReleased: boolean; active: boolean; runId: string } | null;
  }
}

const SID = "sess-screenshot-old";
const LIVE = new Set(["queued", "running", "waiting_for_approval", "stopping", "unknown"]);
const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);

interface HeldRun {
  run_id: string;
  session_id: string;
  status: string;
  created_at: number;
  updated_at: number;
  idempotencyKey: string;
}

const heldRuns = new Map<string, HeldRun>();
window.__requests = [];
window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});

function headerValue(headers: HeadersInit | undefined, name: string): string {
  if (!headers) return "";
  if (headers instanceof Headers) return headers.get(name) ?? "";
  if (Array.isArray(headers)) return headers.find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? "";
  const found = Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase());
  return found ? String(found[1]) : "";
}

function fixtureError(code: string, message: string) {
  return { error: { message, type: "invalid_request_error", param: null, code } };
}

function mintRunId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `run_${[...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function projectStatus(run: HeldRun) {
  return {
    object: "hermes.run",
    run_id: run.run_id,
    session_id: run.session_id,
    status: run.status,
    created_at: run.created_at,
    updated_at: run.updated_at,
  };
}

function runRecord(run: HeldRun) {
  return {
    run_id: run.run_id,
    session_id: run.session_id,
    status: run.status,
    created_at: run.created_at,
    updated_at: run.updated_at,
  };
}

function pathRunId(path: string): string {
  const match = path.match(/\/v1\/runs\/([^/]+)/);
  if (!match?.[1]) return "";
  try { return decodeURIComponent(match[1]); } catch { return ""; }
}

function runtimeSessionId(path: string): string {
  const match = path.match(/\/api\/runtime\/sessions\/([^/]+)\//);
  if (!match?.[1]) return "";
  try { return decodeURIComponent(match[1]); } catch { return ""; }
}

function runForSession(sessionId: string): HeldRun | null {
  let found: HeldRun | null = null;
  if (!sessionId) return null;
  for (const run of heldRuns.values()) {
    if (run.session_id === sessionId) found = run;
  }
  return found;
}

function sessionRunBody(path: string, url: string) {
  const sessionId = runtimeSessionId(path);
  const key = new URL(url, location.href).searchParams.get("idempotency_key");
  const owned = runForSession(sessionId);
  const matched = key == null ? owned : (owned && owned.idempotencyKey === key ? owned : null);
  const record = matched ? runRecord(matched) : null;
  const busy = Boolean(matched && LIVE.has(matched.status));
  if (key != null) return { busy, run: record, resolution: { idempotency_key: key, state: record ? "found" : "not_found" } };
  return { busy, run: record };
}

window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const headers = init?.headers || (input instanceof Request ? input.headers : undefined);
  const body = typeof init?.body === "string" ? init.body : "";
  const record = { url, method, key: headerValue(headers, "Idempotency-Key"), body };
  window.__requests?.push(record);
  const path = new URL(url, location.href).pathname;
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  if (path.endsWith("/sessions/run/resolve") || /\/api\/runtime\/sessions\/[^/]+\/run$/.test(path)) {
    return json(sessionRunBody(path, url));
  }
  if (path.endsWith("/sessions/execution") || /\/api\/runtime\/sessions\/[^/]+\/execution$/.test(path)) {
    const run = runForSession(runtimeSessionId(path));
    return json({ busy: Boolean(run && LIVE.has(run.status)), execution: null });
  }
  if (path.endsWith("/sessions/background") || /\/api\/runtime\/sessions\/[^/]+\/background$/.test(path)) return json({ processes: [] });
  if (path.includes("/sessions/folders")) return json({ folders: [], assignments: {} });
  if (path.includes("/subagents")) {
    const parsed = new URL(url, location.href);
    const limit = Number(parsed.searchParams.get("limit") || 50);
    const offset = Number(parsed.searchParams.get("offset") || 0);
    return json({ data: [], running: 0, stalled: 0, total: 0, now: Date.now() / 1000, limit, offset });
  }
  if (path.includes("/context")) return json({ used: null, window: null, source: "no-data" });
  if (/\/api\/sessions\/[^/]+\/effort$/.test(path)) {
    const id = decodeURIComponent(path.split("/sessions/")[1]?.split("/")[0] ?? "");
    return json({ session_id: id, effort: null, source: "unset", applied: "unset", running: null });
  }
  if (path.endsWith("/health") || path.endsWith("/health/")) return json({ status: "ok" });
  if (path.includes("/api/model/options")) {
    return json({ provider: "fixture", model: "fixture-model", providers: [{ slug: "fixture", name: "Fixture", authenticated: true, models: ["fixture-model"] }] });
  }
  if (path.includes("/api/sessions") && path.includes("/messages")) return json({ data: [], has_more: false });
  if (path.endsWith("/api/sessions") || path.includes("/api/sessions?")) {
    return json({
      data: [{ id: SID, source: "api_server", title: "IMG_2478.jpeg", model: null, started_at: 1, last_active: 1, message_count: 0, preview: null, pinned: false, archived: false }],
      has_more: false,
    });
  }
  if (path.endsWith("/v1/runs") && method === "POST") {
    let input: { session_id?: unknown } = {};
    try {
      input = body ? JSON.parse(body) as { session_id?: unknown } : {};
    } catch {
      return json(fixtureError("invalid_request", "Invalid JSON"), 400);
    }
    const sessionId = input.session_id;
    if (typeof sessionId !== "string" || sessionId.length < 1 || sessionId.length > 256) {
      return json(fixtureError("invalid_request", "session_id required"), 400);
    }
    const prior = record.key ? [...heldRuns.values()].find((run) => run.idempotencyKey === record.key) : undefined;
    if (prior) {
      if (prior.session_id !== sessionId) return json(fixtureError("idempotency_key_conflict", "Idempotency-Key was already used with a different request payload"), 409);
      return json({ run_id: prior.run_id, status: "started", replayed: true }, 202);
    }
    const now = Date.now() / 1000;
    const run: HeldRun = {
      run_id: mintRunId(),
      session_id: sessionId,
      status: "queued",
      created_at: now,
      updated_at: now,
      idempotencyKey: record.key,
    };
    heldRuns.set(run.run_id, run);
    return json({ run_id: run.run_id, status: "started", replayed: false }, 202);
  }
  if (path.includes("/v1/runs/") && path.endsWith("/stop") && method === "POST") {
    const id = pathRunId(path);
    const run = heldRuns.get(id);
    if (!run) return json(fixtureError("run_not_found", `Run not found: ${id}`), 404);
    if (!TERMINAL.has(run.status)) {
      run.status = "stopping";
      run.updated_at = Date.now() / 1000;
    }
    return json(projectStatus(run));
  }
  if (path.includes("/v1/runs/") && path.includes("/events")) {
    const id = pathRunId(path);
    const run = heldRuns.get(id);
    if (!run) return json(fixtureError("run_not_found", `Run not found: ${id}`), 404);
    const payload = { event: "run.queued", run_id: run.run_id, session_id: run.session_id, seq: 0, timestamp: Date.now() / 1000 };
    return new Response(`id: 0\ndata: ${JSON.stringify(payload)}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  }
  if (/\/v1\/runs\/[^/]+$/.test(path)) {
    const id = pathRunId(path);
    const run = heldRuns.get(id);
    if (!run) return json(fixtureError("run_not_found", `Run not found: ${id}`), 404);
    return json(projectStatus(run));
  }
  return json(fixtureError("route_not_found", "夹具没有这条路由"), 404);
};

function Harness() {
  useResumeRuns();
  const live = useLiveRun(SID);
  window.__hook = { active: live.isActive, error: live.run?.error ?? "", status: live.run?.status ?? "" };
  return <ChatPage />;
}

window.__selectSession = (id) => useUiStore.getState().setSelectedSessionId(id);
window.__uiState = () => ({ sessionId: useUiStore.getState().selectedSessionId, value: useUiStore.getState().composerDraft.value });
useUiStore.setState({ selectedSessionId: SID });
window.__changes = 0;
runs.addEventListener("hp-run-changed", () => { window.__changes = (window.__changes || 0) + 1; });
window.__runState = () => {
  const run = runs.get(SID);
  return run ? {
    error: run.error,
    status: run.status,
    unverified: Boolean(run.unverified),
    locallyReleased: Boolean(run.locallyReleased),
    active: runs.isActive(SID),
    runId: run.runId,
  } : null;
};
const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <Harness />
  </QueryClientProvider>,
);
window.fixtureReady = true;
