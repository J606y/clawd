# 执行循环只读审查

观察时刻：2026-11-04T20:08:25Z。模型 grok-4.7。只读，未跑测试/导入/探针，未改产品。不声称全内核无缺陷。

## 结构（只读到的链路）

```
clawd_cli → hermes_cli.main
  CLI 对话线程  hermes_cli/cli_chat_turn_mixin._chat_run_agent
  单次查询      hermes_cli/oneshot.py
        │
API  gateway/platforms/api_server._run_agent
  session_authority 有 → run_api_turn；停走 authority.interrupt / pending_stops
  无                 → run_in_executor → run_conversation
  POST /v1/runs/{id}/stop → api_server_runs._handle_stop_run
        │
AIAgent.run_conversation = TurnFacadeMixin
  租约 admit_durable_turn_lease →（新）_commit_pending_effort
  → conversation_loop.run_conversation / _run_conversation_turn
       模型等待 interruptible_api_call / streaming / direct_api_call
       工具     turn_tool_round → run_agent._execute_tool_calls
                → tool_executor 顺序(1s 轮询) / 并行(5s 轮询)
  → turn_finalizer.finalize_turn（clear_interrupt、任务资源清理）
  finally 释放租约、relay、accounting ContextVar、review 计数
```

状态/后台/传输只碰到取消与终态的直接调用：`session_authority.interrupt`、`pending_stops`、`finish_logical_calls`、API run status。未展开 provider、压缩、持久化内部。

## 已读 / 未全读

已读入口与循环：`run_agent.py`（调度/关闭/工具分发）、`agent/turn_facade.py`、`turn_facade_lease.py`、`conversation_loop.py` 回合主路径、`interrupt_control.py`、`tools/interrupt.py`、`turn_iteration_prep.py` 中断出口、`turn_api_call.handle_api_interrupt`、`chat_completion_helpers` 可中断调用与 `direct_api_call`、`chat_completion_nonstream.py`、`tool_executor.py` 顺序/并行等待与清理、`turn_tool_round.py`、`turn_loop_errors.py`、`turn_finalizer.py` 收尾段、CLI `_chat_run_agent`、API `_run_agent` / `_execute_run` / `_handle_stop_run`、`commit_deferred_effort` 前置条件。

未全盘扫描：provider transport、压缩、SessionDB 写入细节、TUI `prompt_turn.py`、ACP、子代理全路径、`gateway/run.py` 主体。

## 动态文件（待稳定复核，勿按过期快照派修）

| 文件 | sha256 | 状态 |
|---|---|---|
| agent/turn_facade.py | `bafbab725a37c0790ad7da2c63a06968d00215d74bbfe31f2a34946fad50c9a7` | 已改，+21 |
| clawd_server/effort_service.py | `8cd685880f82709654679de6e99dac558e7735b9c7dc85fa16c92fcc2aec2d98` | 未跟踪 |
| gateway/platforms/api_server.py | `8f01bf9801575eefc08536b32e6ab70e0444eff1c2f34db0adfcf7afc4f8b4a7` | 已改 |
| gateway/platforms/api_server_runs.py | `35d586dc559e64fff774f84916c40a1c48bf0e340f82e554afd1a4a35b284ea3` | 已改 |
| run_agent.py | `77228b287ca611e0fcc51fe1fa7c1efc032d148ff642165c8606530f4520d00d` | 已改，仅 `_ensure_db_session` 对 `ProjectInheritanceError` 再抛 |

稳定对照：`hermes_cli/cli_chat_turn_mixin.py` `aad4a245b8187ec2da7e0d5bb9a33bc9d68fb2e3a873015c3c1fa02118fdbab8`。

## 按严重程度

- 高：1。租约回合在进模型前必失败。
- 中：2。CLI 异常把内存历史换成空列表。3。旧 API 停/断线在 agent 注册前停不住执行线程。
- 低：本范围无单独低严重项。

## 已确认

### 1. 高 · 确定 · 待稳定复核

位置：`agent/turn_facade.py:31` 调用，`:115` 模型循环前无保护，`:221` finally 吞掉同一错误。被调 `clawd_server/effort_service.py:502-507`。

描述：新代码在持有 session turn lease 时调用 `commit_deferred_effort(db, session_id)`，不传 `authority`，`admission_held` 默认为 False。被调函数在检查“无 pending 则 no-op”之前就 `raise EffortServiceError`。文档写缺 pin 是 no-op，与被调契约相反。`:115` 不捕获，异常冒出 `run_conversation`。finally 仍会 `lease.release()`，租约不漏，但回合从未进模型/工具。`:221` 同一调用被吞，pin 也不会提交。

触发：CLI/API/oneshot 任一有 `session_db`、`session_id`、持久化开启且 `acquire_session_turn_lease` 成功的回合。无 session 或 `_persist_disabled` 不进此路径。

修复（只写报告）：本入口没有准入锁证明，禁止在这里调用。真正提交放到持有 admission lock 的准入方，且在复制 `model_options` 之前。

