import { useQuery, useMutation, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";
import { api, type CronJob, type CronJobInput, type HealthDetailed, type HealthResponse, type MessagePage, type ModelOptions, type SessionPatch, type SessionSummary } from "./api.js";
import {
  createCustomEndpoint,
  createMcp,
  getAdvanced,
  getPluginConfig,
  listConnectionPlatforms,
  listMemoryProviders,
  listToolsets,
  setAdvanced,
  setConnectionPlatformEnabled,
  setMemoryProviderConfig,
  setPluginConfig,
  setSkillEnabled,
  setToolsetEnabled,
  type AdvancedRead,
  type ConnectionEnabledResult,
  type ConnectionPlatformsResult,
  type CustomEndpointInput,
  type CustomEndpointResult,
  type McpCreateInput,
  type McpCreateResult,
  type MemoryProviderConfigResult,
  type MemoryProvidersResult,
  type PluginConfigResult,
  type SkillEnabledResult,
  type ToolsetEnabledResult,
  type ToolsetsResult,
} from "./admin-api.js";
import type { EditBoundary } from "./message-edit.js";
import {
  sidecar,
  type ActionResult,
  type ActivityItem,
  type BackgroundProcess,
  type ConfigSetResult,
  type ConfigView,
  type ContextUsage,
  type CronModelView,
  type DirListing,
  type FileRoot,
  type HostStatus,
  type ImageItem,
  type ImageModel,
  type KanbanTaskDetail,
  type KanbanTasks,
  type LogQuery,
  type LogResult,
  type LogSources,
  type McpServer,
  type ModelsView,
  type PluginInfo,
  type ProfilesView,
  type SessionFolders,
  type SessionFolderAction,
} from "./sidecar.js";
import { loadProjectIndex, projectsApi, type ProjectIndex, type ProjectRecord } from "./projects.js";
import type { CreateProjectBody } from "./project-model.js";
import {
  type SettingsView,
  type Skill,
  type SkillBackup,
  type SubagentsView,
  type UpdateChannel,
  type UpdateInfo,
  type UpdateRun,
  type Usage,
  type WebhookCreate,
  type WebhookDelivery,
  type WebhooksView,
} from "./sidecar.js";

export const queryKeys = {
  sessions: ["sessions"] as const,
  sessionHead: ["sessions", "head", { limit: 30 }] as const,
  activeSessions: ["sessions", "active"] as const,
  messages: (sessionId: string, offset: number, limit: number) =>
    ["session", sessionId, "messages", { order: "latest", offset, limit, includeCompacted: true }] as const,
  modelOptions: ["model-options"] as const,
  jobs: ["jobs"] as const,
  health: ["health"] as const,
  healthDetailed: ["health-detailed"] as const,
  hostStatus: ["host-status"] as const,
  activity: (limit: number) => ["activity", { limit }] as const,
  kanbanTasks: (status: string | null) => ["kanban-tasks", status] as const,
  kanbanTask: (id: string) => ["kanban-task", id] as const,
  skills: ["skills"] as const,
  skill: (path: string) => ["skill", path] as const,
  skillBackups: (path: string) => ["skill-backups", path] as const,
  settings: ["settings"] as const,
  config: ["config"] as const,
  modelsAdmin: ["models-admin"] as const,
  cronModel: ["cron-model"] as const,
  mcp: ["mcp"] as const,
  plugins: ["plugins"] as const,
  profiles: ["profiles"] as const,
  webhooks: ["webhooks"] as const,
  usage: (days: number) => ["usage", days] as const,
  logSources: ["log-sources"] as const,
  logs: (q: LogQuery) => ["logs", q.source, q.lines, q.level, q.component, q.q] as const,
  toolsets: ["toolsets"] as const,
  memoryProviders: ["memory-providers"] as const,
  connectionPlatforms: ["connection-platforms"] as const,
  pluginConfig: (name: string) => ["plugin-config", name] as const,
  advanced: (key: string) => ["settings-advanced", key] as const,
  updateInfo: ["update-info"] as const,
  updateRun: ["update-run"] as const,
  updatePlan: ["update-plan"] as const,
  fileRoots: ["file-roots"] as const,
  dir: (root: string, path: string) => ["dir", root, path] as const,
  imageModels: ["image-models"] as const,
  imageHistory: ["image-history"] as const,
  subagents: (sessionId: string, limit: number) => ["subagents", sessionId, limit] as const,
  context: (sessionId: string) => ["context", sessionId] as const,
  folders: ["session-folders"] as const,
  projects: ["projects"] as const,
  legacyFolders: ["legacy-folders"] as const,
  commentary: (ids: readonly number[]) => ["commentary", ids] as const,
  background: (sessionId: string) => ["session-background", sessionId] as const,
};

export const QUERY_FORBIDDEN = [
  "startRun",
  "steer",
  "approve",
  "clarify",
  "stopRun",
  "resolveRun",
  "getTaskLiveFrame",
  "controlTaskLive",
  "sendTaskLiveInput",
] as const;

export function useSessionList(
  opts: { limit?: number; offset?: number; source?: string } = {},
  enabled = true,
): UseQueryResult<Awaited<ReturnType<typeof api.listSessions>>> {
  return useQuery({
    queryKey: [...queryKeys.sessions, opts] as const,
    queryFn: ({ signal }) => api.listSessions(opts, signal),
    enabled,
  });
}

export function useSessionHead(enabled: boolean): UseQueryResult<Awaited<ReturnType<typeof api.listSessions>>> {
  return useQuery({
    queryKey: queryKeys.sessionHead,
    queryFn: ({ signal }) => api.listSessions({ limit: 30 }, signal),
    enabled,
    refetchInterval: enabled ? 30_000 : false,
  });
}

export function useActiveSessions(visible: boolean): UseQueryResult<SessionSummary[]> {
  return useQuery({
    queryKey: queryKeys.activeSessions,
    queryFn: ({ signal }) => sidecar.activeSessions(signal),
    enabled: visible,
    refetchInterval: visible ? 5_000 : false,
  });
}

export function useCronSessions(jobId: string, enabled: boolean): UseQueryResult<SessionSummary[]> {
  return useQuery({
    queryKey: [...queryKeys.sessions, { source: "cron", limit: 200, jobId }] as const,
    queryFn: ({ signal }) => api.listSessions({ source: "cron", limit: 200 }, signal),
    enabled: enabled && jobId.length > 0,
    select: (page) => page.data.filter((session) => session.id.startsWith(`cron_${jobId}_`)).slice(0, 10),
  });
}

export function useSessionMessages(sessionId: string, offset: number, limit: number, enabled = true): UseQueryResult<MessagePage> {
  return useQuery({
    queryKey: queryKeys.messages(sessionId, offset, limit),
    queryFn: ({ signal }) => api.messages(sessionId, { limit, offset }, signal),
    enabled: enabled && sessionId.length > 0,
  });
}

export function useModelOptions(): UseQueryResult<ModelOptions> {
  return useQuery({ queryKey: queryKeys.modelOptions, queryFn: () => api.modelOptions() });
}

export function useJobs(): UseQueryResult<CronJob[]> {
  return useQuery({ queryKey: queryKeys.jobs, queryFn: () => api.jobs() });
}

export function useHealth(visible = true): UseQueryResult<HealthResponse> {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.health(signal),
    enabled: visible,
    refetchInterval: (query) => (visible ? (query.state.status === "error" ? 5_000 : 30_000) : false),
  });
}

