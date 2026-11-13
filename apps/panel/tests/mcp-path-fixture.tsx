import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { McpTab } from "@/pages/settings/mcp-tab";
import "@/styles/index.css";
import "@/pages/settings/settings.css";
import { apiPayload } from "./mcp-path-data.mjs";

declare global {
  interface Window {
    fixtureError?: string;
    mcpTraffic?: { method: string; url: string }[];
  }
}

window.mcpTraffic = [];
window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input instanceof Request ? input.url : input);
  const method = String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  window.mcpTraffic?.push({ method, url });
  const path = new URL(url, location.href).pathname;
  if (method === "GET" && path.endsWith("/panel-core/mcp")) {
    return Response.json(apiPayload());
  }
  return new Response(JSON.stringify({ error: "blocked by mcp-path fixture" }), {
    status: 599,
    headers: { "content-type": "application/json" },
  });
};

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })}>
    <section className="settings-hub" aria-label="设置">
      <div className="body">
        <div className="pane">
          <div className="page">
            <McpTab />
          </div>
        </div>
      </div>
    </section>
  </QueryClientProvider>,
);
