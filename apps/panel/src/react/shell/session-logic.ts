import { ApiError, type SessionSummary } from "@/lib/api.js";
import { isLiveSource } from "@/lib/session-live-retention.js";
import type { SessionFolder } from "@/lib/sidecar.js";
import type { SessionListFilter } from "@/stores/ui";

export const PAGE = 100;
export const HEAD_PAGE = 30;
export const SEARCH_CAP = 1000;
export const LIVE_POLL_MS = 5_000;
export const HEAD_POLL_MS = 30_000;
export const SESSION_ROW = 68;
export const FOLDER_HEADER = 44;
export const FOLDER_CREATE = 40;
export const FOLDER_EDITOR = 108;
export const FOLDER_EMPTY = 36;
export const FOLDER_ERROR = 72;

export const CHAT_SOURCES = [
  "tui",
  "api_server",
  "dashboard",
  "cli",
  "whatsapp",
  "telegram",
  "discord",
  "slack",
  "signal",
  "email",
  "subagent",
] as const;

export const AUTO_SOURCES = ["cron", "webhook"] as const;

const CHAT_SET = new Set<string>(CHAT_SOURCES);
const AUTO_SET = new Set<string>(AUTO_SOURCES);

export const VIEW_SOURCES: Record<SessionListFilter, readonly (string | null)[]> = {
  chats: CHAT_SOURCES,
  auto: AUTO_SOURCES,
  live: [],
};

export const SOURCE_LABEL: Record<string, string> = {
  api_server: "面板",
  tui: "面板",
  dashboard: "面板",
  cli: "终端",
  cron: "定时",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  webhook: "Webhook",
};

const FOLDER_FORBIDDEN = /[\u0000-\u001f\u007f-\u009f\ud800-\udfff\p{Cf}]/u;

export interface SizedRow {
  id: string;
  size: number;
}

export interface ListCursor {
  offset: number;
  hasMore: boolean;
}

export function byRecent(a: SessionSummary, b: SessionSummary): number {
  return (b.last_active ?? b.started_at) - (a.last_active ?? a.started_at);
}

export function accepts(filter: SessionListFilter, source: string | undefined): boolean {
  const src = source ?? "api_server";
  if (filter === "live") return isLiveSource(src);
  return (filter === "chats" ? CHAT_SET : AUTO_SET).has(src);
}

export function dedupeSort(items: readonly SessionSummary[]): SessionSummary[] {
  const seen = new Set<string>();
  const unique: SessionSummary[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    unique.push(item);
  }
  unique.sort(byRecent);
  return unique;
}

export function mergeSessions(
  current: readonly SessionSummary[],
  items: readonly SessionSummary[],
  filter: SessionListFilter,
  deleted: ReadonlySet<string>,
  since: number,
  editedAt: ReadonlyMap<string, number>,
  renamePending: ReadonlySet<string>,
): SessionSummary[] {
  const byId = new Map(current.map((session) => [session.id, session]));
  for (const item of items) {
    if (!accepts(filter, item.source) || deleted.has(item.id)) continue;
    const old = byId.get(item.id);
    if (old && (renamePending.has(item.id) || (editedAt.get(item.id) ?? -1) >= since)) continue;
    byId.set(item.id, old ? { ...old, ...item } : item);
  }
  return [...byId.values()].sort(byRecent);
}

export function visibleSessions(sessions: readonly SessionSummary[], query: string): SessionSummary[] {
  const q = query.trim().toLowerCase();
  const list = q
    ? sessions.filter((item) => `${item.title ?? ""} ${item.preview ?? ""}`.toLowerCase().includes(q))
    : sessions;
  return [...list.filter((item) => item.pinned), ...list.filter((item) => !item.pinned)];
}

export function unfiledSessions(
  sessions: readonly SessionSummary[],
  folders: readonly SessionFolder[],
  assignments: Readonly<Record<string, string>>,
): SessionSummary[] {
  const ids = new Set(folders.map((folder) => folder.id));
  return sessions.filter((session) => {
    const assigned = assignments[session.id];
    return !assigned || !ids.has(assigned);
  });
}

/** 文件夹按归属展示，不受下方列表筛选影响。已加载行优先（本地改名/置顶），归属摘要补齐页外会话；顶部搜索仍生效。 */
export function folderSessionItems(
  loaded: readonly SessionSummary[],
  assigned: readonly SessionSummary[],
  folderId: string,
  assignments: Readonly<Record<string, string>>,
  query: string,
  deleted: ReadonlySet<string> = new Set(),
): SessionSummary[] {
  const byId = new Map<string, SessionSummary>();
  for (const session of assigned) {
    if (!session?.id || assignments[session.id] !== folderId || deleted.has(session.id)) continue;
    byId.set(session.id, session);
  }
  for (const session of loaded) {
    if (assignments[session.id] !== folderId || deleted.has(session.id)) continue;
    byId.set(session.id, session);
  }
  return visibleSessions([...byId.values()], query);
}

export function failMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? `${fallback}：${error.message}` : fallback;
}

export function folderNameError(name: string, folders: readonly SessionFolder[], editorId: string): string {
  const trimmed = name.trim();
  if (!trimmed || [...trimmed].length > 40 || FOLDER_FORBIDDEN.test(trimmed)) {
    return "项目名称需为 1–40 个字符，且不能包含控制字符或不可见字符";
  }
  if (folders.some((folder) => folder.id !== editorId && folder.name.toLocaleLowerCase() === trimmed.toLocaleLowerCase())) {
    return "项目重名，请使用其他名称";
  }
  return "";
}

function offsetOf(rows: readonly SizedRow[], id: string): number | null {
  let start = 0;
  for (const row of rows) {
    if (row.id === id) return start;
    start += row.size;
  }
  return null;
}

/** 滚动离开顶部后，数据插入或重排时把原来正读的那一行留在原处。顶部不补偿，新会话应出现在最上面。 */
export function anchoredScrollTop(
  previous: readonly SizedRow[],
  next: readonly SizedRow[],
  scrollTop: number,
): number | null {
  if (scrollTop <= 1 || previous.length === 0) return null;
  let start = 0;
  let index = 0;
  while (index < previous.length) {
    const size = previous[index]?.size ?? 0;
    if (start + size > scrollTop) break;
    start += size;
    index += 1;
  }
  if (index >= previous.length) {
    index = previous.length - 1;
    start = 0;
    for (let i = 0; i < index; i += 1) start += previous[i]?.size ?? 0;
  }
  const anchor = previous[index];
  if (!anchor) return null;
  const delta = scrollTop - start;
  const direct = offsetOf(next, anchor.id);
  if (direct !== null) return Math.max(0, direct + delta);
  for (let i = index + 1; i < previous.length; i += 1) {
    const id = previous[i]?.id;
    if (!id) continue;
    const found = offsetOf(next, id);
    if (found !== null) return found;
  }
  return null;
}
