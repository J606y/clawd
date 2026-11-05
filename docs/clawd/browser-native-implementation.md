# 浏览器任务 target 原生化

未跑测试、未构建、未部署、未做网络探针。身份是 grok-4.7，未改配置。父代理已在 `clawd_server/native.py` 的 `install` 里调用 `browser_native.install`；本文件不把“未安装”写成现状。

## 真实进程拓扑

| 路径 | 进程 | supervisor / 签名连接 |
|---|---|---|
| 面板 API 聊天 | `clawd_server.server` 同进程：API adapter 的 `_run_agent` 与侧车线程 | 登记簿同进程可见。云端签名 socket 不在 `SUPERVISOR_REGISTRY`，在 browser-harness daemon |
| 默认 delegate_task | 父进程内的子 agent | 与父进程相同 |
| managed worker | `python -m agent.managed_worker` 子进程。仅 safe/kanban，或 `gateway.managed_workers` 且 CLI custom | supervisor 若被拉起也不在面板进程。daemon socket 路径写入 profile 登记簿后，侧车可连 |
| TUI | 独立进程 | 同上，靠登记簿里的绝对 socket 目录，不靠同进程表 |
| browser-harness daemon | 独立进程，AF_UNIX；Windows 为 loopback TCP + port 文件 token | **云端签名连接的持有者**。既有 IPC：`ping`、`connection_status`、CDP `method` |

同进程 `SUPERVISOR_REGISTRY` 不是面板云端链路。画面、输入、关闭走 daemon 的既有 JSON 行协议，不新开 `wss`。

## 已实现

- 安装点仍是 `browser_native.install(mod)`。缺侧车符号整次拒绝。不改 sidecar、`native.py`、`bind.py`、`server.py`。
- 登记簿 `<profile>/browser-tasks/registry.sqlite3`。新增 `transport`、`daemon_dir`、`daemon_stem`、`daemon_pid`。profile 只取上下文覆盖，否则 `CLAWD_HOME`/`HERMES_HOME`；未设置或生产 home 拒绝。
- 云 autospawn 回执：父进程用子环境调用 harness 自己的 `start_remote_daemon`，只解析返回的 `cdpUrl`/`id`，校验后写入子环境。不读 `/proc/<pid>/environ`，不读其他进程 secret。非 Linux 同一条回执。
- 已有云 daemon 但登记簿没有这条 `wss`：失败关闭，不猜 endpoint。
- 本机已有 Chrome、没有 `BU_CDP_WS`：用 harness 自己的 loopback 监听判断，daemon `ping` 的 `browser_kind`+`pid` 作回执，`Target.createTarget` 的 id 才登记。不扫页、不认用户页。endpoint 记为 `harness`，browser id 为 `daemon:<pid>`。
- 面板画面/输入/关闭：登记簿里的 socket 目录 + stem + pid，经 `browser_harness._ipc.request`。pid 不符则 `browser_replaced`。不直连第二条云 websocket。
- provider 只给 HTTP discovery：先用既有 SSRF 地板；通过后走既有 `resolve_cdp_override`。得到 `wss` 才当云端。私网或解析不出 websocket：`discovery_unresolved` / `endpoint_blocked`，不降级到可见页，不加新协议。
- 无身份 `browser_dialog` 仍应答 supervisor 上非任务页的 dialog。任务已创建页必须带 session。该函数不关页。
- `apps/panel/src/react/lib/task-live.ts` 是真消费者（`task-dock.tsx` 的 `@/lib/task-live`）。`src/lib/task-live.ts` 不是这条别名。接口未改，本路未改前端。

## 调用顺序

1. 父代理绑定 profile 后 `native.install` → `browser_native.install(mod)`。
2. `browser_exec` 解析身份 → 回执（provider `wss`、`start_remote_daemon` 返回，或 daemon ping）→ 子进程 preamble 在 daemon 已有 socket 上 `Target.createTarget` → `register_created(..., transport="harness", daemon_dir, daemon_stem, daemon_pid)`。
3. 侧车 `live_task` / control / input 读 `session_view`，核对 `owns`，再 `harness_ping` + `harness_call` / `capture_harness_jpeg` / `close_harness_target`。
4. 不要对同一条签名 URL 再 `SUPERVISOR_REGISTRY.get_or_start`。`ensure_cdp_supervisor`、vault attach 与 `get_or_start` 都拒绝 `wss`。vault 填已登记 daemon 页时走既有 `Runtime.evaluate`，不新开 websocket。

## 本路收口（未运行）

