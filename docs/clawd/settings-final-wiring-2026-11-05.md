# 设置最终静态接线

静态核对，未运行。没有执行测试、构建、类型检查、import、`py_compile`、产品探针、CLI 或配置读写。准备好的用例不是通过。不是验收，也不是上线许可。

旧报告里的“路由未注册”是时间快照。当前字节里 `custom-endpoints`、`/settings/advanced` 和技能导出绑定已经存在，没有再实现一遍。

## 接线表

| 路由 | 当前导出与参数 | 客户端 | 页面消费 | 保存 / 生效 | 秘密 |
| --- | --- | --- | --- | --- | --- |
| POST `/skills/enabled` `{name,enabled}` | `admin_skills.set_skill_enabled(mod, name, enabled)`。路由 `load_export` 后 `_write`。`admin_native._bind_skill_export` 绑定同一导出，签名是 `(name, enabled)` 再传入 `mod`。导出缺失时两边都是 `503 admin_route_unbound`，不留 CLI，也不留 `save_disabled_skills` | `setSkillEnabled` | `skills-tab.tsx` → `useSetSkillEnabled`。HTTP 包装在侧车原 `_route` 之前，不会落到侧车 `:6059` 的 CLI 函数 | `saved: true`。`applied` / `effectiveNow` / `hotLoaded` / `loadedNow` / `processEnvChanged` / `restartRequired` 均为 false。新对话读文件，本进程不热加载 | 不回传 `skills.disabled` 名单 |
| POST `/mcp/servers` 对象 | `create_mcp(mod, body)` | `createMcp` | 钩子已有。`mcp-tab.tsx` 是否调用不在本路，未改页面 | 写入事实由导出给出；路由不把缺省改成已生效。丢掉顶层 `env` / `headers` / 密钥名 | `env` 只写不回 |
| POST `/models/custom-endpoints` `{id,baseUrl,apiKey?,models}` | `create_custom_endpoint(mod, body)` | `createCustomEndpoint`。`models-tab.tsx` 仍自己 POST 同一路径，不经过这个函数 | 页面读 `hasKey`，空密码框，成功后清空 | `saved: true`，热加载字段全 false。`hasKey` 只表示密钥槽是否写入 | 响应无密钥。GET `/models` 的 `hasKey` 为 true / false / null，null 是读不到，不是未设置 |
| GET/POST `/settings/advanced` | `get_advanced(mod, key)` / `set_advanced(mod, key, value)`。路由先拒绝矩阵外的键 | `getAdvanced` / `setAdvanced` | `config-tab.tsx` 已有调用点；本路未改页面 | 保存回读对象。`applied` 保持 false。不是热加载 | GET 的秘密叶子由导出掩码。没有明文查看接口 |
| GET `/tools/toolsets`、POST `/tools/toolsets/enabled` | `list_toolsets(mod)` / `set_toolset_enabled(mod, name, enabled)` | `listToolsets` / `setToolsetEnabled` | 配置页组件是否挂上不在本路 | 路由补缺省时只补 `effectiveNow: false` 与 `applied: false`，不改成已生效 | 无秘密 |
| GET `/memory/providers`、PUT `/memory/providers/config` | `list_providers(mod)` / `set_provider_config(mod, name, fields)`。`fields` 是对象 | `listMemoryProviders` / `setMemoryProviderConfig(name, fields)` | 页面未在本路改 | 列表 `applied` / `effectiveNow` 为 false。没有 setup 命令 | 秘密字段只有 `valueSet`，没有 `value` |
| GET `/connections/platforms`、POST `/connections/platforms/enabled` | `list_platforms(mod)` / `set_platform_enabled(mod, id, enabled)` | `listConnectionPlatforms` / `setConnectionPlatformEnabled(id, enabled)` | 页面未在本路改 | 启用状态不是本进程已连接 | `tokens[]` 只有 `key` / `required` / `valueSet` |
| GET `/plugins/config?name=`、PUT `/plugins/config` | `get_plugin_config(mod, name)` / `set_plugin_config(mod, name, fields)`。请求 `fields` 必须是对象；响应 `fields` 是字段数组 | `getPluginConfig` 返回 `PluginConfigField[]`。`setPluginConfig` 提交 `Record`，回读也是数组 | `plugins-tab.tsx` 用 `asConfig` 要求数组，提交 `changedFields` 对象 | `loadedNow` / `applied` / `hotLoaded` / `effectiveNow` 为 false。不加载插件 | 秘密行只有 `valueSet` 和合法环境变量名 |
| GET `/logs` | `get_logs(mod, source, lines, level, component, q)` | `sidecar.logs(LogQuery)`，查询名一致 | `logs-tab.tsx` | 只读。journal 源 `501 journal_host_boundary`，不是空列表 | 行文本走导出脱敏 |

