# 核心后台调度只读审查

审查对象是子代理完成消费、API 会话自动续跑、重启恢复。只读工作树，未改产品，未跑测试、构建、探针、调度，未读生产库或进程，未提交。

基线提交 `c314285f077e91937c995d419b9fa40af9eb4981` 等于当前 `HEAD`。续跑模块是未跟踪新文件。`tools/async_delegation.py` 相对 HEAD 干净。`gateway/wake.py`、`gateway/platforms/api_server_runs.py`、`gateway/platforms/api_server_run_idempotency.py` 有未提交改动，下文按工作树而不是旧快照。`api_server_runs.py` 审查期间仍在被别的路修改；停止路径与续跑挂钩在写本文前又读过当前行。

本审查代理的系统标识是 grok-4.7。`docs/clawd/continuation-verification.md` 记载隔离探针广告模型是 `hermes-agent`，执行器输出固定 `test-executor-not-llm`。那次结果不能当成 grok-4.7 回合。本次没有复跑，也不用 `test-not-llm` 或普通 user self-POST 证明真实 LLM 完成。

## 800 字摘要

完成通知由 gateway 独占认领后写入 `async_delegation_complete`，不 self-POST。API connect 后才 arm 续跑。绑定面板不启动旧 sidecar 线程。去重靠 continuation journal 与 `runs_idempotency`。完成单元是 `delegation_id`，不是 child；interim 不触发续跑。计数和清单图标不在本文。

高：回执在 `create_task` 返回时写入，早于模型结束。此后崩溃，通知已消费；无 authority 时不会重跑。租约等待失败仍被标成 completed。journal 租约 30 秒且不认死进程，claim 失败不排重试，recover 只跑一轮，会遗失唤醒。`_reusable` 只 `kill(pid, 0)`，忽略 `owner_started`，PID 复用会把死 run 当成存活并写回执。停止口打不中续跑：无 authority 时不 cancel task，有 authority 时 request_id 是新 uuid，对不上代次。已发射的续跑不再复查更新的人话。

中：busy/draining 五次后永久回执。计划探针缺失默认放行。外持有者探针未接线。预检租约没用压缩谱系根。只看最新 300 条消息。审批队列按 run id，续跑按 session id 探测；已写入 waiting_for_approval 的 run 仍能挡住，空窗需验证。面板直启没有 FIFO。

未跑测试、探针、生产库和真实模型回合。`test-not-llm` 不能证明 LLM 完成。既有文档里的探针模型不是 grok-4.7，本次未复跑。已确认与疑点在正文分开；压缩旋转和双 admission 执行只标需验证。

## 1. 调度结构与持久化时序

### 调用链

1. 子代理单元在 `tools/async_delegation.py` 落一行 `async_delegations`。批的 child 先写进同一行的 `result_json`（`record_unit_child`），单元结束才有一条最终事件。interim 带 `task_failure_notice`，不得认领最终行。
2. 事件进入 `process_registry.completion_queue`。gateway `_async_delegation_watcher` 与 TUI poller 都可能取走内存副本。跨进程只允许一个 `claim_completion_delivery` 成功。
3. API 且 `origin_session_id` 存在时，每个单元单独投递，不合并成一个模型回合。`_self_post_api_server` 对 `async_delegation` 只调用 `persist_delegation_delivery`，不调用 `deliver_wake`。
4. `append_delegation_delivery` 按谱系里的 `delegation_id + delivery_notice` 去重，并拒绝活动 turn lease。成功后 `note_persisted_delivery` 只调度评估。
5. `ContinuationService.evaluate` 读最近消息、journal、闸门，然后 `freeze` → `claim` → 再决策 → `admit_continuation`。
6. `admit_continuation` 在 durable `runs_idempotency` 上 `reserve`，写入固定续跑句，再 `create_task` 调 `_run_agent` 或带 `test-not-llm` 标记的钩子。无标记钩子直接拒绝，不会落到 self-POST。
7. `admit` 返回后 journal `add_receipts`，并删掉该会话的 request 行。
8. run 进入终态时 `_set_run_status` 调 `note_run_settled`，再评估一次。
9. 进程启动时 `recover` 扫 pending/leased request、2 小时 watch、以及 2 小时内的完成通知。没有周期扫描。

