import type { SessionSummary } from "./api.js";

export const LIVE_RETENTION_MS = 10 * 60 * 1000; // 10 minutes (600,000 ms)
export const STORAGE_KEY = "hp_live_retention_v1";
const DEFAULT_MAX_ENTRIES = 100;

/** 实时仅展示用户对话，不展示自动任务和委派的子代理。 */
export function isLiveSource(source: string | undefined): boolean {
  return !["cron", "webhook", "subagent"].includes(source ?? "api_server");
}

export interface RetainedSessionState {
  session: SessionSummary;
  status: "active" | "completed";
  endedAt?: number;
  deadline?: number;
  lastConfirmedActiveAt: number;
  restoredFromActiveCache?: boolean;
}

interface SerializedEntry {
  session: SessionSummary;
  status: "active" | "completed";
  endedAt?: number;
  deadline?: number;
  lastConfirmedActiveAt: number;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface LiveRetentionOptions {
  now?: () => number;
  storage?: StorageLike | null;
  maxEntries?: number;
  retentionMs?: number;
}

export class LiveRetentionTracker {
  private readonly now: () => number;
  private readonly storage: StorageLike | null;
  private readonly maxEntries: number;
  private readonly retentionMs: number;
  private entries = new Map<string, RetainedSessionState>();
  private deletedIds = new Set<string>();

  constructor(options: LiveRetentionOptions = {}) {
    this.now = options.now ?? (() => Date.now());
    if (options.storage !== undefined) {
      this.storage = options.storage;
    } else {
      try {
        this.storage = typeof window !== "undefined" ? window.localStorage ?? null : null;
      } catch {
        // 浏览器安全策略拒绝访问存储时，本页仍使用内存完成保留期。
        this.storage = null;
      }
    }
    this.maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;
    this.retentionMs = options.retentionMs ?? LIVE_RETENTION_MS;

    this.loadFromStorage();
  }

  get size(): number {
    return this.entries.size;
  }

  getEntry(id: string): RetainedSessionState | undefined {
    return this.entries.get(id);
  }

  markDeleted(id: string): void {
    this.deletedIds.add(id);
    this.entries.delete(id);
    this.saveToStorage();
  }

  /**
   * Reconciles active session snapshot with retained sessions.
   * - Active sessions are updated to status: 'active'.
   * - Sessions previously active but not in activeSessions transition to status: 'completed'
   *   with fixed deadline = endedAt + retentionMs (10 min).
   * - If an entry was restored from active cache after a reload and never observed active,
   *   it conservatively uses lastConfirmedActiveAt as fallback endedAt to prevent resurrecting
   *   after long offline intervals.
   * - Sessions whose deadline has passed (now >= deadline) are evicted.
   * - Returns all current retained session summaries.
   */
  reconcile(activeSummaries: SessionSummary[]): SessionSummary[] {
    const currentTime = this.now();
    const activeIds = new Set<string>();

    for (const item of activeSummaries) {
      if (!item || !isLiveSource(item.source) || this.deletedIds.has(item.id)) continue;
      activeIds.add(item.id);
      const existing = this.entries.get(item.id);

      const confirmedTime = Math.max(
        item.last_active ?? item.started_at ?? currentTime,
        existing?.lastConfirmedActiveAt ?? 0,
        currentTime,
      );

      this.entries.set(item.id, {
        session: existing ? { ...existing.session, ...item } : item,
        status: "active",
        lastConfirmedActiveAt: confirmedTime,
        endedAt: undefined,
        deadline: undefined,
        restoredFromActiveCache: false,
      });
    }

    // Check inactive entries
    for (const [id, entry] of this.entries.entries()) {
      if (this.deletedIds.has(id)) {
        this.entries.delete(id);
        continue;
      }

      if (!activeIds.has(id)) {
        if (entry.status === "active") {
          // Transitioning to completed
          let endedAt: number;
          let deadline: number;

          if (entry.restoredFromActiveCache) {
            // Fallback for sessions loaded from cache as active before restart/reload
            endedAt = entry.lastConfirmedActiveAt;
            deadline = endedAt + this.retentionMs;
          } else {
            // First observed inactive by this running tracker
            endedAt = currentTime;
            deadline = currentTime + this.retentionMs;
          }

          if (currentTime >= deadline) {
            // Expired immediately
            this.entries.delete(id);
            continue;
          }

          entry.status = "completed";
          entry.endedAt = endedAt;
          entry.deadline = deadline;
          entry.restoredFromActiveCache = false;
        } else if (entry.status === "completed") {
          // Deadline is fixed, check expiration
          if (entry.deadline !== undefined && currentTime >= entry.deadline) {
            this.entries.delete(id);
            continue;
          }
        }
      }
    }

    this.enforceCapacity();
    this.saveToStorage();

    return Array.from(this.entries.values()).map((e) => e.session);
  }

