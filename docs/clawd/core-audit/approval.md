# clawd 核心工具审批只读审查

审查范围：`validate_tool_calls` 到真实执行器、人工审批响应、网关审批入口，以及这些入口调用的 schema / 插件 `pre_tool_call`。未展开浏览器、文件沙箱专项和当前记忆实现。子代理权限只在执行门禁观察。

方法：只读源码。未运行测试、构建、探针，未执行产品工具，未访问生产凭据或 `/root/.hermes`。结论以当前工作树为准，不沿用旧报告。基线 `c314285f077e91937c995d419b9fa40af9eb4981` 仅作只读对照。下列三文件相对基线已脏，行号以本次读取为准，稳定后须复核：

| 文件 | 工作树 blob | 与审批相关的现状 |
|---|---|---|
| `gateway/platforms/api_server_runs.py` | `7e5bfdf1cdcf08a6525260e79e1bd6b751dfd9d6` | 审批处理函数仍在；同文件新增续跑终态通知 |
| `tui_gateway/methods_prompt.py` | `c330c8777d3260d622d38804f9cf63bd10451990` | `approval.respond` 仍在 1350–1354 |
| `tui_gateway/server.py` | `386210fe4fe856d34f51daa2e930ad1ea4397765` | 服务端审批回包仍在 729–731 |

其余引用文件本次 `git diff --stat` 无改动。

## 结构、入口与信任边界

一次模型工具批次的真实顺序是：

1. `agent/turn_tool_validation.py` `validate_tool_calls`（72–236）只核对名字是否在 `agent.valid_tool_names`、参数是否为 JSON。它不审批副作用。
2. `agent/turn_tool_round.py` `run_tool_round`（80–171）在校验通过后展开本地批次、去重，先把 tool-call 行写入会话，再调用 `agent._execute_tool_calls`。
3. 执行器在 `agent/tool_executor.py`。顺序路径经 `execute_tool_calls_sequential`（1857）和 `terminal_approval_batch`；并行路径由 `_plan_tool_batch_segments` 分段。真正的命令门在工具处理器里调用 `tools.approval.check_all_command_guards` / `check_execute_code_guard`，不是在 `validate_tool_calls`。
4. 人的决定进入 `tools.approval.resolve_gateway_approval`（143–177）。等待方在 `tools/approval_gateway_wait.py` `_await_gateway_decision`。只有 `once` / `session` / `always` 且未被判为拒绝时，`_human_decision`（915–919）才放行并可能持久化。

信任边界：

- API 权威门（`session_authority` 存在时）要求 run 归属、`approve` 权限、精确 `request_id`、整数 `execution_generation`，且 choice 必须在该提示提供的集合内。这里铸造的 `Principal(..., session:approve)` 是 HTTP 鉴权之后的内部代表，不是“人已经点过同意”的标记。
- 无权威对象时的旧 API 门、TUI RPC、聊天 `/approve`、部分平台按钮，不核对 `execution_generation`，并且可以不带 `request_id` 去消费 FIFO 队列。
- `ack_gateway_approval` 只标记卡片已收到，不写入 `result`，不能当成授权。
- 进程级 `HERMES_YOLO_MODE` 在导入时冻结。`approvals.mode: off`、会话 `/yolo`、以及仅本地 CLI 本人可提交的 `hermes -z`（`unattended_turn`）会跳过可恢复审批层。硬线、用户 deny 规则仍在这之前。
- 插件 `pre_tool_call` 的 `approve` 会进入同一人审门；钩子异常对普通 `pre_tool_call` 是失败即放行，对已标记 `approve` 的门是失败即拒绝。

## 逐文件

### `agent/turn_tool_validation.py`、`agent/turn_tool_round.py`

校验只拒绝未知名字和坏 JSON。名字修复（`repair_tool_call`，`agent/agent_runtime_helpers.py` 2555–2602）只能改成已在 `valid_tool_names` 中的名字，模糊匹配截止 0.7。执行前的别名表 `_LEGACY_TOOL_ALIASES`（`model_tools.py` 624–627、906）只在名字已经通过校验后生效。静态未见“别名把受门工具换成不受门处理器”的核心映射。`tool_call` 桥在执行时展开底层工具，底层仍走自己的处理器和门；这不是命令门的跳过。

