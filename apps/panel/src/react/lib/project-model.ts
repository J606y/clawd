import type { SessionSummary } from "./api.js";

/** 旧文件夹映射只记录缺口，不填写猜测目录。 */
export interface LegacyMappingNeed {
  folder_id: string;
  name: string;
  primary_path: null;
  status: "needs_explicit_path";
}

export interface ProjectSessionRow {
  session_id?: unknown;
  title?: unknown;
  created_at?: unknown;
  last_activity_at?: unknown;
  message_count?: unknown;
}

export interface CreateProjectBody {
  name: string;
  primary_path?: string;
  create_directory?: boolean;
}

const COLLAPSE_KEY = "hp_project_collapsed";
export const LEGACY_MAPPING_KEY = "hp_legacy_folder_mapping_needs";

export function projectPathError(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "项目必须绑定绝对目录";
  if (trimmed.includes("~")) return "目录必须是绝对路径，不解析 ~";
  if (!trimmed.startsWith("/")) return "目录必须是绝对路径，不解析相对路径";
  if (trimmed.includes("\0")) return "目录不能包含空字符";
  if (trimmed.split("/").includes("..")) return "目录不能包含 ..";
  return "";
}

/** 显式目录创建仍原样传递。文件夹界面不要用这个，避免再要求手填路径。 */
export function createProjectBody(name: string, primaryPath: string, createDirectory: boolean): CreateProjectBody {
  return {
    name: name.trim(),
    primary_path: primaryPath.trim(),
    create_directory: createDirectory === true,
  };
}

/** 只提交名称。目录由 POST /api/projects 自动生成，不传路径、不猜测。 */
export function createNamedProjectBody(name: string): { name: string } {
  return { name: name.trim() };
}

export function archivePatchBody(): { archived: true } {
  return { archived: true };
}

export function restorePatchBody(): { archived: false } {
  return { archived: false };
}

export function archiveConfirmText(name: string): string {
  return `归档「${name}」？不会删除会话、项目记忆或工作目录，只从当前列表隐藏。`;
}

export function projectSessionToSummary(row: ProjectSessionRow): SessionSummary | null {
  if (!row || typeof row.session_id !== "string" || row.session_id.length === 0) return null;
  return {
    id: row.session_id,
    source: "",
    title: typeof row.title === "string" ? row.title : null,
    model: null,
    started_at: typeof row.created_at === "number" ? row.created_at : 0,
    last_active: typeof row.last_activity_at === "number" ? row.last_activity_at : null,
    message_count: typeof row.message_count === "number" ? row.message_count : 0,
    preview: null,
    pinned: false,
    archived: false,
  };
}

/** 已核对到的旧文件夹一律记为待显式映射。不复制、不猜测 primary_path。 */
export function legacyMappingNeeds(folders: readonly { id?: string; name?: string }[]): LegacyMappingNeed[] {
  const needs: LegacyMappingNeed[] = [];
  const seen = new Set<string>();
  for (const folder of folders) {
    const id = typeof folder?.id === "string" ? folder.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const name = typeof folder.name === "string" && folder.name.trim() ? folder.name.trim() : id;
    needs.push({ folder_id: id, name, primary_path: null, status: "needs_explicit_path" });
  }
  return needs;
}

function readStoredNeeds(raw: string | null): LegacyMappingNeed[] {
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const row = item as { folder_id?: unknown; name?: unknown };
      if (typeof row.folder_id !== "string" || !row.folder_id.trim()) return [];
      const name = typeof row.name === "string" && row.name.trim() ? row.name.trim() : row.folder_id;
      return [{ folder_id: row.folder_id, name, primary_path: null, status: "needs_explicit_path" as const }];
    });
  } catch {
    return [];
  }
}

export function rememberLegacyMappingNeeds(
  needs: readonly LegacyMappingNeed[],
  storage: { getItem(key: string): string | null; setItem(key: string, value: string): void },
): LegacyMappingNeed[] {
  const byId = new Map(readStoredNeeds(storage.getItem(LEGACY_MAPPING_KEY)).map((item) => [item.folder_id, item]));
  for (const need of needs) {
    byId.set(need.folder_id, { folder_id: need.folder_id, name: need.name, primary_path: null, status: "needs_explicit_path" });
  }
  const next = [...byId.values()];
  storage.setItem(LEGACY_MAPPING_KEY, JSON.stringify(next));
  return next;
}

