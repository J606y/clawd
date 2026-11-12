import { ApiError } from "./api.js";
import type { ProjectBindResult } from "./projects.js";

const PENDING_KEY = "hp_pending_new_chat_project";

let memory: string | null = null;
let loaded = false;

function readStored(): string {
  try {
    return globalThis.sessionStorage?.getItem(PENDING_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeStored(value: string): void {
  try {
    globalThis.sessionStorage?.setItem(PENDING_KEY, value);
  } catch {
    // 存储不可用时只保留本次内存选择，不猜测项目。
  }
}

/** 新对话所属项目。空表示用户显式选择的普通模式，不是绑定失败后的回退。 */
export function getPendingNewChatProjectId(): string | null {
  if (!loaded) {
    memory = readStored().trim();
    loaded = true;
  }
  return memory ? memory : null;
}

export function setPendingNewChatProjectId(id: string | null): void {
  memory = (id ?? "").trim();
  loaded = true;
  writeStored(memory);
}

export class ProjectBindBlocked extends Error {
  readonly sessionId: string;
  readonly projectId: string;
  readonly status: number;

  constructor(message: string, sessionId: string, projectId: string, status = 0) {
    super(message);
    this.name = "ProjectBindBlocked";
    this.sessionId = sessionId;
    this.projectId = projectId;
    this.status = status;
  }
}

/** Only freshly created, unsent sessions may be cleaned up here. */
export async function prepareCreatedSession(input: {
  sessionId: string;
  projectId: string | null;
  bind: (sessionId: string, projectId: string) => Promise<ProjectBindResult>;
  lock: () => Promise<unknown>;
  remove: (sessionId: string) => Promise<unknown>;
}): Promise<void> {
  try {
    await bindCreatedSessionOrBlock(input);
    await input.lock();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    try {
      await input.remove(input.sessionId);
    } catch (cleanupError) {
      throw new ApiError("server", 0, `会话未发送，初始化失败：${message}；清理空会话 ${input.sessionId} 失败：${cleanupError instanceof Error ? cleanupError.message : String(cleanupError)}`);
    }
    throw new ApiError("server", 0, message);
  }
}

export type NewChatBind =
  | { mode: "normal"; projectId: null }
  | { mode: "project"; projectId: string };

/**
 * 首发在 start 之前调用。没有所选项目时不请求绑定，返回普通模式。
 * 有所选项目时必须绑定成功且 project_id 一致；任何失败都抛出，绝不返回普通模式。
 */
export async function bindCreatedSessionOrBlock(input: {
  sessionId: string;
  projectId: string | null | undefined;
  bind: (sessionId: string, projectId: string) => Promise<ProjectBindResult>;
}): Promise<NewChatBind> {
  const projectId = (input.projectId ?? "").trim();
  if (!projectId) return { mode: "normal", projectId: null };
  try {
    const result = await input.bind(input.sessionId, projectId);
    if (!result || result.success !== true || result.session_id !== input.sessionId || result.project_id !== projectId) {
      throw new ProjectBindBlocked("绑定结果不是所选项目，已停止，不会改以普通模式发送", input.sessionId, projectId);
    }
    return { mode: "project", projectId };
  } catch (error) {
    if (error instanceof ProjectBindBlocked) throw error;
    const status = error instanceof ApiError ? error.status : 0;
    const message = error instanceof Error ? error.message : "绑定项目失败";
    throw new ProjectBindBlocked(message, input.sessionId, projectId, status);
  }
}


const OPEN_KEY = "hp_open_project_view";

let openMemory: string | null = null;
let openLoaded = false;
const openListeners = new Set<() => void>();

function readOpen(): string {
  try {
    return globalThis.sessionStorage?.getItem(OPEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeOpen(value: string): void {
  try {
    globalThis.sessionStorage?.setItem(OPEN_KEY, value);
  } catch {
    // 记不住时只保留本次内存选择，不猜测项目。
  }
}

/** 当前打开的项目内页。空表示不遮住对话。不是绑定失败后的回退。 */
export function getOpenProjectId(): string | null {
  if (!openLoaded) {
    openMemory = readOpen().trim();
    openLoaded = true;
  }
  return openMemory ? openMemory : null;
}

export function setOpenProjectId(id: string | null): void {
  openMemory = (id ?? "").trim();
  openLoaded = true;
  writeOpen(openMemory);
  openListeners.forEach((listener) => listener());
}

export function subscribeOpenProject(listener: () => void): () => void {
  openListeners.add(listener);
  return () => openListeners.delete(listener);
}