  private enforceCapacity(): void {
    if (this.entries.size <= this.maxEntries) return;

    // First purge any already expired entries
    const currentTime = this.now();
    for (const [id, entry] of this.entries.entries()) {
      if (entry.deadline !== undefined && currentTime >= entry.deadline) {
        this.entries.delete(id);
      }
    }

    if (this.entries.size <= this.maxEntries) return;

    // If still exceeding, evict completed entries earliest deadline first
    const entriesList = Array.from(this.entries.entries());
    const completed = entriesList.filter(([, e]) => e.status === "completed");
    completed.sort((a, b) => (a[1].deadline ?? 0) - (b[1].deadline ?? 0));

    while (this.entries.size > this.maxEntries && completed.length > 0) {
      const [id] = completed.shift()!;
      this.entries.delete(id);
    }

    // 正在执行的会话由服务端快照决定，不能为缓存容量而隐藏。
  }

  private loadFromStorage(): void {
    if (!this.storage) return;
    try {
      const raw = this.storage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as SerializedEntry[];
      if (!Array.isArray(parsed)) return;

      const currentTime = this.now();
      for (const item of parsed) {
        if (!item || !item.session || !item.session.id || !isLiveSource(item.session.source)) continue;
        const id = item.session.id;

        if (item.status === "completed") {
          if (item.deadline !== undefined && currentTime >= item.deadline) {
            continue; // Already expired
          }
          this.entries.set(id, {
            session: item.session,
            status: "completed",
            endedAt: item.endedAt,
            deadline: item.deadline,
            lastConfirmedActiveAt: item.lastConfirmedActiveAt ?? item.endedAt ?? 0,
            restoredFromActiveCache: false,
          });
        } else if (item.status === "active") {
          // Was active when saved. If user was offline longer than retentionMs,
          // lastConfirmedActiveAt + retentionMs <= currentTime, so will be evicted.
          const fallbackDeadline = (item.lastConfirmedActiveAt ?? 0) + this.retentionMs;
          if (currentTime >= fallbackDeadline) {
            continue; // Stale from long offline interval
          }
          this.entries.set(id, {
            session: item.session,
            status: "active",
            lastConfirmedActiveAt: item.lastConfirmedActiveAt ?? 0,
            restoredFromActiveCache: true,
          });
        }
      }
    } catch {
      // Graceful degradation on storage read error or invalid JSON
    }
  }

  private saveToStorage(): void {
    if (!this.storage) return;
    try {
      const serialized: SerializedEntry[] = Array.from(this.entries.values()).map((e) => ({
        session: e.session,
        status: e.status,
        endedAt: e.endedAt,
        deadline: e.deadline,
        lastConfirmedActiveAt: e.lastConfirmedActiveAt,
      }));
      this.storage.setItem(STORAGE_KEY, JSON.stringify(serialized));
    } catch {
      // Graceful degradation on quota exceeded or storage disabled
    }
  }
}
