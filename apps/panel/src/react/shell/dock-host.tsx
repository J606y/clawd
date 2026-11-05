import { useLayoutEffect, useRef } from "react";
import { AgentDock, LiveDock, TaskDock } from "@/docks/docks";
import { NAV_EASE, POP_MS, reducedMotion } from "@/lib/route-motion";
import type { DockState, DockView } from "./dock-state";
import { originTransform, pickOrigin, sheetFromOrigin, type OriginRect } from "./origin-motion";

function DockBody(props: { snap: DockView; onClose: () => void }) {
  if (props.snap.kind === "live") return <LiveDock />;
  if (props.snap.kind === "task") return <TaskDock key={props.snap.key} sessionId={props.snap.sessionId} />;
  return <AgentDock sessionId={props.snap.sessionId} agentId={props.snap.agentId} onClose={props.onClose} />;
}

function buttonRects(agentId: string): OriginRect[] {
  if (!agentId) return [];
  const selector = `[data-view-agent="${CSS.escape(agentId)}"]`;
  return [...document.querySelectorAll(selector)].map((node) => {
    const rect = node.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  });
}

function layoutBox(sheet: HTMLElement): OriginRect | null {
  const previous = sheet.style.transform;
  sheet.getAnimations().forEach((anim) => anim.cancel());
  sheet.style.transform = "none";
  const rect = sheet.getBoundingClientRect();
  sheet.style.transform = previous;
  if (rect.width < 1 || rect.height < 1) return null;
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

function play(sheet: HTMLElement, from: string, to: string): Animation {
  sheet.getAnimations().forEach((anim) => anim.cancel());
  return sheet.animate(
    [{ transform: from }, { transform: to }],
    { duration: reducedMotion() ? 0 : POP_MS, easing: NAV_EASE, fill: "both" },
  );
}

export function DockHost(props: {
  state: DockState;
  narrow: boolean;
  onClose: () => void;
  onLeaveSettled?: () => void;
}) {
  const snap = props.state.leaving ?? props.state.current;
  const dialogRef = useRef<HTMLDialogElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const restingRef = useRef<OriginRect | null>(null);
  const settleRef = useRef(props.onLeaveSettled);
  settleRef.current = props.onLeaveSettled;
  const leaving = props.state.leaving !== null;
  const mobileAgent = snap?.kind === "agent" && props.narrow;

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!mobileAgent || !dialog) return;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [mobileAgent, snap?.key]);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    const sheet = sheetRef.current;
    if (!mobileAgent || !dialog || !sheet || leaving) return;
    if (!dialog.open) dialog.showModal();
    const resting = layoutBox(sheet);
    if (!resting) return;
    restingRef.current = resting;
    const origin = snap?.origin ?? null;
    const motion = origin && !reducedMotion() ? sheetFromOrigin(resting, origin) : null;
    if (!motion) return;
    play(sheet, originTransform(motion), "none");
  }, [mobileAgent, snap?.key, snap?.origin, leaving]);

  useLayoutEffect(() => {
    if (!leaving || !mobileAgent) return;
    const sheet = sheetRef.current;
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      settleRef.current?.();
    };
    if (!sheet) {
      settle();
      return;
    }
    if (reducedMotion()) return;
    const resting = restingRef.current ?? layoutBox(sheet);
    const origin = pickOrigin(buttonRects(snap?.agentId ?? ""), snap?.origin ?? null);
    if (!resting || !origin) {
      settle();
      return;
    }
    restingRef.current = resting;
    const motion = sheetFromOrigin(resting, origin);
    if (!motion) {
      settle();
      return;
    }
    const computed = getComputedStyle(sheet).transform;
    const from = !computed || computed === "none" ? "none" : computed;
    const anim = play(sheet, from, originTransform(motion));
    anim.addEventListener("finish", settle);
    // cancel() rejects finished with AbortError; other failures remain visible.
    void anim.finished.then(settle, (error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        console.error("子代理关闭动画失败", error);
      }
    });
    return () => {
      settled = true;
      anim.cancel();
    };
  }, [leaving, mobileAgent, snap?.key, snap?.agentId, snap?.origin]);

  if (!snap) return null;
  if (mobileAgent) {
    return (
      <dialog
        ref={dialogRef}
        className="agent-modal"
        data-leave={leaving ? "" : undefined}
        aria-label="子代理详情"
        onCancel={(event) => {
          event.preventDefault();
          props.onClose();
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) props.onClose();
        }}
      >
        <div ref={sheetRef} className="agent-sheet">
          <AgentDock sessionId={snap.sessionId} agentId={snap.agentId} onClose={props.onClose} />
        </div>
      </dialog>
    );
  }
  return (
    <div className="side-dock" data-kind={snap.kind} data-leaving={leaving ? "" : undefined}>
      <DockBody snap={snap} onClose={props.onClose} />
    </div>
  );
}
