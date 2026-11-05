# MCP 新增实现

静态核对，未运行测试、构建、import、CLI 或浏览器。不是验收，也不是上线许可。

## 实现文件

- `clawd_server/admin_mcp.py`：冻结导出 `create_mcp(mod, body)`
- `apps/panel/src/react/pages/settings/mcp-tab.tsx`：原列表下方的结构化新增表单
- `tests/clawd_settings_cases/test_mcp_contract.py`：契约用例，未执行
- 未改公共路由和 lib。集成人已有 `POST /mcp/servers` 与 `createMcp`，本路只消费。

## 调用链

1. 表单调用 `@/lib/admin-api` 的 `createMcp`，类型 `McpCreateInput`。不直连 `request`，不走 `clawd mcp add`。
2. `createMcp` POST `mcp/servers`。`admin_routes.dispatch_frozen` 已调用 `admin_mcp.create_mcp(mod, body)`，并丢掉顶层 `env`。本路没有改这个文件。
3. `create_mcp` 拒绝 profile/目录字段，证明进程 home 后调用真实 `hermes_cli.mcp_config._save_mcp_server`。该函数在 `hermes_cli/mcp_config.py:233`：先 `validate_mcp_server_entry`，失败返回 false；成功则 `load_config` 后写入 `mcp_servers.<name>` 并 `save_config`。
4. 回读比对命令、地址、参数、启用和 env 值。响应只有 `envKeys`，没有 env 明文。`applied/hotLoaded/effectiveNow/loadedNow/processEnvChanged/restartRequired` 都是 false。
5. 表单不把保存响应当成列表。成功后再 `refetchQueries(queryKeys.mcp)`。名称和环境变量名没出现在另行加载的列表里时，保留表单。

## 缺口

- 不提供目录安装、预设安装、HTTP Bearer/OAuth。这些不在冻结请求里，没有做成假成功。
- 不调用 `discover_plugins`。只拒绝配置里已有同名或 `source=plugin`。插件仅在内存里提供的同名服务，本路不能证明，因此没有猜测覆盖。
- 不返回 profile 名。没有显式 `HERMES_HOME`/`CLAWD_HOME`、面板 home 不一致、线程 home 覆盖不一致，或目标是生产 `~/.hermes` / `/root/.hermes` 时，返回 `409 profile_scope_unsafe`，不写。
- 锁是面板进程写锁加核心配置锁，不是跨进程文件锁。
- 生效仍是新会话或网关重启。没有进程内热加载接口，所以不报 `applied: true`。
- 路由 `_drop` 和客户端 `withoutSecrets` 只剥顶层 `env`。本导出不把 env 值放进 `server`；若回读字符串等于某个 env 值，返回 500 且不带该值。
- 密码框样式用内联复制现有文本框，因为 `settings.css` 不在本路。没有新增动画。原卡片顺序、确认删除和测试连接未改。

## 静态核对

- `create_mcp` 存在，保存点是 `_save_mcp_server(name, wanted)`。源码无 `record_mcp_install`、`mcp_catalog`、`get_active_profile`。
- 表单 import `createMcp` / `McpCreateInput`，env 值为 `type="password"`，不回填列表里的值。
- 已观察到 `admin_routes.py` 含 `POST /mcp/servers` 和 `create_mcp`。这是集成人接线，不是本路改动。
- `admin_mcp.py` 与测试文件的写入检查通过。tsx 没有对应 linter，未做类型检查。

## 未运行

未执行 pytest、构建、py_compile、import、CLI、浏览器或网络。用例准备了拒绝密钥、拒绝 profile 猜测、拒绝目录字段、拒绝生产目录、重复名、env 只写不回，以及一次真实 `_save_mcp_server` 落盘。这些都还没有跑。
