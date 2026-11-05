// 把 state.db 的扁平消息流整理成可渲染的"回合"：用户消息 + 助手回合（思考、工具步骤、正文）。

import type { ContentPart, Message } from "./api.js";
import { isPlanModeLightNote, parseUserText } from "./attachments.js";

export type StepStatus = "running" | "done" | "failed";

export interface ToolStep {
  kind: "tool";
  id: string;
  name: string;
  args: Record<string, unknown> | null;
  preview: string | null;
  status: StepStatus;
  output: string | null;
  /** 运行中拿到的只是结果预览（服务端截到 500 字以内），回合结束后由完整历史替换。 */
  outputPartial?: boolean;
}

export interface NoteStep {
  kind: "note";
  text: string;
  images?: string[];
}

export type Step = ToolStep | NoteStep;

export interface UserTurn {
  role: "user";
  /** 历史原始消息；实时气泡没有可靠的服务器边界，不能编辑。 */
  message?: Message;
  text: string;
  images: string[];
  files: { name: string; text: string }[];
  uploads: { name: string; absPath: string; size: string; image?: boolean }[];
  /** 入库时被替换成占位符、已无法显示的图片数。 */
  droppedImages: number;
  /** 回合进行中的插话：用户原话，但不是任务起点，不提供编辑。 */
  steer?: boolean;
}

export interface AssistantTurn {
  role: "assistant";
  reasoning: string;
  steps: Step[];
  text: string;
  images?: string[];
  /** 回合起止（epoch 秒）：起点取触发它的用户消息，终点取最后一条助手/工具消息。 */
  startedAt?: number;
  endedAt?: number;
}

/** 回合正文的展示片段：旁白按正文显示，相邻的工具调用合成一组。start 是组内首步在 steps 中的下标，用作稳定的渲染键。 */
export type Segment = { kind: "note"; text: string; images?: string[] } | { kind: "tools"; start: number; steps: ToolStep[] };

const segmentsCache = new WeakMap<Step[], Segment[]>();

export function segments(steps: Step[]): Segment[] {
  const cached = segmentsCache.get(steps);
  if (cached) return cached;
  const out: Segment[] = [];
  steps.forEach((s, i) => {
    if (s.kind === "note") {
      out.push({ kind: "note", text: s.text, ...(s.images?.length ? { images: s.images } : {}) });
      return;
    }
    const last = out.at(-1);
    if (last?.kind === "tools") last.steps.push(s);
    else out.push({ kind: "tools", start: i, steps: [s] });
  });
  segmentsCache.set(steps, out);
  return out;
}

/** 网关自注入的通知（子任务完成、后台命令结束等）：显示成时间线上的一行，原文可展开。 */
export interface NoticeTurn {
  role: "notice";
  title: string;
  failed: boolean;
  /** 被通知事件自身的耗时（秒），不是回合用时。 */
  seconds?: number;
  detail: string;
}

export type Turn = UserTurn | AssistantTurn | NoticeTurn;

/** 整行开头才算脚手架。压缩会把这些块接在人话后面，同句里的引用不能拆。 */
const SCAFFOLD_LINE_PREFIXES = ["[CONTEXT", "[PRIOR CONTEXT", "[Your active task list", "[Planning state preserved", "[IMPORTANT: Background", "[System:", "[Skills pruned", "[ASYNC DELEGATION"] as const;

function scaffoldLine(line: string): boolean {
  const head = line.trimStart();
  return SCAFFOLD_LINE_PREFIXES.some((prefix) => head.startsWith(prefix));
}

