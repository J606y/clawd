import { useEffect, useRef, useState } from "react";

export interface WidgetThemeVars {
  textPrimary: string;
  surface1: string;
  border: string;
}

const CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' https://cdnjs.cloudflare.com",
  "style-src 'unsafe-inline' https://cdnjs.cloudflare.com",
  "img-src data: blob:",
  "font-src data:",
  "connect-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

function cssToken(value: string): string {
  return value.replace(/[<>;{}]/g, "");
}

export function readWidgetTheme(): WidgetThemeVars {
  const style = getComputedStyle(document.documentElement);
  return {
    textPrimary: style.getPropertyValue("--text").trim(),
    surface1: style.getPropertyValue("--bg-elevated").trim() || style.getPropertyValue("--bg").trim(),
    border: style.getPropertyValue("--border").trim(),
  };
}

function bridgeScript(): string {
  return `(function () {
    function apply(vars) {
      if (!vars || typeof vars !== "object") return;
      var root = document.documentElement;
      if (typeof vars.textPrimary === "string") root.style.setProperty("--text-primary", vars.textPrimary);
      if (typeof vars.surface1 === "string") root.style.setProperty("--surface-1", vars.surface1);
      if (typeof vars.border === "string") root.style.setProperty("--border", vars.border);
    }
    window.sendPrompt = function (text) {
      parent.postMessage({ type: "hp-widget-send", text: String(text == null ? "" : text) }, "*");
    };
    window.addEventListener("message", function (event) {
      if (event.source !== parent) return;
      var data = event.data;
      if (!data || data.type !== "hp-widget-theme") return;
      apply(data.vars);
    });
    function postHeight() {
      var body = document.body;
      var height = Math.max(document.documentElement.scrollHeight, body ? body.scrollHeight : 0);
      parent.postMessage({ type: "hp-widget-height", height: height }, "*");
    }
    function watch() {
      if (window.ResizeObserver) {
        var observer = new ResizeObserver(postHeight);
        observer.observe(document.documentElement);
        if (document.body) observer.observe(document.body);
      }
      postHeight();
    }
    if (document.body) watch();
    else document.addEventListener("DOMContentLoaded", watch);
  })();`;
}

export function buildWidgetSrcDoc(code: string, theme: WidgetThemeVars): string {
  const vars = `--text-primary: ${cssToken(theme.textPrimary)}; --surface-1: ${cssToken(theme.surface1)}; --border: ${cssToken(theme.border)};`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}"><style>:root { ${vars} } html, body { margin: 0; background: var(--surface-1); color: var(--text-primary); }</style><script>${bridgeScript()}</script></head><body>${code}</body></html>`;
}

export function WidgetFrame({
  title,
  code,
  onPrompt,
}: {
  title: string;
  code: string;
  onPrompt: (text: string) => void;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const onPromptRef = useRef(onPrompt);
  onPromptRef.current = onPrompt;
  const [srcDoc] = useState(() => buildWidgetSrcDoc(code, readWidgetTheme()));
  const [height, setHeight] = useState(48);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const pushTheme = () => {
      frame.contentWindow?.postMessage({ type: "hp-widget-theme", vars: readWidgetTheme() }, "*");
    };
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow || !event.data || typeof event.data !== "object") return;
      const data = event.data as { type?: unknown; height?: unknown; text?: unknown };
      if (data.type === "hp-widget-height") {
        const next = Number(data.height);
        if (!Number.isFinite(next) || next < 0) return;
        setHeight(Math.min(4000, Math.ceil(next)));
        return;
      }
      if (data.type === "hp-widget-send" && typeof data.text === "string") onPromptRef.current(data.text);
    };
    window.addEventListener("message", onMessage);
    frame.addEventListener("load", pushTheme);
    const themeNode = document.documentElement;
    const observer = new MutationObserver(pushTheme);
    observer.observe(themeNode, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", pushTheme);
    return () => {
      window.removeEventListener("message", onMessage);
      frame.removeEventListener("load", pushTheme);
      observer.disconnect();
      media.removeEventListener("change", pushTheme);
    };
  }, []);

  return (
    <iframe
      ref={frameRef}
      className="widget-frame"
      title={title || "小组件"}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      referrerPolicy="no-referrer"
      style={{ height }}
    />
  );
}
