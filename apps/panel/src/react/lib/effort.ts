// 思考强度：每个对话各记各的。改当前对话时，之后新建的对话从这一档开始，更早的对话不变。
// 已有会话的档位以服务器为准；这里的 localStorage 只缓存显示，并记住还没有会话 id 时的默认档。
// 随每次发送作为 model_options.reasoning_effort 传给 Hermes。取值与 Hermes 的 VALID_REASONING_EFFORTS 一致。
// 非 Gemini、非 Grok 保持六档。Gemini（除 Hydrogen 外）只显示低/中/高。Grok 与 Hydrogen 显示低/中/高/Ultra，第四档发出 xhigh。
// 旧的更高偏好仅在该模型下按有效档执行，不改写存储。
// 每次发送都带明确的值。新对话沿用最近一次选定的档；改某一条只改它和之后新建的对话。
// ultra 是 Hermes 内部的最高档：没有任何上游通道接受它，Hermes 会自动降到该通道支持的最近一档（目前即 max）。

export const EFFORT_CHANGED = "hp-effort-changed";

export interface EffortLevel {
  id: string;
  label: string;
  hint: string;
}

/** 从快到聪明；下标即滑块位置。 */
export const EFFORT_LEVELS: EffortLevel[] = [
  { id: "low", label: "低", hint: "简单问题，最快" },
  { id: "medium", label: "中", hint: "日常使用 · 推荐" },
  { id: "high", label: "高", hint: "复杂任务" },
  { id: "xhigh", label: "超高", hint: "更深入的推理" },
  { id: "max", label: "最高", hint: "最慢，最彻底" },
  // 说明区固定一行高度（见 hp-effort-picker 的 .hint）：太长会被截断，别写成两行的长度。
  { id: "ultra", label: "Ultra", hint: "当前通道上按「最高」执行" },
];

/** Grok 第四档 id 必须是 ultra，动画按这个 id 挂。请求仍由 modelEffort 收成 xhigh。 */
export const GROK_EFFORT_LEVELS: EffortLevel[] = [
  { id: "low", label: "低", hint: "简单问题，最快" },
  { id: "medium", label: "中", hint: "日常使用 · 推荐" },
  { id: "high", label: "高", hint: "复杂任务" },
  { id: "ultra", label: "Ultra", hint: "官方最高档" },
];

/** Hydrogen 第四档 id 必须是 ultra，动画按这个 id 挂。请求仍由 modelEffort 收成 xhigh。 */
export const HYDROGEN_EFFORT_LEVELS: EffortLevel[] = [
  { id: "low", label: "低", hint: "简单问题，最快" },
  { id: "medium", label: "中", hint: "日常使用 · 推荐" },
  { id: "high", label: "高", hint: "复杂任务" },
  { id: "ultra", label: "Ultra", hint: "官方最高档" },
];

export const DEFAULT_EFFORT = "medium";

const KEY = "hp_effort_by_session";
const NEXT_KEY = "hp_effort_next";
/** 记住最近这么多个对话的档位；超出的丢掉最旧的（已删除的对话也靠它自然淡出）。 */
const MAX_ENTRIES = 300;
const events = new EventTarget();

// 内存里始终有一份：浏览器拿不到存储（隐私模式等）时，本次页面内仍然生效。
let bySession: Record<string, string> | null = null;
/** 还没有会话 id 的新对话，以及之后新建对话的起点。改档时更新，建会话后不退回「中」。 */
let draft = readNext();

function readNext(): string {
  try {
    const raw = window.localStorage.getItem(NEXT_KEY);
    if (isLevel(raw)) return raw;
  } catch {
    // 存储不可用：本次页面内用内存里的档。
  }
  return DEFAULT_EFFORT;
}

function writeNext(level: string): void {
  draft = level;
  try {
    window.localStorage.setItem(NEXT_KEY, level);
  } catch {
    // 同上，只在内存里生效。
  }
}

function isLevel(id: unknown): id is string {
  return typeof id === "string" && EFFORT_LEVELS.some((l) => l.id === id);
}

export function isEffortId(id: unknown): id is string {
  return isLevel(id);
}

function load(): Record<string, string> {
  if (bySession) return bySession;
  const loaded: Record<string, string> = {};
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      for (const [sid, level] of Object.entries(parsed)) if (isLevel(level)) loaded[sid] = level;
    }
  } catch {
    // 存储不可用或内容损坏：当作没有记录，全部回落到默认档。
  }
  bySession = loaded;
  return loaded;
}

function remember(sessionId: string, level: string): void {
  const map = load();
  delete map[sessionId]; // 重新插入，让它排到最新
  map[sessionId] = level;
  const keys = Object.keys(map);
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) delete map[k];
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // 同上，只在内存里生效。
  }
}

/** 某个对话当前的档位；sessionId 为空表示还没创建的新对话。 */
export function currentEffort(sessionId = ""): string {
  if (!sessionId) return draft;
  return load()[sessionId] ?? DEFAULT_EFFORT;
}

/** 本地缓存里有没有这个会话的档。没有就返回 null，不用默认档假装服务器确认过。 */
export function cachedSessionEffort(sessionId: string): string | null {
  if (!sessionId) return null;
  return load()[sessionId] ?? null;
}

export function setEffort(id: string, sessionId = ""): void {
  if (!isLevel(id)) return;
  if (sessionId) remember(sessionId, id);
  writeNext(id);
  events.dispatchEvent(new Event(EFFORT_CHANGED));
}

/** 用服务器回读覆盖这个会话的缓存。不改「之后新建对话」的默认档。 */
export function hydrateSessionEffort(sessionId: string, level: string): void {
  if (!sessionId || !isLevel(level)) return;
  remember(sessionId, level);
  events.dispatchEvent(new Event(EFFORT_CHANGED));
}

/** 新对话发出第一条消息、拿到会话 id 后：把当前起点记到这个对话上。起点保持不变，之后新建的对话继续用它。 */
export function adoptDraftEffort(sessionId: string): void {
  if (!sessionId) return;
  remember(sessionId, draft);
  events.dispatchEvent(new Event(EFFORT_CHANGED));
}

export function onEffortChange(fn: () => void): () => void {
  events.addEventListener(EFFORT_CHANGED, fn);
  return () => events.removeEventListener(EFFORT_CHANGED, fn);
}
