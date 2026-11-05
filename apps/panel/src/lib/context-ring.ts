export type ContextRingLevel = "ok" | "warn" | "danger" | "full";

/** 超过 60 黄，超过 90 红，100 闭合红。刚好 60、90 仍停在上一档。 */
export function contextRingLevel(percent: number): ContextRingLevel {
  if (percent >= 100) return "full";
  if (percent > 90) return "danger";
  if (percent > 60) return "warn";
  return "ok";
}

export function contextRingStroke(level: ContextRingLevel): string {
  if (level === "warn") return "var(--warning, #ff9f0a)";
  if (level === "danger" || level === "full") return "var(--danger, #ff453a)";
  return "var(--accent, #0a84ff)";
}
