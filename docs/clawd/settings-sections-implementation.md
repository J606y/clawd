# 工具集、记忆提供方、连接配置实现

静态核对，未运行。本文件不是验收通过、测试通过或上线许可。没有执行测试、构建、import、py_compile、CLI、浏览器、网络或数据库探针，没有读取生产秘密，没有改 config-tab 或公共路由库。

报告路径：`/root/hermes-workspace/clawd/docs/clawd/settings-sections-implementation.md`

## 工具集

文件：

- `/root/hermes-workspace/clawd/clawd_server/admin_tools.py`
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/settings/tools-section.tsx`

冻结导出：`list_toolsets`、`set_toolset_enabled`、`ToolsSection`。

真实调用点：

- 目录：`hermes_cli/tools_config.py` `_get_effective_configurable_toolsets`。空目录或异常返回 `503 tools_unavailable`，不回空列表冒充成功。
- 启用回读：同文件 `_get_platform_tools`，`include_default_mcp_servers=False`。配置只读项（`stt`）读自己的 `enabled`，缺键按核心默认视为开，这不是猜 profile。
- 写入：同文件 `_save_platform_tools`。配置只读项直接写该段 `enabled` 后 `save_config`。
- 目标平台：`_toolset_configuration_platform`。这里的 `cli` 是工具平台键，不是 Hermes profile，也不是猜出来的 default。
- 旧仪表盘 `hermes_cli/web_routers/tools.py` 的 toggle 还会在启用后派 post-setup。本实现不调用那条安装路径。
- 会话消费：`gateway/run_turn.py` 在构建运行时读 `platform_toolsets`；`agent/conversation_loop.py` 的 `_refresh_bot_chat_tools` 对已冻结的网关策略不放宽工具集。桌面/TUI 若稍后自己做回合间刷新，会再读配置。这次写入不调用该刷新。
- 归属：`clawd_server/admin_common.py` `panel_home`。不调用 `_profile_scope`，不写进程环境。

成功体始终 `saved: true`、`applied: false`、`effectiveNow: false`、`restartRequired: false`。文案不把保存称为已生效。启用时核心写入可能从 `agent.disabled_toolsets` 去掉刚启用的名字，否则下一次回读仍会被压成关闭；这是核心保存助手的行为，不是在改运行中的会话。

界面沿用设置页的 `card list`、`row`、`Switch`。开关失败显示服务端错误，不先把本地状态画成已切换。组件直接请求 `panel-sidecar/tools/toolsets`，因为 `admin-api.ts` 不属于本组。

## 记忆提供方

文件：

- `/root/hermes-workspace/clawd/clawd_server/admin_memory.py`
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/settings/memory-section.tsx`

冻结导出：`list_providers`、`set_provider_config`、`MemorySection`。

真实调用点：

- 名单：`plugins/memory/__init__.py` `list_memory_provider_names`。不调用 `discover_memory_providers`，那个函数会做可用性探测。
- 当前值：`load_config()` 的 `memory.provider`。缺键为 null。不匹配安全名称的字面值省略，不改成 builtin。配置里若字面是 `default`，那是 `agent/memory_provider.py` `is_core_memory_provider` 的内置哨兵，不是配置档名，也不能当写目标。
- 可写面：`plugins/memory/config_schema.py` `get_provider_config_schema`。存储复用 `hermes_cli/web_routers/memory_providers.py` 的非秘密应用与 JSON 写入。不调用会写秘密的 `_write_provider_flat`、`_write_provider_honcho`、`_save_submitted_secrets`，也不调用会改 `memory.provider` 的 `_update_memory_provider_config`。
- 秘密：字段种类、`secret_name` 或脱敏后与原文不同，都只回 `valueSet`。提交秘密字段或秘密子键返回 `403 secret_key`，整次不写。策略不可用则拒绝。
- 归属同样只走 `panel_home`。

保存成功不表示当前会话记忆已换。响应 `applied: false`、`effectiveNow: false`。没有 setup，没有项目记忆页。

## 连接

文件：

