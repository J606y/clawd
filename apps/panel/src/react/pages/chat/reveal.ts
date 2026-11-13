export interface ProseRegion {
  kind: "prose";
  text: string;
  start: number;
}

export interface BlockRegion {
  kind: "block";
  block: "code" | "table" | "list";
  text: string;
  start: number;
}

export type RevealRegion = ProseRegion | BlockRegion;

const CJK = /[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]/;

const SURROGATE = /[\uD800-\uDBFF]/;

export function codePointLength(text: string): number {
  // 纯 BMP 文本：UTF-16 长度即码点数，免去迭代器 + 数组分配。
  if (!SURROGATE.test(text)) return text.length;
  return [...text].length;
}

export function sliceCodePoints(text: string, count: number): string {
  if (count <= 0) return "";
  // 纯 BMP 文本：按 UTF-16 下标切即按码点切。
  if (!SURROGATE.test(text)) return text.slice(0, count);
  let out = "";
  let seen = 0;
  for (const ch of text) {
    if (seen >= count) break;
    out += ch;
    seen += 1;
  }
  return out;
}

/** 每帧放出字数。积压多时快，追平时变慢，至少 1。 */
export function releaseCount(backlog: number): number {
  if (backlog <= 0) return 0;
  return Math.max(1, Math.ceil(backlog / 12));
}

// 单例 Segmenter：构造开销只付一次（实测单例本身不治本，但没理由每帧重建）。
let sharedSegmenter: Intl.Segmenter | null = null;

function segmenterWords(text: string): string[] {
  if (!sharedSegmenter) sharedSegmenter = new Intl.Segmenter("zh", { granularity: "word" });
  return [...sharedSegmenter.segment(text)].map((part) => part.segment).filter((part) => part.length > 0);
}

function fallbackWords(text: string): string[] {
  const chars = [...text];
  const out: string[] = [];
  let i = 0;
  while (i < chars.length) {
    const ch = chars[i] ?? "";
    if (CJK.test(ch)) {
      let j = i + 1;
      while (j < chars.length && j - i < 3 && CJK.test(chars[j] ?? "")) j += 1;
      if (j - i === 1 && i + 1 < chars.length && CJK.test(chars[i + 1] ?? "")) j = i + 2;
      out.push(chars.slice(i, j).join(""));
      i = j;
      continue;
    }
    if (/\s/u.test(ch)) {
      out.push(ch);
      i += 1;
      continue;
    }
    let j = i + 1;
    while (j < chars.length && !CJK.test(chars[j] ?? "") && !/\s/u.test(chars[j] ?? "")) j += 1;
    out.push(chars.slice(i, j).join(""));
    i = j;
  }
  return out;
}

export function segmentWords(text: string): string[] {
  if (typeof Intl.Segmenter === "function") return segmenterWords(text);
  return fallbackWords(text);
}

export interface SegmentState {
  /** 已分词的文本（words + pending 覆盖的原文前缀）。 */
  text: string;
  /** 已确定的词：不含最后一个未闭合词。 */
  words: string[];
  /** 最后一个词：还可能随新增文本继续变长，渲染时照常显示。 */
  pending: string;
}

/**
 * 增量分词：只对本帧新增的后缀分词，已确定的词直接复用。
 * 最后一个词先留作 pending，下次与新增文本一起重分——词边界变化时
 * 最多只影响末尾一个词的显示。文本不是追加关系时全量重分。
 */
export function segmentWordsIncremental(prev: SegmentState | null, text: string): SegmentState {
  if (!prev || !text.startsWith(prev.text)) {
    const all = segmentWords(text);
    const pending = all.pop() ?? "";
    return { text, words: all, pending };
  }
  if (text.length === prev.text.length) return prev;
  const tail = prev.pending + text.slice(prev.text.length);
  const parts = segmentWords(tail);
  const pending = parts.pop() ?? "";
  return { text, words: [...prev.words, ...parts], pending };
}

export function segmentStateWords(state: SegmentState): string[] {
  return state.pending ? [...state.words, state.pending] : state.words;
}

function lineAt(text: string, index: number): { line: string; end: number } {
  const nl = text.indexOf("\n", index);
  const end = nl === -1 ? text.length : nl + 1;
  return { line: text.slice(index, end), end };
}

function atLineStart(text: string, index: number): boolean {
  return index === 0 || text[index - 1] === "\n";
}

function isTableLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.indexOf("|", 1) > 0;
}

function isListLine(line: string): boolean {
  return /^\s*(?:[-*+]|\d+[.)])\s+\S/.test(line);
}

function isListContinuation(line: string): boolean {
  return line.trim() === "" || /^\s{2,}\S/.test(line);
}

export function splitRegions(text: string): RevealRegion[] {
  const regions: RevealRegion[] = [];
  let i = 0;
  let proseStart = 0;
  const flushProse = (end: number) => {
    if (end <= proseStart) return;
    regions.push({ kind: "prose", text: text.slice(proseStart, end), start: proseStart });
  };
  while (i < text.length) {
    const { line, end } = lineAt(text, i);
    const fence = /^(```|~~~)/.exec(line);
    if (fence && atLineStart(text, i)) {
      flushProse(i);
      const mark = fence[1] ?? "```";
      let j = end;
      while (j < text.length) {
        const next = lineAt(text, j);
        j = next.end;
        if (next.line.startsWith(mark)) break;
      }
      regions.push({ kind: "block", block: "code", text: text.slice(i, j), start: i });
      i = j;
      proseStart = j;
      continue;
    }
    if (isTableLine(line) && atLineStart(text, i)) {
      flushProse(i);
      let j = end;
      while (j < text.length) {
        const next = lineAt(text, j);
        if (!isTableLine(next.line)) break;
        j = next.end;
      }
      regions.push({ kind: "block", block: "table", text: text.slice(i, j), start: i });
      i = j;
      proseStart = j;
      continue;
    }
    if (isListLine(line) && atLineStart(text, i)) {
      flushProse(i);
      let j = end;
      while (j < text.length) {
        const next = lineAt(text, j);
        if (!isListLine(next.line) && !isListContinuation(next.line)) break;
        j = next.end;
      }
      regions.push({ kind: "block", block: "list", text: text.slice(i, j), start: i });
      i = j;
      proseStart = j;
      continue;
    }
    i = end;
  }
  flushProse(text.length);
  return regions;
}

export function anchoredScrollTop(keys: readonly string[], sizes: ReadonlyMap<string, number>, estimate: number, anchorKey: string, viewportOffset: number): number | null {
  const index = keys.indexOf(anchorKey);
  if (index < 0) return null;
  let offset = 0;
  for (let i = 0; i < index; i += 1) offset += sizes.get(keys[i] ?? "") ?? estimate;
  return Math.max(0, offset - viewportOffset);
}
