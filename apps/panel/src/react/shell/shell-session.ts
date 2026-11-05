import { useSyncExternalStore } from "react";
import type { SessionSummary } from "@/lib/api.js";

let current: SessionSummary | null = null;
let skipRenameBlur = false;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function getShellSession(): SessionSummary | null {
  return current;
}

export function setShellSession(session: SessionSummary | null): void {
  current = session;
  emit();
}

export function useShellSession(): SessionSummary | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getShellSession,
    getShellSession,
  );
}

export function markSkipRenameBlur(): void {
  skipRenameBlur = true;
}

export function consumeSkipRenameBlur(): boolean {
  const skip = skipRenameBlur;
  skipRenameBlur = false;
  return skip;
}
