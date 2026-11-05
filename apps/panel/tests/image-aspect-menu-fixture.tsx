import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ImagePage } from "@/pages/image/image-page";
import "@/styles/index.css";

declare global {
  interface Window {
    fixtureReady?: boolean;
    fixtureError?: string;
  }
}

window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});

window.fetch = async (input) => {
  const url = String(input instanceof Request ? input.url : input);
  if (url.includes("image/models")) return Response.json({ data: [] });
  if (url.includes("image/history")) return Response.json({ data: [] });
  return Response.json({});
};

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ImagePage />
  </QueryClientProvider>,
);
window.fixtureReady = true;