### `agent/tool_executor.py`、`agent/tool_dispatch_helpers.py`、`agent/terminal_approval_batch.py`

`terminal` 不在 `_PARALLEL_SAFE_TOOLS`（33–44），混批里它是顺序屏障，不会和别的工具同一段并行启动。桌面且已注册 notify、且相邻调用全是 `terminal` 时，`terminal_approval_batch` 先收集审批再按序放行；准备阶段不启动 shell。取消会在 `release` 前经 `check_cancelled` 抛出。

写文件的人审在处理器内部，晚于启动顺序门。`_dispatch_authorized_once` 在 743 行先 `_advance_start_order`，然后才 `execute`。`write_file_tool`（`tools/file_tools.py` 873–876）进入函数后才调用 `_check_approval_required_write`。并行段里，前一个调用一旦推进启动顺序，后一个不重叠的并行工具就可以开始执行。

### `tools/approval.py`、`tools/approval_gateway_wait.py`

`resolve_gateway_approval` 在锁内按 `request_id` 精确弹出；没有 `request_id` 时弹出最旧一条；`resolve_all` 清空该会话队列。重复的同一 `request_id` 在条目已弹出后返回 0，不会再次执行。

超时后的选择不能在条目已删除后重放。但 `_drop_entry`（194–231）会收下“截止检查之后、出队之前”落入的选择。这是有意的窄窗口，不是可保存的过期票据。

等待中若本线程已见 interrupt，`_await_gateway_decision`（256–258）强制 `choice = "deny"`，即使队列里已经写入同意。取消后尚未启动的命令不会因迟到的 approve 继续。已经进入工具函数的命令不在此保证内：顺序执行器在中断后仍等最多 3 秒，若 future 已完成则采用其结果（`tool_executor.py` 952–958）。

`_human_decision`（915–919）把除 `None` 和 `"deny"` 以外的任何字符串当成同意。`session` / `always` 才会持久化；其他非拒绝字符串仍是一次放行。

无 CLI、无网关、无 ask，且不落在 cron / `-q` / 无人值守平台时，`check_all_command_guards`（1211–1216）和 `_run_approval_gate`（1060–1062）默认自动同意。无人值守平台和 cron / `-q` 的配置默认是 deny（`approval_context.py` 347–371）。

### 网关审批入口

权威 API：`api_server_runs.py` 1413–1427 在 `session_authority` 存在时转入 `api_server_authority_runs.py` `respond_run`（113–133）。body 必须恰好是 `request_id`、`execution_generation`、`choice`；generation 必须是该 admission 的 int；随后 `session_pending_controls.py` 95–99 只接受该提示的 `choices`，并带精确 `request_id`。`session_authority.respond`（1053–1067）还要求调用者在订阅者中，且 generation 仍是 running。这条门不把内部标记当成同意。铸造 Principal 发生在 HTTP `approve` 权限和 run 归属检查之后。

旧 API 分支（1428–1454）只在没有 `session_authority` 时到达。它校验 choice 枚举，但 `request_id` 可缺，缺省消费最旧一条，且 `all` / `resolve_all` 可一次同意全部。没有 `execution_generation`。本次未探测运行中的 runner 是否总有 `session_authority`，因此这条分支的现网可达性标为需验证；分支本身在源码中存在。

TUI：`methods_prompt.py` 1340–1354 的 `approval.respond` 把 `params.get("choice", "deny")` 原样送入队列，`all` 原样变成 `resolve_all`，不核对 `execution_generation`。`_sess`（`session_registry.py` 304–325）只判断内存里有没有这个 `session_id`。会话找不到时，1527–1554 用 `request_id` 扫描所有活会话。schema `ApprovalRespondParams`（`contracts/prompt_voice.py` 332–338）的 `choice` 是自由字符串，没有枚举，也没有 generation。同名规范方法 `CanonicalApprovalParams`（`canonical_sessions.py` 265–292）要求 `execution_generation`，但这个 `@method` 处理器不执行那份约束。

服务端卡片回包 `server.py` 718–731 同样接受任意 `choice`，且 `result.all` 为真时同意该会话全部待决项。`request_id` 来自原提示，这一条绑定是好的。

