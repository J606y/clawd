# 设置客户端类型收口

静态实现和源码核对。未运行。没有执行本报告的用例，也没有执行测试、构建、类型检查、import、`py_compile`、产品探针、CLI、配置读写、commit、push 或部署。准备好的用例不是通过。不是验收，也不是上线许可。

未删文件。未读生产凭据。未碰原面板、`/usr/local/lib/hermes-agent` 或 `/root/hermes-workspace/hermes-panel`。未改样式、动画、页面、`api.ts` 或其他业务源码。分页收齐函数没有覆盖。

## 类型 / 消费接线表

| 路由 | 真实成功体 | 客户端类型 | 查询钩子 | 页面实际消费 | 未接点 |
| --- | --- | --- | --- | --- | --- |
| GET `/models` | 每行 `hasKey: true \| false \| null`。null 是读不到，不是未设置，也不是密钥 | `sidecar.ts` `ProviderInfo.hasKey: boolean \| null`。`admin-api.ts` 合并同型字段 | `useModelsAdmin` 仍返回 `ModelsView` | `models-tab.tsx` 仍把 `hasKey` 收成 `unknown` 再读。路由已接通 | 页面不读 `ProviderInfo.hasKey`。本路不能改页面 |
| POST `/models/custom-endpoints` | `{hasKey: boolean, id}`，无密钥。热加载字段为 false | `createCustomEndpoint` → `CustomEndpointResult`。提交可带 `apiKey`，响应经 `withoutSecrets` 丢掉 | `useCreateCustomEndpoint` 现返回 `CustomEndpointResult` | 页面自己 POST 同一路径，不调用这个函数 | 函数和钩子没有被表单调用。路径本身有表单 |
| GET `/plugins/config` | `fields` 是字段数组。秘密行只有 `valueSet` / 合法 `env`，没有 `value` | `PluginConfigResult.fields?: PluginConfigField[]`。秘密行是 `PluginConfigSecretField`，没有 `value` | `usePluginConfig` → `PluginConfigResult`。不再标成 `Record` | `plugins-tab.tsx` 直接 `getPluginConfig`，`asConfig` 要求数组 | 钩子无人调用。函数已被页面调用 |
| PUT `/plugins/config` | 提交 `fields` 必须是对象。回读 `fields` 仍是数组。数组体会 400 | `setPluginConfig(name, fields: Record<string, unknown>)` → `PluginConfigResult` | `useSetPluginConfig` 提交对象，成功类型是 `PluginConfigResult` | 页面提交 `changedFields` 对象，回读走 `asConfig` | 同上，函数已接，钩子未接 |
| GET `/memory/providers` | `{data, active, activeOmitted, applied: false, effectiveNow: false}`。秘密字段只有 `valueSet` | `MemoryProvidersResult`。秘密行 `MemoryProviderSecretField` 没有 `value` | `useMemoryProviders` → `MemoryProvidersResult`。不再是 `unknown[]` | `memory-section.tsx` 自己 `request("memory/providers")` 并本地解析 | typed 函数和钩子未被该表单调用。路由有表单 |
| PUT `/memory/providers/config` | 提交 `fields` 对象。回读 `name` 和本次非秘密 `fields` 数组。`applied` / `effectiveNow` 为 false | `setMemoryProviderConfig` → `MemoryProviderConfigResult` | `useSetMemoryProviderConfig` 同返回 | 页面自己 PUT，结果只按 `WriteFacts` 读生效事实 | 同上 |
| GET `/connections/platforms` | `{data, applied: false, effectiveNow: false}`。`tokens[]` 只有 `key` / `required` / `valueSet` | `ConnectionPlatformsResult` / `ConnectionTokenStatus` | `useConnectionPlatforms` → `ConnectionPlatformsResult`。不再是 `unknown[]` | `connections-section.tsx` 自己 `request` | typed 函数和钩子未被该表单调用。路由有表单 |
| POST `/connections/platforms/enabled` | `{id, enabled}` 加生效事实，全 false。无令牌 | `setConnectionPlatformEnabled` → `ConnectionEnabledResult` | `useSetConnectionPlatformEnabled` 同返回 | 页面自己 POST | 同上 |
| GET `/tools/toolsets` | 行上有 `name` / `enabled` / `description` / `platform` / `configOnly`，以及 `applied: false` / `effectiveNow: false` | `ToolsetsResult` / `ToolsetInfo` | `useToolsets` → `ToolsetsResult` | `tools-section.tsx` 自己 `request`，本地类型已读 `platform` / `configOnly` | typed 函数和钩子未被该表单调用。路由有表单 |
| POST `/tools/toolsets/enabled` | `{name, enabled, platform, configOnly}`，生效事实为 false | `setToolsetEnabled` → `ToolsetEnabledResult` | `useSetToolsetEnabled` 同返回 | 页面自己 POST | 同上 |
| GET/POST `/settings/advanced` | `plan`、掩码 `value`、`userSet`、`secretLeaves`、profile 事实。`applied` 保持 false。环境项 `restartRequired` 才可为 true | `AdvancedRead`。`value: PluginPublicValue`，含 null，不是明文秘密。提交 `value` 仍是 `unknown`，因为页面传入的是已检查过的 JSON 值 | `useAdvanced` / `useSetAdvanced` → `AdvancedRead` | `config-tab.tsx` 直接调用 `getAdvanced` / `setAdvanced`，自己的 `AdvancedView` 读这些字段 | 钩子未用。函数已接。`WriteFacts` 不再带 `value?: string \| null`，避免把开放对象收成字符串 |
| POST `/skills/enabled` | 已有 `SkillEnabledResult` | 未改签名 | `useSetSkillEnabled` | `skills-tab.tsx` 已调用 | 无。`sidecar.setSkillEnabled` 仍在，没有调用方，未删 |
| POST `/mcp/servers` | `{name, server}`，无 `env` / `headers` | `createMcp` → `McpCreateResult` | `useCreateMcp` 同返回 | `mcp-tab.tsx` 调用 `createMcp`，变量仍标 `WriteFacts` | 钩子未用。函数已接 |
| GET `/logs` | 未改 | `sidecar.logs(LogQuery)` | `useLogs` | `logs-tab.tsx` | 无 |
| 定时任务 | 不新造路由 | `api.ts` 的 `CronJobInput` 已有可选 `deliver` / `skills` / `repeat`。本路未改 `api.ts` | 已有 job 钩子 | `jobs-tab.tsx` 会提交这三个字段 | 无新缺口 |

