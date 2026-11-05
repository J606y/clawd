import { useState } from "react";
import { useNavigate } from "react-router";
import type { HealthDetailed, SessionSummary } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useActivity, useHealthDetailed } from "@/lib/queries";
import type { ActivityItem } from "@/lib/sidecar";
import { relativeTime } from "@/lib/time";
import { toolLabel, toolSummary } from "@/lib/tools";
import { useUiStore } from "@/stores/ui";
import { ActivitySubagents } from "./subagents";
import { usePageVisible } from "@/pages/status/visible";
import "./activity.css";

const SOURCE_LABEL: Record<string, string> = {
  api_server: "面板",
  tui: "面板",
  cli: "终端",
  cron: "定时",
  webhook: "Webhook",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  subagent: "子任务",
};

interface Queues {
  active_api_runs?: number;
  active_delegations?: number;
  active_children?: number | null;
  process_completions?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function countText(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "不可用";
}

function readQueues(health: HealthDetailed | undefined): Queues | null {
  const raw = health?.readiness?.checks?.background_queues;
  if (!isRecord(raw)) return null;
  return {
    active_api_runs: typeof raw.active_api_runs === "number" ? raw.active_api_runs : undefined,
    active_delegations: typeof raw.active_delegations === "number" ? raw.active_delegations : undefined,
    active_children: typeof raw.active_children === "number" ? raw.active_children : null,
    process_completions: typeof raw.process_completions === "number" ? raw.process_completions : undefined,
  };
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "刷新失败";
}

function statusName(status: ActivityItem["status"]): string {
  if (status === "failed") return "失败";
  if (status === "pending") return "进行中";
  return "完成";
}

export function ActivityPage() {
  const visible = usePageVisible();
  const navigate = useNavigate();
  const setSelectedSessionId = useUiStore((state) => state.setSelectedSessionId);
  const activityQuery = useActivity(80, visible);
  const healthQuery = useHealthDetailed(visible);
  const loaded = activityQuery.isFetched && healthQuery.isFetched;
  const failed = activityQuery.error ?? healthQuery.error;
  const error = failed ? errorText(failed) : "";
  const items = activityQuery.data ?? [];
  const queues = readQueues(healthQuery.data);
  const [sessionsOpen, setSessionsOpen] = useState(true);
  const [subagentsOpen, setSubagentsOpen] = useState(false);
  const now = Date.now();

  const openSession = (item: ActivityItem) => {
    const summary: SessionSummary = {
      id: item.sessionId,
      source: item.source ?? "",
      title: item.title,
      model: null,
      started_at: item.timestamp,
      last_active: item.timestamp,
      message_count: 0,
      preview: null,
      pinned: false,
      archived: false,
    };
    setSelectedSessionId(item.sessionId);
    navigate("/");
    return summary;
  };

  return (
    <section className="activity-page" aria-label="活动">
      <div className="page">
        <div className="head"><h1>活动</h1>{error ? <span className="error">{error}</span> : null}</div>
        {queues ? (
          <div className="now">
            <div className="card"><div className="k">面板/接口进行中的回合</div><div className="v">{queues.active_api_runs ?? 0}</div></div>
            <div className="card"><div className="k">进行中的子代理</div><div className="v">{countText(queues.active_children)}</div></div>
            <div className="card"><div className="k">完成单元</div><div className="v">{countText(queues.active_delegations)}</div></div>
            <div className="card"><div className="k">后台进程待收尾</div><div className="v">{queues.process_completions ?? 0}</div></div>
          </div>
        ) : null}
        <details
          className="activity-section"
          id="sessions-section"
          open={sessionsOpen}
          onToggle={(event) => setSessionsOpen(event.currentTarget.open)}
        >
          <summary aria-label="展开或折叠会话活动">会话活动</summary>
          <div className="section-body">
            <p className="sub">所有会话（面板、定时任务、WhatsApp 等）最近的工具调用，点一条可打开对应对话。</p>
            {!loaded ? (
              <div className="loading">加载中…</div>
            ) : items.length ? (
              <div className="card list">
                {items.map((item, index) => (
                  <button
                    type="button"
                    className="item"
                    key={`${item.sessionId}-${item.timestamp}-${item.tool}-${index}`}
                    onClick={(event) => {
                      const summary = openSession(item);
                      event.currentTarget.dispatchEvent(new CustomEvent("hp-open-session", { detail: summary, bubbles: true, composed: true }));
                    }}
                  >
                    <span className="dot" data-s={item.status} aria-label={statusName(item.status)} />
                    <span className="what">
                      <span>
                        <b>{toolLabel(item.tool)}</b>
                        <code>{toolSummary({ kind: "tool", id: "", name: item.tool, args: item.args, preview: null, status: "done", output: null })}</code>
                      </span>
                      <small>{SOURCE_LABEL[item.source ?? ""] ?? item.source ?? ""} · {item.title || item.sessionId}</small>
                    </span>
                    <span className="when">{relativeTime(item.timestamp, now)}</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="empty">最近没有工具调用</div>
            )}
          </div>
        </details>
        <details
          className="activity-section"
          id="subagents-section"
          open={subagentsOpen}
          onToggle={(event) => setSubagentsOpen(event.currentTarget.open)}
        >
          <summary aria-label="展开或折叠子代理">子代理</summary>
          <div className="section-body">
            <ActivitySubagents />
          </div>
        </details>
      </div>
    </section>
  );
}