/** 压缩/系统脚手架行（给模型看的，不是任何人说的话）的通知标题；不是脚手架返回 undefined。 */
function scaffoldTitle(text: string): string | undefined {
  const head = text.trimStart();
  // agent/context_compressor.py _SYNTHETIC_USER_ROW_PREFIXES（上游原文）；OUT-OF-BAND、Cronjob Response 各有分支。
  // [PRIOR CONTEXT 也会出现在 role=assistant 的压缩行上。
  if (head.startsWith("[ASYNC DELEGATION")) return "后台子任务完成";
  if (head.startsWith("[Skills pruned")) return "技能已裁剪";
  if (["[CONTEXT", "[PRIOR CONTEXT"].some((prefix) => head.startsWith(prefix))) return "上下文已压缩";
  if (head.startsWith("[Your active task list")) return "任务列表已恢复";
  if (head.startsWith("[Planning state preserved")) return "规划状态已恢复";
  if (["[IMPORTANT: Background", "[System:"].some((prefix) => head.startsWith(prefix))) return "系统通知";
  return undefined;
}

/** 人话在前、脚手架从后续整行开始时拆开。整段就是脚手架的仍走原通知分支。 */
function splitAppendedScaffold(text: string): { human: string; scaffold?: string } {
  const lines = text.split("\n");
  let at = -1;
  for (let i = 1; i < lines.length; i++) {
    if (scaffoldLine(lines[i]!)) {
      at = i;
      break;
    }
  }
  if (at < 0) return { human: text };
  const human = lines.slice(0, at).join("\n").trim();
  const scaffold = lines.slice(at).join("\n").trim();
  if (!human || !scaffold || !scaffoldTitle(scaffold)) return { human: text };
  return { human, scaffold };
}

// 插话入库时的包装（api_server 的 steer，display_kind=steer）；只显示用户原话。
const STEER_WRAP = /^\s*\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*([\s\S]*?)\s*(?:\[\/OUT-OF-BAND USER MESSAGE\]\s*)?$/;

/** 旧面板续跑前缀。核心新行用 CLAWD_CONTINUATION_KIND / CLAWD_CONTINUATION_MARK，不靠这段前缀做人话解锁。 */
export const AUTO_CONTINUE_MARK = "[面板自动续跑]";
/** 核心续跑行固定前缀（gateway/clawd_continuation_decision.py MARK）。只用于识别，不是次数门槛。 */
export const CLAWD_CONTINUATION_MARK = "[clawd自动续跑]";
/** 核心续跑行 display_kind。有此 kind 即按续跑展示，不要求正文再匹配成功次数。 */
export const CLAWD_CONTINUATION_KIND = "clawd_continuation";
/** 旧侧车未打 display_kind 时的前缀文本。核心行不依赖这段全文。 */
export const AUTO_CONTINUE_TEXT = `${AUTO_CONTINUE_MARK} 你之前派出的后台子任务已完成，结果见上方的完成通知。请据此继续完成之前的任务；若此前是在等用户确认或回答，就继续等待并说明。`;

function isAutoContinue(m: Message): boolean {
  if (m.role !== "user") return false;
  if (m.display_kind === CLAWD_CONTINUATION_KIND) return true;
  // 其他 display_kind 不是续跑；误标成通用通知的旧入站仍按前缀修复展示。
  if (m.display_kind && !isLegacyInput(m)) return false;
  const text = splitContent(m.content).text.trimStart();
  return text.startsWith(CLAWD_CONTINUATION_MARK) || text.startsWith(AUTO_CONTINUE_MARK);
}

/** 历史中误标为通用通知的 API 原始输入。来源必须由侧车按接收记录精确关联；
 * 专用通知、生产者卡片、隐藏行和协议包装仍保留机器语义。
 * 只修复展示/回合边界，不修改存储，也不授予历史编辑权限。 */