export function useHealthDetailed(visible = true): UseQueryResult<HealthDetailed> {
  return useQuery({
    queryKey: queryKeys.healthDetailed,
    queryFn: () => api.healthDetailed(),
    enabled: visible,
    refetchInterval: visible ? 10_000 : false,
  });
}

export function useHostStatus(visible = true): UseQueryResult<HostStatus> {
  return useQuery({
    queryKey: queryKeys.hostStatus,
    queryFn: () => sidecar.status(),
    enabled: visible,
    refetchInterval: visible ? 10_000 : false,
  });
}

export function useActivity(limit = 80, visible = true): UseQueryResult<ActivityItem[]> {
  return useQuery({
    queryKey: queryKeys.activity(limit),
    queryFn: () => sidecar.activity(limit),
    enabled: visible,
    refetchInterval: visible ? 10_000 : false,
  });
}

export function useSkills(): UseQueryResult<Skill[]> {
  return useQuery({ queryKey: queryKeys.skills, queryFn: () => sidecar.skills() });
}

export function useSkill(path: string, enabled = true): UseQueryResult<{ path: string; content: string; modifiedAt: number }> {
  return useQuery({
    queryKey: queryKeys.skill(path),
    queryFn: () => sidecar.skill(path),
    enabled: enabled && path.length > 0,
  });
}

