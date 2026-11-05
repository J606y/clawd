/**
 * 向上弹出的菜单的水平偏移（相对触发按钮左边缘，单位 px）：保证菜单左右各留 16px，
 * 触发按钮靠近屏幕左右边缘（例如手机上放在右下角）时也不会被截断。
 */
export function menuOffset(host: HTMLElement, menuWidth: number): number {
  const w = Math.min(menuWidth, window.innerWidth - 32);
  const left = host.getBoundingClientRect().left;
  return Math.max(16 - left, Math.min(0, window.innerWidth - 16 - w - left));
}

/**
 * 同上，但弹层的**右**边缘对齐到触发按钮所在容器（工具条）的右边缘，返回 CSS `right` 偏移（px，相对按钮右边缘）。
 * 按钮在右对齐的工具条里、且宽度会变（例如标签文字随选择变化）时，以按钮左边缘为锚的弹层会跟着晃；
 * 工具条的右边缘不动，弹层就不动，并且与输入框右边缘对齐。没有父容器时退回按钮自己的右边缘。
 * 左右各留 16px 的规则与 menuOffset 一致。
 */
export function menuRightOffset(host: HTMLElement, menuWidth: number): number {
  const w = Math.min(menuWidth, window.innerWidth - 32);
  const hostRight = host.getBoundingClientRect().right;
  const edge = (host.parentElement ?? host).getBoundingClientRect().right;
  const panelRight = Math.max(16 + w, Math.min(edge, window.innerWidth - 16));
  return hostRight - panelRight;
}
