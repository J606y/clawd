import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "@/lib/api";
import { useKanbanTask, useKanbanTasks } from "@/lib/queries";
import type { KanbanTask } from "@/lib/sidecar";
import { relativeTime } from "@/lib/time";
import { useUiStore } from "@/stores/ui";
import { usePageVisible } from "@/pages/status/visible";
import "./kanban.css";

const STATUS_LABEL: Record<string, string> = {
  triage: "待分类",
  todo: "待办",
  scheduled: "已排期",
  ready: "就绪",
  running: "进行中",
  blocked: "受阻",
  review: "待审核",
  done: "已完成",
  archived: "已归档",
};

const STATUS_ORDER = ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived"];

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "刷新失败";
}

export function KanbanPage() {
  const visible = usePageVisible();
  const navigate = useNavigate();
  const setSelectedSessionId = useUiStore((state) => state.setSelectedSessionId);
  const [filter, setFilter] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 全量拉一次、前端按状态筛：筛选时各状态的数量 chip 不会丢。
  const tasksQuery = useKanbanTasks(null, visible);
  const detailQuery = useKanbanTask(selectedId, visible);
  const now = Date.now();

  const allTasks = tasksQuery.data?.tasks ?? [];
  const tasks = filter === null ? allTasks : allTasks.filter((task) => task.status === filter);
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const task of allTasks) {
      map.set(task.status, (map.get(task.status) ?? 0) + 1);
    }
    return map;
  }, [allTasks]);
  const failed = tasksQuery.error ?? detailQuery.error;
  const error = failed ? errorText(failed) : "";
  const detail = detailQuery.data ?? null;

  const openSession = (task: KanbanTask) => {
    if (!task.session_id) return;
    setSelectedSessionId(task.session_id);
    navigate("/");
  };

  return (
    <section className="kanban-page" aria-label="看板">
      <div className="page">
        <div className="head">
          <h1>看板</h1>
          {error ? <span className="error">{error}</span> : null}
        </div>
        <p className="sub">看板任务按状态筛选，点一条看详情、评论和事件；有关联对话的可以直接打开。</p>
        <div className="filters" role="group" aria-label="按状态筛选">
          <button
            type="button"
            className={filter === null ? "chip active" : "chip"}
            aria-pressed={filter === null}
            onClick={() => setFilter(null)}
          >
            全部
          </button>
          {STATUS_ORDER.filter((status) => counts.has(status)).map((status) => (
            <button
              key={status}
              type="button"
              className={filter === status ? "chip active" : "chip"}
              aria-pressed={filter === status}
              onClick={() => setFilter(status)}
            >
              {statusLabel(status)}（{counts.get(status) ?? 0}）
            </button>
          ))}
        </div>
        {!tasksQuery.isFetched ? (
          <div className="loading">加载中…</div>
        ) : tasks.length ? (
          <div className="card list">
            {tasks.map((task) => (
              <button
                key={task.id}
                type="button"
                className={selectedId === task.id ? "row selected" : "row"}
                aria-pressed={selectedId === task.id}
                onClick={() => setSelectedId(task.id)}
              >
                <span className="title">{task.title}</span>
                <span className="meta">
                  <span className={`status status-${task.status}`}>{statusLabel(task.status)}</span>
                  {task.assignee ? <span>负责人：{task.assignee}</span> : null}
                  <span>{relativeTime(task.created_at, now)}</span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty">这个状态下没有任务</div>
        )}
        {selectedId ? (
          <div className="detail card">
            {!detailQuery.isFetched ? (
              <div className="loading">加载中…</div>
            ) : detail ? (
              <>
                <div className="detail-head">
                  <h2>{detail.title}</h2>
                  <span className={`status status-${detail.status}`}>{statusLabel(detail.status)}</span>
                </div>
                <dl className="facts">
                  <div><dt>任务</dt><dd>{detail.id}</dd></div>
                  {detail.assignee ? <div><dt>负责人</dt><dd>{detail.assignee}</dd></div> : null}
                  {detail.created_at ? <div><dt>创建</dt><dd>{relativeTime(detail.created_at, now)}</dd></div> : null}
                  {detail.started_at ? <div><dt>开始</dt><dd>{relativeTime(detail.started_at, now)}</dd></div> : null}
                  {detail.completed_at ? <div><dt>完成</dt><dd>{relativeTime(detail.completed_at, now)}</dd></div> : null}
                  {detail.workspace ? <div><dt>工作区</dt><dd>{detail.workspace}</dd></div> : null}
                </dl>
                {detail.body ? <p className="body">{detail.body}</p> : null}
                {detail.session_id ? (
                  <button type="button" className="open-session" onClick={() => openSession(detail)}>
                    打开关联对话
                  </button>
                ) : (
                  <p className="no-session">没有关联对话</p>
                )}
                <h3>评论（{detail.comments.length}）</h3>
                {detail.comments.length ? (
                  <ul className="comments">
                    {detail.comments.map((comment) => (
                      <li key={comment.id}>
                        <span className="author">{comment.author}</span>
                        <span className="time">{relativeTime(comment.created_at, now)}</span>
                        <span className="text">{comment.body}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="empty-line">暂无评论</p>
                )}
                <h3>事件（{detail.events.length}）</h3>
                {detail.events.length ? (
                  <ul className="events">
                    {detail.events.map((event) => (
                      <li key={event.id}>
                        <span className="kind">{event.kind}</span>
                        <span className="time">{relativeTime(event.created_at, now)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="empty-line">暂无事件</p>
                )}
              </>
            ) : (
              <div className="empty">任务不存在或已被删除</div>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
