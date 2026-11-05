# clawd 接线缺口 — 2026-11-05

观察时刻：2026-11-05T05:47:57+08:00。这是静态总账，不是验收，不是修复，不是上线许可。本轮没有测试、构建、导入产品、网络、浏览器或探针。没有改公共入口，没有改 `docs/clawd/implementation-completion-manifest.json`。

六份细查报告尚未产生，禁止轮询：

- `/root/hermes-workspace/clawd/docs/clawd/resumption-memory-todo-detail-2026-11-05.md`
- `/root/hermes-workspace/clawd/docs/clawd/resumption-event-files-detail-2026-11-05.md`
- `/root/hermes-workspace/clawd/docs/clawd/resumption-migration-detail-2026-11-05.md`
- `/root/hermes-workspace/clawd/docs/clawd/resumption-runtime-detail-2026-11-05.md`
- `/root/hermes-workspace/clawd/docs/clawd/resumption-distribution-detail-2026-11-05.md`
- `/root/hermes-workspace/clawd/docs/clawd/resumption-licenses-detail-2026-11-05.md`

总账：`/root/hermes-workspace/clawd/docs/clawd/resumption-owner-manifest-2026-11-05.json`。其中没有任何 `pass`。子代理自报、交接叙述和模块存在都不能升为通过。

## 启动与注册链

活入口是 `python -m clawd_server`。`clawd_server/__main__.py:85-94` 在资源导出失败时退出 2，成功后调用 `run_serve`。`PanelServer.start` 的顺序是：API 适配器、`arm_adapter`、`load_sidecar`、`bind`、侧车线程。`bind` 调用 `native.install`。安装顺序是 browser、runtime 各至多一次，然后 admin 必须留下自己的 `config_set`，并且 `admin_routes.install` 必须把删除路由挂到 `Handler._route`。

`APIServerAdapter.connect` 先挂 `_http_route_table`，再 `register_project_routes`，再 `register_effort_routes`，最后才是 `/p/{profile}/{tail:.*}`。这只是源码顺序，没有启动服务器。

`server.py:220-228` 自己 `serve_forever`，不调用 `sidecar.main()`。`bind` 会替换 `_continue_watcher`、`_continue_tick` 和 `main`。所以面板活路径不会再启动名为 `auto-continue` 的侧车线程。

## 未安装 native 的旧分支

旧函数还在，而且绕过 `bind` 时仍可达：

- `apps/panel/sidecar/hermes_panel_sidecar.py:5983`、`:5985` 调用 `get_session_run`
- 同文件 `:6096` 调用 `get_run_wait`
- 同文件 `:6168-6183`，`main()` 和 `__main__` 会启动 `auto-continue`

`runtime_native.install` 只在 `bind` 之后替换这两个名字。直接跑侧车脚本，或 `load_sidecar()` 后不 `bind`，走的是旧实现。测试里的未绑定导入不是产品活路径，也不能用来宣布旧路径已退役。不要删侧车源码假装退役。`build/lib` 是旧副本，不删、不当活入口。

run/lease 在事实缺失时是否返回 unknown，由 runtime detail 复核。本文件不标已实现。

## 设置公共接线

已接到安装链、但未运行：

- `admin_native.install` 替换既有 config、模型、MCP、插件、配置档、webhook、settings 名称，并拒绝 `_hermes` 子进程。
- 宿主 `restart_service` / `start_update` 在 `bind` 和 `admin_native` 都保持拒绝，且不读系统安装目录。
- 删除预览/确认已挂上。`admin_routes.py` 只增加 `POST /profiles/delete-preview` 与 `POST /profiles/delete-confirm`，并把 `token`、`fingerprint` 传给 `confirm_delete_profile`。`admin_profiles.py:219` 的预览凭证是 120 秒。`admin-api.ts:83` 打 preview。没有执行预览或删除。只带名称的 `delete_profile` 不能当确认。

未接到公共路由：

- `admin_routes.py` 没有技能、MCP 新增、工具集、记忆、连接、日志的新路径。
- 快照时 `admin_mcp.py` 没有 `create_mcp`。
- 快照时没有 `admin_skills.py`、`admin_tools.py`、`admin_memory.py`、`admin_connections.py`、`admin_logs.py`。
- 工作清单仍是 `contract-only`，`runtimeVerified` 为 false。设置 owner 正在改这些页，缺文件只是 05:47 快照，不能当成下一轮的永久缺文件名单。

公共集成人独占，其他实现路不能抢：

- `clawd_server/admin_routes.py`
- `clawd_server/admin_native.py`
- `clawd_server/admin_common.py`
- `apps/panel/src/react/lib/admin-api.ts`
- `apps/panel/src/react/lib/sidecar.ts`
- `apps/panel/src/react/lib/queries.ts`
- `apps/panel/src/react/lib/api.ts`

