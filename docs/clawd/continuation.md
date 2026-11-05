# 核心自动续跑

子代理完成后，API 会话的下一轮由核心续跑服务负责，不依赖面板、sidecar 或额外网页请求。

## 所有权

- 模块：`gateway.clawd_continuation`
- API adapter `connect` 成功绑定后 `arm_adapter`，`disconnect` 时 `disarm_adapter`
- `persist_delegation_delivery` 仍只写完成通知，不 self-POST 普通 user
- 通知落库后若服务已启动，才调度评估；未启动则不产生回合

## 触发

完成通知必须是真正的 `async_delegation_complete`：排除 `delivery_notice`、`presentation_suppressed`、以及 `[ASYNC DELEGATION TASK FAILED — ` 的 interim。它必须晚于最后一条助手回复和最后一条真人输入。派发之后已经答完的人话不否决更晚的完成通知。插话（`display_kind=steer`）算真人。续跑行和历史通知不是真人输入。

## 闸门

批准、clarify、计划审批、显式取消、连续上限、provider 无法继承、所有权不明，都在代码里阻止，不把“如果等确认则等待”塞进提示。提示是固定短句，不复制原用户 prompt 或任务正文。

模型、provider、effort 来自会话已确认的 runtime lock。续跑只使用原 `session_id` 和该行已有的 `cwd`/`session_key`，不新建会话，也不把进程当前目录写成默认工作目录。没有 lock 时用最近一次 model+provider。两者都没有则失败关闭。

## 持久化

账本在会话库同目录的 `clawd_continuation.db`，是状态的唯一事实来源。含去重 receipt、冻结请求体、租约、重试次数和可见事件。`GET /api/sessions/{session_id}/continuation` 只读该账本，不创建 run。

幂等键同时写入 durable `runs_idempotency`。存储不可用、所有权无法确认、或存在无法归属的持有者时失败关闭并写事件。忙或 draining 推迟重试，不丢也不双跑。

CLI/TUI 会话不走本机制。