面板绑定路径：`clawd_server/server.py` 的 `_start_sidecar` 只 `serve_forever`，不调用 sidecar `main`。`bind()` 另外把 `_continue_watcher` / `_continue_tick` 换成会抛错的函数。因此绑定入口不会和核心续跑双调度。直接执行 sidecar `__main__` 仍会起旧线程，见低危残留。

两条运行入口不一样：

- 完整 gateway 会 `initialize_session_authority`。`_run_agent` 看见 `gateway_runner.session_authority` 后走 `run_api_turn` → `admit_session_input`，FIFO 在已有 `started`/`unknown` admission 时不领取下一条。
- `clawd_server/server.py` 直接 `APIServerAdapter.connect()`，不设置 `gateway_runner`。`getattr(None, "session_authority", None)` 为 None，续跑走进程内 `_run_agent` → `AIAgent.run_conversation`。真正的会话租约在 `agent/turn_facade.py` 的 `admit_durable_turn_lease`，键是压缩谱系根，不是续跑预检用的原始 id。

### 持久化时序

| 时刻 | 已持久的事实 | 还没有的事实 |
| --- | --- | --- |
| 派发 | `async_delegations` running，含 owner pid / start time | 完成事件 |
| 单元完成 | `event_json`，`delivery_state=pending` | 会话里的通知行 |
| claim | `delivery_claim`，attempts+1 | 通知行 |
| persist | 谱系上的 delivery 行 | 续跑 receipt |
| freeze/claim | journal request + 30 秒 lease | 模型回合 |
| reserve | `runs_idempotency` 行，owner pid/start | 续跑 receipt |
| create_task 返回 | journal receipt，request 行被删 | 模型是否跑完 |
| 回合终态 | run status；若走 authority，还有 admission generation | 无第二套 receipt |

完成身份是 `clawd-continue-{session_id}-{delegation_id}`。没有稳定 id 时退到消息行 id。同一单元的重复通知共享身份。批的多个 child 不各发一次续跑。

人话、插话（`display_kind=steer`）和最后一条助手回复构成屏障。完成通知必须排在屏障之后，且落在 2 小时窗口内。续跑行和带 `display_kind` 的非 steer 行不是人话。派发之后已经答完的人话不否决更晚的完成通知。

取消、连续 5 次续跑、5 次尝试耗尽会写 receipt，之后不再触发。审批、clarify、计划、provider 不明、闸门不明只写 blocked 事件，不写 receipt，也不排重试。忙租约走 defer，不增加 attempts。`admit` 返回的 `busy`/`draining` 会增加 attempts。

## 2. 逐文件

