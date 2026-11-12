# clawd 核心缺陷审查（会话 / 运行 / 恢复 / 持久化）

- 审查时点：2026-11-06，只读，未运行测试、未构建、未改源码。
- 目标：`list_servers` 确认 `beqCprBT` = `HUAWEI CLOUD SG SERVER`，online，agent 2.2.0。非 HK PROXY，非 HUAWEI CLOUD HK。
- 远端源码：`/root/hermes-workspace/clawd`。核心 HEAD `a3c447d2d297a724219edc62c88b1802e4cb3f75`，分支 `clawd`，porcelain 22。面板 `apps/panel` HEAD `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29`，porcelain 123。
- 访问：只经已读过的 `/root/hermes-workspace/clawd-sg-migrate/moss.py` 的 `call_tool`/`run`。未 SSH。未输出密钥。未读 `/usr/local/lib/hermes-agent`、`/root/.hermes`、`clawd_server/bundled` 历史副本。
- 分类：`confirmed-static` = 调用链逻辑闭合，尚未运行；`risk` = 前提未证；`duplicate-current-fix` = 与已有 owner 的三项重叠，不另修。
- 其他 owner 正在写 `effort_service.py` / `server.py` / chat / shell / runs。相关结论绑定下面的 sha256，不能当成稳定现状。

## 摘要

8 项，按严重度。前 5 项是本路新证据。后 2 项是已有修复的重叠，只记账。第 6 项生产者契约已由测试锁死，消费者在 chat owner 的文件里，hash 可能过期。

## 发现

### CORE-01 confirmed-static 高 — fork 先结束原会话，失败不交还副本

- 入口：`POST /api/sessions/{session_id}/fork`，`APIServerAdapter._http_route_table` 注册于 `gateway/platforms/api_server.py:1571`，处理函数 `2813-2852`。
- 文件 hash：`e653b26b4bcb8e816cc673c62304727b511bbb24b980cfff8024d35beeab7a5c` `gateway/platforms/api_server.py`
- 出错分支：`2834-2837` `create_session` 写子会话；`2838` `end_session(source, "branched")`；`2839-2840` 另一次 `get_messages` + `replace_messages`；`2847-2850` 标题 `ValueError` 直接 `400`，响应体没有 fork id。复制或标题之前的异常同样发生在 `end_session` 之后，且不回滚。
- 消费者影响：调用方只看到 400/500，不会切到副本。原会话已被标成 `branched`。副本可能已有消息（标题失败）或为空（复制失败）。没有单事务，也没有“失败则恢复原会话并返回副本 id”的边界。
- 复现（未跑）：对已有会话 `POST /api/sessions/{id}/fork`，标题用会触发 `set_session_title` `ValueError` 的重名；或在 `end_session` 之后让 `replace_messages` 抛错。预期原行 `end_reason=branched`，响应无新会话 id，库里仍有 fork 行。
- 重叠：不是上传 413，不是 effort GET，不是“主会话误切子会话”。成功路径才会把客户端带去子会话；本缺陷是失败路径把原会话先结束。

### CORE-02 confirmed-static 高 — 历史读取异常变成空列表，回合仍成功写回原会话

- 入口：`POST /api/sessions/{id}/chat` `3097-3117`；`POST /v1/runs` 在 `gateway/platforms/api_server_runs.py:901-902` 同样调用。
- 出错分支：`gateway/platforms/api_server.py:2533-2541` `_conversation_history_for_session`。库不可用返回 `[]` 且不记日志；`get_messages_as_conversation` 任何异常只 warning 后返回 `[]`。两个调用方都不把空列表当失败。
- 为何不是“模型稍后自己重读”：`agent/turn_facade_lease.py:374-401`（hash `49cd9f3f6fe9cf0e98a6591d19ad53833349964a1a7528dc52e69ac3eefb5527`）只在租约争用后重载转录。无争用时保留调用方传入的空历史。`agent/session_persistence.py:548-574` 的 flush 按未打持久标记的新消息追加，不会用库存历史替换这次空上下文。
- 文件 hash：`api_server.py` 同上 `e653b26b…`；`api_server_runs.py` `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b`。
- 消费者影响：HTTP 仍是正常完成。模型看不到已有消息，新的 user/assistant 行追加进真实会话。这是“捕获异常后按空成功继续”，不是 503。
- 复现（未跑）：会话已有消息。在 `get_session` 成功之后让 `get_messages_as_conversation` 抛错，再 `POST /api/sessions/{id}/chat`。预期 200 和一条无上文的回复，且原消息仍在、新行被追加。
- 重叠：三项都不是。

### CORE-03 confirmed-static 高 — stop 在 agent 登记前不取消任务，中断异常仍回 stopping

