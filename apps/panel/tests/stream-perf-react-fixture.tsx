import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { runs, RUN_CHANGED, RUN_FINISHED } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";

declare global {
  interface Window {
    fixtureReady?: boolean;
    fixtureError?: string;
    __messages?: HistoryRow[];
    __sessionId?: string;
    __prepare?: (config: ScenarioConfig) => { streamChars: number; chunkCount: number; historyRows: number; codeLines: number };
    __run?: () => Promise<RunResult>;
  }
}

interface HistoryRow {
  id: number;
  role: string;
  content: string;
  timestamp: number;
  tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>;
  tool_call_id?: string;
  tool_name?: string;
}

interface ScenarioConfig {
  sessionId: string;
  historyTurns: number;
  streamTargetChars: number;
  includeTools: boolean;
  includeCode: boolean;
}

interface RunResult {
  rafIntervals: number[];
  longTasks: Array<{ duration: number; startTime: number }>;
  receiveTimeline: Array<{ version: number; timestamp: number; ambiguous: false }>;
  domCommitTimeline: Array<{ version: number; timestamp: number }>;
  settleDurationMs: number;
  streamFrameCount: number;
  streamChars: number;
  chunkCount: number;
  elapsedMs: number;
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
const SESSION = () => window.__sessionId || "perf-sess";

function pageOf(rows: HistoryRow[], url: URL) {
  const limit = Number(url.searchParams.get("limit") || rows.length || 0);
  const offset = Number(url.searchParams.get("offset") || 0);
  const end = Math.max(0, rows.length - offset);
  const start = Math.max(0, end - limit);
  const messages = rows.slice(start, end);
  return { data: messages, pagination: { returned: messages.length, limit, offset } };
}

window.fetch = async (input, init) => {
  const url = new URL(String(input instanceof Request ? input.url : input), location.href);
  const path = url.pathname;
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  if (path.includes("/messages") && !path.includes("message-origins")) return json(pageOf(window.__messages ?? [], url));
  if (path.includes("/sessions/commentary")) return json({ items: {} });
  if (path.includes("/sessions/message-origins")) return json({ items: {} });
  if (path.endsWith("/sessions/background") || /\/api\/runtime\/sessions\/[^/]+\/background$/.test(path)) return json({ processes: [] });
  if (path.endsWith("/sessions/execution") || /\/api\/runtime\/sessions\/[^/]+\/execution$/.test(path)) return json({ busy: false, execution: null });
  if (path.endsWith("/sessions/run") || path.includes("/sessions/run/resolve") || /\/api\/runtime\/sessions\/[^/]+\/run$/.test(path)) {
    const key = url.searchParams.get("idempotency_key") ?? "";
    return json({ busy: false, run: null, ...(key ? { resolution: { idempotency_key: key, state: "not_found" } } : {}) });
  }
  if (path.includes("/sessions/folders")) return json({ folders: [], assignments: {} });
  if (path.includes("/subagents")) {
    const limit = Number(url.searchParams.get("limit") || 50);
    const offset = Number(url.searchParams.get("offset") || 0);
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
  if (path.includes("/api/sessions") || path.endsWith("/sessions")) {
    const id = SESSION();
    return json({ data: [{ id, source: "api_server", title: "perf", model: null, started_at: 1, last_active: 1, message_count: 1, preview: null, pinned: false, archived: false }], has_more: false });
  }
  if (path.includes("/runs/")) return json({ status: "running", run_id: `run-${SESSION()}`, session_id: SESSION(), created_at: 100 });
  return json({ error: { message: "夹具没有这条路由", code: "fixture_not_found" } }, 404);
};

function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function historyOf(config: ScenarioConfig): HistoryRow[] {
  const rows: HistoryRow[] = [];
  for (let t = 0; t < config.historyTurns; t++) {
    rows.push({ id: t * 2 + 1, role: "user", content: `User query ${t}: ${"Please analyze this task component. ".repeat(2)}`, timestamp: 1000 + t * 10 });
    if (config.includeTools && t % 5 === 0) {
      rows.push({
        id: t * 2 + 2,
        role: "assistant",
        content: `Step commentary before tool call in turn ${t}.\n`,
        tool_calls: [{ id: `tool-${t}`, function: { name: "bash", arguments: "{\"command\":\"git status\"}" } }],
        timestamp: 1000 + t * 10 + 2,
      });
      rows.push({
        id: t * 2 + 2 + 10000,
        role: "tool",
        tool_call_id: `tool-${t}`,
        tool_name: "bash",
        content: `Output for step ${t}: status clean, branch hk ahead of origin/hk by 1 commit.`,
        timestamp: 1000 + t * 10 + 4,
      });
    } else {
      rows.push({ id: t * 2 + 2, role: "assistant", content: `Assistant response ${t}: ${"Here is the intermediate result for discussion. ".repeat(5)}`, timestamp: 1000 + t * 10 + 2 });
    }
  }
  return rows;
}

function streamText(config: ScenarioConfig): { text: string; codeLines: number } {
  if (!config.includeCode) {
    const base = "In this benchmark, we test the assistant text streaming capabilities under throttling.\n\n";
    return { text: base.repeat(Math.ceil(config.streamTargetChars / base.length)).slice(0, config.streamTargetChars), codeLines: 0 };
  }
  const codeLines = Array.from({ length: 300 }, (_, index) => `  const item${index + 1}: number = ${index + 1};`);
  const codeBlock = `\n\n\`\`\`ts\nexport function calculateMetrics(): void {\n${codeLines.join("\n")}\n}\n\`\`\`\n\n`;
  const remain = Math.max(1000, config.streamTargetChars - codeBlock.length);
  const half = Math.ceil(remain / 2);
  const leadBase = "In this analysis, we examine the streaming pipeline performance and architectural boundaries.\n\n";
  const tailBase = "Following the code inspection, we conclude that incremental parsing and stable DOM retention are essential.\n\n";
  const lead = leadBase.repeat(Math.ceil(half / leadBase.length)).slice(0, half);
  const tail = tailBase.repeat(Math.ceil(Math.max(0, config.streamTargetChars - codeBlock.length - lead.length) / tailBase.length)).slice(0, Math.max(0, config.streamTargetChars - codeBlock.length - lead.length));
  return { text: lead + codeBlock + tail, codeLines: codeLines.length };
}

function chunksOf(text: string, seed: number): string[] {
  const next = prng(seed);
  const chunks: string[] = [];
  let idx = 0;
  let burst = 0;
  while (idx < text.length) {
    burst += 1;
    const size = burst % 12 === 0 ? Math.min(400, text.length - idx) : Math.min(Math.floor(next() * 6) + 3, text.length - idx);
    chunks.push(text.slice(idx, idx + size));
    idx += size;
  }
  return chunks;
}

function marker(version: number): string {
  return `⟦v${version}⟧`;
}

function store() {
  return runs as unknown as { runs: Map<string, Record<string, unknown>> };
}

function blankRun(id: string) {
  return {
    sessionId: id,
    runId: `run-${id}`,
    status: "running",
    userText: "合成压测提问",
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
    locallyReleased: false,
  };
}

function bodyText(): string {
  return document.querySelector(".mount-row .assistant-body")?.textContent ?? "";
}

window.__prepare = (config) => {
  window.__sessionId = config.sessionId;
  window.__messages = historyOf(config);
  useUiStore.setState({ selectedSessionId: config.sessionId });
  const built = streamText(config);
  const chunks = chunksOf(built.text, config.historyTurns + 42);
  (window as unknown as { __chunks?: string[]; __codeLines?: number }).__chunks = chunks;
  (window as unknown as { __codeLines?: number }).__codeLines = built.codeLines;
  const rows = window.__messages ?? [];
  const visible = rows[rows.length - 1]?.content.slice(0, 24) || "对话";
  return { streamChars: built.text.length, chunkCount: chunks.length, historyRows: rows.length, codeLines: built.codeLines, visibleMark: visible };
};

window.__run = async () => {
  const id = SESSION();
  const chunks = (window as unknown as { __chunks?: string[] }).__chunks ?? [];
  const started = performance.now();
  store().runs.set(id, blankRun(id));
  runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const longTasks: RunResult["longTasks"] = [];
  function recordLongTasks(list: PerformanceObserverEntryList) {
    for (const entry of list.getEntries()) longTasks.push({ duration: entry.duration, startTime: entry.startTime });
  }
  const observer = new PerformanceObserver(recordLongTasks);
  observer.observe({ type: "longtask", buffered: true });
  const rafIntervals: number[] = [];
  let last = performance.now();
  let raf = 0;
  const tick = (now: number) => {
    rafIntervals.push(now - last);
    last = now;
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  const receiveTimeline: RunResult["receiveTimeline"] = [];
  const domCommitTimeline: RunResult["domCommitTimeline"] = [];
  let seen = 0;
  function noteVisible(now: number) {
    const text = bodyText();
    while (seen < chunks.length && text.includes(marker(seen + 1))) {
      seen += 1;
      domCommitTimeline.push({ version: seen, timestamp: now });
    }
  }
  let text = "";
  let sent = 0;
  const budgetAt = started + 110000;
  for (let index = 0; index < chunks.length; index++) {
    if (performance.now() > budgetAt) break;
    const version = index + 1;
    const piece = `${chunks[index]}${marker(version)}`;
    text += piece;
    const current = store().runs.get(id);
    if (!current) throw new Error("missing run");
    current.text = text;
    current.lastEventAt = Date.now();
    store().runs.set(id, current);
    runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
    receiveTimeline.push({ version, timestamp: performance.now(), ambiguous: false });
    sent = version;
    const delay = (chunks[index]?.length ?? 0) >= 400 ? 200 : 16;
    await new Promise((resolve) => setTimeout(resolve, delay));
    noteVisible(performance.now());
  }
  const deadline = performance.now() + 4000;
  while (seen < sent && performance.now() < deadline) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
    noteVisible(performance.now());
  }
  const streamFrameCount = rafIntervals.length;
  const settleStart = performance.now();
  const current = store().runs.get(id);
  if (current) {
    current.status = "completed";
    current.lastEventAt = Date.now();
    store().runs.set(id, current);
  }
  runs.dispatchEvent(new CustomEvent(RUN_CHANGED, { detail: id }));
  runs.dispatchEvent(new CustomEvent(RUN_FINISHED, { detail: id }));
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const settleDurationMs = performance.now() - settleStart;
  cancelAnimationFrame(raf);
  observer.disconnect();
  return {
    rafIntervals,
    longTasks,
    receiveTimeline,
    domCommitTimeline,
    settleDurationMs,
    streamFrameCount,
    streamChars: text.length,
    chunkCount: chunks.length,
    chunksSent: sent,
    completed: sent === chunks.length,
    elapsedMs: performance.now() - started,
  };
};

function Boot() {
  useEffect(() => { window.fixtureReady = true; }, []);
  return (
    <div className="chat-slot" id="chat-slot">
      <ChatPage />
    </div>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(
  <QueryClientProvider client={client}>
    <Boot />
  </QueryClientProvider>,
);
