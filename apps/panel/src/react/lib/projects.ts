import { API_BASE, ApiError, AUTH_EXPIRED_EVENT, request, type SessionSummary } from "./api.js";
import {
  migrationIsApplied,
  migrationPlanAccepted,
  parseAutoMigration,
  projectMemoryScope,
  projectPathError,
  projectSessionToSummary,
  type AutoMigrationResult,
  type CreateProjectBody,
  type MigrationMappingInput,
  type ProjectMemoryScope,
  type ProjectSessionRow,
} from "./project-model.js";

export interface ProjectRecord {
  id: string;
  slug?: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  primary_path: string | null;
  archived: boolean;
  created_at?: number;
  session_count?: number;
  folders?: { path?: string; label?: string | null; is_primary?: boolean; added_at?: number }[];
}

export interface ProjectIndex {
  projects: ProjectRecord[];
  sessions: SessionSummary[];
  assignments: Record<string, string>;
  sessionErrors: Record<string, string>;
  conflicts: string[];
}

export interface ProjectBindResult {
  success: boolean;
  session_id: string;
  project_id: string | null;
}

export interface ProjectMemoryView {
  entries: string[];
  usage?: string;
  char_count?: number;
  char_limit?: number;
  instructions?: string;
  instructionsError?: string;
  archives?: { name: string }[];
}

export interface ProjectMemoryWriteResult {
  success: true;
  entry_count?: number;
  message?: string;
  usage?: string;
}

/** 记忆拒绝。401 missing_identity 不是登录失效，不能当成退回登录。 */
export class ProjectMemoryAccessError extends ApiError {
  readonly editable = false;

  constructor(status: number, code: string, message: string) {
    super(code === "unauthorized" ? "unauthorized" : "server", status, message, code);
    this.name = "ProjectMemoryAccessError";
  }
}

function memoryScopeOrThrow(sessionId: unknown, generation?: unknown): ProjectMemoryScope {
  const scope = projectMemoryScope(sessionId, generation);
  if ("editable" in scope) throw new ProjectMemoryAccessError(0, scope.code, scope.message);
  return scope;
}

function memoryErrorBody(payload: unknown, status: number): ProjectMemoryAccessError {
  const body = payload && typeof payload === "object" ? payload as { error?: unknown; message?: unknown } : {};
  const code = typeof body.error === "string" && body.error.trim() ? body.error : "memory_refused";
  const message = typeof body.message === "string" && body.message.trim() ? body.message : code;
  if (status === 401 && code === "unauthorized") window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  return new ProjectMemoryAccessError(status, code, message);
}

async function projectMemoryCall<T>(
  projectId: string,
  sessionId: unknown,
  generation: unknown,
  method: "GET" | "POST",
  operations?: unknown,
): Promise<T> {
  const scope = memoryScopeOrThrow(sessionId, generation);
  if (!projectId.trim()) throw new ProjectMemoryAccessError(0, "project_not_found", "缺少项目 id，未当作当前会话身份");
  if (method === "POST" && !Array.isArray(operations)) {
    throw new ProjectMemoryAccessError(0, "missing_operations", "operations 必须是数组，未发送");
  }
  const query = new URLSearchParams({ session_id: scope.sessionId });
  if (scope.executionGeneration != null) query.set("execution_generation", String(scope.executionGeneration));
  const path = `api/projects/${encodeURIComponent(projectId)}/memory${method === "GET" ? `?${query.toString()}` : ""}`;
  const body = method === "POST"
    ? JSON.stringify({
      session_id: scope.sessionId,
      operations,
      ...(scope.executionGeneration != null ? { execution_generation: scope.executionGeneration } : {}),
    })
    : undefined;
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch(new URL(path, API_BASE), {
      method,
      body,
      signal: ctrl.signal,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
    });
    const text = await res.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : undefined;
    } catch {
      throw new ProjectMemoryAccessError(res.status, "invalid_json", "项目记忆返回无法解析，未当作成功");
    }
    if (!res.ok) throw memoryErrorBody(payload, res.status);
    if (method === "GET") return requireProjectMemory(payload, projectId) as T;
    return requireProjectMemoryWrite(payload) as T;
  } catch (error) {
    if (error instanceof ProjectMemoryAccessError || error instanceof ApiError) throw error;
    throw new ProjectMemoryAccessError(0, "network", "项目记忆请求失败，未当作成功");
  } finally {
    window.clearTimeout(timer);
  }
}

