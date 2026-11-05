export const HISTORY_SYNC_MS = 3000;
export const IDLE_HISTORY_MS = 30_000;

/** 打开的页面要及时发现另一端发起的任务，不能等历史刷新间隔。 */
export function syncPollMs(_active: boolean): number {
  return HISTORY_SYNC_MS;
}

export function historyReloadMs(active: boolean): number {
  return active ? HISTORY_SYNC_MS : IDLE_HISTORY_MS;
}
