import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ApprovalChoice, Message, OutgoingContent } from "./api.js";
import type { Attachment } from "./attachments.js";
import type { ClarifyAnswer } from "./clarify.js";
import { RUN_CHANGED, RUN_FINISHED, runs, type LiveRun } from "./runs.js";
import type { SessionRunState } from "./sidecar.js";

export interface UseLiveRun {
  run: LiveRun | null;
  isActive: boolean;
  start: (
    sessionId: string,
    text: string,
    attachments: Attachment[],
    content: OutgoingContent,
    model?: string,
    provider?: string,
    accepted?: { run_id: string; status: string },
  ) => void;
  stop: (sessionId: string) => Promise<void>;
  steer: (sessionId: string, text: string, attachments?: Attachment[]) => Promise<boolean>;
  respondApproval: (sessionId: string, choice: ApprovalChoice) => Promise<void>;
  respondClarify: (sessionId: string, answers: ClarifyAnswer[]) => Promise<void>;
  recover: (sessionId: string) => Promise<boolean>;
  observe: (sessionId: string, runId: string, startedAt: number, stopping?: boolean) => boolean;
  reconcile: (sessionId: string, state?: SessionRunState) => Promise<void>;
  clear: (sessionId: string) => void;
  restoreSteers: (sessionId: string, messages: Message[]) => void;
  isRestoredLocal: (sessionId: string, runId: string) => boolean;
}

function sessionDetail(event: Event): string {
  return event instanceof CustomEvent && typeof event.detail === "string" ? event.detail : "";
}

function snapshot(run: LiveRun | undefined): LiveRun | null {
  return run ? { ...run } : null;
}

let resumedIds: string[] | null = null;

export function useLiveRun(sessionId: string): UseLiveRun {
  "use no memo";
  // Each mounted hook owns its snapshot and listeners. No session cache survives
  // unmount, and subscribe refreshes changes occurring before React subscribes.
  const source = useMemo(() => {
    const read = () => ({ run: snapshot(runs.get(sessionId)), isActive: runs.isActive(sessionId) });
    let value = read();
    return {
      getSnapshot: () => value,
      subscribe: (onStoreChange: () => void) => {
        const changed = (event: Event) => {
          if (sessionDetail(event) !== sessionId) return;
          value = read();
          onStoreChange();
        };
        runs.addEventListener(RUN_CHANGED, changed);
        runs.addEventListener(RUN_FINISHED, changed);
        value = read();
        return () => {
          runs.removeEventListener(RUN_CHANGED, changed);
          runs.removeEventListener(RUN_FINISHED, changed);
        };
      },
    };
  }, [sessionId]);
  const state = useSyncExternalStore(source.subscribe, source.getSnapshot);
  return {
    run: state.run,
    isActive: state.isActive,
    start: (id, text, attachments, content, model = "", provider = "", accepted) => {
      runs.start(id, text, attachments, content, model, provider, accepted);
    },
    stop: (id) => runs.stop(id),
    steer: (id, text, attachments = []) => runs.steer(id, text, attachments),
    respondApproval: (id, choice) => runs.respondApproval(id, choice),
    respondClarify: (id, answers) => runs.respondClarify(id, answers),
    recover: (id) => runs.recover(id),
    observe: (id, runId, startedAt, stopping = false) => runs.observe(id, runId, startedAt, stopping),
    reconcile: (id, state) => runs.reconcile(id, state),
    clear: (id) => runs.clear(id),
    restoreSteers: (id, messages) => runs.restoreSteers(id, messages),
    isRestoredLocal: (id, runId) => runs.isRestoredLocal(id, runId),
  };
}

export function useResumeRuns(): string[] {
  const [ids, setIds] = useState<string[]>(() => resumedIds ?? []);
  useEffect(() => {
    if (resumedIds === null) resumedIds = runs.resumeAll();
    setIds(resumedIds);
  }, []);
  return ids;
}
