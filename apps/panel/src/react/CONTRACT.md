# React 地基契约

后续页面代理只改自己的目录。共享文件只有地基和总指挥能改。需要改共享文件时写进报告，不要自己改。

## 路由

`createHashRouter`，SPA，不做 SSR。对话页在布局里始终挂载，离开时只加 `hidden`（`display: none`），不卸载。这一阶段没有离场动画，所以一切走就隐藏。以后的离场动画结束前不能加 `hidden`。路由动画不能加在玻璃元素的祖先上，祖先不得有 `transform`、`opacity < 1`、`filter`、`mask`、`clip-path`、`will-change`。

| 路径 | 页面 | 加载 |
| --- | --- | --- |
| `/` | 对话 | 常驻，不懒加载 |
| `/image` | 生图 | `React.lazy` |
| `/files` | 文件 | `React.lazy` |
| `/activity` | 活动 | `React.lazy` |
| `/status` | 状态 | `React.lazy` |
| `/settings` | 设置，标签按配置 | `React.lazy` |
| `/settings/:tab` | 设置标签 | `React.lazy` |
| `/jobs` | 替换到 `/settings/jobs` | 重定向 |
| `/skills` | 替换到 `/settings/skills` | 重定向 |
| `/admin`、`/admin/:tab` | 已知标签进对应设置，否则配置 | 重定向 |
| 其他 | 对话 | 重定向 |

设置标签只有：`config` 配置、`jobs` 定时任务、`skills` 技能、`usage` 用量、`logs` 日志、`models` 模型、`mcp` MCP、`plugins` 插件与配置档、`webhooks` Webhooks、`update` 更新。未知标签替换到 `/settings/config`。

空 hash 第一次解析是 `/`，仍显示对话。库在导航到 `/` 时会写成 `#/`，不再是 Lit 的空串。不要把路由再存进 Zustand。

## 目录所有权

- `src/react/shell/` 布局
- `src/react/pages/chat/` 对话
- `src/react/pages/image/` 生图
- `src/react/pages/files/` 文件
- `src/react/pages/activity/` 活动
- `src/react/pages/status/` 状态
- `src/react/pages/settings/` 设置，含定时任务
- `src/react/docks/` 实时画面、任务画面、子代理详情
- `src/react/components/ui/` 基础组件。Dialog、Popover、DropdownMenu 实心，Portal 到 `document.body`，不再套玻璃
- `src/react/styles/` 令牌、`.glass`、弹簧
- `src/react/stores/ui.ts` 纯客户端状态
- `src/react/lib/queries.ts`、`src/react/lib/stream.ts` 数据契约
- `src/react/lib/` 里从 Lit 抄来的文件只改导入路径。这一阶段导入路径没有改，函数体未动

同屏玻璃只给侧栏、顶栏、输入框。玻璃只用 `.glass`，不要各处重写 `backdrop-filter`。不要玻璃套玻璃。玻璃不要持续动画。按下缩放加在实心按钮上，不要加在玻璃容器上。

## Zustand

`useUiStore` 只放这些键。路由不在里面。

| 键 | 类型 | 存储 |
| --- | --- | --- |
| `drawerOpen` | `boolean` | 内存，默认 `false` |
| `themePref` | `"system" \| "light" \| "dark"` | `localStorage` `hp.theme`。`system` 会删掉该键和 `data-theme` |
| `effortBySession` | `Record<string, string>` | 读 `hp_effort_by_session`。写入必须走抄来的 `setEffort`，因为 `runs.ts` 读的是那份内存 |
| `effortNext` | `string` | `currentEffort("")`，默认 `medium` |
| `composerDraft` | `{ value: string; attachments: Attachment[] }` | 内存 |
| `sessionListFilter` | `"live" \| "chats" \| "auto"` | 内存，默认 `chats` |
| `sessionListQuery` | `string` | 内存 |
| `selectedSessionId` | `string \| null` | 内存。不在 hash 里 |
| `liveView` | `LiveView \| null` | `sessionStorage` `hp-live-view-v1`。不含 clientId 或租约 |
| `imageModelPref` | `string` | `hp.image.model` |
| `imageAspectPref` | `"square" \| "landscape" \| "portrait"` | `hp.image.aspect`，无效值回 `square` |

