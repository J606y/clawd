# 插件非秘密配置实现

静态核对，未运行。没有执行测试、构建、import、探针，没有 commit、push、deploy，没有接触生产、原面板或官方安装目录。

## 结论

已安装插件的非秘密配置可以经冻结导出读写。保存走 `ctx.set_config` 的同一写入器，读回原始 `config.yaml`。`loadedNow` 固定为 false。本进程不加载插件，不安装，不热加载。

页面已接到集成人已有的 `getPluginConfig` / `setPluginConfig`。路由文件本次未改；静态看到它已经调用这两个导出。

## 真实消费接线

配置不在插件目录里另存一份，也不走记忆提供方的 `config_schema.py`。

| 环节 | 位置 | 行为 |
| --- | --- | --- |
| 清单 | `hermes_cli.plugins_settings._manifest_config_schema` | 读已安装目录的 `plugin.yaml` `config_schema`，或可移植包 `plugin.json` 的 `extensions.com.nousresearch.hermes.config_schema` |
| 插件读取 | `hermes_cli.plugins.PluginContext.get_config` | `plugins.entries.<id>.settings.<key>`，没有再看旧的 `config` 子树 |
| 展示 | `get_plugin_config` 的 `_consumed` | 同一顺序：`load_config_readonly`，settings 优先，否则 legacy `config` |
| 写入 | `save_plugin_settings` → `save_plugin_setting` | 原子写入 `plugins.entries.<id>.settings`。这是 `ctx.set_config` 和 TUI `plugins.manage settings` 的写入器 |
| 保存回读 | `read_user_config_raw` | 核对原始文件里的非秘密值，不把环境变量展开后的视图当成写入证明 |
| HTTP | `clawd_server/admin_routes.py` | `GET /plugins/config?name=` 调用 `get_plugin_config(mod, name)`；`PUT /plugins/config` 调用 `set_plugin_config(mod, name, fields)`。`load_export` 直接导入模块，不经过 `admin_native` 赋值 |
| 页面 | `plugins-tab.tsx` | 使用 `admin-api.ts` 已有的 `getPluginConfig` / `setPluginConfig`，不另造路径 |

没有调用 TUI 那条路径上的 `broadcast_plugin_event`，也没有 `restart_mcp_servers_with_new_inputs`。那两步会让桌面半边或 MCP 进程立刻吃到新值，属于热加载，本次明确不做。

`requires_env`、清单 `config_keys`（例如 `memory.byterover`）和记忆提供方自己的 `get_config_schema()` 不是这条表单。改它们会写到别的配置段。

## 秘密与 profile

- 清单 `type: secret` 或 `secret: true`，以及 `secret_key` / `secret_name`（面板启发式加核心 `_is_secret_config_key`）命中的字段：整单 `403 secret_key`，一个键都不写。
- GET 对秘密字段只返回 `valueSet` 和符合 `^[A-Z][A-Z0-9_]{0,127}$` 的环境变量名，没有明文，不读 `/api/env/reveal`。
- 非秘密字符串经 `redact_text`。脱敏结果和原文不同就整值隐藏，避免把掩码再存回去。
- 密钥规则或脱敏函数不可用时拒绝，不放行。
- 读写前调用 `panel_home`。请求线程上的其他 home 覆盖，或面板目录与进程 `HERMES_HOME` 不一致，沿用既有 `409`。不设置 `os.environ`，不猜 profile。
- 同名插件有多个时 `409 plugin_name_ambiguous`，不取发现顺序的第一个。页面用列表里的完整键（如 `image_gen/fal`）。
- 托管安装 `403 config_managed`。管理员锁定键 `403 config_locked`。
- 下架插件不返回、不写入。

PUT 成功体固定：

```json
{"ok":true,"saved":true,"applied":false,"processEnvChanged":false,"hotLoaded":false,"effectiveNow":false,"loadedNow":false,"restartRequired":false}
```

`restartRequired` 为 false，不是“已经生效”。下次 `ctx.get_config` 会读到新文件；注册时缓存了配置的插件不会因此更新。启用开关仍是另一条事实：只改准入名单，`loadedNow: false` 且 `restartRequired: true`。

## 页面

保留配置档区、搜索、仅显示已启用、骨架屏和 `clawd plugins` 安装提示。每行增加“配置”，用现有按钮、表单、提示和开关样式，没有改 `settings.css`。

没有声明 `config_schema` 时显示说明，不造空开关，也不把 `requires_env` 画成可填密钥。秘密行只显示已设置/未设置。保存后若服务端报告 `loadedNow`、`applied`、`hotLoaded` 或 `effectiveNow` 为 true，提示与约定不符，不当成加载成功。

## 缺口

1. 本仓库 `plugins/**/plugin.yaml` 与 `plugin.json` 静态搜索没有 `config_schema`。当前捆绑插件打开后是空清单，这是清单事实，不是写入器缺失。用户安装或可移植包只要声明了 schema，走同一条路径。
2. 不热加载，不安装，不重连 MCP，不广播 `plugin.<id>.settings.changed`。已加载插件的进程内缓存不会更新。
3. `admin-api.ts` 把 `fields` 标成 `Record`。运行时 GET/PUT 成功体里的 `fields` 是字段对象数组，页面按数组渲染。该类型文件不在本次允许修改范围，没有改。
4. 路由 `_drop` 和客户端 `withoutSecrets` 只剥顶层或字典里的秘密键名，不递归字段数组。明文门禁在 `admin_plugins.py` 自己：秘密字段不放入 `value`。
5. 启用切换现在对碰撞的显示名返回 `409`，不再启用发现到的第一个。页面改传完整键。旧客户端若仍只传碰撞的显示名，会得到 `409` 而不是静默选中一个。
6. 路由与客户端函数是静态看到的现成接线，本次没有跑通过它们。

## 文件

- `clawd_server/admin_plugins.py`
- `apps/panel/src/react/pages/settings/plugins-tab.tsx`
- `tests/clawd_settings_cases/test_plugins_contract.py`（已写，未运行）
- 本报告：`docs/clawd/settings-plugins-implementation.md`

未改 `admin_routes.py`、`admin_native.py`、`admin_common.py`、`admin-api.ts`、`sidecar.ts`、`queries.ts`、`api.ts`。