聊天 `/approve`（`slash_commands.py` 1206–1218）不带 `request_id`，默认消费最旧一条；参数含 `all` 时全部同意。斜杠门禁默认关闭：未配置 `allow_admin_from` 时每个人都是 admin（`slash_access.py` 7–8、39–41）。`/approve` 不在普通用户地板 `{help, whoami}` 里，但门禁未启用时这个地板不起作用。

Telegram 按钮（`plugins/platforms/telegram/adapter.py` 4746–4768）先做回调授权，再按 `approval_id` 取出 `session_key`，然后 `resolve_gateway_approval(session_key, choice)`，不传 `request_id`。卡片上的命令和实际放行的队列项可以不是同一条。Discord 按钮路径传了 `request_id`，两条表面不一致。同样不传 `request_id` 的调用还有 Feishu、QQ、Teams、Matrix、WhatsApp Cloud、Slack、relay 适配器中对应的 `resolve_gateway_approval(session_key, choice)`。

### 插件钩子与内部通知

`hermes_cli/plugins.py` `_dispatch_pre_tool_call_hooks`（2019–2027）在执行前改参数；终端审批看到的是修改后的参数。`modify` 不能在同意之后再改命令并沿用已收集的同意：桌面批次若参数变化会拒绝启动（`terminal_approval_batch.py` 268–276）。钩子分发本身失败时，普通 pre-hook 不阻断（`tool_executor.py` 661–675）；已经要求人审的 `approve` 在门异常时拒绝（`plugins.py` 2010–2015）。

内部唤醒 `run_internal_session_turn`（`api_server_runs.py` 680–736）不带 HTTP、不带 API key，也不注册 approval notify。它绑定 `platform=api_server`（`api_server.py` 3620–3623）。该平台属于无人值守集合，危险命令走 `approvals.unattended_mode`，默认 deny。因此“内部通知回合”在默认配置下不是自动授权。`notification_category` 只影响展示，不写入审批结果。

续跑通知是本次脏 diff 新增的。`_set_run_status`（376–377）在 run 进入终态时调用 `note_run_settled`。决策器在 `gates.approval` 时拒绝续跑（`clawd_continuation_decision.py` 328–329）。但探针 `approval_pending`（`clawd_continuation_gates.py` 34–44）用会话 id 查 `get_pending_gateway_approval`，而 API run 的队列键是 `run_id`（`api_server_runs.py` 631–633）。权威路径的 `waiting_for_approval` 写在 GET 投影（`api_server_authority_runs.py` 93–94），`session_api*.py` 中没有把它写入 `_run_statuses`。队列键和状态写入对不上。未运行，不能把这说成“待决命令已被执行”；能确定的是探针可能看不见仍挂在 `run_id` 上的审批。即便漏看，新回合仍是 `api_server`，默认仍 deny 危险命令，不会把未答的选择写成同意。

子代理：`check_execute_code_guard` 的本地观众整脚本跳过（1238–1300）明确排除 `is_delegated_child_process_context()`。会话 YOLO 按 session key 判断；子进程是否共用父 key，本次未沿记忆实现追查，标需验证。

## 问题

### 高 — 不带 request_id 的同意会放行另一条待决命令

- 位置：`plugins/platforms/telegram/adapter.py:4768`；`gateway/slash_commands.py:1218`；`tools/approval.py:165–166`。同类不传 `request_id` 的还有 Feishu / QQ / Teams / Matrix / WhatsApp Cloud / Slack / relay 的对应调用。
- 描述：按钮或 `/approve` 只标识会话。队列按 FIFO 弹出最旧项。用户点的是卡片 B，执行的可以是仍挂起的命令 A。
- 触发：同一会话同时有两条未合并的待决审批（命令文本或 pattern key 不同就不会合并）。对较新的卡片点 once/deny，或在群里发送 `/approve`。斜杠门禁未配置时，任何能发命令的会话成员都能触发。
- 修复建议（仅报告，未改源码）：

```python
count = resolve_gateway_approval(session_key, choice, request_id=request_id)
if count != 1:
    return already_resolved
```

按钮 payload 必须带创建时的 `request_id`。`/approve` 无 id 时只在队列长度为 1 时允许；多条时拒绝并列出 id。

- 置信度：确定。未运行。