export function useSkillBackups(path: string, enabled = true): UseQueryResult<SkillBackup[]> {
  return useQuery({
    queryKey: queryKeys.skillBackups(path),
    queryFn: () => sidecar.skillBackups(path),
    enabled: enabled && path.length > 0,
  });
}

export function useSkillBackup(path: string, id: string, enabled = true): UseQueryResult<{ id: string; content: string }> {
  return useQuery({
    queryKey: ["skill-backup", path, id] as const,
    queryFn: () => sidecar.skillBackup(path, id),
    enabled: enabled && path.length > 0 && id.length > 0,
  });
}

export function useSettings(): UseQueryResult<SettingsView> {
  return useQuery({ queryKey: queryKeys.settings, queryFn: () => sidecar.settings() });
}

export function useConfigYaml(): UseQueryResult<ConfigView> {
  return useQuery({ queryKey: queryKeys.config, queryFn: () => sidecar.config() });
}

export function useModelsAdmin(): UseQueryResult<ModelsView> {
  return useQuery({ queryKey: queryKeys.modelsAdmin, queryFn: () => sidecar.models() });
}

export function useCronModel(): UseQueryResult<CronModelView> {
  return useQuery({ queryKey: queryKeys.cronModel, queryFn: () => sidecar.cronModel() });
}

export function useMcpServers(): UseQueryResult<McpServer[]> {
  return useQuery({ queryKey: queryKeys.mcp, queryFn: () => sidecar.mcp() });
}

export function usePlugins(): UseQueryResult<PluginInfo[]> {
  return useQuery({ queryKey: queryKeys.plugins, queryFn: () => sidecar.plugins() });
}

export function useProfiles(): UseQueryResult<ProfilesView> {
  return useQuery({ queryKey: queryKeys.profiles, queryFn: () => sidecar.profiles() });
}

export function useProfileDetail(name: string, enabled = true): UseQueryResult<{ name: string; text: string }> {
  return useQuery({
    queryKey: ["profile", name] as const,
    queryFn: () => sidecar.profileDetail(name),
    enabled: enabled && name.length > 0,
  });
}

export function useWebhooks(): UseQueryResult<WebhooksView> {
  return useQuery({ queryKey: queryKeys.webhooks, queryFn: () => sidecar.webhooks() });
}

export function useWebhookDeliveries(route = "", limit = 30, visible = true): UseQueryResult<WebhookDelivery[]> {
  return useQuery({
    queryKey: ["webhook-deliveries", route, limit] as const,
    queryFn: () => sidecar.webhookDeliveries(route, limit),
    enabled: visible,
    refetchInterval: visible ? 15_000 : false,
  });
}

export function useUsage(days: number, visible = true): UseQueryResult<Usage> {
  return useQuery({
    queryKey: queryKeys.usage(days),
    queryFn: () => sidecar.usage(days),
    enabled: visible,
    staleTime: 60_000,
  });
}

export function useLogSources(): UseQueryResult<LogSources> {
  return useQuery({ queryKey: queryKeys.logSources, queryFn: () => sidecar.logSources() });
}

export function useLogs(q: LogQuery, follow: boolean): UseQueryResult<LogResult> {
  return useQuery({
    queryKey: queryKeys.logs(q),
    queryFn: () => sidecar.logs(q),
    refetchInterval: follow ? 5_000 : false,
  });
}

export function useUpdateInfo(force = false, channel?: UpdateChannel): UseQueryResult<UpdateInfo> {
  return useQuery({
    queryKey: [...queryKeys.updateInfo, { force, channel }] as const,
    queryFn: () => sidecar.updateCheck(force, channel),
  });
}

export function useUpdateRun(running: boolean): UseQueryResult<UpdateRun> {
  return useQuery({
    queryKey: queryKeys.updateRun,
    queryFn: () => sidecar.updateStatus(),
    refetchInterval: running ? 3_000 : false,
  });
}

export function useUpdatePlan(enabled: boolean): UseQueryResult<{ plan: string; exitCode: number }> {
  return useQuery({
    queryKey: queryKeys.updatePlan,
    queryFn: () => sidecar.updatePlan(),
    enabled,
  });
}

export function useKanbanTasks(status: string | null, visible = true): UseQueryResult<KanbanTasks> {
  return useQuery({
    queryKey: queryKeys.kanbanTasks(status),
    queryFn: () => sidecar.kanbanTasks(status ?? undefined),
    enabled: visible,
    refetchInterval: visible ? 10_000 : false,
  });
}

