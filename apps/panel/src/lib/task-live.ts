import { request } from "./api.js";

const BASE = new URL("panel-core/", document.baseURI);

export type TaskLiveStatus = "waiting" | "active" | "ended";

export interface TaskLiveFrame {
  taskId: string;
  sessionId: string;
  status: TaskLiveStatus;
  targetId: string | null;
  title: string;
  image: string | null;
  cleanupError: string;
  takenOver?: boolean;
  controlledByYou?: boolean;
  leaseRemainingSeconds?: number;
}

export interface TaskControlResult {
  ok: boolean;
  pending?: boolean;
  takenOver?: boolean;
  controlledByYou: boolean;
  leaseSeconds?: number;
  leaseRemainingSeconds?: number;
  taskId?: string;
  targetId?: string;
}

export type TaskInputKey = "Enter" | "Backspace" | "Tab" | "Escape";

export type TaskInputPayload =
  | {
      clientId: string;
      sessionId: string;
      taskId: string;
      targetId: string;
      kind: "click";
      xRatio: number;
      yRatio: number;
    }
  | {
      clientId: string;
      sessionId: string;
      taskId: string;
      targetId: string;
      kind: "scroll";
      xRatio: number;
      yRatio: number;
      deltaX: number;
      deltaY: number;
    }
  | {
      clientId: string;
      sessionId: string;
      taskId: string;
      targetId: string;
      kind: "text";
      text: string;
    }
  | {
      clientId: string;
      sessionId: string;
      taskId: string;
      targetId: string;
      kind: "key";
      key: TaskInputKey;
    };

export interface TaskInputResult {
  ok: boolean;
  inserted?: boolean;
  x?: number;
  y?: number;
  key?: string;
}

/**
 * 获取任务单页实时画面。
 * 冻结接口：GET panel-core/live/task?session_id=<sid>[&task_id=<id>][&client_id=<cid>]
 * 同源鉴权 request(api.js)
 */
export async function getTaskLiveFrame(
  sessionId: string,
  taskId?: string | null,
  signal?: AbortSignal,
  clientId?: string | null,
): Promise<TaskLiveFrame> {
  const p = new URLSearchParams();
  p.set("session_id", sessionId);
  if (taskId) p.set("task_id", taskId);
  if (clientId) p.set("client_id", clientId);

  return request<TaskLiveFrame>(
    `live/task?${p.toString()}`,
    { method: "GET", signal },
    5000,
    BASE,
  );
}

/**
 * 任务单页控制租约（接管 / 交还）。
 * 冻结接口：POST panel-core/live/task/control
 */
export async function controlTaskLive(
  body: {
    action: "takeover" | "release";
    clientId: string;
    sessionId?: string;
    taskId?: string;
    targetId?: string;
  },
  keepalive = false,
  signal?: AbortSignal,
): Promise<TaskControlResult> {
  return request<TaskControlResult>(
    "live/task/control",
    {
      method: "POST",
      body: JSON.stringify(body),
      keepalive,
      signal,
    },
    6000,
    BASE,
  );
}

/**
 * 任务单页输入（仅作用在登记的 targetId 上）。
 * 冻结接口：POST panel-core/live/task/input
 */
export async function sendTaskLiveInput(
  body: TaskInputPayload,
  signal?: AbortSignal,
): Promise<TaskInputResult> {
  return request<TaskInputResult>(
    "live/task/input",
    {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    },
    6000,
    BASE,
  );
}
