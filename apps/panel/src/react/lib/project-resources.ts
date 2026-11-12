// 项目档案 / 指示 / 记忆的契约客户端。
// 不读 session，不调用 /memory，不在 URL 或请求头里放 token。
import { API_BASE, ApiError, AUTH_EXPIRED_EVENT, request, type ApiErrorKind } from "./api.js";
import { relativeTime } from "./time.js";

export interface ProjectFileResource {
  name: string;
  size: number;
  updated_at: number | null;
  content_type: string;
}

export interface ProjectMemoryResource {
  id: string;
  title: string;
  content: string;
  updated_at: number | null;
}

export interface ProjectResourceLimits {
  file_bytes: number;
  instructions_chars: number;
}

export interface ProjectResourcesSnapshot {
  project_id: string;
  archived: boolean;
  instructions: string;
  files: ProjectFileResource[];
  memories: ProjectMemoryResource[];
  limits: ProjectResourceLimits;
}

export interface InstructionSaveResult {
  instructions: string;
  confirmed: boolean;
}

export interface FileUploadResult {
  success: true;
  project_id: string;
  file: ProjectFileResource;
}

export interface OperationTicket {
  generation: number;
  projectId: string;
}

const UNKNOWN_TIME = "更新时间未知";

export class ProjectResourcesError extends ApiError {
  constructor(status: number, message: string, code?: string) {
    super(kindForStatus(status), status, message, code);
    this.name = "ProjectResourcesError";
  }
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 429) return "rate-limited";
  if (status === 0) return "network";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "server";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function projectIdError(projectId: string): string {
  if (typeof projectId !== "string" || projectId.length === 0 || projectId.trim() !== projectId) {
    return "缺少项目，没有请求";
  }
  if (projectId.includes("/") || projectId.includes("\\") || projectId.includes("..")) {
    return "项目 id 无效，没有请求";
  }
  return "";
}