- `gateway/clawd_continuation.py`：服务生命周期、evaluate/apply、recover。问题在 claim 失败不重试、回执早于回合、异常变一次性 block。
- `gateway/clawd_continuation_journal.py`：BEGIN IMMEDIATE 的 receipt/request/lease。claim 不看 pid 死活；`pending_sessions` 用 `next_attempt_at <= now` 把仍有效的租约也列进来，但调用方不会因此排定时器。
- `gateway/clawd_continuation_decision.py`：纯决策。屏障和 interim 排除是对的。`cancelled` 用“该会话最新 run 状态”，不是“这次完成所属的那次 run”。这符合“取消必须挡住续跑”，不单列缺陷。已启动后不再回来问决策，见高危。
- `gateway/clawd_continuation_gates.py`：审批/clarify/计划/turn lease/幂等库最新状态。计划探针缺失时放行。租约查询没用谱系根。
- `gateway/clawd_continuation_admit.py`：幂等 reserve 与发射。`_reusable` 的 pid 检查不比 start time。发射不登记 agent，不传 admission request id。终态不看 `result["completed"]`。
- `gateway/wake.py`：persist 不 self-POST。通知续跑的异常被吞掉，投递仍算成功。
- `gateway/run_notifications.py`：API 完成走 persist；claim 失败不把兄弟单元写进同一段文本。这是独占消费，不是双唤醒。
- `tools/async_delegation.py`：单元账本、claim、orphan sweep、重启重放。相对 HEAD 未改。计数不在本文。`claim_completion_delivery` 在行不存在时返回 True，只影响没有账本行的旧事件。
- `gateway/platforms/api_server_run_idempotency.py`：reserve 在 BEGIN IMMEDIATE 内，同键不会双插入。新增 `discover_for_session` 明确“死 owner 不改 status”。续跑的 `_reusable` 没有用这个口径，也没有用 `owner_started`。
- `gateway/platforms/api_server_runs.py`：arm/disarm、终态后再评估、停止口。停止口不取消续跑 task。
- `gateway/platforms/api_server_authority_runs.py`：stop/approval 要求 admission 的 `execution_generation`。续跑 run id 不是这条 admission 的 request id。
- `gateway/session_api_turn.py`：未传 `request_id`/`active_run_id` 时每次 `uuid.uuid4().hex`。与续跑幂等键不是同一个身份。
- `hermes_state_detached_results.py`：delivery 去重与活动租约拒绝。查询用谱系根，续跑预检没有用同一把钥匙。
- `hermes_state_compression.py` / `agent/turn_facade.py`：真正 turn lease 的键和等待失败的返回值。续跑预检和终态判断都没接上。
- `clawd_server/bind.py` / `server.py`：绑定入口禁用旧 watcher。`continuation_status()` 只表示模块可导入，不是已启动。
- `apps/panel/sidecar/hermes_panel_sidecar.py`：旧调度仍在 `main`。绑定路径不调用它。

## 3. 已确认问题

### 高：回执写在发射时，崩溃或租约失败后通知被吃掉

文件：`gateway/clawd_continuation.py:317-331`，`gateway/clawd_continuation_admit.py:91-133`。

`record_attempt` 之后 `await admit`。`admit` 在 `create_task` 后立刻返回，不等 `_run_agent`。返回后 `add_receipts` 删除 request。receipt 的含义是“已经排队”，不是“回合成功”。

触发：

- 回执写入后进程退出。任务随进程死掉。下次 evaluate 看见 receipt，`open_notices` 被跳过。authority 路径若 admission 已提交，`recover_api_turns` 可能仍跑那一条；没有 authority 时没有任何账本会重跑这个回合。
- `run_conversation` 抢不到谱系租约时返回 `completed: False`（`agent/turn_facade.py:102-111`，`agent/turn_facade_lease.py:445-482`）。`_go` 不看这个字段，把 run 标成 `completed`（`clawd_continuation_admit.py:122-126`）。回执已经在。

影响：遗失唤醒，而且表面像已经续跑过。不是双唤醒。

置信度：确定。这是当前源码的顺序，不依赖未跑的交错。

修复建议（只报告，不改产品）：

```python
# admit 只返回 run_id；回执改到回合终态且 result 明确成功之后
if not isinstance(result, dict) or result.get("completed") is False or result.get("interrupted"):
    adapter._set_run_status(run_id, "failed", error="continuation_not_completed")
    raise ContinuationAdmitError("continuation_not_completed")
# _apply 在 admit 成功且上述检查通过后再 add_receipts
```

崩溃恢复应把 continuation `request_key` 当作 admission `request_id` 存下来。已有 queued admission 时只等待它，不要再 `create_task`。

### 高：死租约持有者不让出，recover 不排下一次

文件：`gateway/clawd_continuation.py:174`、`310-311`、`234-251`、`317-318`；`gateway/clawd_continuation_journal.py:171-199`、`243-248`。

holder 是 `clawd:{pid}:{id(self)}`。`claim` 只比较字符串和 `lease_expires_at`，不看 pid 是否仍是原来的进程。租约 30 秒，回合期间不续期。`record_attempt` 把 `next_attempt_at` 设成现在，于是 `pending_sessions` 会列出仍被别人持有的行。`_apply` 在 `claim` 失败时直接 `return`，不 `call_later`。`recover` 只在 `start` 时跑一轮。watch 表没有周期读者。

