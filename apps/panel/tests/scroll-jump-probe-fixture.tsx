import { createRoot } from "react-dom/client";
import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "@/pages/chat/chat-page";
import { useUiStore } from "@/stores/ui";
import "@/styles/index.css";
import "@/shell/shell.css";

const SID = "fixture-scroll-jump";
const params = new URLSearchParams(location.search);
const token = params.get("token") || "unset";
const variant = params.get("case") === "short" ? "short" : "plugin";

declare global {
  interface Window {
    __fetches?: Array<{ t: number; path: string; method: string }>;
    __releaseLate?: () => void;
    __lateReleased?: boolean;
    fixtureReady?: boolean;
    fixtureError?: string;
    __token?: string;
    __variant?: string;
  }
}

window.__fetches = [];
window.__token = token;
window.__variant = variant;
window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});
window.addEventListener("unhandledrejection", (event) => {
  window.fixtureError = String(event.reason);
});

function line(label: string, n: number): string {
  return Array.from({ length: n }, (_, i) => `${label} ${i + 1}`).join("\n");
}

function pluginMessages() {
  let id = 1;
  let call = 1;
  const nextCall = (name: string, args: Record<string, string>) => {
    const item = { id: `call_${call++}`, type: "function", function: { name, arguments: JSON.stringify(args) } };
    return item;
  };
  const user = (text: string) => ({ id: id++, role: "user", content: text, timestamp: 1_700_000_000 + id, display_metadata: { gateway_input_owner: "fixture", submission_id: "syn" } });
  const assistant = (text: string, reasoning: string, calls: ReturnType<typeof nextCall>[] = []) => ({
    id: id++,
    role: "assistant",
    content: text,
    reasoning: reasoning || null,
    tool_calls: calls.length ? calls : null,
    timestamp: 1_700_000_000 + id,
    finish_reason: calls.length ? "tool_calls" : "stop",
  });
  const tool = (name: string, callId: string, text: string) => ({
    id: id++,
    role: "tool",
    content: text,
    tool_call_id: callId,
    tool_name: name,
    timestamp: 1_700_000_000 + id,
  });
  const skill = nextCall("skill_view", { name: "synthetic-skill" });
  const extract = nextCall("web_extract", { urls: "https://example.invalid/a" });
  const extract2 = nextCall("web_extract", { urls: "https://example.invalid/b" });
  const read = nextCall("read_file", { path: "synthetic.txt" });
  const term = nextCall("terminal", { command: "printf synthetic" });
  const longLine = `${"synthetic-fence ".repeat(3)}\n${"synthetic-token ".repeat(700)}`;
  return [
    user("合成短问句一二三四五六七八"),
    assistant("先查看合成技能说明，再继续。", "合成思考。".repeat(8), [skill]),
    tool("skill_view", skill.id, longLine),
    assistant("继续提取合成页面。", "", [extract]),
    tool("web_extract", extract.id, line("合成提取行", 14)),
    assistant("", "再取一页。", [extract2]),
    tool("web_extract", extract2.id, line("合成第二页", 20)),
    assistant(line("合成结论", 8), "合成收束思考。".repeat(40)),
    { id: id++, role: "session_meta", content: null, timestamp: 1_700_000_000 + id },
    user("合成短问句一二三四五六七八"),
    assistant("并行查看合成材料。", "合成并行思考。".repeat(12), [read, term]),
    tool("read_file", read.id, line("合成文件", 6)),
    tool("terminal", term.id, "synthetic-ok"),
    assistant("", "合成空正文，只有思考和工具。".repeat(6), [nextCall("search_files", { pattern: "synthetic" })]),
    tool("search_files", "call_6", "no matches"),
    assistant("合成收尾一句。", ""),
  ];
}

function shortMessages() {
  const rows = [];
  let id = 1;
  for (let n = 0; n < 8; n += 1) {
    rows.push({ id: id++, role: "user", content: `合成对照问句 ${n + 1}`, timestamp: 1_700_000_100 + id });
    rows.push({ id: id++, role: "assistant", content: line(`合成对照段落 ${n + 1}`, 6), reasoning: n === 0 ? "合成对照思考。" : "", timestamp: 1_700_000_100 + id });
  }
  return rows;
}

const messages = variant === "short" ? shortMessages() : pluginMessages();

window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const path = new URL(url, location.href).pathname;
  window.__fetches?.push({ t: performance.now(), path, method });
  const json = (data: unknown, status = 200) => Response.json(data, { status });
  if (path.includes("/messages")) return json({ data: messages, pagination: { returned: messages.length } });
  if (path.includes("/commentary")) return json({ items: {} });
  if (path.includes("/sessions/run")) return json({ busy: false, run: null });
  if (path.includes("/sessions/execution")) return json({ busy: false, execution: null });
  if (path.includes("/sessions/background")) return json({ processes: [] });
  if (path.includes("/subagents")) return json({ data: [], running: 0, now: Date.now() });
  if (path.includes("/context")) {
    return new Promise((resolve) => {
      window.__releaseLate = () => {
        window.__lateReleased = true;
        resolve(json({ used: 12, window: 1000, source: "no-data" }));
      };
    });
  }
  if (path.includes("/api/model/options") || path.endsWith("/model/options")) {
    return json({ provider: "fixture", model: "fixture-model", providers: [{ slug: "fixture", name: "Fixture", authenticated: true, models: ["fixture-model"] }] });
  }
  if (path.endsWith("/api/sessions") || path.includes("/api/sessions")) {
    return json({ data: [{ id: SID, source: "api_server", title: "合成滚动探针", model: "fixture-model", started_at: 1, last_active: 1, message_count: messages.length, preview: null, pinned: false, archived: false }], has_more: false });
  }
  if (path.includes("/health")) return json({ status: "ok" });
  return json({ data: [], items: {}, processes: [], busy: false, run: null });
};

function Harness() {
  useEffect(() => {
    window.fixtureReady = true;
  }, []);
  return (
    <div className="shell">
      <aside />
      <main>
        <div className="topbar" />
        <div className="stage">
          <div className="pane chat-pane chat-slot">
            <ChatPage />
          </div>
        </div>
      </main>
    </div>
  );
}

useUiStore.setState({ selectedSessionId: SID });
const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <Harness />
  </QueryClientProvider>,
);
