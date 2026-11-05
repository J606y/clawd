import { memo } from "react";
import { formatBytes } from "@/lib/attachments";
import type { ApprovalChoice } from "@/lib/api";
import type { LiveRun } from "@/lib/runs";
import { splitSteerTimeline } from "@/lib/steer-timeline";
import { AUTO_CONTINUE_MARK, CLAWD_CONTINUATION_KIND, CLAWD_CONTINUATION_MARK } from "@/lib/transcript";
import { ApprovalCard, AssistantBody, NoticeRow, RunStatusLine, StepGroups, UserAttachments, leakedText } from "./turns";
import { leakedTailSettled, projectLeakedTail } from "./leaked-tail";

const STEER_LABEL = { sending: "发送中", sent: "已插话", failed: "插话失败" } as const;

/** 只决定这一行是续跑通知还是用户气泡。不改通知文案、布局、间距或出字动画。 */
function isLiveContinuation(run: LiveRun & { display_kind?: string | null }): boolean {
  if (run.display_kind === CLAWD_CONTINUATION_KIND) return true;
  if (run.display_kind) return false;
  const text = run.userText.trimStart();
  return text.startsWith(CLAWD_CONTINUATION_MARK) || text.startsWith(AUTO_CONTINUE_MARK);
}

function userFiles(run: LiveRun) {
  return {
    images: run.attachments.flatMap((item) => item.kind === "image" ? [item.dataUrl] : []),
    files: run.attachments.flatMap((item) => item.kind === "text" ? [{ name: item.name, text: item.text }] : []),
    uploads: run.attachments.flatMap((item) => {
      if (item.kind === "file") return [{ name: item.name, absPath: item.absPath, size: formatBytes(item.bytes), ...(item.image ? { image: true } : {}) }];
      if (item.kind === "image" && item.absPath) return [{ name: item.name, absPath: item.absPath, size: formatBytes(item.originalBytes ?? item.bytes), image: true }];
      return [];
    }),
    droppedImages: 0,
  };
}

export const LiveTail = memo(function LiveTail({
  run,
  userInHistory,
  pace,
  onApprove,
  skipSteerTexts = [],
}: {
  run: LiveRun;
  userInHistory: boolean;
  pace: boolean;
  onApprove: (choice: ApprovalChoice) => void;
  skipSteerTexts?: readonly string[];
}) {
  const active = run.status === "starting" || run.status === "running";
  const unconfirmed = Boolean(run.unverified && !run.runId);
  const timeline = splitSteerTimeline(run);
  const skipSteers = [...skipSteerTexts];
  const tail = timeline[timeline.length - 1];
  const tailText = tail?.kind === "assistant" ? tail.text : run.text;
  const ended = leakedTailSettled(run.status, run.stopping);
  const projected = projectLeakedTail(tailText, ended);
  const thinking = [run.thinking, projected.reasoning].filter(Boolean).join("\n\n");
  const toolRunning = run.steps.some((step) => step.kind === "tool" && step.status === "running");
  const streaming = Boolean(projected.text.trim());
  const files = userFiles(run);
  const renderEarlier = (part: (typeof timeline)[number], index: number) => {
    if (part.kind === "steer") {
      const at = skipSteers.indexOf(part.steer.text);
      if (at >= 0) { skipSteers.splice(at, 1); return null; }
      return (
        <div key={`steer-${index}`} className="turn user steer">
          <span className="steer-tag" data-s={part.steer.status}>{STEER_LABEL[part.steer.status]}</span>
          <UserAttachments images={[]} files={part.steer.files} uploads={part.steer.uploads} droppedImages={0} />
          {part.steer.text ? <div className="bubble">{part.steer.text}</div> : null}
        </div>
      );
    }
    const partLeak = leakedText(part.text);
    if (!part.steps.length && !partLeak.text.trim() && !partLeak.reasoning) return null;
    return (
      <div key={`pre-${index}`} className="turn assistant live-prefix">
        {partLeak.reasoning ? (
          <details className="reasoning">
            <summary>Thought</summary>
            <div>{partLeak.reasoning}</div>
          </details>
        ) : null}
        <StepGroups steps={part.steps} live={false} />
        <AssistantBody text={partLeak.text} images={[]} streaming={false} pace={false} ownerKey="" />
      </div>
    );
  };
  return (
    <>
      {run.remote || userInHistory ? null : isLiveContinuation(run) ? (
        <NoticeRow turn={{ role: "notice", title: "子任务已完成，自动继续", failed: false, detail: run.userText }} />
      ) : (
        <div className="turn user">
          <UserAttachments {...files} />
          {run.userText ? <div className="bubble">{run.userText}</div> : null}
        </div>
      )}
      {timeline.slice(0, -1).map(renderEarlier)}
      <div className="turn assistant">
        {thinking ? (
          <details className={`reasoning${active && !projected.text.trim() ? " streaming" : ""}`}>
            <summary>Thought</summary>
            <div>{thinking}</div>
          </details>
        ) : null}
        <StepGroups steps={tail?.kind === "assistant" ? tail.steps : []} live={active} />
        <AssistantBody
          text={active ? tailText : projected.text}
          images={[]}
          streaming={active}
          pace={pace}
          releaseHeld={ended}
          ownerKey={`${run.sessionId}:${run.runId}:${run.startedAt}:${timeline.length}`}
        />
        {active && !unconfirmed && !run.approval && !run.clarify ? (
          <div className="run-status-slot">
            <RunStatusLine
              sessionId={run.sessionId}
              runId={run.runId}
              startedAt={run.startedAt}
              lastEventAt={Math.floor(run.lastEventAt / 1000) * 1000}
              stopping={run.stopping}
              streaming={streaming}
              busy={toolRunning}
              tokenLabel={run.streamGone || run.replayTruncated ? "" : run.tokenLabel ?? ""}
            />
          </div>
        ) : null}
        {run.approval ? (
          <ApprovalCard
            description={run.approval.description}
            command={run.approval.command}
            choices={run.approval.choices}
            pending={run.approval.pending}
            onChoose={onApprove}
          />
        ) : null}
        {run.error && active ? <div className="notice err">{run.error}</div> : null}
      </div>
    </>
  );
});