同样不能抢的启动入口：`clawd_server/native.py`、`bind.py`、`server.py`，以及 `gateway/platforms/api_server.py` 的 `connect` 注册区。

## 无安全接口，必须保持缺口

这些没有安全的进程内接口。禁止假开关、空列表、`applied:true`、`processSwitched:true`、`loadedNow:true`，也禁止从用户目标里静默删掉：

- 宿主更新 / `clawd update`（不能把拒绝后的入口说成升级可用）
- 宿主重启
- 进程级 profile 切换
- 插件热加载与插件安装器
- 记忆 setup 命令
- 电脑控制授权
- MCP 目录安装
- 消息配对流
- 环境明文查看
- Webhook 投递日志（保持 501）
- systemd journal（保持 501）

契约在 `docs/clawd/settings-core-contract.md:64`。出现真正的安全接口之前，不指定实现文件。

## 交给 detail 的范围

下列项在总账里是 `detail-pending`，不是已实现，也不是失败。报告没到就保持这个状态。

| 项 | 报告 | 本轮只保留的边界 |
| --- | --- | --- |
| 空闲记忆改绑、todo 可信投影 | memory-todo | 看见 `turn_facade.py` 有改绑函数名，不等于已接 |
| IPv6 authority/home、事件错配/3 次 250ms/SSE、附件 fd 与 absPath | event-files | 面板监听可接受 `::1`，侧车线程写死 `127.0.0.1`；这不是 home 解析修复 |
| write_token、条件回滚、崩溃认领 | migration | `migration.py` 存在，没有实际迁移；跨库不是原子事务 |
| effort 围栏、未安装旧分支语义、completion units 与本进程/全局计数 | runtime | 无 authority 时提交函数返回 None 并继续，这不是失败关闭 |
| stage v2、Nix、包内资源 | distribution | 不按 MANIFEST 递归旧误报重派 |
| 四个未知内部包许可 | licenses | 未运行提取，不能套 MIT |

真实 wheel/sdist 是 `blocked`，不是 detail 写完就能过。必须同时有 `CLAWD_DISTRIBUTION_WHEEL` 和 `CLAWD_DISTRIBUTION_SDIST`，打开档案，按 `RESOURCE_CONTRACT.json` 的 `files[].path` 核普通文件、大小、sha256、无符号链接、无清单外文件、无 desktop，并核对许可。两个变量缺一，先记 blocked/2。测试里的 `AssertionError` 只说明调用方没给档案，不是产品缺陷，也不能改写成通过。

`scripts/clawd_acceptance/stages.py:136-143` 的 package 阶段只导入 `build_wheel`，没有构建。这是编排命令未接线。本轮不改该脚本。

## 其他 owner 正在改

- 设置页：技能启用不再走已拒绝 CLI、MCP 新增、自定义模型端点、开放对象、43 条汉化、工具集、记忆提供方、连接掩码、已装插件非秘密配置、定时任务 `deliver`/`skills`/`repeat`、更新页去掉立即更新、日志 journal 501。文件范围见总账 `carry-settings-pages`。不新建顶层 tab，不建项目记忆页。
- 续跑：成功 5 次硬停仍属未完成。后续文件是 decision、continuation、journal 消费点，以及必要时 chat-page 和两份 transcript。不要恢复侧车 watcher。`MAX_ATTEMPTS` 是失败预算，保留。旧回执不能整表删除。
- 品牌：metadata、CLI/TUI、Web、docs、插件仍在改。交接里的 fixture、侧车旧文案、`website/sidebars.ts`、`plugin-catalog/`、`optional-skills/` 还没被本轮重核，不能称已收口，也不能按字符串一刀切。

## 浏览器

`browser_native.install` 只替换面板 live_task。daemon 强制任务授权和云 dialog 的 target/session/frame 仍未接线。POSIX 0600 socket、客户端登记和 `owner_task_id` 都不是任务 token。精确方案由浏览器 owner 准备。未授权前不改已安装的 browser-harness，不读生产包，不关用户浏览器，不认领未知页，不回落 VNC。

## 不要做的事

- 不把 03:07 清单里的 “absent” 再派一次。那些文件现在存在。
- 不把本总账喂给 `scripts/clawd_acceptance/gate.py`。门禁只认 schema 1 和四种旧状态，而且仍读旧清单。
- 旧清单要等六份 detail 返回后，由集成人亲自核对再改。
- 核心审查报告还没按原始 ID 归并。不要把几份 JSON 的计数相加，也不要把建议代码当成已修复。
- 不删除 `build/lib`、artifacts、回滚资产，不读 `/root/.hermes`，不碰 `/usr/local/lib/hermes-agent`、原面板和线上目录。