function isLegacyInput(m: Message): boolean {
  if (m.role !== "user" || m.display_kind !== "internal_notification" || m.input_origin !== "api_queue") return false;
  const meta = metadataOf(m);
  if (meta.presentation_suppressed === true || typeof meta.display_text === "string" || meta.reply_expected === false) return false;
  const text = splitContent(m.content).text.trim();
  return !scaffoldTitle(text) && !NOTIFICATION_FOOTER.test(text)
    && !/^(?:\[ASYNC DELEGATION|\[INTERNAL NOTIFICATION|\[OUT-OF-BAND USER MESSAGE|Cronjob Response:|HEARTBEAT\b)/.test(text);
}

/** 原始任务输入的展示分类；不是身份认证。插话、脚手架、自动续跑另有分支。 */
export function isHumanMessage(m: Message): boolean {
  return m.role === "user" && (!m.display_kind || isLegacyInput(m)) && !isAutoContinue(m) && !scaffoldTitle(splitContent(m.content).text);
}

/** 一轮运行的输入行：人写的消息或自动续跑。 */
export function isTurnStart(m: Message): boolean {
  return isHumanMessage(m) || isAutoContinue(m);
}

// 网关追加在通知末尾、写给模型看的来源声明（gateway/run_notifications.py INTERNAL_NOTIFICATION_FOOTER）。
const NOTIFICATION_FOOTER = /\s*\[INTERNAL NOTIFICATION — generated by the Hermes gateway[^\]]*\]\s*$/;

function metadataOf(m: Message): Record<string, unknown> {
  let v: unknown = m.display_metadata;
  if (typeof v === "string") {
    try {
      v = JSON.parse(v);
    } catch {
      return {};
    }
  }
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** null 表示不显示（hidden、被网关压制的诊断通知）。 */
function noticeOf(m: Message, text: string): NoticeTurn | null {
  const meta = metadataOf(m);
  if (m.display_kind === "hidden" || meta.presentation_suppressed === true) return null;
  const detail = text.replace(NOTIFICATION_FOOTER, "").trim();
  const head = detail.split("\n", 1)[0]!;
  const raw = typeof meta.duration_seconds === "number" ? meta.duration_seconds : Number(/Total duration: ([\d.]+)s/.exec(detail)?.[1]);
  const seconds = Number.isFinite(raw) ? raw : undefined;
  if (m.display_kind === "async_delegation_complete" || head.startsWith("[ASYNC DELEGATION")) {
    const failed = head.includes("FAILED") || (typeof meta.failed_count === "number" && meta.failed_count > 0);
    return { role: "notice", title: failed ? "后台子任务失败" : "后台子任务完成", failed, seconds, detail };
  }
  if (m.display_kind === "process_complete") {
    return { role: "notice", title: "后台命令已结束", failed: false, seconds, detail };
  }
  if (isAutoContinue(m)) return { role: "notice", title: "子任务已完成，自动继续", failed: false, detail };
  const title = scaffoldTitle(text);
  return { role: "notice", title: title ?? "系统通知", failed: false, seconds, detail: title ? text : detail };
}

export function splitContent(content: Message["content"]): { text: string; images: string[] } {
  if (content == null) return { text: "", images: [] };
  if (typeof content === "string") return { text: content, images: [] };
  const text: string[] = [];
  const images: string[] = [];
  for (const part of content as ContentPart[]) {
    if (part.type === "text" && "text" in part) text.push(part.text);
    else if (part.type === "image_url" && "image_url" in part) images.push(part.image_url.url);
  }
  return { text: text.join("\n\n"), images };
}

function parseArgs(raw: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(raw);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** 工具结果常见形态 {"output": "...", "exit_code": 0, "error": null}；取人可读部分，否则原样返回。 */
function readableOutput(raw: string): { text: string; failed: boolean } {
  try {
    const v: unknown = JSON.parse(raw);
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const o = v as Record<string, unknown>;
      const failed = Boolean(o.error) || (typeof o.exit_code === "number" && o.exit_code !== 0) || o.success === false;
      if (typeof o.output === "string") {
        const err = typeof o.error === "string" && o.error ? `\n${o.error}` : "";
        return { text: o.output + err, failed };
      }
      return { text: JSON.stringify(v, null, 2), failed };
    }
  } catch {
    // 纯文本结果。
  }
  return { text: raw, failed: false };
}

/**
 * 回合进行中时，服务端已把"用户消息、调用工具的助手消息、工具结果"写进历史，而这一段由实时视图展示。
 * 已完成的上一轮一定以"有正文、没有工具调用的助手消息"收尾；尾部不是这样，就说明末尾有一段进行中的回合，
 * 从最后一条人写的消息起整段裁掉，避免和实时视图重复显示。尾部的网关通知行不参与判断。
 */
export function withoutInFlight(messages: Message[], observedSince?: number): Message[] {
  if (observedSince !== undefined) {
    // 订阅端没有原始 input。保留库里的用户消息，只让 SSE 展示此任务的助手内容。
    // 创建时间由原生状态提供；不把最后一条 user（可能是插话）猜成任务 input；网关通知行也不能当作任务起点。
    const boundary = messages.findIndex((m) => isTurnStart(m) && typeof m.timestamp === "number" && m.timestamp >= observedSince);
    if (boundary >= 0) return [...messages.slice(0, boundary), ...messages.slice(boundary).filter((m) => m.role === "user")];
    // 缺当前输入不代表整页都属本轮：保留明确较早或缺少时间证据的历史。
    return messages.filter((m) => m.role === "user" || typeof m.timestamp !== "number" || !Number.isFinite(m.timestamp) || m.timestamp < observedSince);
  }
  let end = messages.length;
  while (end > 0 && messages[end - 1]!.role === "user" && messages[end - 1]!.display_kind && !isTurnStart(messages[end - 1]!)) end--;
  const last = messages[end - 1];
  if (!last) return messages;
  const content = splitContent(last.content);
  const completedAssistant = last.role === "assistant" && !last.tool_calls?.length && (content.text.trim() !== "" || content.images.length > 0);
  if (completedAssistant) return messages;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (isTurnStart(m)) return messages.slice(0, i);
    // 起点缺失/类型未知时，不能跨过上一条完整回复去裁更早的回合。
    const body = splitContent(m.content);
    if (m.role === "assistant" && !m.tool_calls?.length && (body.text.trim() || body.images.length)) return messages;
  }
  // 分页若只有未完成的助手/工具过程，仍由实时流承载，避免重复。
  // 出现未识别的输入则不能证明整页属于本轮，保留以防误隐藏。
  return messages.some((m) => m.role === "user") ? messages : [];
}

export interface ContinuationState {
  /** 完成通知晚于最后助手回复和最后人话时的那一行。只供既有展示判断，不调度，也不要求人话解锁。 */
  trigger: Message | null;
  /** 成功连续次数不再构成硬停。恒为 false。失败预算、取消、审批等待不在这个字段。 */
  limited: boolean;
}

/**
 * 后台子任务完成后，完成通知是否晚于助手最后一条消息、其后没有人写的消息。
 * 续跑由核心调度；这里只消费已入库行的展示语义，不发起回合，也不读失败次数。
 * 成功次数不是门槛。limited 恒为 false，不因连续续跑或人话重置而翻转。
 */
export function continuationState(messages: Message[]): ContinuationState {
  let lastAssistant = -1;
  let lastHuman = -1;
  for (let i = 0; i < messages.length; i++) {
    if (messages[i]!.role === "assistant") lastAssistant = i;
    if (isHumanMessage(messages[i]!)) lastHuman = i;
  }
  let trigger: Message | null = null;
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]!;
    if (m.display_kind === "async_delegation_complete" && i > lastAssistant && i > lastHuman && typeof m.id === "number" && metadataOf(m).presentation_suppressed !== true) trigger = m;
  }
  return { trigger, limited: false };
}

