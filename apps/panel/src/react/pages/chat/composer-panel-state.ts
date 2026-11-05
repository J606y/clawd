export type ComposerPanel = "idle" | "tasks" | "subagents" | "dismissed";

export type ComposerPanelEvent =
  | { type: "reset" }
  | { type: "toggle"; panel: "tasks" | "subagents" }
  | { type: "auto-subagents" };

/** 一个联合状态。手动选择后进入 dismissed，进行中轮询不能再把列表抢开。 */
export function reduceComposerPanel(state: ComposerPanel, event: ComposerPanelEvent): ComposerPanel {
  if (event.type === "reset") return "idle";
  if (event.type === "auto-subagents") return state === "idle" ? "subagents" : state;
  return state === event.panel ? "dismissed" : event.panel;
}