触发：adapter 重启或进程在租约未过期时挂掉，且完成通知已经持久化。这一轮 recover 抢不到租约，之后没有定时器。租约过期后也不会自己再评估，除非又来一条通知、一次 run 终态，或再重启一次。

影响：遗失唤醒，最长可以一直丢到 2 小时窗口之外。不是死锁：没有两把锁互相等待。是丢唤醒。

置信度：确定。

修复建议：

```python
def _holder_alive(holder: str, now: float) -> bool:
    pid_s, started_s = holder.split(":")[1], None
    pid = int(pid_s)
    if not _pid_exists(pid):
        return False
    # 必须比对进程 start time，不能只 kill(pid, 0)
    return start_time_matches(pid, started_s)

# claim 失败且对方已死：立刻抢租约
# 对方仍活：schedule(lease_expires_at - now)，不要 return 后不管
```

`stop`/`drain` 取消任务前应释放本 holder 的 lease。

### 高：死 owner 的 PID 被复用后，旧 run 被当成仍在跑并写回执

文件：`gateway/clawd_continuation_admit.py:45-52`、`136-146`、`166-167`、`184-185`。对照 `gateway/platforms/api_server_run_idempotency.py:184-206`（reserve 存了 `owner_started`）和 `tools/async_delegation.py:277-288`（账本恢复会比 start time）。

`_pid_alive` 对别的 pid 只 `os.kill(pid, 0)`。`_reusable` 在状态非终态、本进程没有未完成 task、且 `owner != self` 时，只要 pid 存在就当作可复用，然后返回旧 `run_id` 且不再 `_launch`。调用方接着写 receipt。非终态行不会被 `_prune_stale_terminal_locked` 删掉。

触发：续跑 run 停在 `queued`/`running`，原进程已死，内核把同一 pid 分给无关进程，之后评估走到 reused 分支。重启后低 pid 很快被复用，这是本仓库 turn lease 已经单独处理的类别。

影响：旧代次被当成活的，新回合不起，回执阻止再试。不是旧代次重新执行，是旧代次挡住恢复。

置信度：确定（缺 start time 比对）。PID 复用本身是触发条件，不是本次观察到的线上进程。

修复建议：

```python
def _reusable(adapter, record) -> bool:
    status = (record.get("status") or {}).get("status")
    if status in _TERMINAL:
        return True
    owner = int(record.get("owner_pid") or 0)
    started = int(record.get("owner_started") or 0)
    if owner == os.getpid():
        task = (getattr(adapter, "_active_run_tasks", {}) or {}).get(record.get("run_id"))
        return task is not None and not task.done()
    return _same_process(owner, started)  # pid 存在且 start time 一致
```

对不上时不要写 receipt，除非确认没有未完成的 admission。

### 高：取消/停止打不中续跑回合，代次也接不上

文件：`gateway/clawd_continuation_admit.py:91-133`；`gateway/platforms/api_server_runs.py:1558-1580`；`gateway/platforms/api_server_authority_runs.py:138-164`；`gateway/session_api_turn.py:194`。

续跑 `_launch` 不传 `active_run_id`，不注册 `_active_run_agents`，不传 `approval_notify_callback`。task 放进 `_active_run_tasks`。

无 authority 时，`_handle_stop_run` 只有 `agent is not None` 才 `request_hard_interrupt`。agent 为空、task 不为空时只把状态改成 `stopping` 并返回，不 `task.cancel()`。

有 authority 时，停止先走 `stop_run`，用 run id 当 admission `request_id`。续跑没有把 `spec["request_key"]` 传进去，`admit_api_steps` 用新的 `uuid4().hex`。查找失败是 `not_found`，不会落到下面的 task 取消。审批口同样要求 `execution_generation` 与 admission 行一致。续跑 run 没有这条绑定，用户不能用该 run id 的代次批准或停止它。

