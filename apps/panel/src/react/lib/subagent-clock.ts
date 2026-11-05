// 子代理运行时钟。本地每秒只沿已接受的服务器时刻往前走，不把刷新用的本地 now 再加一次。

export const STALE_SAMPLE_S = 1;

export interface HeldClock<T extends { now: number }> {
  scope: string;
  view: T;
  serverNow: number;
  clientNow: number;
}

export function observedServerNow(held: HeldClock<{ now: number }>, clientNow: number): number {
  return held.serverNow + (clientNow - held.clientNow);
}

/** 同一范围里，早于已观察时刻达到 1 秒的样本是陈旧响应，不能替换卡片或回拨。范围变化始终接受。 */
export function holdClock<T extends { now: number }>(
  current: HeldClock<T> | null,
  scope: string,
  view: T | undefined,
  clientNow: number,
): HeldClock<T> | null {
  if (!view || !Number.isFinite(view.now) || !Number.isFinite(clientNow)) {
    return current && current.scope === scope ? current : null;
  }
  if (!current || current.scope !== scope) {
    return { scope, view, serverNow: view.now, clientNow };
  }
  if (view === current.view) return current;
  const observed = observedServerNow(current, clientNow);
  if (view.now < current.serverNow || observed - view.now >= STALE_SAMPLE_S) return current;
  return { scope, view, serverNow: view.now, clientNow };
}

export function clockAt(held: HeldClock<{ now: number }>, clientNow: number): number {
  return observedServerNow(held, clientNow);
}
