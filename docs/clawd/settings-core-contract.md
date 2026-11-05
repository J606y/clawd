# clawd 正式设置契约

静态核对，未运行。失败即拒绝，不读生产配置，不把默认值存在当成已被消费。914 个 schema 叶子不是 914 个功能。完整字段在 `docs/clawd/settings-core-matrix.json`。

## 计数

- schema 字段 914（含虚拟项 `model_context_length`；排除内部 `_config_version`）。
- 表单已暴露 872。核心秘密名 24。面板启发式误锁见矩阵 `panelFalseSecret`；`dashboard.basic_auth.password_hash` 核心后缀未命中，仍保持锁定。
- 允许的读取文件里，点号键字符串命中 44。其余 870 条只证明 schema 有叶子，不证明运行时读取。
- 空对象未进表单 44。`mcp_servers` 等额外根不在 `DEFAULT_CONFIG`。
- 汉化覆盖 871。缺 43 条，写入新文件 `settings-zh-supplement.json`，不改 `settings-zh.json`。翻译里的 tirith、`terminal.singularity_image` 是退役或迁移残留，不是现行叶子。

## 保存与生效

所有新管理写入返回：

```json
{"ok":true,"saved":true,"applied":false,"processEnvChanged":false,"hotLoaded":false,"effectiveNow":false,"loadedNow":false,"restartRequired":false,"note":"..."}
```

环境项 `restartRequired` 才为 true。不得返回 `applied:true` 表示网关已热加载。配置档切换保持 `processSwitched:false`。插件开关保持 `loadedNow:false`。工具集更改只对下一次会话生效，禁止中途改工具集。

错误体沿用侧车：`{"error":{"code":"...","message":"..."}}`。定时任务仍走网关 `/api/jobs`，错误是 `{"error":"..."}`，不要包成侧车码。

共通拒绝：`400 invalid_body`、`400 unknown_key`、`403 secret_key`、`403 config_managed`、`403 config_locked`、`409 profile_scope_unsafe`、`500 *_unverified`、`503 redact_unavailable`。秘密策略不可用时拒绝，不放行。

## 秘密

GET 不返回明文，不写日志。只回 `valueSet` 或掩码。禁止移植 `/api/env/reveal`。`security.redact_secrets` 是布尔项，按核心判定可写。`secrets.*` 段名不得把 `enabled` 等非秘密叶子全部锁死；令牌、密码、`password_hash` 仍锁定。

## 冻结导出

实现人不得改路由和 `admin-api.ts`。导出名固定：`admin_skills.set_skill_enabled`、`admin_mcp.create_mcp`、`admin_models.create_custom_endpoint`、`admin_settings.get_advanced`、`admin_settings.set_advanced`、`admin_tools.list_toolsets`、`admin_tools.set_toolset_enabled`、`admin_memory.list_providers`、`admin_memory.set_provider_config`、`admin_connections.list_platforms`、`admin_connections.set_platform_enabled`、`admin_plugins.get_plugin_config`、`admin_plugins.set_plugin_config`、`admin_logs.get_logs`。组件导出 `ToolsSection`、`MemorySection`、`ConnectionsSection`。

## 冻结路由

集成人独占注册。实现人只导出函数。

| 方法与路径 | 请求 | 成功要点 | 额外错误 |
| --- | --- | --- | --- |
| POST `/skills/enabled` | `{name,enabled}` | 写入 `skills.disabled` 并回读 | `404 skill_not_found` `501` 不得再出现 |
| POST `/mcp/servers` | `{name,transport,command?,args?,url?,enabled?}` | 回读无秘密；`env` 只写不回 | `409 mcp_exists` `400 mcp_secret_in_command` |
| POST `/models/custom-endpoints` | `{id,baseUrl,apiKey?,models}` | `{hasKey}`，无密钥 | `409 endpoint_exists` |
| GET `/settings/advanced?key=` | 仅矩阵允许的开放对象 | 秘密子键掩码 | `403 advanced_key_denied` |
| POST `/settings/advanced` | `{key,value}` | 同上生效字段 | `400 advanced_invalid` |
| GET `/tools/toolsets` | 无 | `{name,enabled,description}` | `503 tools_unavailable` |
| POST `/tools/toolsets/enabled` | `{name,enabled}` | `effectiveNow:false` | `404 toolset_not_found` |
| GET `/memory/providers` | 无 | 状态与非秘密字段 | `503 memory_unavailable` |
| PUT `/memory/providers/config` | `{name,fields}` | 秘密字段省略回读 | `403 secret_key` |
| GET `/connections/platforms` | 无 | 启用状态，令牌只报是否已设置 | `503 connections_unavailable` |
| POST `/connections/platforms/enabled` | `{id,enabled}` | 不返回令牌 | `404 platform_not_found` |
| GET `/plugins/config?name=` | 已安装插件 | 非秘密字段 | `404 plugin_not_found` |
| PUT `/plugins/config` | `{name,fields}` | `loadedNow:false` | `403 secret_key` |
| GET `/logs` | 现有查询 | 文件源不变 | journal 源 `501 journal_host_boundary` |

定时任务不新造路由。`CronJobInput` 增加可选 `deliver`、`skills`、`repeat`，值域以 `gateway/platforms/api_server.py` 的创建与 `_UPDATE_ALLOWED_FIELDS` 为准。

## 分组缺口

配置页补开放对象编辑和误锁修正。模型页补端点，不设“当前默认模型”第二入口。MCP 补新增，不补目录安装。技能页修启用切换，不在本批加技能库安装。插件页只编辑已安装配置。记忆提供方放配置页组件，不建项目记忆页。连接放配置页组件，不做配对。工具集放配置页组件。定时任务补三个已有字段。更新页去掉可点击的立即更新。日志去掉宿主 journal。用量保持只读。Webhook 投递保持 501。

## 硬边界

不实现：`clawd update`、宿主重启、进程级配置档切换、插件热加载、插件安装器、记忆 setup 命令、电脑控制授权、环境明文查看、MCP 目录安装、消息配对流。这些没有安全的进程内接口，禁止用空列表或假成功代替。