export function useKanbanTask(id: string | null, visible = true): UseQueryResult<KanbanTaskDetail> {
  return useQuery({
    queryKey: queryKeys.kanbanTask(id ?? ""),
    queryFn: () => sidecar.kanbanTask(id ?? ""),
    enabled: visible && !!id,
  });
}

export function useFileRoots(): UseQueryResult<FileRoot[]> {
  return useQuery({ queryKey: queryKeys.fileRoots, queryFn: () => sidecar.fileRoots() });
}

export function useDir(root: string, path: string, enabled = true): UseQueryResult<DirListing> {
  return useQuery({
    queryKey: queryKeys.dir(root, path),
    queryFn: () => sidecar.listDir(root, path),
    enabled,
  });
}

export function useImageModels(): UseQueryResult<ImageModel[]> {
  return useQuery({ queryKey: queryKeys.imageModels, queryFn: () => sidecar.imageModels() });
}

export function useImageHistory(): UseQueryResult<ImageItem[]> {
  return useQuery({ queryKey: queryKeys.imageHistory, queryFn: () => sidecar.imageHistory() });
}

/** 调用方拿到的仍是 SubagentsView。limit 只作页大小；第 51 个及以后的 child 在同一次查询里收齐。不新增轮询。 */
export function useSubagents(
  sessionId: string,
  limit: number,
  refetchInterval: number | false = false,
  enabled = true,
): UseQueryResult<SubagentsView> {
  return useQuery({
    queryKey: queryKeys.subagents(sessionId, limit),
    queryFn: ({ signal }) => sidecar.collectSubagents(sessionId, limit, signal),
    refetchInterval,
    enabled,
  });
}

export function useContextUsage(sessionId: string, enabled = true): UseQueryResult<ContextUsage> {
  return useQuery({
    queryKey: queryKeys.context(sessionId),
    queryFn: () => sidecar.context(sessionId),
    enabled: enabled && sessionId.length > 0,
  });
}

export function useProjects(): UseQueryResult<ProjectIndex> {
  return useQuery({ queryKey: queryKeys.projects, queryFn: () => loadProjectIndex() });
}

/** 只读核对没有目录的旧文件夹。失败必须保持错误，不能当成没有旧数据，也不能充当项目列表。 */
export function useLegacyFolders(): UseQueryResult<SessionFolders> {
  return useQuery({
    queryKey: queryKeys.legacyFolders,
    queryFn: async () => {
      const payload = await sidecar.sessionFolders();
      if (!payload || !Array.isArray(payload.folders)) throw new Error("旧文件夹核对返回格式异常");
      return payload;
    },
  });
}

export function useSessionFolders(): UseQueryResult<SessionFolders> {
  return useQuery({
    queryKey: queryKeys.folders,
    queryFn: () => Promise.reject(new Error("项目列表不能回退侧车文件夹")),
  });
}

export function useSessionBackground(sessionId: string, visible: boolean): UseQueryResult<BackgroundProcess[]> {
  return useQuery({
    queryKey: queryKeys.background(sessionId),
    queryFn: ({ signal }) => sidecar.sessionBackground(sessionId, signal),
    enabled: visible && sessionId.length > 0,
    refetchInterval: visible ? 10_000 : false,
  });
}

function commentaryFromMessages(client: ReturnType<typeof useQueryClient>, ids: readonly number[]): { items: Record<string, string[]> } {
  const wanted = new Set(ids);
  const items: Record<string, string[]> = {};
  for (const query of client.getQueryCache().getAll()) {
    const data = query.state.data as { messages?: Array<{ id?: number; display_commentary?: unknown }> } | undefined;
    const messages = data && Array.isArray(data.messages) ? data.messages : [];
    for (const message of messages) {
      if (!message || typeof message.id !== "number" || !wanted.has(message.id) || !Array.isArray(message.display_commentary)) continue;
      const texts = message.display_commentary.filter((text): text is string => typeof text === "string" && text.length > 0);
      if (texts.length) items[String(message.id)] = texts;
    }
  }
  return { items };
}

export function useCommentary(ids: readonly number[], enabled = true): UseQueryResult<{ items: Record<string, string[]> }> {
  const client = useQueryClient();
  return useQuery({
    queryKey: queryKeys.commentary(ids),
    queryFn: () => commentaryFromMessages(client, ids),
    enabled: enabled && ids.length > 0,
  });
}

