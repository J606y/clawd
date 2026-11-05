// 导航方向契约。push 由 navigate() 在改 hash 之前写入；pop 由 popstate 写入。
// routeMotion 不消费：同一 hash 读两次结果相同，只有完全相等才命中。

export type Motion = "push" | "pop" | "none";

export const NAV_MS = 350;
export const POP_MS = 280;
export const NAV_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";

type Latch = { hash: string; motion: Exclude<Motion, "none"> };

let latch: Latch | null = null;
let installed = false;
let pushAt = 0;
const trail: string[] = [];
let trailAt = -1;

function remember(hash: string): void {
  if (trailAt >= 0 && trail[trailAt] === hash) return;
  trail.splice(trailAt + 1);
  trail.push(hash);
  trailAt = trail.length - 1;
}

export function latchRouteMotion(hash: string, motion: Motion): void {
  if (motion === "none") {
    latch = null;
    return;
  }
  if (motion === "push") {
    pushAt = performance.now();
    remember(hash);
  } else {
    const found = trail.lastIndexOf(hash);
    if (found >= 0) trailAt = found;
  }
  latch = { hash, motion };
}

export function routeMotion(hash = location.hash): Motion {
  if (latch && latch.hash === hash) return latch.motion;
  return "none";
}

/** 锁存命中时和 routeMotion 一样。锁存被伴随的 popstate 盖掉时，按自己记下的来路判返回。 */
export function directionFor(hash = location.hash): Motion {
  const latched = routeMotion(hash);
  if (latched !== "none") return latched;
  if (trailAt > 0) {
    const found = trail.lastIndexOf(hash, trailAt - 1);
    if (found >= 0) {
      trailAt = found;
      return "pop";
    }
  }
  return "none";
}

export function resetRouteMotion(): void {
  latch = null;
  pushAt = 0;
  trail.length = 0;
  trailAt = -1;
}

export function reducedMotion(): boolean {
  if (typeof matchMedia !== "function") return false;
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function activeMotion(motion: Motion, reduced = reducedMotion()): Motion {
  return reduced ? "none" : motion;
}

/** 只注册一次。没有 window 时直接返回。popstate 时 location.hash 已是返回后的地址。 */
export function installRouteMotion(): void {
  if (typeof window === "undefined" || installed) return;
  installed = true;
  if (trailAt < 0) remember(location.hash);
  window.addEventListener("popstate", () => {
    // 设置 location.hash 时，Chromium 会在同一次前进导航里再派发 popstate。
    // 那不是用户返回，不能把刚记下的 push 盖掉。
    if (performance.now() - pushAt < 300) return;
    latchRouteMotion(location.hash, "pop");
  });
}
