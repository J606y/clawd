// 回合旁白来自会话消息上的 display_commentary。不再按消息 id 向侧车跨会话取数。

import type { Message } from "./api.js";

export type Commentary = ReadonlyMap<number, string[]>;

const RECENT_WINDOW_S = 60;
const known = new Map<number, string[]>();
/** 已确认没有旁白的 id，避免把空投影反复当未查。 */
const none = new Set<number>();

/** 从服务端投影收集旁白。缺字段时不另开请求：失败不能拖垮历史，也不授权跨会话读取。 */
export async function loadCommentary(messages: Message[], signal?: AbortSignal): Promise<Commentary> {
  if (signal?.aborted) return known;
  const now = Date.now() / 1000;
  for (const message of messages) {
    if (message.role !== "assistant" || typeof message.id !== "number" || !Array.isArray(message.display_commentary)) continue;
    const texts = message.display_commentary.filter((text): text is string => typeof text === "string" && text.length > 0);
    if (texts.length) {
      known.set(message.id, texts);
      none.delete(message.id);
      continue;
    }
    const age = typeof message.timestamp === "number" ? now - message.timestamp : 0;
    if (age >= RECENT_WINDOW_S) none.add(message.id);
  }
  return known;
}

export function invalidateCommentary(id?: number): void {
  if (id !== undefined) {
    none.delete(id);
    known.delete(id);
  } else {
    none.clear();
    known.clear();
  }
}