决策只把 `latest_run_status == "cancelled"` 当取消（`clawd_continuation_decision.py:326-327`）。`stopping` 不是闸门。停止进行中仍可能 admit 另一个续跑。

触发：用户对续跑 run id，或对仍在跑的父 run，调用 `POST /v1/runs/{id}/stop`。面板直启与完整 gateway 两条都会失败，失败方式不同。

影响：取消打不中内部续跑。authority 路径上，崩溃后若旧 admission 仍 queued，而续跑因不可复用再次 `_launch`，会再插入一个新 request id。那是双唤醒的代码条件；本次没有运行交错，双执行标为需验证，身份分裂本身确定。

置信度：停止打不中为确定。双 admission 执行为需验证。

修复建议：

```python
kwargs["request_id"] = spec["request_key"]
kwargs["active_run_id"] = run_id
# _launch 必须把 agent 放进 _active_run_agents[run_id]
# stop：agent 为空时取消 _active_run_tasks[run_id]，并 interrupt 该 task 里的 agent
# authority stop 找不到 admission 时不得假装已停止
```

### 高：已发射的续跑不再接受更新的人话

文件：`gateway/clawd_continuation.py:312-320`；`gateway/clawd_continuation_decision.py:186-201`。

`claim` 之后同步再决策一次，然后 `await admit`。authority 路径的 `admit_api_turn_async` 会让出事件循环。无 authority 路径在 `create_task` 之后，`_go` 才进入 `_run_agent`。两条路径在发射后都不再调用 `decide`。journal 回执也不因新人话撤销。

触发：评估已决定 admit 之后，用户消息插入，且该消息的 id 晚于完成通知。已排队的 `_go` 仍会跑。谱系租约会让它排在当前回合后面，但不会取消它。人话回合结束后，续跑回合仍执行。

影响：更新的人话不能阻止已经发射的内部续跑。这和“更新的人话不得被追上”相反。屏障只保护尚未发射的评估。

置信度：确定（发射后无第二道决策）。人话是否总能在 `await` 窗口插入，取决于该入口是否在同一事件循环；面板与 gateway 的 HTTP 处理都在该循环上，窗口存在。

修复建议：

```python
async def _go():
    again = service._decide(session_id, db, profile)
    if again.action != "admit":
        raise ContinuationAdmitError(again.reason)
    # 然后再 _run_agent；失败则 release，不写 receipt
```

### 中：并发拒绝会把尝试次数用完并永久回执

文件：`gateway/clawd_continuation.py:317-329`；`gateway/clawd_continuation_decision.py:19`、`336-337`、`297-298`。

真正的会话租约忙走 `defer`，不增加 attempts。`admit_continuation` 的 `busy`/`draining` 在 `record_attempt` 之后发生，每次加一。5 次后 `attempts_exhausted` 写 receipt。

触发：网关并发顶满或处于 draining，续跑在退避合计约 77 秒内连续 5 次被拒。

影响：完成通知被当成耗尽，之后不再续跑。遗失唤醒。

置信度：确定。

修复建议：`busy`/`draining` 不要增加 `attempts`，只 `schedule`。只有模型回合真正失败才计次。不要对这类原因 `add_receipts`。

### 中：计划闸门默认放行；外进程持有者探针是死代码

文件：`gateway/clawd_continuation_gates.py:47-51`；`gateway/clawd_continuation.py:375-378`。

`clawd_plan_approval_pending` 不存在时 `plan_pending` 返回 `False`，不是 `None`，所以 `unknown` 不会因此为真。全仓库没有给 adapter 赋这个属性。`transcript_plan_pending` 只看本次载入的消息。计划若只在侧表，或落在最新 300 条之外，决策会放行。

`clawd_foreign_holder` 同样没有接线。探针不存在时返回 `False`，决策把它当成“没有外进程持有者”，而不是“无法确认”。

触发：计划审批只存在于 adapter 未实现的探针或 300 条窗口外；两个进程共享同一 home，续跑预检不询问进程登记。

