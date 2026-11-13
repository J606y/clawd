/** One turn's approximate output. A new LiveRun starts at zero; skill calls do not. */
export interface TurnUsage {
  outputChars: number;
  thinkingChars: number;
  settledThinkingChars: number;
  thinkingText: string;
  argumentChars: number;
  outputTokens: number;
  thinkingTokens: number;
  settledThinkingTokens: number;
  argumentTokens: number;
  available: boolean;
  lastSeq: number;
  calls: Set<string>;
  /** Settled server-side output for this turn, once run.completed arrived; null while estimating. */
  realOutputTokens: number | null;
}

export function createTurnUsage(): TurnUsage {
  return {
    outputChars: 0, thinkingChars: 0, settledThinkingChars: 0, thinkingText: "",
    argumentChars: 0, outputTokens: 0, thinkingTokens: 0, settledThinkingTokens: 0, argumentTokens: 0,
    available: true, lastSeq: -1, calls: new Set(),
    realOutputTokens: null,
  };
}

/**
 * Calibrated character-rate estimate. ASCII stays about 4 characters per token.
 * CJK text (Chinese/Japanese/Korean) runs about 1.5-1.7 characters per token on the
 * SentencePiece/BPE tokenizers these models use, so ~0.6 tokens per character — the
 * old 1.0 rate overcounted Chinese by roughly two thirds. Other non-ASCII
 * (emoji, symbols) still counts as one: they usually cost one token or more each.
 * This only ever estimates VISIBLE text; hidden thinking is not in the text at all,
 * so the running number stays approximate until the server settles the turn.
 */
export function estimateTokens(text: string): number {
  let tokens = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp <= 0x7f) tokens += 0.25;
    else if (
      (cp >= 0x3400 && cp <= 0x4dbf) || (cp >= 0x4e00 && cp <= 0x9fff) || // CJK Unified Ideographs (+Ext A)
      (cp >= 0x3040 && cp <= 0x309f) || (cp >= 0x30a0 && cp <= 0x30ff) || // Hiragana, Katakana
      (cp >= 0xac00 && cp <= 0xd7a3) // Hangul syllables
    ) tokens += 0.6;
    else tokens += 1;
  }
  return tokens;
}

function sameSnapshot(previous: string, next: string): boolean {
  return next.startsWith(previous) || previous.startsWith(next);
}

export function applyTurnUsage(usage: TurnUsage, event: string, data: Record<string, unknown>): void {
  if (event === "assistant.delta" && typeof data.delta === "string") {
    usage.outputChars += data.delta.length;
    usage.outputTokens += estimateTokens(data.delta);
  }
  if (event === "assistant.commentary" && !data.already_streamed && typeof data.text === "string") {
    usage.outputChars += data.text.length;
    usage.outputTokens += estimateTokens(data.text);
  }
  // A growing snapshot replaces itself. A different thought is another model call and adds.
  if (event === "tool.progress" && data.tool_name === "_thinking" && typeof data.delta === "string") {
    const text = data.delta;
    if (usage.thinkingText && !sameSnapshot(usage.thinkingText, text)) {
      usage.settledThinkingChars += usage.thinkingChars;
      usage.settledThinkingTokens += usage.thinkingTokens;
    }
    usage.thinkingText = text;
    usage.thinkingChars = text.length;
    usage.thinkingTokens = estimateTokens(text);
  }
  if (event === "tool.started") {
    const id = data.call_id ?? data.tool_call_id ?? data.id;
    if (typeof id === "string" && usage.calls.has(id)) return;
    if (typeof id === "string") usage.calls.add(id);
    usage.settledThinkingChars += usage.thinkingChars;
    usage.settledThinkingTokens += usage.thinkingTokens;
    usage.thinkingChars = 0;
    usage.thinkingTokens = 0;
    usage.thinkingText = "";
    if (data.args && typeof data.args === "object") {
      const encoded = JSON.stringify(data.args);
      usage.argumentChars += encoded.length;
      usage.argumentTokens += estimateTokens(encoded);
    } else if (typeof data.preview === "string") {
      usage.argumentChars += data.preview.length;
      usage.argumentTokens += estimateTokens(data.preview);
    }
  }
}

