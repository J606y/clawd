import { splitLeakedThinking } from "@/lib/transcript";

const MARKER = "THINKING:";
const FRAME = /^THINKING:[\t ]*\r?\n([\s\S]*?)\r?\n\$][\t ]*(?:\r?\n|$)/;
const REJECT = /`|^\s*(?:~~~|>|THINKING:)|<\/?[a-z]/im;

export type LeakedTailView = {
  text: string;
  reasoning: string;
  held: boolean;
};

/** 只有终态或用户停止才释放未闭合原文。审批、澄清、出字暂停都不是结束。 */
export function leakedTailSettled(status: string, stopping: boolean): boolean {
  return stopping || status === "completed" || status === "failed" || status === "cancelled";
}

/**
 * 活动尾部的显示投影。最终分类仍交给 splitLeakedThinking；
 * 起始前缀还可能变成合法思考帧时先不画正文，也不提前放进 Thought。
 * 不缓存原文，也不启动计时器。
 */
export function projectLeakedTail(raw: string, ended: boolean): LeakedTailView {
  if (!raw) return { text: "", reasoning: "", held: false };
  const classified = splitLeakedThinking(raw);
  if (classified.reasoning) return { text: classified.text, reasoning: classified.reasoning, held: false };
  if (ended || !couldStillLeak(raw)) return { text: raw, reasoning: "", held: false };
  return { text: "", reasoning: "", held: true };
}

function couldStillLeak(raw: string): boolean {
  if (raw.length < MARKER.length) return MARKER.startsWith(raw);
  if (!raw.startsWith(MARKER)) return false;
  let index = MARKER.length;
  while (index < raw.length && (raw[index] === " " || raw[index] === "\t")) index += 1;
  if (index >= raw.length) return true;
  const next = raw[index];
  if (next === "\r") {
    if (index + 1 >= raw.length) return true;
    if (raw[index + 1] !== "\n") return false;
    index += 2;
  } else if (next === "\n") {
    index += 1;
  } else {
    return false;
  }
  const match = FRAME.exec(raw);
  if (match) {
    const thought = match[1] ?? "";
    if (REJECT.test(thought)) return false;
    if (match[0].length < raw.length) return false;
    return !match[0].slice(match[0].lastIndexOf("$]") + 2).includes("\n");
  }
  const content = raw.slice(index);
  const committed = content.slice(0, content.length - partialCloseLength(content));
  return !REJECT.test(committed);
}

function partialCloseLength(content: string): number {
  const max = Math.min(content.length, 16);
  for (let length = max; length > 0; length -= 1) {
    if (isPartialClose(content.slice(content.length - length))) return length;
  }
  return 0;
}

function isPartialClose(suffix: string): boolean {
  return suffix === "\r" || /^(?:\r\n|\n)\$(?:\][ \t]*\r?)?$/.test(suffix) || suffix === "\n" || suffix === "\r\n";
}