function archiveNames(value: unknown): { name: string }[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const name = (item as { name?: unknown }).name;
    if (typeof name !== "string" || !name.trim() || name.includes("/") || name.includes("\\")) return [];
    return [{ name }];
  });
}

function requireProjectMemory(payload: unknown, projectId: string): ProjectMemoryView {
  if (!payload || typeof payload !== "object") {
    throw new ProjectMemoryAccessError(0, "invalid_json", "项目记忆返回格式异常，未当作空记忆");
  }
  const row = payload as { project_id?: unknown; memory?: unknown };
  if (row.project_id !== projectId) {
    throw new ProjectMemoryAccessError(0, "project_mismatch", "项目记忆不属于所请求的项目，已拒绝");
  }
  const memory = row.memory;
  if (!memory || typeof memory !== "object" || !Array.isArray((memory as { entries?: unknown }).entries)) {
    throw new ProjectMemoryAccessError(0, "invalid_json", "项目记忆缺少 entries，未当作空记忆");
  }
  const view = memory as Partial<ProjectMemoryView> & { instructions_error?: unknown; archives?: unknown; archives_error?: unknown };
  if (typeof view.archives_error === "string" && view.archives_error.trim()) {
    throw new ProjectMemoryAccessError(503, "archives_unreadable", view.archives_error);
  }
  if (view.archives === null) {
    throw new ProjectMemoryAccessError(503, "archives_unreadable", "项目档案无法读取，未当作空档案");
  }
  if (view.archives !== undefined && !Array.isArray(view.archives)) {
    throw new ProjectMemoryAccessError(0, "invalid_json", "项目档案返回格式异常，未当作空档案");
  }
  return {
    entries: view.entries as string[],
    usage: typeof view.usage === "string" ? view.usage : undefined,
    char_count: typeof view.char_count === "number" ? view.char_count : undefined,
    char_limit: typeof view.char_limit === "number" ? view.char_limit : undefined,
    instructions: typeof view.instructions === "string" ? view.instructions : "",
    instructionsError: typeof view.instructions_error === "string" ? view.instructions_error : "",
    archives: archiveNames(view.archives),
  };
}

function requireProjectMemoryWrite(payload: unknown): ProjectMemoryWriteResult {
  if (!payload || typeof payload !== "object" || (payload as { success?: unknown }).success !== true) {
    throw memoryErrorBody(payload, 400);
  }
  const row = payload as { entry_count?: unknown; message?: unknown; usage?: unknown };
  return {
    success: true,
    entry_count: typeof row.entry_count === "number" ? row.entry_count : undefined,
    message: typeof row.message === "string" ? row.message : undefined,
    usage: typeof row.usage === "string" ? row.usage : undefined,
  };
}

function requireProject(value: unknown): ProjectRecord {
  if (!value || typeof value !== "object") throw new ApiError("server", 0, "项目返回格式异常");
  const row = value as Partial<ProjectRecord>;
  if (typeof row.id !== "string" || row.id.length === 0) throw new ApiError("server", 0, "项目缺少 id，已拒绝当成空列表");
  if (typeof row.name !== "string") throw new ApiError("server", 0, "项目缺少名称，已拒绝猜测");
  if (typeof row.archived !== "boolean") throw new ApiError("server", 0, "项目归档状态缺失，已拒绝猜测");
  if (row.primary_path != null && typeof row.primary_path !== "string") {
    throw new ApiError("server", 0, "项目目录格式异常");
  }
  return {
    id: row.id,
    slug: typeof row.slug === "string" ? row.slug : undefined,
    name: row.name,
    description: row.description ?? null,
    icon: row.icon ?? null,
    color: row.color ?? null,
    primary_path: row.primary_path ?? null,
    archived: row.archived,
    created_at: typeof row.created_at === "number" ? row.created_at : undefined,
    session_count: typeof row.session_count === "number" ? row.session_count : undefined,
    folders: Array.isArray(row.folders) ? row.folders : undefined,
  };
}