/** 从 reasoning 里去掉整段出现的旁白（与 Hermes history_commentary 的做法一致：只删独占整行的片段）。 */
function withoutCommentary(reasoning: string, notes: string[]): string {
  let rest = reasoning;
  for (const note of [...new Set(notes)].sort((a, b) => b.length - a.length)) {
    let from = 0;
    let at: number;
    while ((at = rest.indexOf(note, from)) >= 0) {
      const end = at + note.length;
      if ((at === 0 || rest[at - 1] === "\n") && (end === rest.length || rest[end] === "\n")) {
        const before = rest.slice(0, at).replace(/\n+$/, "");
        const after = rest.slice(end).replace(/^\n+/, "");
        rest = before && after ? `${before}\n\n${after}` : before + after;
        from = 0;
      } else {
        from = end;
      }
    }
  }
  return rest.trim();
}

/**
 * 保守的旧文本格式兼容：只拆分消息开头完整成对、各自独占一行的
 * THINKING: … $]。这些文本不是已验证的上游协议，不能用自然语言推测边界。
 * 缺少闭合行、嵌套标记、代码或引用歧义时逐字保留；流式与历史使用同一规则。
 */
const LEAKED_THINKING_CACHE_LIMIT = 8;
const leakedThinkingCache = new Map<string, { reasoning: string; text: string }>();

