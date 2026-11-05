import { useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import type { TaskItem } from "@/lib/task-list";
import { reduceComposerPanel, type ComposerPanel } from "./composer-panel-state";
import { SubagentsBar } from "./subagents-bar";
import { TaskList } from "./task-list";

export function ComposerPanels({
  sessionId,
  active,
  tasks,
  taskDockRef,
  children,
}: {
  sessionId: string;
  active: boolean;
  tasks: readonly TaskItem[];
  taskDockRef?: Ref<HTMLDivElement | null>;
  children?: ReactNode;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [panel, setPanel] = useState<ComposerPanel>("idle");
  const [seen, setSeen] = useState(sessionId);
  if (seen !== sessionId) {
    setSeen(sessionId);
    setPanel("idle");
  }
  useEffect(() => {
    const slot = rootRef.current?.closest(".chat-slot");
    if (!slot) return;
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const wasHidden = record.oldValue !== null;
        const nowHidden = slot.hasAttribute("hidden");
        if (wasHidden !== nowHidden) setPanel("idle");
      }
    });
    observer.observe(slot, { attributes: true, attributeFilter: ["hidden"], attributeOldValue: true });
    return () => observer.disconnect();
  }, []);
  const toggle = (name: "tasks" | "subagents") => {
    setPanel((current) => reduceComposerPanel(current, { type: "toggle", panel: name }));
  };
  const open = panel === "tasks" || panel === "subagents";
  return (
    <>
      <div className="composer-panels" ref={rootRef}>
        {tasks.length > 0 ? <div className="task-dock" ref={taskDockRef} aria-hidden="true" /> : null}
        <div className="agent-slot" aria-hidden="true" />
        <div className="panel-shell" data-open={open ? "" : undefined}>
          <div className="slot-tasks">
            {tasks.length > 0 ? (
              <TaskList items={tasks} expanded={panel === "tasks"} onToggle={() => toggle("tasks")} />
            ) : null}
          </div>
          <div className="slot-agents">
            <SubagentsBar
              sessionId={sessionId}
              active={active}
              expanded={panel === "subagents"}
              onToggle={() => toggle("subagents")}
            />
          </div>
        </div>
      </div>
      {children}
    </>
  );
}