影响：内部续跑可能在计划待批时启动。外进程持有者检查不能当安全门。真正回合仍会在 `run_conversation` 抢谱系租约，所以这不一定是双写；它是失败开的预检。

置信度：默认值确定。是否因此双写，需验证，因为后面还有 turn facade 租约。

修复建议：

```python
def plan_pending(session_id, adapter):
    probe = getattr(adapter, "clawd_plan_approval_pending", None)
    if not callable(probe):
        return None  # collect_gates 会把 unknown 变成 block
def _foreign(adapter, session_id):
    probe = getattr(adapter, "clawd_foreign_holder", None)
    if not callable(probe):
        return None  # decide 已把 None 当成 ownership_unconfirmed
```

### 中：预检租约键不是谱系根；消息窗只有 300 条

文件：`gateway/clawd_continuation_gates.py:68-81`；`hermes_state_compression.py:694-718`；`gateway/clawd_continuation.py:349-351`；`hermes_state_messages.py:1373-1419`。

`turn_lease_expires` 用 `conversation_id = session_id`。写入方把租约放在压缩谱系根。子会话上看不见父行，预检认为不忙。`get_messages(..., latest=True, limit=300)` 不带 `include_ancestors`。完成通知若不在这 300 条里，`open_notices` 为空，返回 `no_trigger`，不写 receipt，下次仍看不见。

触发：压缩子会话仍持有根租约；或父回合在 defer 期间写出超过 300 条更新的消息。

影响：预检漏忙会发射续跑，然后在真正租约上等待；等待失败就落到前面的高危回执问题。300 条窗口是遗失唤醒。压缩旋转若发生在 persist 成功之后、evaluate 读行之前，通知留在已结束父会话，本次没有把克隆规则读完，这条标需验证。

置信度：键不一致和 300 条限制确定。压缩克隆是否搬走通知，需验证。

修复建议：预检调用 `_session_turn_lease_key_on_conn`。触发扫描用 `display_kind = 'async_delegation_complete'` 的索引查询，不要靠最新 300 条。

### 中：审批探测键与登记键不一致

文件：`gateway/clawd_continuation_gates.py:34-44`；`gateway/platforms/api_server_runs.py:892`、`1059`；`gateway/platforms/api_server.py:3253-3261`。

`get_pending_gateway_approval(session_id)` / `get_pending_for_session(session_id)` 用原始 session id。API 审批队列的键是 run id，注释写明不能用共享 session key。已把 `_run_statuses` 写成 `waiting_for_approval` 的 run 仍会被 `collect_gates` 看见。authority 的 pending controls 不在 `_run_statuses` 里；那条路径靠 FIFO 挡住后来的 admission。面板直启没有这条 FIFO。

触发：审批通知尚未写入 `_run_statuses`，或 clarify 登在 route/run id 上，同时面板路径没有 authority。

影响：可能在等人的空窗里 admit。空窗有多宽，本次没有运行时序，标需验证。已写入 `waiting_for_approval` 的进程内 run 不会被这个探测键问题单独放过。

置信度：键不一致确定。绕过是否发生，需验证。

修复建议：闸门同时读 authority `live.controls` 和本会话全部非终态 run 的 approval key，任一不明则 `unknown=True`。

### 低：失败被吞掉后没有补评估；事件表无界

文件：`gateway/wake.py:231-235`；`gateway/clawd_continuation.py:261-264`、`388-396`；`gateway/clawd_continuation_journal.py:89-100`；`gateway/clawd_continuation.py:334-346`。

persist 成功后，续跑通知异常只记日志，调用方仍会 complete claim。evaluate 的宽 `except` 一律 `ownership_unconfirmed`，不排重试。通知扫描失败返回空列表，这一轮 recover 会漏掉只存在于消息表的会话。defer 每次插入 events，没有清理。同一会话可以叠多个 timer。

触发：续跑模块导入失败，或评估时数据库短暂不可读，且之后没有新通知。长期 busy 的会话会一直写 events。

影响：一次失败变成遗失唤醒，直到重启或新事件。事件表增长不是正确性缺陷。

