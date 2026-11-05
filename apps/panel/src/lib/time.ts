// 全局共享的时钟：所有"x 分钟前"共用一个 30 秒 tick，避免每个组件各起定时器。

type Listener = () => void;
const listeners = new Set<Listener>();
let timer: number | undefined;

export function subscribeTick(fn: Listener): () => void {
  listeners.add(fn);
  if (timer === undefined) timer = window.setInterval(() => listeners.forEach((l) => l()), 30_000);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer !== undefined) {
      window.clearInterval(timer);
      timer = undefined;
    }
  };
}

const dateFmt = new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" });

/** epochSeconds → "刚刚 / 5 分钟前 / 3 小时前 / 昨天 / 10/3" */
export function relativeTime(epochSeconds: number | null | undefined, now = Date.now()): string {
  if (!epochSeconds) return "";
  const diff = Math.max(0, now / 1000 - epochSeconds);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 2 * 86400) return "昨天";
  return dateFmt.format(new Date(epochSeconds * 1000));
}
