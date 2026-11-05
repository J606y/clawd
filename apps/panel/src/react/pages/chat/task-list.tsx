import { taskIndicator, taskProgress, type TaskItem } from "@/lib/task-list";

const MARK: Record<TaskItem["status"], string> = {
  pending: "☐",
  in_progress: "◐",
  completed: "☑",
  cancelled: "☐",
};

export function TaskList({
  items,
  expanded = false,
  onToggle,
}: {
  items: readonly TaskItem[];
  expanded?: boolean;
  onToggle?: () => void;
}) {
  if (items.length === 0) return null;
  const { done, open } = taskProgress(items);
  const present = new Set<string>();
  for (const item of items) present.add(item.id);
  return (
    <div className="task-list">
      <div className="frame" data-open={expanded ? "" : undefined}>
        <div className="fold" data-open={expanded ? "" : undefined} inert={!expanded}>
          <div>
            <ul>
              {items.map((item) => {
                const nested = typeof item.parent === "string" && item.parent !== "" && present.has(item.parent);
                const view = taskIndicator(item);
                return (
                  <li
                    key={item.id}
                    data-s={view.markStatus}
                    data-nested={nested ? "" : undefined}
                    aria-label={`${view.label} ${item.content}`}
                  >
                    <span className="mark" data-s={view.markStatus} aria-hidden="true">{MARK[view.markStatus]}</span>
                    <span className="text">{item.content}</span>
                    {view.tag ? <span className="tag">{view.tag}</span> : null}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
        <button type="button" className="bar" aria-expanded={expanded} onClick={onToggle}>
          <span>任务 {done}/{open}</span>
          <svg className="chev" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M2.5 6.5 5 4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  );
}