置信度：吞掉异常确定。是否经常触发，需验证。

修复建议：通知失败应让 delivery claim 释放或至少 `journal.watch` 成功后排一次 `recover`。events 按会话只留最后一条。

### 低：没有账本行的完成事件，每个消费者都能“认领”

文件：`tools/async_delegation.py:582-597`。

`delegation_id` 查不到行时 `claim_completion_delivery` 返回 True。现行派发会先 `_persist_dispatch`。只有账本行被剪掉、或旧事件没有行时，gateway 和 TUI 可以同时认为自己认领成功。

触发：pending 行超过 `_MAX_DURABLE_PENDING`（1000）被删，内存队列里仍有事件。

影响：可能双投递。不是当前单元路径的常态。

置信度：代码确定。要剪到这个上限才触发，需验证是否会在续跑窗口内发生。

修复建议：行不存在时返回 False，或单独的不可重放标记，不要返回 True。

### 低：直接运行 sidecar 脚本仍会启动旧调度

文件：`apps/panel/sidecar/hermes_panel_sidecar.py:5851-5858`；对照 `clawd_server/server.py:215-223` 与 `clawd_server/bind.py:130-145`。

绑定服务器不调用 `main`，所以当前产品入口不是双调度。`python hermes_panel_sidecar.py` 仍会起 `auto-continue` 线程，对同一 `HERMES_HOME` 发 `POST /v1/runs`。那是另一套 receipt（`panel_auto_continue.db`），挡不住核心 journal。

触发：有人绕过 `clawd_server` 直接跑脚本，且 key 文件可读。

影响：双唤醒。绑定路径本身未见这个线程。

置信度：两条入口的代码确定。线上是否还有人直接跑脚本，需验证。本次未查进程。

修复建议：脚本 `main` 在导入时若发现核心续跑模块，拒绝启动线程，而不是只在 `bind()` 里替换函数对象。

## 4. 疑点

下列没有足够的代码时序证据，不升格为缺陷。

- 压缩若在 delivery 行写入后、evaluate 读行前把会话旋转走，通知会留在已结束父行。`append_delegation_delivery` 在写入时会拒绝已关闭的压缩父会话，所以只剩极短窗口。是否克隆该行，本次未读完压缩实现。需验证。
- TUI `_session_owns_notification_event` 与 API claim 是否可能同时认为自己拥有同一 API 会话。durable claim 对有行的事件是独占的；没有读到第二套成功路径。需验证。
- authority 路径在 `admit_session_input` 提交后、journal receipt 前崩溃，随后续跑因不可复用再次 `_launch`。两个 request id 都可能被 `recover_api_turns` 和第二次发射执行。身份分裂已确认，双执行未跑。需验证。
- journal `release` 不用 `BEGIN IMMEDIATE`。与 `claim` 的写锁是否丢更新，没有稳定交错证据。需验证。
- `evaluate` 的同一 holder 可重入 `claim`。同键的第二次 `reserve` 会得到 reused，asyncio 在同步段不插入另一任务，较小身份集删掉较大 freeze 的窗口在当前事件循环模型里走不到。不列缺陷。

## 5. 已核对、不列缺陷

- `persist_delegation_delivery` 不把完成通知 self-POST 成普通 user。无标记 executor 拒绝，不会改走 self-POST。
- 绑定面板不启动旧 sidecar 线程。`GET /api/sessions/{id}/continuation` 只读 journal，不 admit。
- 同一 `delegation_id` 的重复 delivery 由谱系匹配返回旧行。interim 不参与续跑身份。批的部分失败若写在最终单元事件里，只触发一次续跑，这是单元语义，不是漏 child。
- 同键 `reserve` 在 `BEGIN IMMEDIATE` 内，两个 evaluate 不会为同一 `request_key` 插入两行。
- 人话早于完成通知不会永久压制更晚的通知。`steer` 算人话。
- `tools/async_delegation.py` 的计数、活动页人数、清单图标已有别的 owner。该文件相对 HEAD 未改，本文不派修、不重复实现。
- `discover_for_session` 是运行路正在加的只读发现口。续跑还没有改用它。不算已修好。

