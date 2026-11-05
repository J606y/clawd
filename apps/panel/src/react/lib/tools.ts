// 工具的中文名与一句话摘要（取最能说明"在做什么"的那个参数）。

import type { Step, ToolStep } from "./transcript.js";

const BROWSER_TOOL = /^(?:browser_(?:exec|navigate|go_back|click|type|scroll|press_key|hover|select_option|snapshot|screenshot|evaluate|tabs|wait_for|close|resize|upload_file|fill_form|drag)|mcp__chrome_devtools__[a-z_]+|mcp__playwright__browser_[a-z_]+)$/;

function browserToolName(name: unknown): boolean {
  return typeof name === "string" && BROWSER_TOOL.test(name.split(".").at(-1) ?? "");
}

/** 只按工具标识识别浏览器活动；不解析用户文本或任意脚本。兼容延迟工具与并行调用包装。 */
export function isBrowserStep(step: Step): boolean {
  if (step.kind !== "tool") return false;
  if (browserToolName(step.name)) return true;
  const name = step.name.split(".").at(-1);
  const calls = name === "tool_call" ? step.args?.calls : name === "parallel" ? step.args?.tool_uses : null;
  return Array.isArray(calls) && calls.slice(0, 50).some((call: unknown) => {
    if (!call || typeof call !== "object") return false;
    const c = call as { name?: unknown; recipient_name?: unknown };
    return browserToolName(name === "tool_call" ? c.name : c.recipient_name);
  });
}

const LABELS: Record<string, string> = {
  terminal: "Bash",
  execute_code: "Execute",
  read_file: "Read",
  write_file: "Write",
  patch: "Edit",
  search_files: "Search",
  web_search: "Web Search",
  web_extract: "Fetch",
  browser_exec: "Browser",
  vision_analyze: "View Image",
  text_to_speech: "Text to Speech",
  delegate_task: "Agent",
  memory: "Memory",
  skill_view: "Skill",
  skill_manage: "Manage Skill",
  skills_list: "List Skills",
  clarify: "AskUserQuestion",
  tool_search: "Tool Search",
  tool_describe: "Tool Details",
  tool_call: "Tool Call",
};

const SUMMARY_KEYS = ["command", "code", "path", "file_path", "query", "url", "pattern", "goal", "task", "question", "name"];

export function toolLabel(name: string): string {
  const base = name.replace(/^functions\./, "");
  const mcp = /^mcp__(.+?)__(.+)$/.exec(base);
  if (mcp) return `${mcp[1]} - ${mcp[2]} (MCP)`;
  if (LABELS[base]) return LABELS[base];
  if (base.startsWith("browser_")) return "Browser";
  return base;
}

interface DisplayCall { name: string; server?: string }

/** Read only structured tool identities, never infer calls from scripts or prose. */
function displayCalls(name: string, args: Record<string, unknown> | null, depth = 0): DisplayCall[] {
  const base = name.replace(/^functions\./, "");
  const mcp = /^mcp__(.+?)__(.+)$/.exec(base);
  if (mcp) return [{ name: base, server: mcp[1] }];
  const wrapper = base.split(".").at(-1);
  const calls = wrapper === "tool_call" ? args?.calls : wrapper === "parallel" ? args?.tool_uses : null;
  if (depth < 3 && Array.isArray(calls)) {
    const nested = calls.slice(0, 50).flatMap((value: unknown) => {
      if (!value || typeof value !== "object") return [];
      const call = value as Record<string, unknown>;
      const child = wrapper === "tool_call" ? call.name : call.recipient_name;
      if (typeof child !== "string") return [];
      const parameters = wrapper === "tool_call" ? call.arguments : call.parameters;
      const childArgs = parameters && typeof parameters === "object" && !Array.isArray(parameters)
        ? parameters as Record<string, unknown> : null;
      return displayCalls(child, childArgs, depth + 1);
    });
    if (nested.length) return nested;
  }
  return [{ name: base }];
}

export function toolActivity(step: ToolStep): string {
  const servers = [...new Set(displayCalls(step.name, step.args).flatMap(c => c.server ? [c.server] : []))];
  return servers.length ? `Calling ${servers.join(", ")}…` : toolLabel(step.name);
}

export function toolGroupSummary(steps: ToolStep[]): string {
  const running = steps.filter(step => step.status === "running");
  if (running.length) return [...new Set(running.map(toolActivity))].join(" · ");
  const counts = new Map<string, { count: number; server: boolean; name: string }>();
  for (const step of steps) for (const call of displayCalls(step.name, step.args)) {
    const key = call.server ? `mcp:${call.server}` : `tool:${call.name}`;
    const value = counts.get(key) ?? { count: 0, server: !!call.server, name: call.server ?? call.name };
    value.count++;
    counts.set(key, value);
  }
  return [...counts.values()].map(({ count, server, name }) => {
    if (server) return `Called ${name}${count > 1 ? ` ${count} times` : ""}`;
    if (name === "read_file") return `Read ${count} ${count === 1 ? "file" : "files"}`;
    return `${toolLabel(name)}${count > 1 ? ` ${count} times` : ""}`;
  }).join(" · ");
}

/** Three-line preview with a hard size cap; preserve Unicode code points. */
export function outputPreview(output: string): { text: string; truncated: boolean } {
  const lines = output.split("\n", 4);
  const first = lines.slice(0, 3).join("\n");
  let end = Math.min(first.length, 4000);
  if (end < first.length && /[\uD800-\uDBFF]/.test(first[end - 1] ?? "")) end--;
  const text = first.slice(0, end);
  return { text, truncated: text.length < output.length };
}

export function toolSummary(step: ToolStep): string {
  const a = step.args;
  if (a) {
    for (const k of SUMMARY_KEYS) {
      const v = a[k];
      if (typeof v === "string" && v.trim()) return v.trim().split("\n")[0]!.slice(0, 200);
    }
  }
  return step.preview ?? "";
}
