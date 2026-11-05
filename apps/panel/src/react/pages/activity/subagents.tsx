import { useEffect, useRef, useState } from "react";
import { useSubagents } from "@/lib/queries";
import { runs, RUN_FINISHED } from "@/lib/runs";
import { clockAt, holdClock, type HeldClock } from "@/lib/subagent-clock";
import type { Subagent, SubagentsView } from "@/lib/sidecar";
import { relativeTime } from "@/lib/time";
import { toolLabel, toolSummary } from "@/lib/tools";
import { usePageVisible } from "@/pages/status/visible";
import { useUiStore } from "@/stores/ui";

const STATUS_LABEL: Record<Subagent["status"], string> = {
  running: "进行中",
  stalled: "疑似卡住",
  done: "已完成",
  ended: "已结束",
};

const MOBILE = "(max-width: 768px)";

function elapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

function cleanTitle(title: string | null): string {
  return (title ?? "").replace(/^Subagent:\s*/i, "").trim() || "（未命名子任务）";
}

export function ActivitySubagents() {
  const visible = usePageVisible();
  const setLiveView = useUiStore((state) => state.setLiveView);
  const setDrawerOpen = useUiStore((state) => state.setDrawerOpen);
  const [poll, setPoll] = useState<number | false>(false);
  const [tick, setTick] = useState(0);
  const query = useSubagents("", 12, poll);
  const heldRef = useRef<HeldClock<SubagentsView> | null>(null);
  const clientNow = Date.now() / 1000;
  heldRef.current = holdClock(heldRef.current, "", query.data, clientNow);
  const shown = heldRef.current?.view;
  const running = typeof shown?.running === "number" ? shown.running : null;
  const skipped = useRef(true);

  useEffect(() => {
    if (!visible) {
      setPoll(false);
      return;
    }
    setPoll(running && running > 0 ? 4000 : 15000);
  }, [visible, running]);

  useEffect(() => {
    if (skipped.current) {
      skipped.current = false;
      return;
    }
    if (visible) void query.refetch();
  }, [visible, query.refetch]);

  useEffect(() => {
    const onFinished = () => {
      if (document.visibilityState === "visible") void query.refetch();
    };
    runs.addEventListener(RUN_FINISHED, onFinished);
    return () => runs.removeEventListener(RUN_FINISHED, onFinished);
  }, [query.refetch]);

  useEffect(() => {
    if (!(typeof running === "number" && running > 0 && visible)) return;
    const clock = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(clock);
  }, [running, visible]);

  const viewAgent = (agent: Subagent, target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    setLiveView(null);
    if (window.matchMedia(MOBILE).matches) setDrawerOpen(false);
    target.dispatchEvent(new CustomEvent("hp-view-subagent", {
      detail: { ...agent, origin: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } },
      bubbles: true,
      composed: true,
    }));
  };

  if (query.isError || (shown && typeof shown.running !== "number")) return <div className="hp-subagents" list-only=""><div className="meta" role="alert">子代理加载失败，请稍后重试</div></div>;
  if (!shown) return <div className="hp-subagents" list-only=""><div className="meta" aria-busy="true">加载子代理…</div></div>;

  const view = shown;
  if (!view.data.length) return <div className="hp-subagents" list-only=""><div className="meta">{view.running > 0 ? `${view.running} 个进行中，本页没有条目` : "最近没有子代理"}</div></div>;
  void tick;
  const now = heldRef.current ? clockAt(heldRef.current, clientNow) : clientNow;
  const ended = Math.max(0, (view.total ?? view.data.length) - view.running);

  return (
    <div className="hp-subagents" list-only="">
      <div className="meta">{view.running ? `${view.running} 个进行中` : "无进行中"} · {ended} 个已结束</div>
      <div className="list">
          {view.data.map((agent) => {
            const active = agent.status === "running";
            const end = agent.endedAt ?? (active ? now : agent.lastActivityAt);
            const tool = agent.lastTool;
            return (
              <div className="card" key={agent.id}>
                <div className="top">
                  {active ? <span className="spin" aria-hidden="true" /> : <span className="dot" data-s={agent.status} aria-hidden="true" />}
                  <span className="title" title={cleanTitle(agent.title)}>{cleanTitle(agent.title)}</span>
                  <span className="st" data-s={agent.status}>{STATUS_LABEL[agent.status]}</span>
                  <button
                    type="button"
                    className="view"
                    aria-label="查看这个子代理的对话"
                    data-view-agent={agent.id}
                    onClick={(event) => viewAgent(agent, event.currentTarget)}
                  >
                    查看
                  </button>
                </div>
                <div className="meta">
                  {active ? "已运行 " : "用时 "}
                  {elapsed(end - agent.startedAt)} · 工具调用 {agent.toolCalls} 次
                  {agent.model ? ` · ${agent.model}` : ""}
                  {agent.parentTitle ? ` · 来自「${agent.parentTitle}」` : ""}
                </div>
                <div className={active && tool ? "tool" : "meta"}>
                  {active && tool ? (
                    <>正在：{toolLabel(tool.name)}<code>{toolSummary({ kind: "tool", id: "", name: tool.name, args: tool.args, preview: null, status: "running", output: null })}</code></>
                  ) : agent.status === "stalled" ? (
                    `最近一次活动 ${relativeTime(agent.lastActivityAt, now * 1000)}，可能已卡住或被中断`
                  ) : "\u00a0"}
                </div>
              </div>
            );
          })}
        </div>
    </div>
  );
}