export function splitLeakedThinking(rawText: string): { reasoning: string; text: string } {
  const cached = leakedThinkingCache.get(rawText);
  if (cached) return cached;

  const unchanged = { reasoning: "", text: rawText };
  const block = /^THINKING:[\t ]*\r?\n([\s\S]*?)\r?\n\$][\t ]*(?:\r?\n|$)/.exec(rawText);
  let result = unchanged;
  if (block && block[1]!.trim()) {
    const thought = block[1]!;
    // 宁可漏识别，也不截断代码/引用示例或嵌套的未知格式。
    if (!/`|^\s*(?:~~~|>|THINKING:)|<\/?[a-z]/im.test(thought)) {
      result = { reasoning: thought, text: rawText.slice(block[0].length) };
    }
  }

  if (leakedThinkingCache.size >= LEAKED_THINKING_CACHE_LIMIT) {
    const oldestKey = leakedThinkingCache.keys().next().value;
    if (oldestKey !== undefined) leakedThinkingCache.delete(oldestKey);
  }
  leakedThinkingCache.set(rawText, result);
  return result;
}

// 渲染身份只用于阅读锚点和DOM复用，不附加可编辑的message引用；WeakMap随回合对象回收。
const turnSources = new WeakMap<object, string>();
export function turnSourceKey(turn: object): string | undefined {
  return turnSources.get(turn);
}

export function buildTurns(messages: Message[], commentary?: ReadonlyMap<number, string[]>, options?: { inFlight?: boolean }): Turn[] {
  const turns: Turn[] = [];
  const byCallId = new Map<string, ToolStep>();
  let current: AssistantTurn | null = null;
  let userAt: number | undefined;
  const append = (turn: Turn, source: Message): void => {
    if (typeof source.id === "number") turnSources.set(turn, `${turn.role}:${source.id}`);
    turns.push(turn);
  };
  const appendScaffoldNotice = (source: Message, scaffold: string): void => {
    const notice = noticeOf(source, scaffold);
    if (!notice) return;
    if (typeof source.id === "number") turnSources.set(notice, `notice:${source.id}:scaffold`);
    turns.push(notice);
  };

  const assistant = (source: Message): AssistantTurn => {
    if (!current) {
      current = { role: "assistant", reasoning: "", steps: [], text: "" };
      if (userAt !== undefined) current.startedAt = userAt;
      append(current, source);
    }
    return current;
  };

  const stepAt = new Map<ToolStep, number>();
  let lastStart = -1;
  messages.forEach((m, i) => { if (isTurnStart(m)) lastStart = i; });

  for (const [index, m] of messages.entries()) {
    const at = typeof m.timestamp === "number" && Number.isFinite(m.timestamp) ? m.timestamp : undefined;
    if (isSteerRow(m)) {
      // 插话：插在本轮中间的用户原话。之后的助手内容另起一段，但用时仍从本轮起点算。
      const raw = splitContent(m.content);
      const parsed = parseUserText(STEER_WRAP.exec(raw.text)?.[1] ?? raw.text);
      const split = splitAppendedScaffold(parsed.text);
      current = null;
      append({ role: "user", text: split.human, images: raw.images, files: parsed.files, uploads: parsed.uploads, droppedImages: parsed.droppedImages, steer: true }, m);
      if (split.scaffold) appendScaffoldNotice(m, split.scaffold);
      continue;
    }
    if ((m.role === "user" || m.role === "assistant") && !m.tool_calls?.length && (isAutoContinue(m) || scaffoldTitle(splitContent(m.content).text))) {
      // 脚手架（含 role=assistant 的压缩上下文）与自动续跑：时间线上一行通知，不是任何人的气泡。
      current = null;
      userAt = at;
      const n = noticeOf(m, splitContent(m.content).text);
      if (n) append(n, m);
      continue;
    }
    if (m.role === "user") {
      // 通知也是回合边界：唤醒类通知之后是助手针对它的新回合，用时从通知算起。
      current = null;
      userAt = at;
      const { text, images } = splitContent(m.content);
      if (m.display_kind && !isLegacyInput(m)) {
        const n = noticeOf(m, text);
        if (n) append(n, m);
        continue;
      }
      const parsed = parseUserText(text);
      const split = splitAppendedScaffold(parsed.text);
      if (!split.human.trim() && !images.length && !parsed.files.length && !parsed.uploads.length && !parsed.droppedImages && isPlanModeLightNote(text.trim())) continue;
      append({ role: "user", ...(isLegacyInput(m) ? {} : { message: m }), text: split.human, images, files: parsed.files, uploads: parsed.uploads, droppedImages: parsed.droppedImages }, m);
      if (split.scaffold) appendScaffoldNotice(m, split.scaffold);
    } else if (m.role === "assistant") {
      const a = assistant(m);
      if (at !== undefined) a.endedAt = at;
      const notes = m.id !== undefined ? (commentary?.get(m.id) ?? []) : [];
      const rawSplit = splitContent(m.content);
      const leaked = splitLeakedThinking(rawSplit.text);
      const text = leaked.text;
      const images = rawSplit.images;
      const combinedReasoning = [m.reasoning, leaked.reasoning].filter(Boolean).join("\n\n");
      const reasoning = combinedReasoning && notes.length ? withoutCommentary(combinedReasoning, notes) : combinedReasoning;
      if (reasoning) a.reasoning = a.reasoning ? `${a.reasoning}\n\n${reasoning}` : reasoning;
      for (const note of notes) {
        // 同一条带边界的内容已由消息正文投影；旁白缓存不能再显示一遍。
        if (leaked.reasoning && note === rawSplit.text) continue;
        a.steps.push({ kind: "note", text: note });
      }
      if (m.tool_calls?.length) {
        if (text.trim() || images.length) a.steps.push({ kind: "note", text, ...(images.length ? { images } : {}) });
        for (const call of m.tool_calls) {
          const step: ToolStep = {
            kind: "tool",
            id: call.id,
            name: call.function.name,
            args: parseArgs(call.function.arguments),
            preview: null,
            status: "running",
            output: null,
          };
          a.steps.push(step);
          byCallId.set(call.id, step);
          stepAt.set(step, index);
        }
      } else {
        if (text.trim()) a.text = a.text ? `${a.text}\n\n${text}` : text;
        if (images.length) a.images = [...(a.images ?? []), ...images];
      }
    } else if (m.role === "tool" && m.tool_call_id) {
      const step = byCallId.get(m.tool_call_id);
      if (current && at !== undefined) (current as AssistantTurn).endedAt = at;
      if (step) {
        const { text, failed } = readableOutput(splitContent(m.content).text);
        step.output = text;
        step.status = failed ? "failed" : "done";
      }
    }
  }
  // 历史里没有结果的调用（被中断）不应永远显示"运行中"。回合仍在进行（inFlight）时，最后一个回合起点之后的
  // 调用还没出结果是正常的——插话、通知行都不结束本轮——只有更早回合里没结果的才算中断。
  for (const step of byCallId.values()) {
    if (step.status === "running" && !(options?.inFlight && (stepAt.get(step) ?? -1) > lastStart)) step.status = "failed";
  }
  return turns;
}

export interface LiveHistoryView {
  steps?: readonly unknown[];
  thinking?: string;
  text?: string;
  remote?: boolean;
  startedAt?: number;
}

export interface HistoryBesideLive {
  messages: Message[];
  stripped: boolean;
  hideDrawnSteers: boolean;
}

function liveOwnsProcess(live: LiveHistoryView | null | undefined): boolean {
  if (!live) return false;
  if ((live.steps?.length ?? 0) > 0) return true;
  if ((live.thinking ?? "").trim()) return true;
  if ((live.text ?? "").trim()) return true;
  return false;
}

function isSteerRow(m: Message): boolean {
  return m.role === "user" && (m.display_kind === "steer" || (m.display_kind === "internal_notification" && m.input_origin === "api_steer" && metadataOf(m).presentation_suppressed !== true));
}

function steerBubbleText(m: Message): string {
  const raw = splitContent(m.content).text;
  return parseUserText(STEER_WRAP.exec(raw)?.[1] ?? raw).text;
}

/** 只去掉本轮起点之后、实时尾栏会再画的插话。上一轮同文，或本轮还没入库的插话，都不占用历史气泡。 */
function omitDrawnSteerBubbles(messages: Message[], turns: Turn[], drawn: readonly { text: string }[]): Turn[] {
  if (!drawn.length) return turns;
  const pending = drawn.map((item) => item.text);
  let start = -1;
  for (let i = 0; i < messages.length; i++) if (isTurnStart(messages[i]!)) start = i;
  const ids = new Set<number>();
  const unkeyed: string[] = [];
  for (let i = messages.length - 1; i > start && pending.length; i--) {
    const row = messages[i]!;
    if (!isSteerRow(row)) continue;
    const at = pending.lastIndexOf(steerBubbleText(row));
    if (at < 0) continue;
    pending.splice(at, 1);
    if (typeof row.id === "number") ids.add(row.id);
    else unkeyed.push(steerBubbleText(row));
  }
  if (!ids.size && !unkeyed.length) return turns;
  const leftover = [...unkeyed];
  return turns.filter((turn) => {
    if (turn.role !== "user" || !turn.steer) return true;
    const key = turnSourceKey(turn);
    if (key?.startsWith("user:")) return !ids.has(Number(key.slice(5)));
    const at = leftover.lastIndexOf(turn.text);
    if (at < 0) return true;
    leftover.splice(at, 1);
    return false;
  });
}

export function shouldStripInFlightHistory(
  live: LiveHistoryView | null | undefined,
  options: { localActive: boolean; ownsHistory: boolean },
): boolean {
  return options.localActive && !options.ownsHistory && liveOwnsProcess(live);
}

/** 实时投影没有步骤、思考和正文时，库里已有的过程不能裁掉。已经有步骤（或思考/正文）时仍走 withoutInFlight。 */
export function historyBesideLive(
  messages: Message[],
  live: LiveHistoryView | null | undefined,
  options: { localActive: boolean; ownsHistory: boolean },
): HistoryBesideLive {
  const strip = shouldStripInFlightHistory(live, options);
  if (!strip) return { messages, stripped: false, hideDrawnSteers: false };
  const observedSince = live?.remote && typeof live.startedAt === "number" ? live.startedAt / 1000 : undefined;
  return {
    messages: withoutInFlight(messages, observedSince),
    stripped: true,
    hideDrawnSteers: observedSince !== undefined,
  };
}

export function visibleTurnsBesideLive(
  messages: Message[],
  live: LiveHistoryView | null | undefined,
  options: { localActive: boolean; ownsHistory: boolean; serverBusy?: boolean; drawnSteers?: readonly { text: string }[] },
  commentary?: ReadonlyMap<number, string[]>,
): Turn[] {
  const view = historyBesideLive(messages, live, options);
  const turns = buildTurns(view.messages, commentary, { inFlight: !view.stripped && (options.localActive || Boolean(options.serverBusy)) });
  return view.hideDrawnSteers ? omitDrawnSteerBubbles(view.messages, turns, options.drawnSteers ?? []) : turns;
}

/** 历史末条助手正文已经包含这段实时正文时返回空，避免实时层再画一遍。 */
export function liveTextBesideHistory(liveText: string, savedText: string): string {
  const streaming = liveText.trim();
  return streaming && !savedText.trimEnd().endsWith(streaming) ? liveText : "";
}