function unsafeFileNameCode(name: string): boolean {
  for (let index = 0; index < name.length; index += 1) {
    const code = name.charCodeAt(index);
    // 0x2f 是单斜杠，0x5c 是单个反斜杠，0x00-0x1f 与 0x7f 是控制字符。
    // 用码点判断，避免正则字面量被双重转义后放过真实控制字符。
    if (code === 0x2f || code === 0x5c || code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

export function resourceFileNameError(name: string): string {
  if (typeof name !== "string" || name.length === 0 || name.trim() !== name) {
    return "档案名无效，没有请求";
  }
  if (name === "." || name === ".." || unsafeFileNameCode(name)) {
    return "档案名不安全，没有请求";
  }
  return "";
}

export function resourcesPath(projectId: string): string {
  const error = projectIdError(projectId);
  if (error) throw new ProjectResourcesError(0, error);
  return `api/projects/${encodeURIComponent(projectId)}/resources`;
}

export function resourceFilePath(projectId: string, name: string): string {
  const nameError = resourceFileNameError(name);
  if (nameError) throw new ProjectResourcesError(0, nameError);
  return `${resourcesPath(projectId)}/files/${encodeURIComponent(name)}`;
}

export function formatResourceUpdated(value: number | null | undefined, now = Date.now()): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return UNKNOWN_TIME;
  const label = relativeTime(value, now);
  if (!label) return UNKNOWN_TIME;
  return `${label}更新`;
}

export function formatFileSize(size: number): string {
  if (typeof size !== "number" || !Number.isFinite(size) || size < 0) return "";
  if (size < 1024) return `${Math.floor(size)} B`;
  if (size < 1024 * 1024) {
    const digits = size < 10 * 1024 ? 1 : 0;
    return `${(size / 1024).toFixed(digits)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function createOperationGuard() {
  let generation = 0;
  let projectId = "";
  return {
    begin(nextProjectId: string): OperationTicket {
      generation += 1;
      projectId = nextProjectId;
      return { generation, projectId };
    },
    current(): OperationTicket {
      return { generation, projectId };
    },
    matches(ticket: OperationTicket): boolean {
      return ticket.generation === generation && ticket.projectId === projectId;
    },
  };
}

export function acceptScopedResult(ticket: OperationTicket, current: OperationTicket, resultProjectId: string): boolean {
  return ticket.generation === current.generation
    && ticket.projectId === current.projectId
    && resultProjectId === ticket.projectId;
}

export function uploadBlockedReason(
  files: readonly { name: string }[],
  file: { name: string; size: number },
  limit: number | null,
  archived: boolean,
): string {
  if (archived) return "项目已归档，没有上传";
  const nameError = resourceFileNameError(file.name);
  if (nameError) return nameError;
  if (limit != null && file.size > limit) return `档案超过 ${limit} 字节上限，没有上传`;
  for (const item of files) {
    if (item.name === file.name) return `已有同名档案「${file.name}」，没有覆盖`;
  }
  return "";
}

export function instructionsBlockedReason(
  content: string | null,
  limit: number | null,
  archived: boolean,
  loaded: boolean,
): string {
  if (!loaded || content == null) return "指示还没读取成功，不能保存";
  if (archived) return "项目已归档，没有保存";
  if (limit != null && content.length > limit) return `指示超过 ${limit} 字上限，没有保存`;
  return "";
}

function errorCode(payload: unknown): string | undefined {
  if (!isRecord(payload)) return undefined;
  const err = payload.error;
  if (isRecord(err) && typeof err.code === "string") return err.code;
  if (typeof payload.code === "string") return payload.code;
  return undefined;
}

export function resourcesErrorMessage(payload: unknown, status: number): string {
  if (isRecord(payload)) {
    const err = payload.error;
    if (isRecord(err) && typeof err.message === "string" && err.message.trim()) return err.message;
    if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
    if (typeof err === "string" && err.trim()) return err;
  }
  if (status === 409) return "已有同名档案，没有覆盖";
  if (status > 0) return `请求失败（${status}），没有当成成功`;
  return "请求失败，没有当成成功";
}

function rejectEmpty(payload: unknown, what: string): void {
  if (payload == null) throw new ProjectResourcesError(0, `${what}是空的，没有当成成功`);
  if (isRecord(payload) && Object.keys(payload).length === 0) {
    throw new ProjectResourcesError(0, `${what}是空对象，没有当成成功`);
  }
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseFile(value: unknown): ProjectFileResource {
  if (!isRecord(value)) throw new ProjectResourcesError(0, "档案条目无法识别，已拒绝");
  const name = value.name;
  if (typeof name !== "string" || resourceFileNameError(name)) {
    throw new ProjectResourcesError(0, "档案名无效，已拒绝这条响应");
  }
  if (!finiteNumber(value.size) || value.size < 0) throw new ProjectResourcesError(0, "档案大小无效，已拒绝");
  if (!(value.updated_at == null || finiteNumber(value.updated_at))) {
    throw new ProjectResourcesError(0, "档案更新时间无效，没有编造时间");
  }
  if (typeof value.content_type !== "string") throw new ProjectResourcesError(0, "档案类型缺失，已拒绝");
  return {
    name,
    size: value.size,
    updated_at: value.updated_at == null ? null : value.updated_at,
    content_type: value.content_type,
  };
}

function parseMemory(value: unknown): ProjectMemoryResource {
  if (!isRecord(value)) throw new ProjectResourcesError(0, "记忆条目无法识别，已拒绝");
  if (typeof value.id !== "string" || value.id.length === 0) throw new ProjectResourcesError(0, "记忆缺少 id，已拒绝");
  if (typeof value.title !== "string") throw new ProjectResourcesError(0, "记忆缺少标题，已拒绝");
  if (typeof value.content !== "string") throw new ProjectResourcesError(0, "记忆缺少正文，已拒绝");
  if (!("updated_at" in value) || !(value.updated_at == null || finiteNumber(value.updated_at))) {
    throw new ProjectResourcesError(0, "记忆更新时间无效，没有编造时间");
  }
  return {
    id: value.id,
    title: value.title,
    content: value.content,
    updated_at: value.updated_at == null ? null : value.updated_at,
  };
}

export function parseProjectResources(payload: unknown, projectId: string): ProjectResourcesSnapshot {
  rejectEmpty(payload, "资源响应");
  if (!isRecord(payload)) throw new ProjectResourcesError(0, "资源响应无法识别，没有当成空项目");
  if (payload.success === true && !("files" in payload) && !("instructions" in payload)) {
    throw new ProjectResourcesError(0, "资源响应只有空成功，已拒绝");
  }
  if (payload.project_id !== projectId) throw new ProjectResourcesError(0, "资源不属于当前项目，已拒绝");
  if (typeof payload.archived !== "boolean") throw new ProjectResourcesError(0, "归档状态缺失，已拒绝猜测");
  if (typeof payload.instructions !== "string") throw new ProjectResourcesError(0, "指示缺失，没有当成空指示");
  if (!Array.isArray(payload.files)) throw new ProjectResourcesError(0, "档案列表缺失，没有当成尚无档案");
  if (!Array.isArray(payload.memories)) throw new ProjectResourcesError(0, "记忆列表缺失，没有当成尚无记忆");
  if (!isRecord(payload.limits)) throw new ProjectResourcesError(0, "限额缺失，已拒绝");
  if (!finiteNumber(payload.limits.file_bytes) || payload.limits.file_bytes < 0) {
    throw new ProjectResourcesError(0, "档案上限无效，已拒绝");
  }
  if (!finiteNumber(payload.limits.instructions_chars) || payload.limits.instructions_chars < 0) {
    throw new ProjectResourcesError(0, "指示上限无效，已拒绝");
  }
  return {
    project_id: projectId,
    archived: payload.archived,
    instructions: payload.instructions,
    files: payload.files.map(parseFile),
    memories: payload.memories.map(parseMemory),
    limits: {
      file_bytes: payload.limits.file_bytes,
      instructions_chars: payload.limits.instructions_chars,
    },
  };
}

function presentForeignProject(payload: Record<string, unknown>, projectId: string): boolean {
  return Object.prototype.hasOwnProperty.call(payload, "project_id") && payload.project_id !== projectId;
}

function missingProjectIdentity(payload: Record<string, unknown>): boolean {
  return !Object.prototype.hasOwnProperty.call(payload, "project_id");
}

export function parseInstructionsWrite(payload: unknown, projectId: string): { success: true; project_id: string; instructions: string } {
  rejectEmpty(payload, "指示响应");
  if (!isRecord(payload) || payload.success !== true) throw new ProjectResourcesError(0, "指示没有保存成功");
  if (presentForeignProject(payload, projectId)) throw new ProjectResourcesError(0, "指示响应不属于当前项目，已拒绝");
  if (typeof payload.instructions !== "string") throw new ProjectResourcesError(0, "指示响应缺少内容，没有当成已保存");
  if (missingProjectIdentity(payload)) throw new ProjectResourcesError(0, "指示响应缺少项目身份，已拒绝");
  return { success: true, project_id: projectId, instructions: payload.instructions };
}

export function parseFileWrite(payload: unknown, projectId: string): FileUploadResult {
  rejectEmpty(payload, "上传响应");
  if (!isRecord(payload) || payload.success !== true) throw new ProjectResourcesError(0, "上传没有成功");
  if (presentForeignProject(payload, projectId)) throw new ProjectResourcesError(0, "上传响应不属于当前项目，已拒绝");
  if (!isRecord(payload.file)) throw new ProjectResourcesError(0, "上传响应缺少档案，没有当成成功");
  if (missingProjectIdentity(payload)) throw new ProjectResourcesError(0, "上传响应缺少项目身份，已拒绝");
  return { success: true, project_id: projectId, file: parseFile(payload.file) };
}

function throwHttp(payload: unknown, status: number): never {
  if (status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  throw new ProjectResourcesError(status, resourcesErrorMessage(payload, status), errorCode(payload));
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ProjectResourcesError(response.status, "响应无法解析，没有当成成功");
  }
}

export async function readProjectResources(projectId: string, signal?: AbortSignal): Promise<ProjectResourcesSnapshot> {
  const payload = await request<unknown>(resourcesPath(projectId), { method: "GET", signal, cache: "no-store" });
  return parseProjectResources(payload, projectId);
}

export async function saveProjectInstructions(
  projectId: string,
  content: string,
  signal?: AbortSignal,
  limit?: number | null,
): Promise<InstructionSaveResult> {
  if (typeof content !== "string") throw new ProjectResourcesError(0, "指示不是文本，没有发送");
  const blocked = instructionsBlockedReason(content, limit ?? null, false, true);
  if (blocked && blocked !== "项目已归档，没有保存") throw new ProjectResourcesError(0, blocked);
  const written = parseInstructionsWrite(
    await request<unknown>(`${resourcesPath(projectId)}/instructions`, {
      method: "PUT",
      body: JSON.stringify({ content }),
      signal,
      cache: "no-store",
    }),
    projectId,
  );
  const snapshot = await readProjectResources(projectId, signal);
  return {
    instructions: snapshot.instructions,
    confirmed: snapshot.instructions === content && snapshot.instructions === written.instructions,
  };
}

export async function uploadProjectFile(
  projectId: string,
  file: Blob & { name: string },
  signal?: AbortSignal,
  onProgress?: (ratio: number) => void,
): Promise<FileUploadResult> {
  const nameError = resourceFileNameError(file.name);
  if (nameError) throw new ProjectResourcesError(0, nameError);
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const url = new URL(`${resourcesPath(projectId)}/files`, API_BASE).toString();
  const form = new FormData();
  form.append("file", file, file.name);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", onAbort);
      fn();
    };
    const onAbort = () => xhr.abort();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.timeout = 120000;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    xhr.onerror = () => finish(() => reject(new ProjectResourcesError(0, "上传失败，没有当成成功")));
    xhr.ontimeout = () => finish(() => reject(new ProjectResourcesError(0, "上传超时，没有当成成功")));
    xhr.onabort = () => finish(() => reject(new DOMException("Aborted", "AbortError")));
    xhr.onload = () => {
      let payload: unknown;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) as unknown : undefined;
      } catch {
        finish(() => reject(new ProjectResourcesError(xhr.status, "上传响应无法解析，没有当成成功")));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        let failure: unknown;
        try {
          throwHttp(payload, xhr.status);
        } catch (error) {
          failure = error;
        }
        finish(() => reject(failure));
        return;
      }
      let result: FileUploadResult;
      try {
        result = parseFileWrite(payload, projectId);
      } catch (error) {
        finish(() => reject(error));
        return;
      }
      finish(() => resolve(result));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    xhr.send(form);
  });
}

export async function downloadProjectFile(projectId: string, name: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(new URL(resourceFilePath(projectId, name), API_BASE), {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    signal,
    headers: { Accept: "*/*" },
  });
  if (!response.ok) {
    throwHttp(await readJson(response).catch(() => undefined), response.status);
  }
  const type = response.headers.get("content-type") ?? "";
  const disposition = response.headers.get("content-disposition") ?? "";
  if (!disposition && /^text\/html\b/i.test(type)) {
    throw new ProjectResourcesError(response.status, "下载返回了页面而不是档案，没有保存");
  }
  return response.blob();
}