vault 调用链：`browser_vault` → `_eval_js` / `_eval_js_secret` → 插件页后端，或登记簿里本任务、本会话的 daemon 页（`daemon_dir` + `stem` + `pid` + `targetId`）经 `harness_page_eval`，或本机 supervisor。会话 `cdp_url` 为签名 websocket 且没有这条回执时拒绝，不 `get_or_start`。凭据仍走既有 fill；失败文本不回显脚本。焦点只在已登记 target 上读 origin，不 `Target.getTargets`。

云 dialog：本地 supervisor 仍用既有 target/frame 父链。登记为 harness 的任务不改走 supervisor，也不开第二条 websocket。daemon `pending_dialog` 没有 target、session、frame，父链无法建立，应答失败关闭。这不是 daemon 已强制校验。

`Browser.close` 与 `Target.disposeBrowserContext` 在不能证明独占整浏览器时拒绝。`Target.closeTarget` 与租约清理仍可用。无身份的 `/browser connect` 非任务页 dialog 保留。

## 状态

| 路径 | 状态 | 证据 |
|---|---|---|
| 面板 API 同进程登记 + daemon IPC 画面/输入 | implemented | `server.py` 同进程；`browser_native._harness_of` |
| 跨进程（managed worker / TUI）经登记簿 socket | implemented | `task_connection` + 绝对 `daemon_dir`；未跑 |
| 云 autospawn 回执，含非 Linux | implemented | `start_remote_daemon` 返回；preamble 无 `/proc` |
| 本地 Chrome 无 `BU_CDP_WS` | implemented | ping kind/pid + createTarget；无 URL 不猜 |
| provider HTTP discovery | implemented | `promote_provider_discovery`；失败即阻塞 |
| loopback `/json/new` | implemented | 原协议，未改成拒绝 |
| 无身份 dialog，不关用户页 | implemented | `browser_dialog` 不调用 close；无身份只应答非任务页 |
| 云端 dialog 应答 | blocked | 既有 IPC 的 `pending_dialog` 无 target/session/frame。`drain_events` 会清事件。不新开 websocket。见下方最小依赖 |
| daemon 内强制 task 身份 | blocked | 客户端登记簿检查不是 daemon 安全。既有请求无 daemon 自己签发的绑定。见下方最小依赖 |
| vault 填云端页 | implemented | 已登记 daemon 页走既有 `Runtime.evaluate`。未登记的签名 URL 不 `get_or_start`。未跑 |
| `Browser.close` 且调用方有身份 | implemented | 权限边界：任务身份不是独占整浏览器，故拒绝 `Browser.close` / `Target.disposeBrowserContext`。`Target.closeTarget` 与租约清理仍可用。不靠扫页证明 |
| 前端 `src/lib/task-live.ts` | 非消费者 | 真消费者是 `apps/panel/src/react/lib/task-live.ts`。未改 |
| 分发 resources/backend | 未触碰 | 另路拥有 |

## 最小源码依赖

`browser_harness` 是外部包。本仓库没有 `browser_harness/daemon.py`。未读、未改安装包，也未访问官方安装目录。

既有传输仍是 `browser_harness._ipc.request`：Unix socket 为 daemon 目录下的 stem.sock，Windows 为 loopback 端口文件。已用载荷只有 ping 元命令，以及 method、params、可选 session_id。已用方法包括 Target.attachToTarget、Target.createTarget、Target.closeTarget、Target.getTargetInfo、Page.captureScreenshot、Runtime.evaluate、Page.getLayoutMetrics、Input 派发。没有 owner 字段。不能往这个请求加 daemon 不认识的字段，否则可能打断已有截图、输入和关闭。

要在同一 `handle` 上完成后半段，外部源码最少要改：

1. `browser_harness/daemon.py` 的 `handle`：`pending_dialog` 带上该次 attach 已有的 `targetId`、`sessionId`、`frameId`。`drain_events` 在客户端读到前不得丢掉这三项。
2. daemon 自己在本进程 `Target.createTarget` 记下的任务页，`handle` 在请求缺少 daemon 签发并保存的绑定时拒绝 `Page.*`、`Runtime.*`、`Input.*` 和该页的 `Target.closeTarget`。客户端自报 owner 不算绑定。
3. 无任务页的原 `/browser connect` 请求继续可用，但 `Browser.close` 与 `Target.disposeBrowserContext` 在不能证明独占整浏览器时仍拒绝。不要靠列 target 来证明，不要新 socket、新 websocket 或新 HTTP。

这三项未落地前，云 dialog 与 daemon 内任务页授权未完成。客户端拒绝不能算 daemon 已安全。

测试文件已改断言，**未运行**。未启动浏览器，未做网络探针。
