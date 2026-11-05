import type { Message, SessionSummary } from "./api.js";
import { modelEffortFields } from "./model-effort.js";

export interface EditBoundary {
  fromEnd: number;
  expectedContent: string;
  expectedTimestamp: number;
}

/** 原始消息位置，而不是渲染回合位置；分页或重复文字不能定位到别的消息。 */
export function editBoundary(messages: Message[], target: Message): EditBoundary {
  const index = messages.indexOf(target);
  if (index < 0 || target.role !== "user") throw new Error("只能编辑已载入的用户消息");
  if (typeof target.content !== "string" || typeof target.timestamp !== "number" || !Number.isFinite(target.timestamp)) {
    throw new Error("这条消息缺少可靠的历史定位信息，不能编辑");
  }
  if (/--- 附件：|\[附件已上传：|\[screenshot(?: removed)?|\[OUT-OF-BAND USER MESSAGE/.test(target.content)) {
    throw new Error("附件或运行中插话的原始内容无法可靠重建，暂不支持编辑");
  }
  return { fromEnd: messages.length - index, expectedContent: target.content, expectedTimestamp: target.timestamp };
}

export interface EditAttempt {
  requestId: string;
  session?: SessionSummary;
  startAttempted: boolean;
}

export interface EditInput {
  sessionId: string;
  boundary: EditBoundary;
  originalText: string;
  text: string;
  model: string;
  provider: string;
  effort: string;
}

interface EditServices {
  branch: (body: EditBoundary & { sessionId: string; requestId: string; model: string; provider: string }) => Promise<SessionSummary>;
  lock: (sessionId: string, model: string, provider: string) => Promise<unknown>;
  start: (body: unknown) => Promise<{ run_id: string; status: string }>;
}

/** 副本建立成功后也不删除它；失败保留编辑框，重复点击不会重复建立副本。 */
export async function resendEdited(input: EditInput, attempt: EditAttempt, services: EditServices) {
  const text = input.text.trim();
  if (!text || text === input.originalText.trim()) throw new Error("内容为空或没有改动");
  if (!input.model || !input.provider) throw new Error("无法确认当前模型与提供方，请先重新选择模型");
  if (attempt.startAttempted) throw new Error("上次发送结果尚未确认，请打开已创建的副本查看，避免重复发送");
  attempt.session ??= await services.branch({ sessionId: input.sessionId, requestId: attempt.requestId, model: input.model, provider: input.provider, ...input.boundary });
  const fields = modelEffortFields(input.model, input.provider, input.effort);
  await services.lock(attempt.session.id, fields.model, fields.provider);
  attempt.startAttempted = true;
  let started: { run_id: string; status: string };
  try {
    started = await services.start({ session_id: attempt.session.id, input: text, ...fields });
    if (!started.run_id) throw new Error("服务器未返回运行编号，请打开副本确认发送结果");
  } catch (error) {
    // 只有确定被拒的 4xx 才允许再发；超时/5xx 可能已被服务端接受。
    const status = error && typeof error === "object" && "status" in error ? error.status : 0;
    if (typeof status === "number" && status >= 400 && status < 500 && status !== 408) attempt.startAttempted = false;
    throw error;
  }
  return { session: attempt.session, started };
}
