import type { LiveRun, Steer } from "./runs.js";
import type { Step } from "./transcript.js";

export type SteerTimelinePart =
  | { kind: "assistant"; steps: Step[]; text: string }
  | { kind: "steer"; steer: Steer };

/** Visual-only boundaries: never change the run's text, tools or steering payload. */
export function splitSteerTimeline(run: Pick<LiveRun, "steps" | "text" | "steers">): SteerTimelinePart[] {
  const parts: SteerTimelinePart[] = [];
  let steps: Step[] = [];
  let text = "";
  let next = 0;
  const boundary = (steer: Steer) => steer.anchor ?? { step: run.steps.length, offset: run.text.length };
  const flush = () => {
    parts.push({ kind: "assistant", steps, text });
    steps = [];
    text = "";
  };
  for (let index = 0; index <= run.steps.length; index++) {
    const step = run.steps[index];
    const source = step?.kind === "note" ? step.text : step ? "" : run.text;
    let offset = 0;
    while (next < run.steers.length && boundary(run.steers[next]!).step <= index) {
      const steer = run.steers[next++]!;
      const end = Math.max(offset, Math.min(source.length, boundary(steer).offset));
      if (end > offset) {
        if (step?.kind === "note") steps.push({ kind: "note", text: source.slice(offset, end) });
        else text += source.slice(offset, end);
      }
      offset = end;
      flush();
      parts.push({ kind: "steer", steer });
    }
    if (step?.kind === "note") {
      // Images belong to the whole persisted note; emit them once, never once per text slice.
      if (offset === 0) steps.push(step);
      else if (offset < source.length || step.images?.length) steps.push({ ...step, text: source.slice(offset) });
    } else if (step) steps.push(step);
    else text += source.slice(offset);
  }
  // A boundary outside the current replay is not evidence that its earlier content vanished.
  // Keep those steering messages visible at the tail until replay catches up.
  for (; next < run.steers.length; next++) {
    flush();
    parts.push({ kind: "steer", steer: run.steers[next]! });
  }
  flush();
  return parts;
}