## 6. 未覆盖

- 未运行任何测试、构建、探针、续跑或队列消费。不能把源码顺序写成已经在运行时复现。
- 未读生产 `state.db`、`clawd_continuation.db`、`runs_idempotency.db`，未列进程。
- 未用外部模型跑完一个续跑回合。`test-not-llm` 只覆盖调度形状。
- 未覆盖 cron、浏览器登记、活动页刷新。
- 未验证 TUI 与 API 在共享 home 上的真实认领交错。
- grok-4.7 与既有探针广告模型 `hermes-agent` 不符。该结论来自 `docs/clawd/continuation-verification.md`，本次未复跑。

## 7. 按严重程度

高（5，均确定；其中双 admission 执行需验证）：

1. 回执早于回合成功，崩溃或租约失败后遗失唤醒。
2. 死 journal 租约不让出，recover 不排重试。
3. PID 复用把死 run 当成可复用并写回执。
4. 停止/代次打不中续跑回合。
5. 已发射续跑不再服从更新的人话。

中（4）：并发拒绝耗尽尝试；计划/外持有者默认放行；租约键与 300 条窗口；审批键不一致（绕过需验证）。

低（3）：吞异常且不补评估；无账本行的 claim 返回 True；直接跑 sidecar 脚本仍会起旧线程。

不建议为本报告再派实现。运行路已拥有 `api_server_runs.py` 与幂等库的未提交改动，todo 路拥有关联显示。上述续跑缺陷不在那两路已写的 diff 里。

## 8. 审查指纹

工作树 SHA-256，写报告前读取：

- `tools/async_delegation.py` `df13ef142f2812d4453266d00c6b69e1ef50d53ca2bbbe331451f23171b1dce3`（干净）
- `gateway/clawd_continuation.py` `300f059641a218a78bf8be30fe7a1e4e162f44053b3105efe0bfc78341bbe476`
- `gateway/clawd_continuation_admit.py` `57e699c2ce583fa8f1d024442a5da01be3d1524ce906d16e4357132ee0a35282`
- `gateway/clawd_continuation_decision.py` `9bd9fd9d65e78a7f9d9ebd240b63d10b5262c4e1c82557c0110bad53285751e1`
- `gateway/clawd_continuation_gates.py` `6d2fa59a5c89ecf198799fb41e51fd7c3c6e671a0e23d2008ea0324ea1511ae9`
- `gateway/clawd_continuation_journal.py` `c256bb89bf4868424e4c0e845e6f78804f39a4adbe5a2f023bbec7748c94ca8e`
- `gateway/wake.py` `7fe985e4606b769355c942ba137e5d52037cd96d61c1a6072a2e837f782b1a32`（脏）
- `gateway/platforms/api_server_runs.py` `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b`（脏，审查期间变过）
- `gateway/platforms/api_server_run_idempotency.py` `dd15cfdd6670c30729d0e1d1ef816d378c8cebba0fbfe8994f47d2eca334d921`（脏）
- `gateway/platforms/api_server.py` `a6b4ef32a53c5b9110665542307f0ff99bb03dc3c756fe3a6ba03fd49087ba31`
- `gateway/platforms/api_server_authority_runs.py` `41a510fb9354776ba57ac57d02c0fa33ae974b192b5ffb783dc97e67f9676520`
- `gateway/session_api_turn.py` `52780ebd3d64a6107b03fa53f62a1cf7e752fd363096d626aa89f66aaefab948`（`request_id` 仍在 194 行）
- `gateway/run_notifications.py` `31eba2b09a301ef5294a72bc5c12974648a3e9ec21ebefdb400ff3ed97776a87`
- `clawd_server/bind.py` `a0cfe9432fbd526d11850105d040d0d0cf1e85f9ceabada2aa57ff04f20d101b`
- `clawd_server/server.py` `edc8f9bcbc64ca27ba3fb5e3b85f1eb6925c267cd75e65540c39939762051f30`
