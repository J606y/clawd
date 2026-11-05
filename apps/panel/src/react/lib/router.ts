// 极简 hash 路由：刷新保持页面，手机返回键可用；不引入路由库。

import { latchRouteMotion } from "./route-motion.js";

export type View = "chat" | "image" | "files" | "activity" | "status" | "settings";

const VIEWS = new Set<View>(["chat", "image", "files", "activity", "status", "settings"]);

export function currentView(): View {
  const v = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  // 旧版"管理"、"定时任务"、"技能"已并入设置：旧书签与历史记录继续可用（`#/admin/<标签>`、`#/jobs`、`#/skills`）。
  if (v === "admin" || v === "jobs" || v === "skills") return "settings";
  return VIEWS.has(v as View) ? (v as View) : "chat";
}

/** `#/settings/usage` 里的第二段（子页）；没有则为空串。 */
export function currentSub(): string {
  const [first, second] = window.location.hash.replace(/^#\/?/, "").split("/");
  // 已并入设置的旧页面：第一段本身就是对应的标签。
  if (first === "jobs" || first === "skills") return first;
  return second ?? "";
}

export function navigate(view: View, sub = ""): void {
  // 对话最终是空串，其它页面是 #/view 或 #/view/sub，必须和写入后的 location.hash 一致。
  const hash = view === "chat" ? "" : sub ? `#/${view}/${sub}` : `#/${view}`;
  if (window.location.hash === hash) return;
  latchRouteMotion(hash, "push");
  if (hash) window.location.hash = hash;
  else history.pushState(null, "", window.location.pathname + window.location.search);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
