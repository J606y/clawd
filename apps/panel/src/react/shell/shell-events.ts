import type { SessionSummary } from "@/lib/api.js";
import type { OriginRect } from "./origin-motion";

export interface LiveOpenDetail {
  scope: string;
  active: boolean;
  token: string;
  sessionId: string;
}

export interface AgentOpenDetail {
  id: string;
  parentId: string;
  origin: OriginRect | null;
}

function readOrigin(value: unknown): OriginRect | null {
  if (!value || typeof value !== "object") return null;
  const rect = value as { x?: unknown; y?: unknown; width?: unknown; height?: unknown };
  if (typeof rect.x !== "number" || typeof rect.y !== "number") return null;
  if (typeof rect.width !== "number" || typeof rect.height !== "number") return null;
  if (![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)) return null;
  if (rect.width < 1 || rect.height < 1) return null;
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

export function readSession(event: Event): SessionSummary | null {
  if (!(event instanceof CustomEvent)) return null;
  const detail: unknown = event.detail;
  if (!detail || typeof detail !== "object") return null;
  const value = detail as Partial<SessionSummary>;
  if (typeof value.id !== "string" || typeof value.source !== "string") return null;
  if (typeof value.pinned !== "boolean" || typeof value.archived !== "boolean") return null;
  if (typeof value.started_at !== "number" || typeof value.message_count !== "number") return null;
  return {
    id: value.id,
    source: value.source,
    title: typeof value.title === "string" ? value.title : null,
    model: typeof value.model === "string" ? value.model : null,
    started_at: value.started_at,
    last_active: typeof value.last_active === "number" ? value.last_active : null,
    message_count: value.message_count,
    preview: typeof value.preview === "string" ? value.preview : null,
    pinned: value.pinned,
    archived: value.archived,
  };
}

export function readId(event: Event): string {
  if (!(event instanceof CustomEvent)) return "";
  return typeof event.detail === "string" ? event.detail : "";
}

export function readRename(event: Event): { id: string; title: string | null } | null {
  if (!(event instanceof CustomEvent) || !event.detail || typeof event.detail !== "object") return null;
  const value = event.detail as { id?: unknown; title?: unknown };
  if (typeof value.id !== "string") return null;
  if (typeof value.title !== "string" && value.title !== null) return null;
  return { id: value.id, title: value.title };
}

export function readLiveOpen(event: Event): LiveOpenDetail {
  const detail: unknown = event instanceof CustomEvent ? event.detail : null;
  const value = detail && typeof detail === "object" ? detail as { scope?: unknown; active?: unknown; token?: unknown; sessionId?: unknown } : {};
  const token = typeof value.token === "string" && value.token ? value.token : `task-open-${Date.now()}`;
  return {
    scope: typeof value.scope === "string" ? value.scope : "global",
    active: value.active === true,
    token,
    sessionId: typeof value.sessionId === "string" ? value.sessionId : "",
  };
}

export function readAgent(event: Event): AgentOpenDetail | null {
  if (!(event instanceof CustomEvent) || !event.detail || typeof event.detail !== "object") return null;
  const value = event.detail as { id?: unknown; parentId?: unknown; origin?: unknown };
  if (typeof value.id !== "string" || !value.id) return null;
  return {
    id: value.id,
    parentId: typeof value.parentId === "string" ? value.parentId : "",
    origin: readOrigin(value.origin),
  };
}