- 入口：`POST /v1/runs/{run_id}/stop`，`api_server_runs.py:1549-1580`。运行体 `1117-1244`。
- 文件 hash：`554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b`
- 出错分支：`1158-1161` 只在 `create_agent` 之前看一次 `_stopping_run_ids`。`1196` 才写入 `_active_run_agents`。stop 在这个窗口看到 `agent is None` 且 `task is not None`，不走 409，`1572-1573` 标 `stopping`，`1574-1576` 因没有 agent 不发中断，也不 `task.cancel()`，`1580` 返回 200 `stopping`。之后执行循环不再读 `_stopping_run_ids`，`_finish` 会把状态写成真实终态（常为 completed）。agent 已登记时，`request_hard_interrupt` 被 `suppress(Exception)` 吃掉，HTTP 仍是 stopping。
- 消费者影响：客户端以为已停止。窗口内的回合继续跑完。中断函数抛错时同样被报成已接受。
- 复现（未跑）：`POST /v1/runs` 后、`_active_run_agents` 写入前立刻 `POST /v1/runs/{id}/stop`。预期 200 stopping，随后 `run.completed`。另：让 `request_hard_interrupt` 抛错，预期仍 200 stopping 且回合继续。
- 重叠：不是三项。authority 且无 `delivery_id` 的路径走 `stop_run`（`1558-1565`），不在这个窗口里。

### CORE-04 confirmed-static 高 — 提供方鉴权失败被标成 completed

- 入口：会话 chat 与 `/v1/runs` 都进 `APIServerAdapter._run_agent`。
- 出错分支：`gateway/platforms/api_server.py:3850-3858` 捕获 `_ProviderAuthResolutionError`，返回的 dict 只有 `final_response` 文本，没有 `failed`、`error`、`completed=False`。
- 消费者：`gateway/platforms/api_server_openai_routes.py:136-143` `_response_status` 因此返回 `completed`（hash `7c58fd232db426cbaed5deccf8ce0262af38d029f192ae5b6c9c7676991263a1`）。`api_server_runs.py:229-239` `terminal_run_status` 同样把缺省 `completed` 当成完成。会话 chat `3131-3140` 把该状态放进 HTTP 200。
- 注释只说明“不返回 HTTP 错误、把文本给调用方”，没有把终态标成失败。注释不能抵消两个状态映射的契约：未完成却报 completed。
- 复现（未跑）：让这两条路由的 `_run_agent` 抛 `_ProviderAuthResolutionError`。预期 chat JSON `status=completed`，`GET /v1/runs/{id}` 也是 completed，正文是鉴权错误文本。
- 重叠：不是 effort GET 本身。假完成会让续跑/档位把这次失败当成已结束，只记相邻影响。

### CORE-05 confirmed-static 中高 — 排队交接把本进程 SSE 订户封口

- 入口：`POST /v1/runs` 之后的 `_execute_run`，`api_server_runs.py:1228-1244`。
- 文件 hash：`554cb28b…` 同上。
- 出错分支：`AdmissionQueued`（`gateway/session_api_turn.py:33-38`）表示输入已提交，下一个 owner 应跑一次，本进程不得标 failed。该分支把状态设回 `queued` 后 `return`，但 `finally` 仍 `put_event(None)`。`_RunStream.put_nowait` `178-185` 见到 `None` 把 `terminal=True`，之后同流事件直接丢弃。
- 消费者影响：订户先看到 `run.queued`，再看到流关闭。TTL 内重连会立刻读到终态哨兵，收不到后续完成事件。工作是否真由下一 owner 执行，本函数不调度；只确认本进程订阅被提前封口。
- 复现（未跑）：SSE 已连上后让 `observe_api_turn` 抛 `AdmissionQueued`。预期持久状态仍是 queued，本流关闭，且不再投递后续事件。
- 重叠：不是三项。这是订阅 owner / 终态恢复边界。

### CORE-06 confirmed-static（快照）中 — 已安装 run-wait 永不返回 retrying，chat 仍把它当失败