```python
def _commit_pending_effort(agent, session_id: str) -> None:
    return  # turn lease != admission lock；缺 authority/admission_held 必抛
```

并删除 `:115` 与 `:221` 两处调用，直到调用方能满足 `effort_service.py:480-484`。

验证：有 session 的 CLI/API 发一轮，断言进入模型调用且不抛 `effort_admission_required`；无 pending pin 时回合成功；有 pending 且无准入证明时 pin 保留、回合仍成功。

### 2. 中 · 确定

位置：`hermes_cli/cli_chat_turn_mixin.py:361-371` 把 `messages` 写成 `[]`；`:500-501` 用该结果覆盖 `conversation_history`。`agent/turn_context.py:1176` 空历史不会从 SessionDB 回载。

描述：`run_conversation` 抛 `Exception` 时，CLI 用空 `messages` 替换本会话内存历史。下一轮只看见新用户句。flush 按未标记消息追加，不据此删除旧行，但活会话模型上下文已断，直到显式 resume/回载。问题 1 使该路径在每个租约回合上立即可达。`run_agent.py:347` 再抛的 `ProjectInheritanceError` 同样落入此处理；再抛本身是故意失败关闭，不是缺陷。

触发：对话 CLI 的 agent 线程里 `run_conversation` 抛出任意 `Exception`（含问题 1）。`KeyboardInterrupt` 不进这个 `except`。

修复：

```python
turn.result = {
    "final_response": chat_error_response(...),
    "messages": list(self.conversation_history),
    "api_calls": 0,
    "completed": False,
    "failed": True,
    "error": _summary,
}
```

验证：mock `run_conversation` 抛错，断言下一轮传入的 history 仍含抛错前的用户/助手行。

### 3. 中 · 确定 · 待稳定复核

位置：`gateway/platforms/api_server_runs.py:1146-1148` 只在创建 agent 前看一次 `_stopping_run_ids`；`:1180-1186` 之后才登记并跑；`:1559-1567` 仅当 `agent is not None` 才 `request_hard_interrupt`，不取消执行线程。`api_server.py:3263-3269` 断线时 agent 未登记则只 `task.cancel()`。`api_server_runs.py:92-114` 写明 handler 取消不能停 worker；`run_in_executor` 的 Future 取消不停已运行线程。

描述：`session_authority is None` 的旧路径上，停或 SSE 断线若落在 `_create_agent` 期间（agent 尚未进入 `_active_run_agents`），停止位被记下但无人再读，线程继续 `run_conversation`，工具副作用仍会发生。authority 路径另走 `stop_run`：未采纳时写入 `pending_stops`，`adopt_agent`（`session_authority.py:1014-1017`）再硬停。该路径本快照未发现同样空洞。

触发：无 `session_authority` 时，`POST /v1/runs/{id}/stop` 或会话 SSE 断线发生在任务已过 `:1146`、agent 尚未登记之间。有 authority 的停不走这段。

修复：创建 agent 后、`run_conversation` 前再读 `_stopping_run_ids`；命中则硬停并 `_finish("cancelled")`，不要依赖 `task.cancel()`。断线路径在 agent 未登记时也要留下 worker 在进循环前能看到的停止位。

验证：旧路径里在 `_create_agent` 内阻塞，并发 stop，断言不调用模型、run 终态为 cancelled 而不是随后 completed。

## 疑点

无足够证据的项不单列。下列已看过、不升格为缺陷：`finish_logical_calls` 成功后再入 except 时，逻辑调用表已清空，第二次是空操作（`relay_runtime.py:1189-1203`）。非协作工具在 abandon 后以 daemon 跑完，是 `tool_executor.py:1465-1466`、`:1511-1515` 写明的边界，不是漏 finally。

## 本范围未发现

- 模型等待：非流式 0.3s 轮询会关本次请求 client 并抛 `InterruptedError`；流式监视同样；inline `direct_api_call` 靠 `_active_request_abort`。`/stop` 不靠工具自己轮询也能在顺序 1s、并行 5s 内放弃未完成 future。
- 工具结果配对：顺序中断会补 skip 结果；外层 `Exception` 会给未回答的 `tool_call` 补错误结果（`turn_loop_errors.py:120-142`）。
- 租约/relay/ContextVar/review 计数在 facade `finally` 释放；worker tid 退出时清中断位（`tool_executor.py:551-564`）。
- `ProjectInheritanceError` 再抛是故意的，避免无项目绑定仍跑模型。

未证明这些路径在所有 provider/超时组合下都无竞态。

## 未覆盖

provider 与传输实现、压缩锁、SessionDB 锁持有时间、TUI/ACP 取消、子代理嵌套池、有 authority 时 `observe_api_turn` 内部等待、续跑 `clawd_continuation`（API diff 正在加，未当执行循环缺陷审查）。动态文件在观察后可能已变，问题 1 与 3 必须按新哈希复核后再派修。
