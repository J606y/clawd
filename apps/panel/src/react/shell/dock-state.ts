import type { OriginRect } from "./origin-motion";

export interface DockView {
  kind: "live" | "task" | "agent";
  sessionId: string;
  agentId: string;
  key: string;
  origin?: OriginRect | null;
}

export interface DockState {
  current: DockView | null;
  leaving: DockView | null;
  pending: DockView | null;
}

export const EMPTY_DOCK: DockState = { current: null, leaving: null, pending: null };

function sameView(a: DockView | null, b: DockView | null): boolean {
  return !!a && !!b && a.kind === b.kind && a.key === b.key;
}

/** 同一时刻只显示一个。关掉或换窗时旧窗先离场。侧栏由调用方在 NAV_MS 后卸载；移动端子代理等缩回动画结束再卸载。 */
export function reduceDock(state: DockState, next: DockView | null, reduced: boolean): DockState {
  if (reduced) return { current: next, leaving: null, pending: null };
  if (sameView(state.leaving, next) && next) return { current: next, leaving: null, pending: null };
  if (sameView(state.current, next) && next) return { ...state, current: next };
  if (state.current) return { current: null, leaving: state.current, pending: next };
  if (state.leaving) return { ...state, pending: next };
  return { current: next, leaving: null, pending: null };
}

export function finishDockLeave(state: DockState): DockState {
  return { current: state.pending, leaving: null, pending: null };
}
