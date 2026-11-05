// GPT（Codex 通道）的回合旁白：Hermes 把它并进 reasoning 存库，api_server 历史接口又不返回区分字段，
// 于是由 sidecar 按消息 id 取回，交给 buildTurns 放回正文。只有带 reasoning 的助手消息才可能有旁白。

import type { Message } from "./api.js";
import { sidecar } from "./sidecar.js";

export type Commentary = ReadonlyMap<number, string[]>;

const MAX_IDS = 1000;
const RECENT_WINDOW_S = 60;
const known = new Map<number, string[]>();
/** 已查过且确认没有旁白的 id，避免每次重载重复请求。 */
const none = new Set<number>();

/** 取回这些消息里的旁白。失败时返回已知部分：旁白只是展示增强，不能拖垮历史加载。 */
export async function loadCommentary(messages: Message[], signal?: AbortSignal): Promise<Commentary> {
  if (signal?.aborted) return known;
  const now = Date.now() / 1000;
  const ids = messages
    .filter((m) => m.role === "assistant" && typeof m.id === "number" && m.reasoning && !known.has(m.id) && !none.has(m.id))
    .map((m) => m.id as number)
    .slice(-MAX_IDS);
  if (ids.length) {
    try {
      const { items } = await sidecar.commentary(ids, signal);
      if (signal?.aborted) return known;
      const msgMap = new Map<number, Message>(messages.map((m) => [m.id as number, m]));
      for (const id of ids) {
        const texts = items[String(id)];
        if (texts?.length) {
          known.set(id, texts);
          none.delete(id);
        } else {
          const m = msgMap.get(id);
          const age = m && typeof m.timestamp === "number" ? now - m.timestamp : 0;
          // 仅对产生超过 60s 的历史消息写入持久 none 缓存；刚生成的近期消息不永久锁定，允许收尾或刷新重试。
          if (age >= RECENT_WINDOW_S) {
            none.add(id);
          }
        }
      }
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        throw e;
      }
      // sidecar 不可用时照旧显示（旁白留在思考过程里），下次载入再试。
    }
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
