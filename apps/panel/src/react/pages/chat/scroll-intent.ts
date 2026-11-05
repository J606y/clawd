function isElement(node: EventTarget): node is HTMLElement {
  return typeof (node as HTMLElement).closest === "function" && typeof (node as HTMLElement).matches === "function";
}

export function disclosureLeavesBottom(nodes: readonly EventTarget[]): boolean {
  const elements = nodes.filter(isElement);
  if (elements.some((node) => node.closest(".subagents") || node.closest(".task-list") || node.closest(".model-picker") || node.closest(".effort-picker") || node.closest(".context-ring"))) return false;
  return elements.some((node) => node.matches("summary, button[aria-expanded], button.more"));
}
