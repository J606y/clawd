// 会话档位的服务器读写。localStorage 不是已有会话的事实来源。
import { ApiError, request } from "./api.js";
import { currentEffort, hydrateSessionEffort, isEffortId } from "./effort.js";

export interface SessionEffort {
  session_id: string;
  effort: string | null;
  source: "pending" | "lock" | "session" | "unset";
  applied: "next_turn" | "stored" | "unset";
  running: boolean | null;
  persisted?: boolean;
}

const loaded = new Map<string, string | null>();
const saves = new Map<string, Promise<SessionEffort>>();
const tails = new Map<string, Promise<void>>();
const appliedTicket = new Map<string, number>();
let ticket = 0;

function takeTicket(): number {
  ticket += 1;
  return ticket;
}

function remember(sessionId: string, view: SessionEffort, ticketNo: number): void {
  if ((appliedTicket.get(sessionId) ?? 0) > ticketNo) return;
  appliedTicket.set(sessionId, ticketNo);
  loaded.set(sessionId, view.effort);
  if (view.effort) hydrateSessionEffort(sessionId, view.effort);
}

function asView(body: unknown, expectEffort: boolean): SessionEffort {
  if (!body || typeof body !== "object") {
    throw new ApiError("server", 0, "服务端没有返回档位");
  }
  const row = body as Partial<SessionEffort>;
  const effort = row.effort == null ? null : String(row.effort);
  if (expectEffort && !isEffortId(effort)) {
    throw new ApiError("server", 0, "服务器没有确认档位，未保存");
  }
  if (effort != null && !isEffortId(effort)) {
    throw new ApiError("server", 0, "服务器返回了无法使用的档位");
  }
  const source = row.source;
  const applied = row.applied;
  if (source !== "pending" && source !== "lock" && source !== "session" && source !== "unset") {
    throw new ApiError("server", 0, "服务端返回了无法解析的档位");
  }
  if (applied !== "next_turn" && applied !== "stored" && applied !== "unset") {
    throw new ApiError("server", 0, "服务端返回了无法解析的档位");
  }
  return {
    session_id: String(row.session_id ?? ""),
    effort,
    source,
    applied,
    running: row.running == null ? null : Boolean(row.running),
    persisted: row.persisted,
  };
}

export function invalidateSessionEffort(sessionId: string): void {
  loaded.delete(sessionId);
}

export async function fetchSessionEffort(sessionId: string): Promise<SessionEffort> {
  const pending = tails.get(sessionId);
  if (pending) await pending.catch(() => undefined);
  const ticketNo = takeTicket();
  const body = await request<unknown>(`api/sessions/${encodeURIComponent(sessionId)}/effort`);
  const view = asView(body, false);
  remember(sessionId, view, ticketNo);
  return view;
}

export function saveSessionEffort(sessionId: string, effort: string): Promise<SessionEffort> {
  if (!isEffortId(effort)) {
    return Promise.reject(new ApiError("server", 0, "不是可用的思考强度，未保存"));
  }
  const run = async (): Promise<SessionEffort> => {
    const ticketNo = takeTicket();
    const body = await request<unknown>(`api/sessions/${encodeURIComponent(sessionId)}/effort`, {
      method: "PUT",
      body: JSON.stringify({ effort }),
    });
    const view = asView(body, true);
    remember(sessionId, view, ticketNo);
    return view;
  };
  const prev = tails.get(sessionId) ?? Promise.resolve();
  const job = prev.then(run, run);
  tails.set(sessionId, job.then(() => undefined, () => undefined));
  saves.set(sessionId, job);
  return job.finally(() => {
    if (saves.get(sessionId) === job) saves.delete(sessionId);
  });
}

/** 发送前确认内存里的档来自服务器。读取失败就抛出，不把本地缓存当成已确认。 */
export async function ensureSessionEffort(sessionId: string): Promise<string> {
  try {
    const saving = saves.get(sessionId);
    if (saving) {
      await saving;
      return currentEffort(sessionId);
    }
    if (!loaded.has(sessionId)) await fetchSessionEffort(sessionId);
    return currentEffort(sessionId);
  } catch (error) {
    if (error instanceof ApiError && error.message.startsWith("档位未能从服务器确认")) throw error;
    const message = error instanceof Error ? error.message : "未知错误";
    throw new ApiError("server", error instanceof ApiError ? error.status : 0, `档位未能从服务器确认，消息未发送：${message}`, error instanceof ApiError ? error.code : undefined);
  }
}