- `/root/hermes-workspace/clawd/clawd_server/admin_connections.py`
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/settings/connections-section.tsx`

冻结导出：`list_platforms`、`set_platform_enabled`、`ConnectionsSection`。

真实调用点：

- 目录：`hermes_cli/web_server_messaging.py` `_messaging_platform_catalog`。空目录或异常是 `503 connections_unavailable`。
- 启用位写入：`hermes_cli/config.py` `write_platform_config_field(id, "enabled", bool)`，回读原始配置里的布尔值。不一致则 `500 platform_toggle_unverified`。
- 启用显示：显式 false 优先；显式 true 为开；缺键时仅凭据是否存在按旧仪表盘规则显示为开。这不是猜 profile，也不是声称本进程已连接。
- 令牌：profile 环境文件和 `platforms.<id>.token|api_key` 只转成 `valueSet`。不读 `os.environ`，不回 `redacted_value`，不回明文。响应若出现令牌字符串则 `503 redact_unavailable`。
- WhatsApp 只看当前进程 profile 内 `creds.json` 是否为普通文件。符号链接不跟随，文件内容不读。路径越出 profile 则 `409 profile_scope_unsafe`。
- 网关消费：`gateway/run_startup.py` 在启动适配器时读 `platform_config.enabled`。这次写入不重启网关。`restartRequired` 保持 false，因为这不是环境项；说明放在 note。
- 启用共享监听平台前调用 `_multiplex_port_binding_conflict(id, None)`。冲突返回 `409`，不回显推断出的档名。核对失败则不启用。不调用 `get_active_profile_name` 来选择写目标。
- 不使用 `load_gateway_config` 或 `gateway/run.py`。前者会把令牌放进配置对象，后者导入时会加载环境文件。

没有配对流，没有令牌写入，没有网关重启。

## 未接线

本组不能改 `config-tab.tsx`、`admin_routes.py`、`admin_native.py`、`admin_common.py`、`admin-api.ts`、`sidecar.ts`、`queries.ts`、`api.ts`。因此：

- 三个组件还没有放进配置页。不能当成设置页已经看得见。
- 六条冻结路由还没有注册。组件已经按契约请求 `panel-sidecar/` 下的路径，路由注册后即可调用，注册前会显示服务端错误，不会画成空成功。
- 建议集成人按 `list_toolsets(mod)`、`set_toolset_enabled(mod, name, enabled)`、`list_providers(mod)`、`set_provider_config(mod, name, fields)`、`list_platforms(mod)`、`set_platform_enabled(mod, id, enabled)` 注册，并在现有配置页挂入三个 section。不要新造顶层 tab。

## 缺口

- 工具集开关只写配置平台助手选出的那一个平台，不逐个改每个消息平台的名单。
- 桌面/TUI 后来若自行刷新工具，会读到新配置。本写入不触发它，所以不能声称所有存活会话都被隔离；能确认的是网关冻结策略不会被这次调用放宽。
- 树内 `byterover`、`retaindb` 的配置在类方法 `get_config_schema`（`plugins/memory/byterover/__init__.py`、`plugins/memory/retaindb/__init__.py`），没有 `config_schema.py`。导入提供方运行时被核心 web 路径禁止，因此这里标为不可写并在写入时 `503`。这不是已完成的提供方编辑。
- 没有切换当前记忆提供方的冻结导出。现有配置项里的 provider 下拉仍是那条路，本组不另做。
- 连接的非秘密字段（端口、主机）本次不编辑，也不回传值，避免把误分类的秘密放出来。
- 凭据只在进程环境、不在 profile 环境文件里时，`valueSet` 为 false。这是为了不把别的档的进程环境当成当前档。
- 测试只准备了 `tests/clawd_settings_cases/test_sections_contract.py`。它只读源码和本报告，不导入产品模块。未运行。

## 未运行

未运行测试、构建、import、py_compile、CLI、浏览器、网络和数据库探针。静态语法由写入工具自带检查碰过 Python 文件；那不是本组执行的验收。准备完成不等于通过。