动作：`setDrawerOpen`、`setThemePref`、`setEffortFor(level, sessionId?)`、`setComposerDraft`、`setSessionListFilter`、`setSessionListQuery`、`setSelectedSessionId`、`setLiveView`、`setImageModelPref`、`setImageAspectPref`。

`sessionModel` 是双写，不进这个 store。浏览器侧是 `chat-selection.ts` 的 `hp_chat_model_by_session` / `hp_chat_model_next`；服务端是 `POST api/sessions/{id}/model`。先改界面，`lockModel` 成功后再 `rememberChatModel`。`effortNext` 不是双写。`hp.theme` 和设置里的 `dashboard.theme` 不是同一个开关。

## 流式

`src/react/lib/stream.ts` 只调用抄来的 `runs`，不重写状态机。`LiveRun` 被状态机就地修改，hook 在 `hp-run-changed` / `hp-run-finished` 上加版本并浅拷贝，和 `hp-chat.ts` 的 `snapshot` 一样。不要把 `LiveRun` 放进 Query。

```ts
interface UseLiveRun {
  run: LiveRun | null;
  isActive: boolean;
  start: (sessionId: string, text: string, attachments: Attachment[], content: OutgoingContent, model?: string, provider?: string, accepted?: { run_id: string; status: string }) => void;
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
function useLiveRun(sessionId: string): UseLiveRun;
function useResumeRuns(): string[];
```

`useResumeRuns` 整个应用只调用 `runs.resumeAll()` 一次。壳已经调用。不要再包一层自动重试。

## Query

`QueryClient` 的 mutation 默认 `retry: false`。`useImageGenerate` 再显式 `retry: false`。

禁止进 Query，也不要设自动重试：`startRun`、`steer`、`approve`、`clarify`、`stopRun`、`resolveRun`。任务画面 `getTaskLiveFrame` / `controlTaskLive` / `sendTaskLiveInput` 不进 Query。noVNC（`/live/websockify`、`/live/core/rfb.js`）不进 Query。`liveState`、`liveTakeover`、`liveRelease`、`liveType` 跟租约和画面走，不进 Query。`sessionRun`、`sessionExecution`、`runWait`、`runStatus` 跟发现轮询和状态机走，不进 Query。`uploadFile` 的进度不进 Query。`renewCredential` 不进 Query。`messages` 里的 `sessions/message-origins` 不要另开一个失败会拖垮历史的 Query。

轮询数字来自调用方：活动会话可见时 5000；列表顶栏非实时可见时 30000；健康在线 30000、失败 5000，隐藏时不打；状态和活动可见时 10000；日志跟随 5000；Webhook 投递可见时 15000；更新进行中 3000；后台进程可见时 10000。用量不是固定间隔，`staleTime` 60000。子代理间隔由调用方传入：会话视图有运行时 4000，dock 有运行时 3000。没有源码间隔的端点不要发明 interval。