function useInvalidate() {
  const client = useQueryClient();
  return (key: readonly unknown[]) => client.invalidateQueries({ queryKey: key });
}

export function useCreateSession(): UseMutationResult<SessionSummary, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (title: string) => api.createSession(title),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function usePatchSession(): UseMutationResult<unknown, Error, { sessionId: string; patch: SessionPatch }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ sessionId, patch }) => api.patchSession(sessionId, patch),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function useDeleteSession(): UseMutationResult<unknown, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (sessionId: string) => api.deleteSession(sessionId),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function useForkSession(): UseMutationResult<SessionSummary, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (sessionId: string) => api.forkSession(sessionId),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function useLockModel(): UseMutationResult<unknown, Error, { sessionId: string; model: string; provider: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ sessionId, model, provider }) => api.lockModel(sessionId, model, provider),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function useCreateJob(): UseMutationResult<CronJob, Error, CronJobInput> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: CronJobInput) => api.createJob(input),
    onSuccess: () => invalidate(queryKeys.jobs),
  });
}

export function useUpdateJob(): UseMutationResult<CronJob, Error, { id: string; patch: Partial<CronJobInput> }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, patch }) => api.updateJob(id, patch),
    onSuccess: () => invalidate(queryKeys.jobs),
  });
}

export function useDeleteJob(): UseMutationResult<unknown, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: string) => api.deleteJob(id), onSuccess: () => invalidate(queryKeys.jobs) });
}

export function usePauseJob(): UseMutationResult<unknown, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: string) => api.pauseJob(id), onSuccess: () => invalidate(queryKeys.jobs) });
}

export function useResumeJob(): UseMutationResult<unknown, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: string) => api.resumeJob(id), onSuccess: () => invalidate(queryKeys.jobs) });
}

export function useRunJob(): UseMutationResult<unknown, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: string) => api.runJob(id), onSuccess: () => invalidate(queryKeys.jobs) });
}

export function useRestartService(): UseMutationResult<{ ok: boolean }, Error, { name: string; force?: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, force = false }) => sidecar.restart(name, force),
    onSuccess: () => invalidate(queryKeys.hostStatus),
  });
}

export function useSaveSkill(): UseMutationResult<
  { ok: boolean; unchanged: boolean; modifiedAt: number; backup?: string },
  Error,
  { path: string; content: string; baseModifiedAt: number }
> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ path, content, baseModifiedAt }) => sidecar.saveSkill(path, content, baseModifiedAt),
    onSuccess: (_data, vars) => {
      void invalidate(queryKeys.skills);
      void invalidate(queryKeys.skill(vars.path));
    },
  });
}

export function useSetSkillEnabled(): UseMutationResult<SkillEnabledResult, Error, { name: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, enabled }) => setSkillEnabled(name, enabled),
    onSuccess: () => invalidate(queryKeys.skills),
  });
}

export function useUpdateStart(): UseMutationResult<
  { ok: boolean; runId: string },
  Error,
  { tag?: string; channel?: UpdateChannel }
> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ tag, channel }) => sidecar.updateStart(tag, channel),
    onSuccess: () => invalidate(queryKeys.updateRun),
  });
}

export function useSetSetting(): UseMutationResult<{ ok: boolean }, Error, { key: string; value: unknown }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ key, value }) => sidecar.setSetting(key, value),
    onSuccess: () => invalidate(queryKeys.settings),
  });
}

export function useResetSetting(): UseMutationResult<{ ok: boolean }, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (key: string) => sidecar.resetSetting(key),
    onSuccess: () => invalidate(queryKeys.settings),
  });
}

export function useSetConfig(): UseMutationResult<ConfigSetResult, Error, { key: string; value: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ key, value }) => sidecar.setConfig(key, value),
    onSuccess: () => invalidate(queryKeys.config),
  });
}

export function useDeleteFile(): UseMutationResult<{ ok: boolean }, Error, { root: string; path: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ root, path }) => sidecar.deleteFile(root, path),
    onSuccess: (_data, vars) => invalidate(queryKeys.dir(vars.root, vars.path)),
  });
}

export function useCreateProject(): UseMutationResult<ProjectRecord, Error, CreateProjectBody> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body) => projectsApi.create(body),
    onSuccess: () => invalidate(queryKeys.projects),
  });
}

