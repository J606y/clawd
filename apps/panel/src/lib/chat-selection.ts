export interface ChatModel {
  model: string;
  provider: string;
}

const KEY = "hp_chat_model_by_session";
const NEXT_KEY = "hp_chat_model_next";
const MAX_ENTRIES = 300;

let bySession: Record<string, ChatModel> | null = null;
let next: ChatModel | null | undefined;

function isName(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 200 && !value.includes("\n");
}

function pair(model: unknown, provider: unknown): ChatModel | null {
  return isName(model) && isName(provider) ? { model, provider } : null;
}

function load(): Record<string, ChatModel> {
  if (bySession) return bySession;
  const loaded: Record<string, ChatModel> = {};
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      for (const [sid, value] of Object.entries(parsed)) {
        if (!sid) continue;
        const item = value && typeof value === "object" ? pair((value as ChatModel).model, (value as ChatModel).provider) : null;
        if (item) loaded[sid] = item;
      }
    }
  } catch {
    // 存储不可用或内容损坏：当作没有记录。
  }
  bySession = loaded;
  return loaded;
}

function loadNext(): ChatModel | null {
  if (next !== undefined) return next;
  try {
    const raw = window.localStorage.getItem(NEXT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    const item = parsed && typeof parsed === "object" ? pair((parsed as ChatModel).model, (parsed as ChatModel).provider) : null;
    next = item;
  } catch {
    next = null;
  }
  return next;
}

function persist(map: Record<string, ChatModel>): void {
  const keys = Object.keys(map);
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) delete map[key];
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // 只在内存里生效。
  }
}

/** 某个已有对话记住的模型。读取不改变之后新建对话的起点。 */
export function chatModel(sessionId: string): ChatModel | null {
  if (!sessionId) return null;
  return load()[sessionId] ?? null;
}

/** 之后新建对话使用的模型。没有选过时为空，不回读服务器默认模型。 */
export function nextChatModel(): ChatModel | null {
  return loadNext();
}

/** 记住这个对话的模型，并把它设为之后新建对话的起点。空 sessionId 只更新起点。 */
export function rememberChatModel(sessionId: string, model: string, provider: string): void {
  const item = pair(model, provider);
  if (!item) return;
  if (sessionId) {
    const map = load();
    delete map[sessionId];
    map[sessionId] = item;
    persist(map);
  }
  next = item;
  try {
    window.localStorage.setItem(NEXT_KEY, JSON.stringify(item));
  } catch {
    // 只在内存里生效。
  }
}
