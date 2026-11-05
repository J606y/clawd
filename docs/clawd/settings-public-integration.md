# 设置公共接口接线

静态核对，未运行测试、构建、import、py_compile 或产品探针。不是验收，也不是上线许可。

## 改动文件

- `/root/hermes-workspace/clawd/clawd_server/admin_routes.py`
- `/root/hermes-workspace/clawd/clawd_server/admin_native.py`
- `/root/hermes-workspace/clawd/clawd_server/admin_common.py`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/admin-api.ts`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/sidecar.ts`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/queries.ts`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/api.ts`
- `/root/hermes-workspace/clawd/tests/clawd_settings_cases/test_public_contract.py`（已写，未执行）

未改侧车源码、页面、布局或动画。未改原面板、生产路径或 `/usr/local/lib/hermes-agent`。

## 注册到消费调用点

面板代理 `clawd_server/server.py` 对 `/panel-sidecar/` 使用 `*`，PUT 能转发。侧车类原先只有 `do_GET` / `do_POST`。`admin_routes.install` 给 `Handler` 挂 `do_PUT`，仍进原来的 `_dispatch`，不替换 `_dispatch`。

| 冻结路由 | 已核对的导出 | 现有消费 |
| --- | --- | --- |
| POST `/skills/enabled` | `admin_skills.set_skill_enabled(mod, name, enabled)` | `skills-tab.tsx` → `useSetSkillEnabled` → `admin-api.setSkillEnabled` |
| POST `/mcp/servers` | `admin_mcp.create_mcp(mod, body)` | `useCreateMcp` → `createMcp`。`mcp-tab.tsx` 尚未调用 |
| POST `/models/custom-endpoints` | `admin_models.create_custom_endpoint(mod, body)` | `useCreateCustomEndpoint`。`models-tab.tsx` 尚未调用 |
| GET/POST `/settings/advanced` | `admin_settings.get_advanced(mod, key)`、`set_advanced(mod, key, value)` | `useAdvanced` / `useSetAdvanced`。`config-tab.tsx` 尚未调用 |
| GET `/tools/toolsets`、POST `/tools/toolsets/enabled` | `admin_tools.list_toolsets(mod)`、`set_toolset_enabled(mod, name, enabled)` | `useToolsets` / `useSetToolsetEnabled`。配置页组件尚未挂上 |
| GET `/memory/providers`、PUT `/memory/providers/config` | `list_providers(mod)`、`set_provider_config(mod, name, fields)` | `useMemoryProviders` / `useSetMemoryProviderConfig`。页面尚未挂上 |
| GET `/connections/platforms`、POST `/connections/platforms/enabled` | `list_platforms(mod)`、`set_platform_enabled(mod, id, enabled)` | `useConnectionPlatforms` / `useSetConnectionPlatformEnabled`。页面尚未挂上 |
| GET `/plugins/config`、PUT `/plugins/config` | `get_plugin_config(mod, name)`、`set_plugin_config(mod, name, fields)` | `usePluginConfig` / `useSetPluginConfig`。`plugins-tab.tsx` 尚未调用 |
| GET `/logs` | `admin_logs.get_logs(mod, source, lines, level, component, q)` | `logs-tab.tsx` → `useLogs` → `sidecar.logs` |

技能开关另有一条模块绑定：`admin_native.install` 在导出存在时把 `mod.set_skill_enabled` 指到同一函数。HTTP 先被路由包装截下，不会再走旧的 native 写入。

日志：来源在侧车 `LOG_JOURNAL`（`j-gateway`、`j-dashboard`、`j-sidecar`）时路由直接 `501 journal_host_boundary`，不读 journal。`list_log_sources` 被包装后把 `kind=journal` 标成 `available: false`。现有日志页用这个标志禁用选项，不改页面。文件源交给 `admin_logs.get_logs`。

定时任务不新造路由。`CronJobInput` 增加可选 `deliver`、`skills`、`repeat`。`api.createJob` / `updateJob` 经 `jobWriteBody` 原样转发这三个字段。创建门禁只接受正整数 `repeat`；省略表示不限次数。消费点是 `useCreateJob` / `useUpdateJob`。`jobs-tab.tsx` 目前仍只提交名称、频率、内容。

写入响应缺事实字段时补 `saved/applied/hotLoaded/effectiveNow/loadedNow` 的 false，不把缺省改成已生效。导出若明确返回 `applied: true`，路由不改写。响应里的 `apiKey`、`env`、`token`、`password`、`secret` 会丢掉；删除确认的一次性 `token` 不走这条路径。

## 秘密策略

`admin_common.secret_key` 仍先要求侧车 `_is_secret_key`。规则不可用时 `503 secret_policy_unavailable`，不放行。再查核心 `_is_secret_config_key`。核心不可导入也拒绝。

批准的误锁名单（矩阵 `panelFalseSecret`）不再锁死：`security.redact_secrets` 和列出的 `secrets.bitwarden.*` / `secrets.onepassword.*` 非秘密叶子。`secrets.onepassword.env` 不在名单里，仍然锁定。`dashboard.basic_auth.password_hash` 即使核心后缀未命中也锁定。令牌、密码、`api_key` 继续拒绝。

表单锁定走 `admin_settings.get_settings` → `secret_key`。`config-tab.tsx` 读服务端 `locked`，不在客户端另做一套启发式。

## 未完成和阻塞

- 页面尚未调用新钩子：`mcp-tab.tsx`、`models-tab.tsx`、`plugins-tab.tsx`、`config-tab.tsx`（高级编辑和分组组件）、`jobs-tab.tsx`（三个字段的表单）。这些文件不在本路。开放对象导出已接通；路由在调用前拒绝矩阵外的键。
- `skills-tab.tsx` 仍在请求成功前乐观改开关，并写「新对话生效」。服务端事实已能从 `skillNotice` 读到，页面还没用。
- `update-tab.tsx` 仍有可点击的「立即更新…」。接口侧 `start_update` 保持 403。宿主更新、重启、进程级配置档切换、插件热加载、插件安装器、记忆 setup、电脑控制授权、环境明文、MCP 目录安装、消息配对都没有安全进程内接口，没有做成假成功。
- Webhook 投递日志仍是既有 501，没有改成空列表。
- 未运行：测试、构建、import、py_compile、产品探针。`tests/clawd_settings_cases/test_public_contract.py` 只是写好了。
