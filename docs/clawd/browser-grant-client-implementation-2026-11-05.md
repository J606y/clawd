# 浏览器 daemon grant 客户端实现

检查点：2026-11-05。本文只记录本路静态实现。未跑测试、未 build、未 import、未跑 CLI、未做网络或浏览器探针、未 commit/push/deploy。未读已安装包、`/usr/local/lib/hermes-agent`、原面板或生产配置。未删文件。未改 45/300 租约。

## 改了什么

| 文件 | 作用 |
|---|---|
| `/root/hermes-workspace/clawd/tools/browser_task_registry.py` | 内部列 `task_grant`；harness 无 grant 不登记；内部读取 |
| `/root/hermes-workspace/clawd/tools/browser_task_cdp.py` | 能力探针、创建、画面/输入/关闭附带、dialog 读取 |
| `/root/hermes-workspace/clawd/tests/tools/test_browser_daemon_task_grant.py` | 用例已写，未运行 |
| 本文件 | 实现记录 |

IPC 仍是既有 `browser_harness._ipc.request` 的一行 JSON。没有新 socket、websocket、HTTP 或 VNC。本地 `create_page` / `close_exact` / `PageSocket` 未改。

## 冻结接口

```text
register_created(..., task_grant: str = "") -> None
harness_task_grant(target_id: str, expected_pid: int) -> str
ensure_harness_task_grants(directory: str, stem: str, expected_pid: int) -> None
create_harness_target(directory: str, stem: str, expected_pid: int, url: str = "about:blank") -> tuple[str, str]
harness_pending_dialog(directory: str, stem: str, expected_pid: int) -> dict | None
```

`harness_task_grant` 只给 IPC 附带。`session_view`、`task_connection`、`active_task_targets` 的字典没有 `task_grant` / `taskGrant`。面板 live JSON 仍从 `session_view` 取字段，本路未改 `clawd_server/browser_native.py`。`cleanupError` 写入和读出前会去掉已存 grant 子串。异常文本不包含 grant；登记写入失败不链式带出 sqlite 参数。

可存储的 grant 必须是 ASCII `[A-Za-z0-9_-]{16,128}`。`secrets.token_urlsafe(32)` 的 43 字符落在这个范围。客户端不生成 token。不符合的值视为未授权，不回显原文。

## 调用链

1. 创建前 `ensure_harness_task_grants`：`ping` 核对 `expected_pid`，再发 `{"meta": "capabilities"}`。只接受响应顶层 `task_grants is True`、`dialog_identity is True`、`task_grant_version` 的类型是 `int` 且等于 `1`。`True` 不能充当版本号。否则 `daemon_grant_unsupported`，不建页。pid 不符是 `browser_replaced`，不是不支持。vendor 负责这个响应；本客户端不编字段。
2. `create_harness_target` 在探针通过后才发 `Target.createTarget`。请求不带 `task_grant`。只读 `result.targetId` 和外层兄弟字段 `task_grant`。`result` 里面的同名值忽略。缺外层 grant：只对刚返回的那个 `targetId` 发 `Target.closeTarget`，不带客户端 token，不 `Browser.close`，不列页。关失败仍报 `task_grant_required`，消息加 `close rejected`。
3. 调用方用返回的一对值调用 `register_created(..., transport="harness"|endpoint="harness", task_grant=...)`。空 grant、坏形状、非 harness 行上的客户端自带 grant 都不写成已授权。非 harness 行强制存空字符串。
4. 画面：`capture_harness_jpeg` 仍接受已装调用 `capture_harness_jpeg(*locator, target_id)`，其中 `locator` 是 `(directory, stem, pid)`。内部按类型还原成 `(target_id, expected_pid)`，再走 `harness_call`。
5. 输入：`harness_call(*bound, target_id, method, params)` 同样按类型还原。`Page.*` / `Runtime.*` / `Input.*` 先 `Target.attachToTarget`，两步请求都在 `method` 同级带登记簿 grant，不放进 `params`。显式 `session_id` 时不再 attach，避免换成另一条 session。
6. 关闭：`close_harness_target` 先核对 pid，再读登记簿 grant。没有 grant 或 target 不对，不发 CDP，返回 `close rejected`。`Target.closeTarget` 带 grant。随后的 `Target.getTargetInfo` 不带 grant，这样已关闭页的原错误仍表示关成了，不会被 mismatch 误判成还开着。
7. dialog 读取：先做同一条 capability 探针，再发 `{"meta": "pending_dialog"}`。这条 meta 没有 target，请求不附 grant。返回对象去掉 `task_grant`。daemon 没给的 `targetId` / `sessionId` / `frameId` 不补。若 daemon 给了 `targetId`，再用 `harness_task_grant` 核对；没有登记 grant 则 `task_grant_required`，仍不编身份。

`harness_call` 拒绝 `Target.createTarget`。创建必须走 `create_harness_target`，否则 sibling grant 会被丢掉。旧 pid、错 target、无 grant 在调用 CDP 前拒绝。daemon 返回的 `task_grant_required` / `task_grant_mismatch` / `dialog_unscoped` / `dialog_unowned` / `browser_close_refused` 保持原 code，不剥掉 grant 重试，不扫页。

## 给其他 owner 的接线

- CDP：`create_harness_target` 拿到 `(target_id, task_grant)` 后再 `register_created`。不要再把 `Target.createTarget` 交给 `harness_call`。
- CLI：回执第四行传给 `register_created(task_grant=...)`。`adopt_spool(..., task_grant=)` 只在恰好一个 id 时转发同一把 grant。多个 id 直接 `task_grant_required`，不写行，也不清空 spool。现有 CLI 的宽 `except` 会因此关掉已经登记的主 target；那不是本函数把额外页授权了。
- dialog：应答用 `harness_call(..., method="Page.handleJavaScriptDialog", session_id=<daemon sessionId>, expected_pid=...)`。不要自己填 target/session/frame。三项缺一，不要调用应答。空的显式 `session_id` 会拒绝，不会改附到别的 session。
- 面板画面/输入不用改调用形状。grant 由 `harness_call` / `capture_harness_jpeg` 从登记簿读取。页还没登记时会失败关闭，这是预期。

## 契约缺口

本路没有另造接口。下面这些点不够，调用方不能自行补协议：

- `meta=capabilities` 的三个字段必须在响应顶层。嵌在别的键下会被当成 `daemon_grant_unsupported`。
- grant 字符集和长度是客户端存储规则，不是 daemon 新字段。vendor 若发出别的字母表，创建会被当成未授权并只关该 target。
- `pending_dialog` 请求没有 target，因此不能附带某一页的 grant。应答才走 `harness_call` 的已有 `session_id` 字段。
- spool 仍是每行一个 target id。没有冻结的逐 target grant 格式，所以多个 harness id 不能登记。
- 父进程不发 `meta=set_session`。任务页切会话仍由 vendor helper 带 grant。本路没有新的 set_session 函数。
- `browser_native` 的 `*locator` 把 pid 放在 target 槽。这是已装调用，不是新名称。只在 `harness_call` / `capture_harness_jpeg` 内按 `int`/`str` 识别。

## 未运行

`tests/tools/test_browser_daemon_task_grant.py` 已准备，覆盖拒绝登记、公开脱敏、旧 pid、创建前探针、外层 grant、未授权只关新 target、画面/输入/关闭附带、dialog 不编身份。未执行。未 build，未 import 产品模块，未启动 daemon。