export const projectsApi = {
  list: async (includeArchived = true): Promise<ProjectRecord[]> => {
    const query = includeArchived ? "?include_archived=true" : "";
    const body = await request<{ projects?: unknown }>(`api/projects${query}`);
    if (!body || !Array.isArray(body.projects)) throw new ApiError("server", 0, "项目列表返回格式异常");
    return body.projects.map((item) => requireProject(item));
  },
  create: async (body: CreateProjectBody): Promise<ProjectRecord> => {
    const name = typeof body?.name === "string" ? body.name.trim() : "";
    if (!name) throw new ApiError("server", 0, "文件夹名称不能为空");
    const explicitPath = typeof body.primary_path === "string" ? body.primary_path : "";
    if (explicitPath) {
      const pathError = projectPathError(explicitPath);
      if (pathError) throw new ApiError("server", 0, pathError);
      if (body.create_directory !== true && body.create_directory !== false) {
        throw new ApiError("server", 0, "create_directory 必须显式为布尔值");
      }
    }
    const payload = explicitPath
      ? { name, primary_path: explicitPath, create_directory: body.create_directory === true }
      : { name };
    const result = await request<{ project?: unknown }>("api/projects", { method: "POST", body: JSON.stringify(payload) });
    return requireProject(result?.project);
  },
  patch: async (id: string, patch: { name?: string; archived?: boolean }): Promise<ProjectRecord> => {
    const result = await request<{ project?: unknown }>(`api/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    return requireProject(result?.project);
  },
  sessions: async (id: string): Promise<ProjectSessionRow[]> => {
    const result = await request<{ project_id?: unknown; sessions?: unknown }>(`api/projects/${encodeURIComponent(id)}/sessions`);
    if (!result || result.project_id !== id || !Array.isArray(result.sessions)) {
      throw new ApiError("server", 0, "项目会话归属返回格式异常");
    }
    return result.sessions as ProjectSessionRow[];
  },
  bind: (sessionId: string, projectId: string | null) =>
    request<ProjectBindResult>(`api/sessions/${encodeURIComponent(sessionId)}/project`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }),
  /**
   * 项目记忆只接受调用方传入的当前会话。projectId 只是资源路径。
   * 没有会话、或 generation 不是整数时不发请求，也不创建/绑定会话。
   * 没有记忆页面时，这些方法是唯一客户端；不要用 sessions()[0] 填身份。
   */
  memory: {
    read: (projectId: string, sessionId: unknown, generation?: unknown) =>
      projectMemoryCall<ProjectMemoryView>(projectId, sessionId, generation, "GET"),
    update: (projectId: string, sessionId: unknown, operations: unknown, generation?: unknown) =>
      projectMemoryCall<ProjectMemoryWriteResult>(projectId, sessionId, generation, "POST", operations),
  },
};

/** 项目列表与归属只走 panel-api。失败抛出，不返回空列表，也不读侧车。 */
export async function loadProjectIndex(): Promise<ProjectIndex> {
  const projects = await projectsApi.list(true);
  const sessions: SessionSummary[] = [];
  const assignments: Record<string, string> = {};
  const sessionErrors: Record<string, string> = {};
  const conflicts: string[] = [];
  await Promise.all(projects.map(async (project) => {
    try {
      const rows = await projectsApi.sessions(project.id);
      for (const row of rows) {
        const summary = projectSessionToSummary(row);
        if (!summary) {
          sessionErrors[project.id] = "项目会话缺少 session_id，未当作空归属";
          continue;
        }
        const existing = assignments[summary.id];
        if (existing && existing !== project.id) {
          const conflictText = "有对话同时出现在多个文件夹，已保留先出现的归属";
          if (!conflicts.includes(conflictText)) conflicts.push(conflictText);
          continue;
        }
        assignments[summary.id] = project.id;
        if (!sessions.some((item) => item.id === summary.id)) sessions.push(summary);
      }
    } catch (error) {
      sessionErrors[project.id] = error instanceof Error ? error.message : "项目会话归属加载失败";
    }
  }));
  return { projects, sessions, assignments, sessionErrors, conflicts };
}

export interface MigrationPreviewFolder {
  folder_id: string;
  name: string;
  session_ids: string[];
  primary_path: null;
  recorded_primary_path?: string | null;
  path_source?: string | null;
  status: "needs_explicit_path" | "migrated" | string;
  migration_id?: string | null;
  project_id?: string | null;
  not_migrated_session_ids?: string[];
  guessed?: boolean;
  reuse_offer?: {
    migration_id?: string;
    recorded_primary_path?: string | null;
    project_id?: string;
    requires_confirmation?: boolean;
    empty_proven?: boolean;
    archive_frees_path?: boolean;
  } | null;
}

export interface MigrationPreview {
  operation: "preview";
  read_only: true;
  success: boolean;
  source_missing?: boolean;
  folders: MigrationPreviewFolder[];
  message?: string;
  recovery?: string;
}

export interface MigrationPlan {
  operation: "plan";
  read_only: true;
  applied?: boolean;
  success: boolean;
  conflicts?: { code?: string; message?: string; session_id?: string; folder_id?: string }[];
  folders?: { folder_id?: string; primary_path?: string; create_directory?: boolean; session_ids?: string[] }[];
  message?: string;
  http_status?: number;
}

export interface MigrationReceipt {
  operation?: string;
  migration_id?: string;
  status?: string;
  success?: boolean;
  fully_restored?: boolean;
  message?: string;
  recovery?: string;
  conflicts?: { code?: string; message?: string; session_id?: string }[];
  folders?: {
    folder_id?: string;
    primary_path?: string;
    project_id?: string | null;
    sessions?: { session_id?: string; applied?: boolean; rollback?: string; error?: string }[];
  }[];
  http_status?: number;
  receipt?: MigrationReceipt;
}

export class MigrationClientError extends Error {
  readonly status: number;
  readonly payload: MigrationReceipt;

  constructor(status: number, payload: unknown) {
    const body = payload && typeof payload === "object" ? payload as MigrationReceipt : {};
    const message = typeof body.message === "string" && body.message.trim() ? body.message : "迁移失败，未当作成功";
    super(message);
    this.name = "MigrationClientError";
    this.status = status;
    this.payload = body;
  }
}

async function migrationRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 60000);
  try {
    const res = await fetch(new URL(path, API_BASE), {
      ...init,
      signal: ctrl.signal,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
    const text = await res.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : undefined;
    } catch {
      throw new ApiError("server", res.status, "迁移返回无法解析，未当作成功");
    }
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      throw new MigrationClientError(res.status, payload);
    }
    return payload as T;
  } catch (error) {
    if (error instanceof MigrationClientError || error instanceof ApiError) throw error;
    throw new ApiError("network", 0, "迁移请求失败，未当作成功");
  } finally {
    window.clearTimeout(timer);
  }
}

export const folderMigrationApi = {
  preview: async (): Promise<MigrationPreview> => {
    const payload = await migrationRequest<MigrationPreview>("api/projects/migrations/preview");
    if (!payload || payload.operation !== "preview" || payload.read_only !== true || payload.success !== true) {
      throw new MigrationClientError(409, payload);
    }
    if (!payload.source_missing && !Array.isArray(payload.folders)) {
      throw new ApiError("server", 0, "旧文件夹预览格式异常，未当作没有数据");
    }
    if (payload.folders?.some((folder) => folder.guessed === true || (folder.status === "needs_explicit_path" && folder.primary_path != null))) {
      throw new ApiError("server", 0, "预览含猜测目录，已拒绝使用");
    }
    return payload;
  },
  plan: async (mapping: MigrationMappingInput, reuse?: { migration_id: string }): Promise<MigrationPlan> => {
    const payload = await migrationRequest<MigrationPlan>("api/projects/migrations/preview", {
      method: "POST",
      body: JSON.stringify({
        mappings: [mapping],
        ...(reuse ? { confirm_reuse: true, reuse_migration_id: reuse.migration_id } : {}),
      }),
    });
    if (!migrationPlanAccepted(payload)) throw new MigrationClientError(payload.http_status || 409, payload);
    return payload;
  },
  execute: async (body: {
    confirm: true;
    confirm_reuse?: true;
    reuse_migration_id?: string;
    idempotency_key: string;
    mappings: MigrationMappingInput[];
  }): Promise<MigrationReceipt> => {
    const payload = await migrationRequest<MigrationReceipt>("api/projects/migrations", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!migrationIsApplied(payload) || typeof payload.migration_id !== "string" || !payload.migration_id) {
      throw new MigrationClientError(payload.http_status || 409, payload);
    }
    return payload;
  },
  rollback: async (migrationId: string): Promise<MigrationReceipt> => {
    const payload = await migrationRequest<MigrationReceipt>(`api/projects/migrations/${encodeURIComponent(migrationId)}/rollback`, {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
    if (payload.success !== true || payload.status !== "rolled_back") {
      throw new MigrationClientError(payload.http_status || 409, payload);
    }
    return payload;
  },
  /**
   * POST /api/projects/migrations/auto
   * 忙时后端返回 pending，这里原样交回，不把非 2xx 或 pending 改写成成功。
   */
  auto: async (): Promise<AutoMigrationResult> => {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 60000);
    try {
      const res = await fetch(new URL("api/projects/migrations/auto", API_BASE), {
        method: "POST",
        body: JSON.stringify({}),
        signal: ctrl.signal,
        credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
      });
      const text = await res.text();
      let payload: unknown;
      try {
        payload = text ? JSON.parse(text) : undefined;
      } catch {
        throw new ApiError("server", res.status, "迁移返回无法解析，未当作成功");
      }
      if (res.status === 401) {
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
        throw new ApiError("unauthorized", 401, "未登录，迁移未执行");
      }
      const parsed = parseAutoMigration(payload);
      if (parsed) {
        if (!res.ok) return { success: false, pending: parsed.pending === true, errors: parsed.errors };
        return parsed;
      }
      if (!res.ok) throw new MigrationClientError(res.status, payload);
      throw new ApiError("server", res.status, "迁移返回格式异常，未当作成功");
    } catch (error) {
      if (error instanceof MigrationClientError || error instanceof ApiError) throw error;
      throw new ApiError("network", 0, "迁移请求失败，未当作成功");
    } finally {
      window.clearTimeout(timer);
    }
  },
};

let autoMigrationFlight: Promise<AutoMigrationResult> | null = null;
let autoMigrationLast: AutoMigrationResult | null = null;

/** 同一页面生命周期只自动打一次。进行中的请求合并。失败或 pending 不缓存成成功，重试必须 force。 */
export function migrateLegacyFoldersOnce(force = false): Promise<AutoMigrationResult> {
  if (autoMigrationFlight) return autoMigrationFlight;
  if (!force && autoMigrationLast) return Promise.resolve(autoMigrationLast);
  const run = folderMigrationApi.auto().then((result) => {
    autoMigrationLast = result;
    return result;
  });
  autoMigrationFlight = run.finally(() => {
    autoMigrationFlight = null;
  });
  return autoMigrationFlight;
}