定时任务不新造路由。`api.ts` 的 `CronJobInput` 已有可选 `deliver`、`skills`、`repeat`。`api_server.py` 有 effort owner，未改。页面是否提交这三个字段不在本路。

## 本次补上的缺口

1. `skills.disabled` 是真实读者的整份名单，不是嵌套键。读者是 `agent/skill_utils.py` 的 `get_disabled_skill_names` 和 `hermes_cli/skills_config.py` 的 `get_disabled_skills`，都读 `skills["disabled"]`。它不在 `DEFAULT_CONFIG` 的 `skills` 段里，所以 `commit_set` 原先会 `400 unknown_key`。现在放进 `CORE_RUNTIME_EXACT_KEYS`，只匹配这一个点号键。没有放进 `CORE_RUNTIME_KEYS`，因为那里的前缀覆盖会放行 `skills.disabled.*`。也没有放行 `skills.*` 或 `skills.platform_disabled`。后者有读者，但只在给出平台时读取；面板名单和本批写入都不使用它。
2. 技能 HTTP 与模块绑定指向同一导出。包装截下 `POST /skills/enabled`，不落到侧车 CLI。导出加载失败时，模块属性改成 `503`，不再留着 `native.set_skill_enabled`。
3. `PluginConfig` 的 GET/回读 `fields` 改为字段数组。PUT 提交仍是改动对象，与路由和 `set_plugin_config` 一致。数组体会 `400 invalid_body`，不会当成成功写入。
4. `ProviderInfo.hasKey` 用 `admin-api.ts` 里的模块合并补成 `boolean | null`。`sidecar.ts` 字节未改。GET 模型行的 `null` 是状态未知，不是未设置，也不是密钥。
5. 记忆提供方和连接平台的客户端类型改成真实字段。秘密只保留 `valueSet`。

## 修改文件

- `/root/hermes-workspace/clawd/hermes_cli/config_runtime_keys.py`
- `/root/hermes-workspace/clawd/clawd_server/admin_native.py`
- `/root/hermes-workspace/clawd/clawd_server/admin_routes.py`（只补注释，路由调用未改）
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/admin-api.ts`
- `/root/hermes-workspace/clawd/tests/clawd_settings_cases/test_skills_contract.py`（已写，未执行）
- `/root/hermes-workspace/clawd/tests/clawd_settings_cases/test_public_contract.py`（已写，未执行）
- 本报告：`/root/hermes-workspace/clawd/docs/clawd/settings-final-wiring-2026-11-05.md`

未改 `admin_common.py`。现有秘密策略已经是失败关闭，误锁名单未扩大。未改页面、布局、间距、动画、`sidecar.ts`、`queries.ts`、`api.ts`、`api_server.py`、侧车源码、原面板、生产路径或 `/usr/local/lib/hermes-agent`。未同步 `build/lib` 副本。

## 仍缺，没有假成功

- `queries.ts` 归分页 owner，未改。这些显式注解会在类型检查时对不上新的返回类型：`:816` `usePluginConfig` 仍把 `fields` 标成 `Record`；`:792` `useMemoryProviders`、`:804` `useConnectionPlatforms` 仍是 `unknown[]`。页面插件配置不走这个钩子，直接调用 `getPluginConfig`。
- `apps/panel/src/react/lib/sidecar.ts:147` 的 `ProviderInfo` 字节仍无 `hasKey`。合并声明在 `admin-api.ts`。`models-tab.tsx` 仍用交叉类型读取，并自己 POST 同一条端点路径。`apps/panel/src/lib/sidecar.ts` 不在面板 `tsconfig` 编译根里，也未改。
- `skills.platform_disabled.<platform>` 有读者，本批不写、不开放。技能库安装仍缺，没有加安装器。
- `hermes_cli/config.py` 的 `_KNOWN_CONTAINER_TYPES` 没有 `skills.disabled`。该文件不在本路。面板写入的是 JSON 数组，现有强制转换会存成列表；读者也接受列表和 JSON 数组字符串。没有为了过校验去种空默认列表。
- 侧车 `hermes_panel_sidecar.py:877` 的 CLI 函数体还在文件里。成功安装后模块名被改绑，HTTP 包装不会调用它。未改该文件。
- 没有安全进程内接口，因此仍拒绝而不是空成功：宿主更新、宿主重启、进程级配置档切换、插件热加载、插件安装器、记忆 setup、电脑控制授权、环境明文、MCP 目录安装、消息配对。更新接口保持 `403`。journal 与 webhook 投递日志保持 `501`。

## 未运行

未运行测试、构建、类型检查、import、`py_compile`、产品探针、CLI、写配置或部署。
