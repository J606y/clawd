// 进行中回合的"实时状态"文案。纯函数，不碰 DOM，便于单元测试。

import type { RunWait } from "./sidecar.js";

/** Display only evidenced status codes; a generic rate limit is not proof of HTTP 429. */
export function retryReason(summary: string | undefined, httpStatus?: number): string {
  const t = (summary ?? "").toLowerCase();
  const matched = /\b(?:http(?: error)?|status(?: code)?|api error)\s*[:=]?\s*([45]\d\d)\b/i.exec(t);
  const code = Number.isInteger(httpStatus) && httpStatus! >= 400 && httpStatus! <= 599 ? httpStatus : matched ? Number(matched[1]) : null;
  const suffix = code ? ` (${code})` : "";
  if (/usage[ _]limit|quota|billing|spend.limit|额度/.test(t)) return `Usage limit reached${suffix}`;
  if (/cooling|cooldown|model_cooldown/.test(t)) return `Model cooling down${suffix}`;
  if (code === 429 || /rate.?limit|too many/.test(t)) return `Rate limited${suffix}`;
  if (/timed? ?out|timeout|超时/.test(t)) return `Request timed out${suffix}`;
  if ((code && code >= 500) || /overload|unavailable|bad gateway/.test(t)) return `Upstream error${suffix}`;
  return `Request failed${suffix}`;
}

/**
 * 等重试时的一行说明。remaining 由调用方按"服务器给的剩余秒数 + 本地流逝时间"算好传入。
 * attempt 是已经失败的次数（Hermes 日志里的 "attempt 2/3"）：attempt + 1 >= max 时下一次就是最后一次。
 */
export function retryLine(w: RunWait, remainingSeconds: number | null): string {
  const reason = retryReason(w.summary, w.httpStatus);
  const n = w.attempt;
  const max = w.maxAttempts;
  const progress = Number.isInteger(n) && n! > 0 && Number.isInteger(max) && max! >= n! ? ` · attempt ${n}/${max}` : "";
  const when = remainingSeconds === null || !Number.isFinite(remainingSeconds) ? "Retrying…" : remainingSeconds > 0 ? `Retrying in ${Math.ceil(remainingSeconds)}s` : "Awaiting retry…";
  return `${reason} · ${when}${progress}`;
}

/** 事件流静默多久后，开始向服务器查询是不是在等重试。 */
export const SILENT_QUERY_MS = 15_000;
/** 静默多久后，显示"已 N 没有新进展"。 */
export const SILENT_SHOW_MS = 30_000;

/** 没有查到等待原因、但已经静默了一阵时的提示；静默不够久返回空串。 */
export function silentLine(silentMs: number): string {
  if (silentMs < SILENT_SHOW_MS) return "";
  return "Waiting for model response…";
}