export function usePatchProject(): UseMutationResult<ProjectRecord, Error, { id: string; patch: { name?: string; archived?: boolean } }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, patch }) => projectsApi.patch(id, patch),
    onSuccess: () => invalidate(queryKeys.projects),
  });
}

export function useBindSessionProject(): UseMutationResult<Awaited<ReturnType<typeof projectsApi.bind>>, Error, { sessionId: string; projectId: string | null }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ sessionId, projectId }) => projectsApi.bind(sessionId, projectId),
    onSuccess: () => invalidate(queryKeys.projects),
  });
}

export function useUpdateSessionFolders(): UseMutationResult<SessionFolders, Error, SessionFolderAction> {
  return useMutation({
    mutationFn: () => Promise.reject(new Error("项目事实不能写入侧车；请使用 /api/projects")),
  });
}

export function useSetDefaultModel(): UseMutationResult<ActionResult, Error, { model: string; provider?: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ model, provider }) => sidecar.setDefaultModel(model, provider),
    onSuccess: () => {
      void invalidate(queryKeys.modelsAdmin);
      void invalidate(queryKeys.modelOptions);
    },
  });
}

export function useSetCronModel(): UseMutationResult<ActionResult, Error, { model: string; provider?: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ model, provider }) => sidecar.setCronModel(model, provider),
    onSuccess: () => invalidate(queryKeys.cronModel),
  });
}

export function useSetProviderModels(): UseMutationResult<ActionResult, Error, { provider: string; models: string[] }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ provider, models }) => sidecar.setProviderModels(provider, models),
    onSuccess: () => invalidate(queryKeys.modelsAdmin),
  });
}

export function useSetProviderDiscover(): UseMutationResult<ActionResult, Error, { provider: string; discover: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ provider, discover }) => sidecar.setProviderDiscover(provider, discover),
    onSuccess: () => invalidate(queryKeys.modelsAdmin),
  });
}

export function useSetExcludedProviders(): UseMutationResult<ActionResult, Error, string[]> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (providers: string[]) => sidecar.setExcludedProviders(providers),
    onSuccess: () => invalidate(queryKeys.modelsAdmin),
  });
}

export function useSetMcpEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, enabled }) => sidecar.setMcpEnabled(name, enabled),
    onSuccess: () => invalidate(queryKeys.mcp),
  });
}

export function useRemoveMcp(): UseMutationResult<ActionResult, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => sidecar.removeMcp(name),
    onSuccess: () => invalidate(queryKeys.mcp),
  });
}

export function useTestMcp(): UseMutationResult<{ ok: boolean; output: string }, Error, string> {
  return useMutation({ mutationFn: (name: string) => sidecar.testMcp(name) });
}

export function useSetPluginEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, enabled }) => sidecar.setPluginEnabled(name, enabled),
    onSuccess: () => invalidate(queryKeys.plugins),
  });
}

export function useCreateProfile(): UseMutationResult<ActionResult, Error, { name: string; cloneFrom: string | null; description: string }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, cloneFrom, description }) => sidecar.createProfile(name, cloneFrom, description),
    onSuccess: () => invalidate(queryKeys.profiles),
  });
}

export function useUseProfile(): UseMutationResult<ActionResult, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => sidecar.useProfile(name),
    onSuccess: () => {
      void invalidate(queryKeys.profiles);
      void invalidate(queryKeys.modelOptions);
      void invalidate(queryKeys.jobs);
      void invalidate(queryKeys.settings);
    },
  });
}

export function useDeleteProfile(): UseMutationResult<ActionResult, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => sidecar.deleteProfile(name),
    onSuccess: () => invalidate(queryKeys.profiles),
  });
}

export function useCreateWebhook(): UseMutationResult<{ ok: boolean; name: string; path: string; secret: string }, Error, WebhookCreate> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: WebhookCreate) => sidecar.createWebhook(body),
    onSuccess: () => invalidate(queryKeys.webhooks),
  });
}

export function useDeleteWebhook(): UseMutationResult<ActionResult, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => sidecar.deleteWebhook(name),
    onSuccess: () => invalidate(queryKeys.webhooks),
  });
}

export function useSetWebhookEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, enabled }) => sidecar.setWebhookEnabled(name, enabled),
    onSuccess: () => invalidate(queryKeys.webhooks),
  });
}

export function useTestWebhook(): UseMutationResult<{ ok: boolean; output: string }, Error, string> {
  return useMutation({ mutationFn: (name: string) => sidecar.testWebhook(name) });
}