### 高 — 任意非 deny 字符串都被当成同意，且可一次同意全部

- 位置：`tui_gateway/methods_prompt.py:1352–1354`；`tui_gateway/server.py:729–731`；`tools/approval.py:915–919`；schema `tui_gateway/contracts/prompt_voice.py:332–338`。
- 描述：缺省 choice 是 deny，但传入 `"yes"`、`"allow"`、`"foo"` 或空串以外的任意非 deny 值都会 `grant`。`all: true` 用同一个 choice 清空该会话全部待决项，包括这张卡片没有展示的命令。规范 schema 要求 `execution_generation`，这条活处理器不读它。
- 触发：已连接的 TUI/桌面客户端对 `approval.respond` 或服务端 `approval` 请求回包发送 `{choice: "yes"}` 或 `{choice: "once", all: true}`，且该会话有待决项。
- 修复建议：

```python
allowed = {"once", "session", "always", "deny"}
choice = params.get("choice")
if choice not in allowed or (params.get("all") and not request_id_is_absent_policy):
    raise invalid_params
if not request_id:
    raise invalid_params
resolve_gateway_approval(session_key, choice, request_id=request_id)
```

`all` 不应由回答单张卡片的回包携带。choice 必须再与该条目发布时的 `allow_session` / `allow_permanent` 对齐。

- 置信度：确定（授予逻辑和处理器都不校验）。连接是否另有外层身份门，见下一则，标需验证；即使有外层门，错误 choice 仍会放行。

### 中 — TUI 审批响应不绑定调用者、来源和 execution_generation

- 位置：`tui_gateway/methods_prompt.py:1340–1354` 与 `1527–1545`；`tui_gateway/session_registry.py:304–325`。
- 描述：处理器只要求 `session_id` 仍在本进程内存中。查找失败时，用 `request_id` 扫描所有活会话并对其队列执行 resolve。没有来源主体，没有 generation。对比权威门 `session_authority.py:1053–1067`，这条门弱一截。
- 触发：调用方能向 TUI 网关发送 `approval.respond`，并给出另一个仍在内存中的 `session_id`，或一个从事件里得到的 `request_id`。`request_id` 是 uuid4，不能靠猜测。
- 修复建议：响应必须带 `execution_generation`，并经 `check_approval_generation`；调用连接必须是该会话持有 `session:approve` 的订阅者。禁止跨会话扫描，除非扫描结果再做同一次身份核对。
- 置信度：处理器缺少这些字段是确定的。是否存在未读到的外层连接隔离，需验证。未运行。

### 中 — 并行段里，后一个有副作用的工具可以在前一个写审批返回前启动

- 位置：`agent/tool_executor.py:741–748`；`agent/tool_dispatch_helpers.py:212–256`；`tools/file_tools.py:873–883`。
- 描述：启动顺序在 `execute` 之前推进。SSH 配置等写审批在 `write_file_tool` 内部。规划器把不重叠的 `write_file` 与 `web_search` 等并行安全工具放进同一段。前一个调用推进顺序后，后一个开始执行，此时前一个仍可能停在人审。被门挡住的那次写本身不会在同意前落盘；旁边的副作用会。
- 触发：同一条助手消息里先调用需要写审批的路径（如 SSH config），再调用路径不重叠的 `write_file` / `patch`，或一个 `_PARALLEL_SAFE_TOOLS` 工具。
- 修复建议：把写审批算进授权门，在 `_advance_start_order` 之前完成；未返回同意前不得推进顺序。或让需要人审的工具返回 `None` admission，强制顺序屏障。
- 置信度：控制流确定。未运行，故不声称某次具体写入已经发生。

### 中 — 未归类的非交互上下文默认自动同意

- 位置：`tools/approval.py:1211–1216` 与 `1060–1062`。
- 描述：没有 CLI / 网关 / ask 标记，且不是 cron、`-q`、webhook、msgraph_webhook、api_server 时，危险命令打一条警告后 `_approved()`。这是不安全默认。已核对的内部唤醒会绑定 `api_server`，因此不走这条，而走默认 deny。
- 触发：某个执行线程调用 `check_all_command_guards`，且未设置 `HERMES_GATEWAY_SESSION`、`HERMES_SESSION_PLATFORM`、`HERMES_EXEC_ASK`，也没有上述无人值守标记。本次没有把这条落到一个具体的内部通知调用者。
- 修复建议：此分支改为 `_blocked(...)`，与 `request_tool_approval` 的 `fail_closed_when_no_human` 一致。需要自动同意的调用点必须显式传入已记录的策略，而不是靠“什么标记都没有”。
- 置信度：代码默认确定；具体内部调用者需验证。

