import DOMPurify, { type Config } from "dompurify";
import { buildContent } from "@/lib/attachments";
import { chatModel, nextChatModel } from "@/lib/chat-selection";
import { runs } from "@/lib/runs";
import type { ToolStep } from "@/lib/transcript";
import { useUiStore } from "@/stores/ui";
import { WidgetFrame } from "./widget-frame";

export const WIDGET_CODE_LIMIT = 100000;
const OVER_LIMIT = "小组件超过 100000 字符，未渲染";

const SVG_PURIFY: Config = {
  USE_PROFILES: { svg: true, svgFilters: true },
  FORBID_TAGS: ["script", "foreignObject", "iframe", "embed", "object"],
};

export function isShowWidgetStep(step: ToolStep): boolean {
  const base = step.name.replace(/^functions\./, "").split(".").pop();
  return base === "show_widget";
}

function titleOf(args: Record<string, unknown> | null): string {
  const title = args?.title;
  return typeof title === "string" ? title.trim().slice(0, 200) : "";
}

function closedSvg(code: string): boolean {
  return /<\/svg\s*>/i.test(code);
}

function safeRef(value: string): boolean {
  const ref = value.trim();
  return ref.startsWith("#") || /^data:/i.test(ref);
}

function stripNetwork(html: string): string {
  const host = document.createElement("template");
  host.innerHTML = html;
  for (const node of host.content.querySelectorAll("*")) {
    for (const name of ["href", "xlink:href", "src", "poster"]) {
      const value = node.getAttribute(name);
      if (value !== null && !safeRef(value)) node.removeAttribute(name);
    }
    const style = node.getAttribute("style");
    if (style && /url\s*\(/i.test(style) && !/url\s*\(\s*['"]?data:/i.test(style)) node.removeAttribute("style");
  }
  return host.innerHTML;
}

export function sanitizeWidgetSvg(source: string): string {
  const clean = DOMPurify.sanitize(source, SVG_PURIFY);
  return typeof clean === "string" ? stripNetwork(clean) : "";
}

type WidgetView =
  | { kind: "skip" }
  | { kind: "skeleton"; title: string }
  | { kind: "error"; title: string }
  | { kind: "svg"; title: string; html: string }
  | { kind: "html"; title: string; code: string };

export function sendWidgetPrompt(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  const sessionId = useUiStore.getState().selectedSessionId ?? "";
  if (!sessionId || runs.isActive(sessionId)) return;
  const picked = chatModel(sessionId) ?? nextChatModel();
  runs.start(sessionId, trimmed, [], buildContent(trimmed, []), picked?.model ?? "", picked?.provider ?? "");
}

export function widgetView(step: ToolStep): WidgetView {
  if (!isShowWidgetStep(step)) return { kind: "skip" };
  const title = titleOf(step.args);
  const code = step.args?.widget_code;
  if (typeof code !== "string" || !code.trim()) return { kind: "skeleton", title };
  if (code.length > WIDGET_CODE_LIMIT) return { kind: "error", title };
  const trimmed = code.trimStart();
  if (trimmed.startsWith("<svg")) {
    if (!closedSvg(trimmed)) return { kind: "skeleton", title };
    const html = sanitizeWidgetSvg(trimmed);
    if (!html.includes("<svg")) return { kind: "skeleton", title };
    return { kind: "svg", title, html };
  }
  return { kind: "html", title, code: trimmed };
}

export function WidgetCard({ step }: { step: ToolStep }) {
  const view = widgetView(step);
  if (view.kind === "skip") return null;
  return (
    <section className="widget-card" data-step={step.id} aria-label={view.title || "小组件"}>
      {view.title ? <header>{view.title}</header> : null}
      {view.kind === "error" ? <p className="widget-error" role="alert">{OVER_LIMIT}</p> : null}
      {view.kind === "skeleton" ? <div className="widget-skeleton" aria-busy="true" /> : null}
      {view.kind === "svg" ? <div className="widget-svg" dangerouslySetInnerHTML={{ __html: view.html }} /> : null}
      {view.kind === "html" ? <WidgetFrame title={view.title} code={view.code} onPrompt={sendWidgetPrompt} /> : null}
    </section>
  );
}