- 生产者：`bind` → `native.install` → `runtime_native.install` 替换 sidecar `get_run_wait`。实现是 `gateway/platforms/clawd_runtime_routes.py:122-166` `project_run_wait`。状态只有 `unknown` 或（有终态幂等行且租约空闲时）`idle`。没有 `retrying`。hash `45ac61a9e568b3cc9cd0fb6a3d74c6886801b6b5117de6f84e43f13d231838ad`。sidecar 路由仍是 `apps/panel/sidecar/hermes_panel_sidecar.py:6178-6179` 调用被替换后的 `get_run_wait`。sidecar hash `11840a85bd8ce3a5be3a9abd4797b74cea1050895b19e80a63e95bd1fde8ff82`。
- 测试已锁死该契约：`tests/clawd_runtime_cases/test_installed_run_wait.py` `_assert_durable_unknown` 要求 `state != retrying`，且禁止读 agent.log。hash `f5e696e7409a493b4745abe7c5337d3ae645618a1521f940b681f3894f3dfb6e`。未运行该测试。
- 消费者：`apps/panel/src/react/pages/chat/turns.tsx:594-604` 只有 `state===retrying && available===true` 才清错误，否则设置 `Turn-scoped retry evidence unavailable`。hash `21b67d14595e07e897a3c71b40360fc5bf9bd48b5bd4b833a4cf6c732e1c9067`。会话 chat 不写 `runs_idempotency` 行，因此这条轮询在聊天回合上稳定落到 unknown。
- 缺失边界：原生替换删除了日志重试证据，没有给面板等价字段；面板也没有改成接受 `unknown`。
- 复现（未跑）：安装后的 sidecar `GET /run-wait?session=<聊天会话>`。预期 `state=unknown, available=false, source=durable`。面板状态行 title 出现 unavailable。不要把 planted agent.log 的 Retrying 行当成仍会被读到。
- 重叠：不是 `GET /api/sessions/{id}/effort`。用户可见文案相近，但是另一条路由。chat owner 正在改 chat，`turns.tsx` 这个 hash 可能过期，不能把面板半边说成稳定现状。生产者“永不 retrying”在上述 runtime hash 上是闭合的。

### DUP-01 duplicate-current-fix — 主会话被切到编辑副本

- 已有 owner，不另修。
- 快照证据：`apps/panel/src/react/pages/chat/chat-page.tsx:1121-1122` 编辑重发成功后 `setSelectedSessionId(result.session.id)`。hash `9a16432e91edbb356d06752dfc2d76c03b6116a1b7d63be05f7759a48f931df8`。失败分支 `1126-1127` 只提示副本已保留，没有返回入口。
- 核心也会交出另一个 id：消息列表 `api_server.py:2769-2805` 用 `resolve_resume_session_id`（`hermes_state_messages.py:1452-1491`，hash `b81c7d272f768a548daa91642bf8283dfad2f592528fbe95c029038a13e7f574`）把响应 `session_id` 收成压缩链上的有消息节点；chat 完成 `3131-3138` 用 `result.session_id` 做正文和 `X-Hermes-Session-Id`。压缩子会话是注释里的有意收口；委托子会话不在 `resolve_resume_session_id` 的前进条件里。不能把压缩收口单独说成“误切后无法返回”。
- 该 chat hash 可能因 owner 改动而过期。

### DUP-02 duplicate-current-fix — effort GET 运行状态 unknown

- 已有 owner，不另修，也不把 `effort_service.py` 当前字节当成稳定实现。
- 路由快照：`gateway/platforms/clawd_effort_routes.py:95-111` 任何异常把运行态变成 `unknown`；`127-141` GET 把 `unknown` 传成 `running=None` 后仍可能 200。hash `ddb968815d6c9a9f033eecdb9178cd6789f28d0cb71da63d53476597e8a9a3b8`。
- `clawd_server/effort_service.py` hash `4e2699372896c2ce45a76d7e5ed842ad0e016ec49d14f25786d146f753fac619`。owner 正在写这个文件，以上只说明审查当时的路由层会把异常收成 unknown。

## 明确不单列

- 上传 413：未沿 sidecar 上传实现追完。`clawd_server/server.py:172` 外层 `client_max_size=52*1024*1024`。该文件 hash `6f02e7fdece74187ed121a4ac865c1f213919c9f605dd71d817c42f0d0e949ac`，owner 正在写，不把 413 报成已确认或已修好。
- `project_run_wait` 在无幂等行时返回 unknown 是测试锁死的契约，不是单独缺陷。缺陷是 CORE-06 的消费者仍要求 `retrying`。
- `note_run_settled` 在没有事件循环时静默返回（`gateway/clawd_continuation.py:65-75`，hash `6c2be349953a7b1044549e322b5443d6e34847084bec600c46728eeae2c56d4f`）。面板启动路径在 async `start` 里 `arm_adapter`，这条没有闭合到真实调用线程，不升为确认缺陷。

## 覆盖与未查

已读：`clawd_server/bind.py` `bd309174…`、`runtime_native.py` `367e0ee3…`、`native.py` `58a0af2c…`、`contract.py` `e68ca1c7…`、`server.py` 见上。`server.start` 启动 `APIServerAdapter`，路由表 `api_server.py:1553-1597`，再跟会话创建/读取/fork/chat、`api_server_runs.py` 的启动/停止/事件订阅/终态、`clawd_runtime_routes.py`、幂等库 `discover_for_session`/`update_status`、续跑 `arm_adapter`/`evaluate`、租约准入和 flush 是否整表替换。相关测试只读了 `test_installed_run_wait.py` 的契约断言，未执行。

未查完，不能称全库无遗漏：`hermes_state_api_edit` 正文、`hermes_state_execution.stop_cli_execution` 正文、项目继承提交、上传大小限制实现、`effort_service.py` 正文、agent 主循环、shell/runs 面板实现、TUI。未跑 `scripts/run_tests.sh` 或任何专项。
