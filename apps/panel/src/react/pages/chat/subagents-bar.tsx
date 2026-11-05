import { useEffect, useRef, useState } from "react";
import { useSubagents } from "@/lib/queries";
import { clockAt, holdClock, type HeldClock } from "@/lib/subagent-clock";
import type { Subagent, SubagentsView } from "@/lib/sidecar";
import { relativeTime } from "@/lib/time";
import { toolLabel, toolSummary } from "@/lib/tools";

const STATUS_LABEL: Record<Subagent["status"], string> = {
  running: "进行中",
  stalled: "疑似卡住",
  done: "已完成",
  ended: "已结束",
};

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

function cleanTitle(title: string | null): string {
  return (title ?? "").replace(/^Subagent:\s*/i, "").trim() || "（未命名子任务）";
}

export function SubagentsBar({
  sessionId,
  active,
  compact = false,
  listOnly = false,
  expanded: expandedProp,
  onToggle,
}: {
  sessionId: string;
  active: boolean;
  compact?: boolean;
  listOnly?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
}) {
  const controlled = expandedProp !== undefined;
  const [open, setOpen] = useState<boolean | null>(null);
  const [tick, setTick] = useState(0);
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const [poll, setPoll] = useState(active);
  const heldRef = useRef<HeldClock<SubagentsView> | null>(null);
  const limit = sessionId ? 50 : 12;
  const queryEnabled = listOnly || sessionId.length > 0;
  const query = useSubagents(sessionId, limit, visible && poll && queryEnabled ? (sessionId ? 4000 : 3000) : false, queryEnabled);
  const view = query.data;
  const clientNow = Date.now() / 1000;
  heldRef.current = holdClock(heldRef.current, sessionId, view, clientNow);
  const held = heldRef.current?.scope === sessionId ? heldRef.current : null;
  const shown = held?.view;
  const items = !shown ? [] : sessionId ? shown.data.filter((item) => item.parentId === sessionId) : (listOnly ? shown.data : []);
  const running = items.filter((item) => item.status === "running").length;
  const clock = held ? clockAt(held, clientNow) : clientNow;
  void tick;
  useEffect(() => {
    const onVis = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);
  useEffect(() => {
    if (controlled) return;
    setOpen(null);
  }, [sessionId, controlled]);
  useEffect(() => {
    setPoll(active || running > 0);
  }, [active, running]);
  const spinRef = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const node = spinRef.current;
    if (!node) return;
    const restart = () => {
      node.style.animation = "none";
      void node.offsetWidth;
      node.style.animation = "";
    };
    const slot = node.closest(".slot-agents");
    const onEnd = (event: Event) => {
      const transition = event as TransitionEvent;
      if (transition.target !== slot) return;
      if (transition.propertyName !== "opacity" && transition.propertyName !== "transform") return;
      restart();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") restart();
    };
    slot?.addEventListener("transitionend", onEnd);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      slot?.removeEventListener("transitionend", onEnd);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [running]);
  useEffect(() => {
    if (!visible || running === 0) return;
    const timer = window.setInterval(() => setTick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, [visible, running]);

  const card = (item: Subagent) => {
    const running = item.status === "running";
    const end = item.endedAt ?? (running ? clock : item.lastActivityAt);
    const tool = item.lastTool;
    const title = cleanTitle(item.title);
    return (
      <div className="card" key={item.id}>
        <div className="top">
          {running ? <span className="spin" aria-hidden="true" /> : <span className="dot" data-s={item.status} aria-hidden="true" />}
          <span className="title" title={title}>{title}</span>
          <span className="st" data-s={item.status}>{STATUS_LABEL[item.status]}</span>
          <button
            type="button"
            className="view"
            aria-label="查看这个子代理的对话"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              window.dispatchEvent(new CustomEvent("hp-view-subagent", {
                detail: { ...item, origin: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } },
              }));
            }}
            data-view-agent={item.id}
          >查看</button>
        </div>
        <div className="meta">
          {running ? "已运行 " : "用时 "}{duration(end - item.startedAt)} · 工具调用 {item.toolCalls} 次{item.model ? ` · ${item.model}` : ""}{sessionId || !item.parentTitle ? "" : ` · 来自「${item.parentTitle}」`}
        </div>
        <div className={running && tool ? "tool" : "meta"}>
          {running && tool ? (
            <>正在：{toolLabel(tool.name)}<code>{toolSummary({ kind: "tool", id: "", name: tool.name, args: tool.args, preview: null, status: "running", output: null })}</code></>
          ) : item.status === "stalled" ? (
            `最近一次活动 ${relativeTime(item.lastActivityAt, clock * 1000)}，可能已卡住或被中断`
          ) : "\u00a0"}
        </div>
        {!compact ? <div className="snip" title={item.snippet || undefined}>{item.snippet || "\u00a0"}</div> : null}
      </div>
    );
  };

  if (listOnly) {
    if (query.isError) return <div className="subagents"><div className="meta" role="alert">子代理加载失败，请稍后重试</div></div>;
    if (!shown) return <div className="subagents"><div className="meta" aria-busy="true">加载子代理…</div></div>;
    if (!items.length) return <div className="subagents"><div className="meta">最近没有子代理</div></div>;
    return (
      <div className="subagents">
        <div className="meta">{running ? `${running} 个进行中` : "无进行中"} · {items.length - running} 个已结束</div>
        <div className="list">{items.map(card)}</div>
      </div>
    );
  }
  if (!shown || query.isError || !items.length) return null;
  const expanded = controlled ? Boolean(expandedProp) : Boolean(open);
  const done = items.length - running;
  return (
    <div className="subagents">
      <div className="frame" data-open={expanded ? "" : undefined}>
        <div className="fold" data-open={expanded ? "" : undefined} inert={!expanded}>
          <div><div className="list">{items.map(card)}</div></div>
        </div>
        <button type="button" className="bar" aria-expanded={expanded} onClick={() => { if (controlled) onToggle?.(); else setOpen(!expanded); }}>
          {running ? <span ref={spinRef} className="spin" aria-hidden="true" /> : null}
          <span>子代理 · {running ? `${running} 个进行中` : "无进行中"}{done ? ` · ${done} 个已结束` : ""}</span>
          <svg className="chev" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M2.5 6.5 5 4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  );
}