/**
 * Session-cumulative usage as the gateway reports it on `run.completed`
 * (gateway/platforms/api_server_runs.py::_run_usage): prompt tokens include cache
 * reads, output tokens are completion tokens and include hidden thinking under the
 * provider's billing (Gemini counts thoughts inside candidates tokens; Anthropic
 * counts thinking inside output tokens). The gateway does NOT report a separate
 * reasoning split for the sub2api path, so a turn total can be exact while its
 * thinking share stays unknown.
 */
export interface ServerRunUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

function validUsageCount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value) && value >= 0;
}

/**
 * Strict parse of a `run.completed` usage payload; null when the output count —
 * the one field the turn total needs — is missing or unusable. Absent input/total/
 * cache fields read as 0; present-but-invalid fields reject the whole payload
 * instead of guessing.
 */
export function parseServerUsage(value: unknown): ServerRunUsage | null {
  if (typeof value !== "object" || value === null) return null;
  const u = value as Record<string, unknown>;
  if (!validUsageCount(u.output_tokens)) return null;
  const read = (key: string): number | null => {
    if (u[key] === undefined) return 0;
    return validUsageCount(u[key]) ? (u[key] as number) : null;
  };
  const inputTokens = read("input_tokens");
  const totalTokens = read("total_tokens");
  const cacheReadTokens = read("cache_read_tokens");
  const cacheWriteTokens = read("cache_write_tokens");
  if (inputTokens === null || totalTokens === null || cacheReadTokens === null || cacheWriteTokens === null) return null;
  return { inputTokens, outputTokens: u.output_tokens as number, totalTokens, cacheReadTokens, cacheWriteTokens };
}

/**
 * This turn's real output: the session counters' output delta between two
 * `run.completed` snapshots. Null without a baseline (first observed run) or when
 * the counters went backwards (session reset/compaction) — the caller keeps the
 * estimate then, never a fabricated number.
 */
export function turnOutputTokens(baseline: ServerRunUsage | null, current: ServerRunUsage): number | null {
  if (!baseline) return null;
  const delta = current.outputTokens - baseline.outputTokens;
  if (!Number.isFinite(delta) || delta < 0) return null;
  return delta;
}

/** Record a settled real output on the turn's usage; null clears it back to estimating. */
export function settleTurnUsage(usage: TurnUsage, outputTokens: number | null): void {
  usage.realOutputTokens = outputTokens;
}

function formatCount(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(count);
}

/** Real server total for a settled turn: no `~`, the number is exact. Invalid or zero counts render nothing, same as the estimate path. */
export function formatRealTokens(count: number): string {
  if (!Number.isFinite(count) || !Number.isSafeInteger(count) || count <= 0) return "";
  return `${formatCount(count)} tokens`;
}

export function formatTurnTokens(usage: TurnUsage | undefined): string {
  // A settled real total stands on its own: it comes from the server's counters,
  // not from the event stream, so a gapped replay cannot take it away.
  const real = usage?.realOutputTokens;
  if (typeof real === "number") return formatRealTokens(real);
  if (!usage?.available) return "";
  const parts = [
    usage.outputChars, usage.thinkingChars, usage.settledThinkingChars, usage.argumentChars,
    usage.outputTokens, usage.thinkingTokens, usage.settledThinkingTokens, usage.argumentTokens,
  ];
  if (parts.some(value => !Number.isFinite(value) || value < 0)) return "";
  const count = Math.round(usage.outputTokens + usage.thinkingTokens + usage.settledThinkingTokens + usage.argumentTokens);
  if (!Number.isSafeInteger(count) || count <= 0) return "";
  return `~${formatCount(count)} tokens`;
}