```ts
function useSessionList(opts?: { limit?: number; offset?: number; source?: string }, enabled?: boolean): UseQueryResult<Awaited<ReturnType<typeof api.listSessions>>>;
function useSessionHead(enabled: boolean): UseQueryResult<Awaited<ReturnType<typeof api.listSessions>>>;
function useActiveSessions(visible: boolean): UseQueryResult<SessionSummary[]>;
function useCronSessions(jobId: string, enabled: boolean): UseQueryResult<SessionSummary[]>;
function useSessionMessages(sessionId: string, offset: number, limit: number, enabled?: boolean): UseQueryResult<MessagePage>;
function useModelOptions(): UseQueryResult<ModelOptions>;
function useJobs(): UseQueryResult<CronJob[]>;
function useHealth(visible?: boolean): UseQueryResult<HealthResponse>;
function useHealthDetailed(visible?: boolean): UseQueryResult<HealthDetailed>;
function useHostStatus(visible?: boolean): UseQueryResult<HostStatus>;
function useActivity(limit?: number, visible?: boolean): UseQueryResult<ActivityItem[]>;
function useSkills(): UseQueryResult<Skill[]>;
function useSkill(path: string, enabled?: boolean): UseQueryResult<{ path: string; content: string; modifiedAt: number }>;
function useSkillBackups(path: string, enabled?: boolean): UseQueryResult<SkillBackup[]>;
function useSkillBackup(path: string, id: string, enabled?: boolean): UseQueryResult<{ id: string; content: string }>;
function useSettings(): UseQueryResult<SettingsView>;
function useConfigYaml(): UseQueryResult<ConfigView>;
function useModelsAdmin(): UseQueryResult<ModelsView>;
function useCronModel(): UseQueryResult<CronModelView>;
function useMcpServers(): UseQueryResult<McpServer[]>;
function usePlugins(): UseQueryResult<PluginInfo[]>;
function useProfiles(): UseQueryResult<ProfilesView>;
function useProfileDetail(name: string, enabled?: boolean): UseQueryResult<{ name: string; text: string }>;
function useWebhooks(): UseQueryResult<WebhooksView>;
function useWebhookDeliveries(route?: string, limit?: number, visible?: boolean): UseQueryResult<WebhookDelivery[]>;
function useUsage(days: number, visible?: boolean): UseQueryResult<Usage>;
function useLogSources(): UseQueryResult<LogSources>;
function useLogs(q: LogQuery, follow: boolean): UseQueryResult<LogResult>;
function useUpdateInfo(force?: boolean): UseQueryResult<UpdateInfo>;
function useUpdateRun(running: boolean): UseQueryResult<UpdateRun>;
function useUpdatePlan(enabled: boolean): UseQueryResult<{ plan: string; exitCode: number }>;
function useFileRoots(): UseQueryResult<FileRoot[]>;
function useDir(root: string, path: string, enabled?: boolean): UseQueryResult<DirListing>;
function useImageModels(): UseQueryResult<ImageModel[]>;
function useImageHistory(): UseQueryResult<ImageItem[]>;
function useSubagents(sessionId: string, limit: number, refetchInterval?: number | false): UseQueryResult<SubagentsView>;
function useContextUsage(sessionId: string, enabled?: boolean): UseQueryResult<ContextUsage>;
function useSessionFolders(): UseQueryResult<SessionFolders>;
function useSessionBackground(sessionId: string, visible: boolean): UseQueryResult<BackgroundProcess[]>;
function useCommentary(ids: readonly number[], enabled?: boolean): UseQueryResult<{ items: Record<string, string[]> }>;
```

变更 hook 成功后失效对应 key。`useLockModel` 只失效会话列表，不重拉消息。`useCreateWebhook` 的 `secret` 只留在 mutation 结果里。`useImageGenerate` 超时沿用源码 150000，且不重试。

