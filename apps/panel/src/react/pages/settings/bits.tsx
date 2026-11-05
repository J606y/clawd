import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { ApiError, type SessionSummary } from "@/lib/api";
import { subscribeTick } from "@/lib/time";
import { useUiStore } from "@/stores/ui";


export function errText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function usePageVisible(): boolean {
  return useSyncExternalStore(
    (onStoreChange) => {
      document.addEventListener("visibilitychange", onStoreChange);
      return () => document.removeEventListener("visibilitychange", onStoreChange);
    },
    () => document.visibilityState === "visible",
    () => true,
  );
}

export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => subscribeTick(() => setNow(Date.now())), []);
  return now;
}

export function openSession(from: HTMLElement, session: SessionSummary, navigate: (path: string) => void): void {
  from.dispatchEvent(new CustomEvent<SessionSummary>("hp-open-session", { detail: session, bubbles: true, composed: true }));
  useUiStore.getState().setSelectedSessionId(session.id);
  navigate("/");
}

export function Head({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="head">
      <h1>{title}</h1>
      {children}
    </div>
  );
}

export function Notice({ text, bad }: { text: string; bad?: boolean }) {
  if (!text) return null;
  const failed = bad ?? (text.includes("失败") || text.includes("无法"));
  return <div className={failed ? "notice error" : "notice ok"} role="status">{text}</div>;
}

export function LoadError({ message, onRetry, retryLabel = "重试" }: { message: string; onRetry: () => void; retryLabel?: string }) {
  return (
    <div className="empty">
      {message}
      <br />
      <button type="button" className="link" onClick={onRetry}>{retryLabel}</button>
    </div>
  );
}

export function Loading({ text = "加载中…" }: { text?: string }) {
  return <div className="loading">{text}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export const fieldClass = "";
export const areaClass = "";

export function Switch({
  checked,
  disabled,
  label,
  labelledBy,
  onClick,
}: {
  checked: boolean;
  disabled?: boolean;
  label?: string;
  labelledBy?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={onClick}
      className="switch"
    />
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className="muted">{children}</span>;
}
