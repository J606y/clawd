/** 会话存储只记住旁观画面，不包含接管租约或客户端标识。 */
export const LIVE_VIEW_KEY = "hp-live-view-v1";
export interface LiveView {
  sessionId: string;
  sessionTitle: string;
  sessionSource: string;
}
type ReadStorage = Pick<Storage, "getItem">;
type WriteStorage = Pick<Storage, "setItem" | "removeItem">;

function valid(value: unknown): value is LiveView {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<LiveView>;
  return typeof v.sessionId === "string" && (v.sessionId === "" || /^[A-Za-z0-9_.:@-]{1,200}$/.test(v.sessionId)) &&
    typeof v.sessionTitle === "string" && v.sessionTitle.length <= 300 &&
    typeof v.sessionSource === "string" && v.sessionSource.length <= 100;
}

export function loadLiveView(storage?: ReadStorage): LiveView | null {
  try {
    const raw = (storage ?? window.sessionStorage).getItem(LIVE_VIEW_KEY);
    if (!raw || raw.length > 2000) return null;
    const value: unknown = JSON.parse(raw);
    if (!valid(value) || (value as LiveView & { version?: number }).version !== 1) return null;
    return { sessionId: value.sessionId, sessionTitle: value.sessionTitle, sessionSource: value.sessionSource };
  } catch {
    // 存储被禁用或数据损坏时，不恢复画面；不阻断聊天。
    return null;
  }
}

/** false 表示存储不可用；调用方可报告降级，正常聊天和关闭画面仍然有效。 */
export function saveLiveView(view: LiveView | null, storage?: WriteStorage): boolean {
  if (view !== null && !valid(view)) return false;
  try {
    const store = storage ?? window.sessionStorage;
    if (view === null) store.removeItem(LIVE_VIEW_KEY);
    else store.setItem(LIVE_VIEW_KEY, JSON.stringify({ version: 1, sessionId: view.sessionId, sessionTitle: view.sessionTitle, sessionSource: view.sessionSource }));
    return true;
  } catch {
    return false;
  }
}
