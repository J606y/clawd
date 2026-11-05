/** noVNC 由站点根 /live/ 提供。模块只动态加载一次，失败才把 promise 清空。不要把帧或连接放进 Query。 */
const RFB_MODULE = "/live/core/rfb.js";

export interface Rfb extends EventTarget {
  viewOnly: boolean;
  scaleViewport: boolean;
  clipViewport: boolean;
  resizeSession: boolean;
  dragViewport: boolean;
  showDotCursor: boolean;
  dragScrolls?: boolean;
  disconnect(): void;
  sendKey(keysym: number, code: string | null, down?: boolean): void;
}

export type RfbClass = new (target: HTMLElement, url: string, options?: Record<string, unknown>) => Rfb;

interface GestureDetail {
  type: string;
  clientX: number;
  clientY: number;
  magnitudeX?: number;
  magnitudeY?: number;
}

type GestureHost = Rfb & {
  dragScrolls: boolean;
  _handleGesture(ev: CustomEvent<GestureDetail>): void;
};

let rfbClass: Promise<RfbClass> | null = null;

export function clearRfbModule(): void {
  rfbClass = null;
}

/**
 * 只在 noVNC 1.6 有 _handleGesture 时派生子类。没有就用默认手势。
 * 手机接管：单指拖改写成双指滚动；捏合、双指拖、双指轻点直接丢掉，不发远程。
 */
export function loadRfb(): Promise<RfbClass> {
  rfbClass ??= (import(/* @vite-ignore */ new URL(RFB_MODULE, location.origin).href) as Promise<{ default: RfbClass }>).then(({ default: Base }) => {
    const proto = Base.prototype as { _handleGesture?: (ev: CustomEvent<GestureDetail>) => void };
    if (typeof proto._handleGesture !== "function") return Base;
    class PanelRfb extends (Base as unknown as new (...args: ConstructorParameters<RfbClass>) => GestureHost) {
      override dragScrolls = false;
      private origin: { x: number; y: number } | null = null;
      override _handleGesture(ev: CustomEvent<GestureDetail>): void {
        const detail = ev.detail;
        if (!this.dragScrolls) return super._handleGesture(ev);
        if (detail.type === "pinch" || detail.type === "twodrag" || detail.type === "twotap") return;
        if (detail.type !== "drag") return super._handleGesture(ev);
        if (ev.type === "gesturestart") this.origin = { x: detail.clientX, y: detail.clientY };
        const origin = this.origin ?? { x: detail.clientX, y: detail.clientY };
        const rewritten = new CustomEvent<GestureDetail>(ev.type, {
          detail: {
            ...detail,
            type: "twodrag",
            magnitudeX: detail.clientX - origin.x,
            magnitudeY: detail.clientY - origin.y,
          },
        });
        super._handleGesture(rewritten);
        if (ev.type === "gestureend") this.origin = null;
      }
    }
    return PanelRfb as unknown as RfbClass;
  });
  return rfbClass;
}

export function websockifyUrl(): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/live/websockify`;
}