```ts
function useCreateSession(): UseMutationResult<SessionSummary, Error, string>;
function usePatchSession(): UseMutationResult<unknown, Error, { sessionId: string; patch: SessionPatch }>;
function useDeleteSession(): UseMutationResult<unknown, Error, string>;
function useForkSession(): UseMutationResult<SessionSummary, Error, string>;
function useLockModel(): UseMutationResult<unknown, Error, { sessionId: string; model: string; provider: string }>;
function useCreateJob(): UseMutationResult<CronJob, Error, CronJobInput>;
function useUpdateJob(): UseMutationResult<CronJob, Error, { id: string; patch: Partial<CronJobInput> }>;
function useDeleteJob(): UseMutationResult<unknown, Error, string>;
function usePauseJob(): UseMutationResult<unknown, Error, string>;
function useResumeJob(): UseMutationResult<unknown, Error, string>;
function useRunJob(): UseMutationResult<unknown, Error, string>;
function useRestartService(): UseMutationResult<{ ok: boolean }, Error, { name: string; force?: boolean }>;
function useSaveSkill(): UseMutationResult<{ ok: boolean; unchanged: boolean; modifiedAt: number; backup?: string }, Error, { path: string; content: string; baseModifiedAt: number }>;
function useSetSkillEnabled(): UseMutationResult<{ ok: boolean }, Error, { name: string; enabled: boolean }>;
function useUpdateStart(): UseMutationResult<{ ok: boolean; runId: string }, Error, void>;
function useSetSetting(): UseMutationResult<{ ok: boolean }, Error, { key: string; value: unknown }>;
function useResetSetting(): UseMutationResult<{ ok: boolean }, Error, string>;
function useSetConfig(): UseMutationResult<ConfigSetResult, Error, { key: string; value: string }>;
function useDeleteFile(): UseMutationResult<{ ok: boolean }, Error, { root: string; path: string }>;
function useUpdateSessionFolders(): UseMutationResult<SessionFolders, Error, SessionFolderAction>;
function useSetDefaultModel(): UseMutationResult<ActionResult, Error, { model: string; provider?: string }>;
function useSetCronModel(): UseMutationResult<ActionResult, Error, { model: string; provider?: string }>;
function useSetProviderModels(): UseMutationResult<ActionResult, Error, { provider: string; models: string[] }>;
function useSetProviderDiscover(): UseMutationResult<ActionResult, Error, { provider: string; discover: boolean }>;
function useSetExcludedProviders(): UseMutationResult<ActionResult, Error, string[]>;
function useSetMcpEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }>;
function useRemoveMcp(): UseMutationResult<ActionResult, Error, string>;
function useTestMcp(): UseMutationResult<{ ok: boolean; output: string }, Error, string>;
function useSetPluginEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }>;
function useCreateProfile(): UseMutationResult<ActionResult, Error, { name: string; cloneFrom: string | null; description: string }>;
function useUseProfile(): UseMutationResult<ActionResult, Error, string>;
function useDeleteProfile(): UseMutationResult<ActionResult, Error, string>;
function useCreateWebhook(): UseMutationResult<{ ok: boolean; name: string; path: string; secret: string }, Error, WebhookCreate>;
function useDeleteWebhook(): UseMutationResult<ActionResult, Error, string>;
function useSetWebhookEnabled(): UseMutationResult<ActionResult, Error, { name: string; enabled: boolean }>;
function useTestWebhook(): UseMutationResult<{ ok: boolean; output: string }, Error, string>;
function useImageGenerate(): UseMutationResult<{ item: ImageItem; historySaved: boolean }, Error, { model: string; prompt: string; aspect: string }>;
function useImageDelete(): UseMutationResult<{ ok: boolean }, Error, string>;
function useStopExecution(): UseMutationResult<{ status: "cancelled"; busy: false }, Error, { sessionId: string; executionId: string }>;
function useEditBranch(): UseMutationResult<SessionSummary, Error, EditBoundary & { sessionId: string; requestId: string; model: string; provider: string }>;
```

编辑重发的 `start` 必须走 `useLiveRun.start`，不要把 `resendEdited` 的启动包进 Query。

## 玻璃与主题

令牌在 `src/react/styles/index.css`。手动主题走 `data-theme`。深色写在 `:root[data-theme=dark]`，也写在系统深色且没有 `data-theme=light` 的分支。浅色玻璃 `rgba(255,255,255,.7)`，深色 `rgba(30,32,44,.6)`，blur 20px，saturate 170%。窄屏 `max-width: 768px` blur 12px。降低透明度时 `backdrop-filter` 和 `-webkit-backdrop-filter` 都是 `none`，`background-image: none`，背景改成同一颜色的 85% 不透明。不支持 `backdrop-filter` 时实心。高光在 `.glass::before`，`pointer-events: none`。

弹簧：`src/react/styles/motion.tsx` 导出 `spring = { type: "spring", stiffness: 360, damping: 30 }`。根上是 `LazyMotion` + `m`，`strict`。CSS 令牌 `--spring: 360 30`。