export function readCollapsedMap(storage: { getItem(key: string): string | null }): Record<string, boolean> {
  try {
    const parsed: unknown = JSON.parse(storage.getItem(COLLAPSE_KEY) ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "boolean") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

export function writeCollapsed(
  storage: { getItem(key: string): string | null; setItem(key: string, value: string): void },
  id: string,
  collapsed: boolean,
): Record<string, boolean> {
  const next = { ...readCollapsedMap(storage), [id]: collapsed };
  storage.setItem(COLLAPSE_KEY, JSON.stringify(next));
  return next;
}

export function projectActionError(error: unknown, fallback: string): string {
  const status = error && typeof error === "object" && "status" in error ? Number((error as { status: unknown }).status) : 0;
  const message = error instanceof Error ? error.message : "";
  if (status === 409) return "会话正在执行，不能改动项目归属";
  if (status === 503) return "无法确认会话是否空闲，已拒绝改动";
  if (status === 404) return `${fallback}：目标不存在`;
  if (message) return `${fallback}：${message}`;
  return fallback;
}

/** 项目记忆拒绝不能走 projectActionError：409 在那里专指改归属。 */
export const PROJECT_MEMORY_NO_SESSION = "当前没有可授权的会话，项目记忆不可编辑。不会新建或绑定会话。";

export interface ProjectMemoryScope {
  sessionId: string;
  executionGeneration?: number;
}

export type ProjectMemoryScopeRefusal = {
  editable: false;
  code: "no_session" | "stale_generation";
  message: string;
};

/**
 * 身份只接受调用方已经持有的当前会话 id。
 * 不从项目会话列表里取第一条，也不把 URL 里的项目 id 当成会话。
 * generation 只有已经是整数才附带；字符串不转成数字，也不丢弃后当空闲写入。
 */
export function projectMemoryScope(
  sessionId: unknown,
  generation?: unknown,
): ProjectMemoryScope | ProjectMemoryScopeRefusal {
  if (typeof sessionId !== "string" || sessionId.trim().length === 0) {
    return { editable: false, code: "no_session", message: PROJECT_MEMORY_NO_SESSION };
  }
  if (generation == null) return { sessionId: sessionId.trim() };
  if (typeof generation === "number" && Number.isInteger(generation)) {
    return { sessionId: sessionId.trim(), executionGeneration: generation };
  }
  return {
    editable: false,
    code: "stale_generation",
    message: "execution_generation 不是整数，已拒绝编辑，没有把字符串转成数字。",
  };
}

/** 原样展示服务端拒绝。不把 401/403/409 改写成成功，也不套用归属冲突文案。 */
export function projectMemoryRefusalText(error: unknown): string {
  if (error && typeof error === "object") {
    const row = error as { editable?: unknown; message?: unknown; code?: unknown };
    if (row.editable === true) return "项目记忆状态矛盾，未当作可编辑";
    if (typeof row.message === "string" && row.message.trim()) return row.message;
    if (typeof row.code === "string" && row.code.trim()) return row.code;
  }
  if (error instanceof Error && error.message.trim()) return error.message;
  return "项目记忆被拒绝，未当作成功";
}

export interface MigrationMappingInput {
  folder_id: string;
  primary_path: string;
  create_directory: boolean;
}

/** 幂等键只含用户已填写的 folder_id 与目录。复用必须另含原回执 id，不含猜测路径。 */
export function migrationIdempotencyKey(
  folderId: string,
  primaryPath: string,
  createDirectory: boolean,
  reuseMigrationId?: string,
): string {
  const base = `folder:${folderId.trim()}:${primaryPath.trim()}:${createDirectory === true ? "create" : "existing"}`;
  const reuse = reuseMigrationId?.trim();
  return reuse ? `${base}:reuse:${reuse}` : base;
}

export function migrationPlanBody(mapping: MigrationMappingInput): { mappings: MigrationMappingInput[] } {
  const folderId = mapping.folder_id.trim();
  if (!folderId) throw new Error("缺少 folder_id");
  const pathError = projectPathError(mapping.primary_path);
  if (pathError) throw new Error(pathError);
  if (mapping.create_directory !== true && mapping.create_directory !== false) {
    throw new Error("create_directory 必须显式为布尔值");
  }
  return {
    mappings: [{
      folder_id: folderId,
      primary_path: mapping.primary_path.trim(),
      create_directory: mapping.create_directory === true,
    }],
  };
}

export function migrationExecuteBody(mapping: MigrationMappingInput, reuse?: { migration_id: string }): {
  confirm: true;
  confirm_reuse?: true;
  reuse_migration_id?: string;
  idempotency_key: string;
  mappings: MigrationMappingInput[];
} {
  const plan = migrationPlanBody(mapping);
  const item = plan.mappings[0];
  const reuseId = reuse?.migration_id.trim();
  if (reuse && !reuseId) throw new Error("复用必须给出原回执 id");
  return {
    confirm: true,
    ...(reuseId ? { confirm_reuse: true as const, reuse_migration_id: reuseId } : {}),
    idempotency_key: migrationIdempotencyKey(item.folder_id, item.primary_path, item.create_directory, reuseId),
    mappings: plan.mappings,
  };
}

export function migrationRollbackBody(): { confirm: true } {
  return { confirm: true };
}

/** 预览或核对成功都不是迁移成功。 */
export function migrationIsApplied(receipt: { success?: unknown; status?: unknown; operation?: unknown }): boolean {
  return receipt.success === true && receipt.status === "applied" && receipt.operation !== "preview" && receipt.operation !== "plan";
}

export function migrationPlanAccepted(plan: {
  success?: unknown;
  read_only?: unknown;
  operation?: unknown;
  applied?: unknown;
  conflicts?: unknown;
}): boolean {
  return plan.success === true
    && plan.read_only === true
    && plan.operation === "plan"
    && plan.applied !== true
    && (!Array.isArray(plan.conflicts) || plan.conflicts.length === 0);
}

export function migrationFailureText(error: unknown): string {
  if (error && typeof error === "object" && "payload" in error) {
    const payload = (error as { payload?: { message?: unknown; success?: unknown } }).payload;
    if (payload?.success === true) return "迁移回执自相矛盾，未当作成功";
    if (typeof payload?.message === "string" && payload.message.trim()) return payload.message;
  }
  return projectActionError(error, "迁移失败，未当作成功");
}

export interface AutoMigrationError {
  folder_id: string;
  name: string;
  message: string;
}

export interface AutoMigrationResult {
  success: boolean;
  pending: boolean;
  errors: AutoMigrationError[];
}

/** 只接受契约字段。缺 pending 或 success 不是成功，也不把忙状态改写成完成。 */
export function parseAutoMigration(payload: unknown): AutoMigrationResult | null {
  if (!payload || typeof payload !== "object") return null;
  const row = payload as { success?: unknown; pending?: unknown; errors?: unknown };
  if (typeof row.success !== "boolean" || typeof row.pending !== "boolean" || !Array.isArray(row.errors)) return null;
  const errors: AutoMigrationError[] = [];
  for (const item of row.errors) {
    if (!item || typeof item !== "object") {
      errors.push({ folder_id: "", name: "", message: "" });
      continue;
    }
    const err = item as { folder_id?: unknown; name?: unknown; message?: unknown };
    errors.push({
      folder_id: typeof err.folder_id === "string" ? err.folder_id : "",
      name: typeof err.name === "string" ? err.name : "",
      message: typeof err.message === "string" ? err.message : "",
    });
  }
  return { success: row.success, pending: row.pending, errors };
}

/** 只有明确完成且没有错误才算迁移结束。pending 或错误都不是成功。 */
export function autoMigrationApplied(result: AutoMigrationResult): boolean {
  return result.success === true && result.pending === false && result.errors.length === 0;
}

/** 给界面的固定短句。不拼 folder_id、名称或服务端 message，避免露出内部 ID。 */
export function autoMigrationHint(result: AutoMigrationResult): string {
  if (result.pending) return "旧文件夹正在迁移，可重试";
  return "旧文件夹迁移未完成，可重试";
}
