import { createRoot } from "react-dom/client";
import { ChatStyles } from "@/pages/chat/chat-styles";
import { StepGroups } from "@/pages/chat/turns";
import type { ToolStep } from "@/lib/transcript";
import { runs } from "@/lib/runs";
import { useUiStore } from "@/stores/ui";
import { rememberChatModel } from "@/lib/chat-selection";
import { setThemePref, type ThemePref } from "@/lib/theme";
import "@/styles/index.css";

declare global {
  interface Window {
    fixtureReady?: boolean;
    fixtureError?: string;
    __xss?: number;
    __sendLog?: Array<{ sessionId: string; text: string; model: string; provider: string; content: unknown }>;
    __fetches?: string[];
    __setTheme?: (pref: ThemePref) => void;
  }
}

window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});

document.documentElement.dataset.theme = "light";
useUiStore.getState().setSelectedSessionId("widget-session");
rememberChatModel("widget-session", "widget-model", "widget-provider");
window.__setTheme = setThemePref;
window.__sendLog = [];
window.__fetches = [];
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  window.__fetches?.push(`${init?.method || "GET"} ${url}`);
  return nativeFetch(input, init);
};
runs.start = (sessionId, text, _attachments, content, model = "", provider = "") => {
  window.__sendLog?.push({ sessionId, text, model, provider, content });
};

function step(id: string, args: ToolStep["args"], status: ToolStep["status"] = "done"): ToolStep {
  return { kind: "tool", id, name: "show_widget", args, preview: null, status, output: null };
}

const complete = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle id="widget-dot" cx="16" cy="16" r="8" fill="currentColor"/></svg>`;
const dirty = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" onload="window.__xss=1"><script>window.__xss=1</script><image id="ext" href="https://example.com/pixel.png"/><rect id="widget-rect" width="10" height="10" onclick="window.__xss=1"/></svg>`;
const partial = `<svg xmlns="http://www.w3.org/2000/svg" onload="window.__xss=1"><script>window.__xss=1</script><rect`;
const huge = `<svg xmlns="http://www.w3.org/2000/svg">${"x".repeat(100001)}</svg>`;
const html = `<img src="/widget-external-probe.png" alt=""><div id="grow" style="height:200px">html widget</div>`;

const steps: ToolStep[] = [
  step("complete", { title: "完成图", widget_code: ` \n${complete}` }),
  step("dirty", { title: "脏图", widget_code: dirty }),
  step("partial", { title: "半截", widget_code: partial }),
  step("pending", null, "running"),
  step("missing", { title: "缺代码" }),
  step("over", { title: "超限", widget_code: huge }),
  step("html", { title: "页面", widget_code: ` \n${html}` }),
];

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");

try {
  createRoot(root).render(
    <div className="hp-chat">
      <ChatStyles />
      <div className="turn assistant">
        <StepGroups steps={steps} live={false} />
      </div>
    </div>,
  );
  window.fixtureReady = true;
} catch (error) {
  window.fixtureError = error instanceof Error ? error.message : String(error);
}