路由到表单没有“完全没人调用”的冻结路径。真实未接点是类型消费，不是缺路由：模型页不读新的 `hasKey` 类型、也不走 `createCustomEndpoint`；记忆、连接、工具集表单不走 typed 函数和钩子，而是同一路径上的本地 `request`。这些页面不在允许改的文件里，没有为了接上而改它们，也没有造假开关。

## 本次补上的类型

1. `ProviderInfo.hasKey` 写进 `apps/panel/src/react/lib/sidecar.ts`，类型是 `boolean | null`。`admin-api.ts` 合并同型字段。注释里保留旧锁字符串 `hasKey?: boolean | null`，因为 `tests/clawd_settings_cases/test_public_contract.py` 仍在该文件里找这串。缺字段时页面现有逻辑仍显示状态未知，没有把缺字段收成 false。
2. `useMemoryProviders`、`useConnectionPlatforms`、`usePluginConfig` 改成真实返回。插件 GET/回读是字段数组，PUT 提交仍是改动对象。
3. 其余管理函数的查询返回改成对应结果类型：工具集带 `platform` / `configOnly`，记忆写入回读字段数组，连接开关回读 `id` / `enabled`，端点创建回读布尔 `hasKey`，开放对象回读掩码值。
4. 秘密行从类型上拿掉 `value`。连接令牌没有 `apiKey`。生效字段仍是 `boolean`，没有写成 `true`。
5. `apps/panel/src/lib/sidecar.ts` 不在面板 `tsconfig` 编译根，也不在本路可写名单。它的 `ProviderInfo` 仍无 `hasKey`。未改。

## 分页

未改 `collectSubagentPages`、`requestSubagentsPage`、`useSubagents` 的收齐逻辑。仍保留 `total` / `offset`，非 0 offset 写入 query，收齐后的视图 `offset` 为 0。失败仍抛错，不回空数组。profile 不能在每一页证明为同一个非空字符串时省略 `profileKey`，不写 `default`。`executionStatus` 不改成 `completed`。`returned` 语义未动。设置类型改动没有覆盖这些函数。

## 修改文件

- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/sidecar.ts`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/queries.ts`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/admin-api.ts`
- `/root/hermes-workspace/clawd/apps/panel/tests/settings-wire-contracts.mjs`（已写，未执行）
- 本报告：`/root/hermes-workspace/clawd/docs/clawd/settings-types-final-closeout-2026-11-05.md`

## 未运行

未运行 `apps/panel/tests/settings-wire-contracts.mjs`。未运行 `scripts/run_tests.sh`、面板类型检查、构建、import、CLI、浏览器或 HTTP 探针。退出码未知。