export function useImageGenerate(): UseMutationResult<
  { item: ImageItem; historySaved: boolean },
  Error,
  { model: string; prompt: string; aspect: string }
> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ model, prompt, aspect }) => sidecar.imageGenerate(model, prompt, aspect),
    retry: false,
    onSuccess: () => invalidate(queryKeys.imageHistory),
  });
}

export function useImageDelete(): UseMutationResult<{ ok: boolean }, Error, string> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => sidecar.imageDelete(id),
    onSuccess: () => invalidate(queryKeys.imageHistory),
  });
}

export function useStopExecution(): UseMutationResult<{ status: "cancelled" | "signalled"; busy: boolean }, Error, { sessionId: string; executionId: string }> {
  return useMutation({
    mutationFn: ({ sessionId, executionId }) => sidecar.stopExecution(sessionId, executionId),
  });
}

export function useEditBranch(): UseMutationResult<
  SessionSummary,
  Error,
  EditBoundary & { sessionId: string; requestId: string; model: string; provider: string }
> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body) => sidecar.editBranch(body),
    onSuccess: () => invalidate(queryKeys.sessions),
  });
}

export function useToolsets(enabled = true): UseQueryResult<ToolsetsResult> {
  return useQuery({ queryKey: queryKeys.toolsets, queryFn: () => listToolsets(), enabled });
}

export function useSetToolsetEnabled(): UseMutationResult<ToolsetEnabledResult, Error, { name: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, enabled }) => setToolsetEnabled(name, enabled),
    onSuccess: () => invalidate(queryKeys.toolsets),
  });
}

export function useMemoryProviders(enabled = true): UseQueryResult<MemoryProvidersResult> {
  return useQuery({ queryKey: queryKeys.memoryProviders, queryFn: () => listMemoryProviders(), enabled });
}

export function useSetMemoryProviderConfig(): UseMutationResult<MemoryProviderConfigResult, Error, { name: string; fields: Record<string, unknown> }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, fields }) => setMemoryProviderConfig(name, fields),
    onSuccess: () => invalidate(queryKeys.memoryProviders),
  });
}

export function useConnectionPlatforms(enabled = true): UseQueryResult<ConnectionPlatformsResult> {
  return useQuery({ queryKey: queryKeys.connectionPlatforms, queryFn: () => listConnectionPlatforms(), enabled });
}

export function useSetConnectionPlatformEnabled(): UseMutationResult<ConnectionEnabledResult, Error, { id: string; enabled: boolean }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, enabled }) => setConnectionPlatformEnabled(id, enabled),
    onSuccess: () => invalidate(queryKeys.connectionPlatforms),
  });
}

export function usePluginConfig(name: string, enabled = true): UseQueryResult<PluginConfigResult> {
  return useQuery({
    queryKey: queryKeys.pluginConfig(name),
    queryFn: () => getPluginConfig(name),
    enabled: enabled && name.length > 0,
  });
}

export function useSetPluginConfig(): UseMutationResult<PluginConfigResult, Error, { name: string; fields: Record<string, unknown> }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, fields }) => setPluginConfig(name, fields),
    onSuccess: (_data, vars) => {
      void invalidate(queryKeys.pluginConfig(vars.name));
      void invalidate(queryKeys.plugins);
    },
  });
}

export function useAdvanced(key: string, enabled = true): UseQueryResult<AdvancedRead> {
  return useQuery({
    queryKey: queryKeys.advanced(key),
    queryFn: () => getAdvanced(key),
    enabled: enabled && key.length > 0,
  });
}

export function useSetAdvanced(): UseMutationResult<AdvancedRead, Error, { key: string; value: unknown }> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ key, value }) => setAdvanced(key, value),
    onSuccess: (_data, vars) => {
      void invalidate(queryKeys.advanced(vars.key));
      void invalidate(queryKeys.settings);
    },
  });
}

export function useCreateMcp(): UseMutationResult<McpCreateResult, Error, McpCreateInput> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input) => createMcp(input),
    onSuccess: () => invalidate(queryKeys.mcp),
  });
}

export function useCreateCustomEndpoint(): UseMutationResult<CustomEndpointResult, Error, CustomEndpointInput> {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input) => createCustomEndpoint(input),
    onSuccess: () => invalidate(queryKeys.modelsAdmin),
  });
}
