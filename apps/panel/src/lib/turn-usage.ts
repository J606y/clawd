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
}

export function createTurnUsage(): TurnUsage {
  return {
    outputChars: 0, thinkingChars: 0, settledThinkingChars: 0, thinkingText: "",
    argumentChars: 0, outputTokens: 0, thinkingTokens: 0, settledThinkingTokens: 0, argumentTokens: 0,
    available: true, lastSeq: -1, calls: new Set(),
  };
}

/** ASCII stays about 4 characters per token. Other characters, including Chinese, count as one. */
export function estimateTokens(text: string): number {
  let tokens = 0;
  for (const ch of text) tokens += (ch.codePointAt(0) ?? 0) <= 0x7f ? 0.25 : 1;
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

export function formatTurnTokens(usage: TurnUsage | undefined): string {
  if (!usage?.available) return "";
  const parts = [
    usage.outputChars, usage.thinkingChars, usage.settledThinkingChars, usage.argumentChars,
    usage.outputTokens, usage.thinkingTokens, usage.settledThinkingTokens, usage.argumentTokens,
  ];
  if (parts.some(value => !Number.isFinite(value) || value < 0)) return "";
  const count = Math.round(usage.outputTokens + usage.thinkingTokens + usage.settledThinkingTokens + usage.argumentTokens);
  if (!Number.isSafeInteger(count) || count <= 0) return "";
  const value = count >= 1000 ? `${(count / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(count);
  return `~${value} tokens`;
}
