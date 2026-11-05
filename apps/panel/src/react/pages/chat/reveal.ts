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

export function codePointLength(text: string): number {
  return [...text].length;
}

export function sliceCodePoints(text: string, count: number): string {
  if (count <= 0) return "";
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

function segmenterWords(text: string): string[] {
  const segmenter = new Intl.Segmenter("zh", { granularity: "word" });
  return [...segmenter.segment(text)].map((part) => part.segment).filter((part) => part.length > 0);
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