### 低 — 超时与出队之间的选择仍会执行

- 位置：`tools/approval_gateway_wait.py:248–261`。
- 描述：截止时间已过，但选择在 `_drop_entry` 持锁读到 `entry.result` 之前写入，则按同意执行。条目删除之后的重复 POST 不会执行。
- 触发：审批超时时刻与 approve 到达几乎同时。
- 修复建议：出队时若 `state == "timeout"` 且选择的到达时间晚于已发布的 deadline，丢弃该选择并返回未决。
- 置信度：窗口在源码中确定；可利用性需验证。

### 低 — 取消后已经启动的工具可能在 3 秒宽限内完成并被采用

- 位置：`agent/tool_executor.py:952–958`。
- 描述：中断后若 worker 在 3 秒内结束且 future 未取消，结果照常提交。这不是“取消后才开始”，而是已开始的命令没被停住。
- 触发：用户在 shell 已经启动后取消，命令在宽限内退出。
- 修复建议：中断后只接受带取消标记的结果；已启动进程须先确认杀树，再决定是否采用输出。
- 置信度：代码路径确定。未运行。

## 已核对、不构成问题

- 权威 API 门核对 run 归属、`approve` 权限、精确 `request_id`、整数 generation、提示允许的 choice。内部 Principal 不代替这次 HTTP 同意。
- 等待中的 interrupt 强制 deny，不会在同意已写入后仍执行尚未启动的命令。
- 同一 `request_id` 的第二次 resolve 在条目已弹出后返回 0。
- 核心遗留别名和名字修复不能把未知名字换成校验集合之外的工具。`tool_call` 展开后仍进底层处理器。
- 桌面终端批次在同意前不启动 shell；批次关闭会撤回仍在队列中的请求。
- `ack_gateway_approval` 和内部通知类别都不是同意。
- 内部唤醒绑定 `api_server`，默认 `unattended_mode` 为 deny。
- 委托子进程被排除在本地观众的整脚本 `execute_code` 跳过之外。
- 插件 `modify` 发生在终端审批之前；审批后参数变化会拒绝启动。

## 疑点（不升格为问题）

- 续跑探针用会话 id，API 审批队列键是 `run_id`。权威路径的 `waiting_for_approval` 未见写入 `_run_statuses`。可能漏看仍挂起的审批并另开一回合；该回合默认仍 deny 危险命令。需在权威回合上复核，不能当成待决命令已被执行。
- 旧 API 审批分支是否仍可达，取决于运行中的 `gateway_runner.session_authority` 是否为空。本次未探测进程。
- 子代理是否共用父会话的 YOLO session key，未沿记忆实现追查。
- TUI RPC 外层是否还有连接级身份门，未逐个读完分发器。处理器自身没有 generation / 主体检查，这一点不依赖该疑点。

## 未运行

未运行测试、构建、导入探针或产品工具。以上均为静态控制流。脏文件在写入本报告时的 blob 已列于文首；若并行修改再动 `api_server_runs.py`、`methods_prompt.py`、`server.py` 的审批行，须按新 blob 复核，不能把本报告的行号当成已复现。

## 按严重程度

- 高：无 `request_id` 的 FIFO 同意；任意非 deny choice / `all` 放行。
- 中：TUI 响应缺少身份与 generation；并行段旁路副作用先于写审批；未归类非交互上下文默认自动同意。
- 低：超时边界竞态；取消后 3 秒宽限内完成的结果被采用。

未来测试应在真实执行器上进行，而不是只调 `invoke_tool`：两条待决审批下点新卡片；TUI 发送 `choice=yes` 与 `all=true`；同一消息中 SSH 写审批与不重叠写并行；取消发生在同意之后、进程启动之前与启动之后；权威 API 用过期 generation 和重复 `request_id`。内部唤醒只断言默认无人值守 deny，不能把“通知已发出”写成已授权。
