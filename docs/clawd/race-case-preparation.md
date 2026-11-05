# 续跑竞态用例准备

本文件只记录静态核对过的用例契约。编写时没有运行 pytest、构建、导入探针或网络请求。文件存在、`continuation=started`、执行器 `completed` 都不是续跑通过，也不是外部模型回合。

主题：审批代次、重复唤醒、取消人工输入、重启恢复。下面每节只引用本目录已有测试实际调用的入口。未收集 pytest nodeid；下列是源码函数名拼出的稳定 id，参数化展开没有跑。

集中验收仍由父代理在实现收齐后统一跑。建议命令，本次未执行：

```text
scripts/run_tests.sh -j 1 tests/clawd_race_cases
```

正式核心阶段只发现一次 `tests`，不要把本目录再单独传一遍。禁止给本目录加 skip/xfail 凑绿。

## 接口依赖

续跑唤醒与准入，签名来自当前源码：

- `gateway.wake.persist_delegation_delivery(adapter, *, text: str, session_id: str, evt: Optional[dict] = None) -> None`。写完交付行后调用 `note_persisted_delivery`。它不 self-POST 普通 user。
- `gateway.clawd_continuation.note_persisted_delivery(adapter, session_id: str, evt: Optional[dict] = None) -> None`。只在已 `arm` 且 `running` 时 `watch` 并 `_spawn(evaluate)`。自己不准入。
- `gateway.clawd_continuation.note_run_settled(adapter, session_id: str) -> None`。同样只调度 `evaluate`。本目录没有单独 nodeid 打这个入口。
- `gateway.clawd_continuation.arm_adapter(adapter) -> None` / `disarm_adapter(adapter) -> None` / `service_for(adapter) -> Optional[ContinuationService]` / `ready_fields(adapter) -> dict`。
- `ContinuationService.evaluate(self, session_id: str, db, *, profile: str) -> Decision`。
- `ContinuationService.recover(self) -> None`。对 pending、watched 和近期通知逐个 `evaluate`。
- `ContinuationJournal.claim(self, session_id: str, holder: str, now: float, ttl: float) -> bool`。活租约且持有者不同则返回 False，不调度下一次。
- `gateway.clawd_continuation_admit.admit_continuation(adapter, spec: dict, *, db, profile: str) -> str`。
- `gateway.clawd_continuation_decision.decide(snapshot, *, session_id: str) -> Decision`。
- `is_human(row) -> bool`。`display_kind=steer` 算人话；完成通知和续跑标记不算。
- `open_notices(rows, *, now, session_id)`。只取最后助手回复和最后人话之后的真实完成通知。

审批与取消/人话代次：

- `gateway.platforms.api_server_authority_runs.respond_run(adapter, run_id, body, *, kind)`。body 必须正好是 `request_id`、`execution_generation` 和 `choice` 或 `answer`。代次不是正在跑的 admission generation 则 `stale_generation`。
- `gateway.session_controls.AuthorityConnection.dispatch(self, request)`。审批用例用 `method=approval.respond`。
- `SessionAuthority.respond(self, actor, ref, generation, prompt_id, response, *, kind="approval")`。
- `SessionAuthority.check_approval_generation(self, session_id, generation)`。`execution_generation` 不符或状态不是 `running` 则 `stale_generation`。
- `SessionAuthority.interrupt(self, actor, ref, generation)`。先 `authorize(..., "session:control")`，再比 `handle.execution_generation`。
- `gateway.session_busy_controls.correct(connection, ref, params, *, verb)`。`session.steer` 走这里。params 只能有 `session_id`、`profile`、`text`、`execution_generation`。锁内调用 `check_approval_generation`，再 `steer_agent`。
- `SessionAuthority.steer_agent(self, ref, generation, text)`。只把文本挂到这个 generation。
- `SessionAuthority.adopt_agent(self, session_id, generation, agent)`。只把挂起的 steer/stop 交给同一 generation。
- `hermes_state_runtime.admit_session_input(...)` / `claim_session_input(db, *, epoch, session_id, ...)` / `begin_runtime_epoch(db, *, instance_id)`。`claim` 把 `sessions.runtime_generation` 加一，并写入 admission `generation`。`SessionHandle.execution_generation` 读的就是这个 `runtime_generation`。

夹具只在测试里替换外部边界：`MarkedExecutor`（固定 `test-executor-not-llm`）、`Clock`、`claim` 屏障。`decide`、账本事务、幂等 `reserve`、`check_approval_generation`、`interrupt`、`correct` 不用假返回值。

## 重复唤醒

已有并发消费者，打的是 `evaluate`，不是唤醒入口：

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_continuation_races.py::test_duplicate_completion_events_admit_at_most_once` | 同一通知、两个连接同时过真实 `claim`，再 `evaluate` 一次 | 执行器一次；再评估 `skip`；无 self-POST |
| `tests/clawd_race_cases/test_continuation_races.py::test_two_consumers_recover_and_evaluate_admit_once` | `recover` 与活 `evaluate` 共享账本 | 执行器一次；幂等 scope 不各开一回合 |

缺口是唤醒入口本身被打两次。`append_delegation_delivery` 按 `delegation_id` 只插一行，但 `persist_delegation_delivery` 每次仍调用 `note_persisted_delivery`。本批补：

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_required_wakeup_human_cases.py::test_duplicate_wakeup_persisted_delivery_admits_once` | `arm_adapter` 后对同一 `deleg_dup_wake` 调用两次真实 `persist_delegation_delivery`，第一次落定后再调用一次 `note_persisted_delivery` | 执行器仍一次；续跑行一条；身份已入 receipt；`leaked` 为空；`ready_fields` 的 `started` 不是模型回合 |

未跑。不能把这一条写成已通过，也不能写成 `bg-receipt` 已修。回执仍可能在 admit 返回后、回合结束前写入。

## 取消人工输入

窗口用例打的是账本 `claim` 返回后、准入前的转录和 run 状态，不带 `execution_generation`：

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_continuation_races.py::test_cancel_and_human_in_the_recheck_window_do_not_admit` | `claim` 成功后写入 `display_kind=steer` 人话，并设 `status=cancelled` | 返回的第一眼决策可以是 `admit`；执行器为零；不再次准入 |
| `tests/clawd_race_cases/test_continuation_races.py::test_cancel_arriving_in_the_recheck_window_blocks` | 同一窗口只写 `cancelled` | 事件 `blocked` / `cancelled`；身份入 receipt；执行器为零 |
| `tests/clawd_race_cases/test_continuation_recovery.py::test_human_then_assistant_then_later_notice_is_recoverable` | 通知前人话则跳过；助手回复后的新通知才准入 | 旧通知不复活；新通知执行器一次 |

这三步不是代次门。代次门是 `interrupt` / `session.steer` 的 `execution_generation`。本批补，仍未跑：

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_required_wakeup_human_cases.py::test_stale_generation_cancel_and_human_steer_do_not_latch` | 已 claim 的 generation 上，用 `generation+1` 调 `interrupt` 和 `correct(..., verb="steer")`；subject 为 `internal` 的 actor 走同一过期代次；params 多带 `source=internal`；另一 profile 用正确代次 | `stale_generation` 或 `invalid_params` 或 `profile_mismatch`；`pending_stops` / `pending_steers` 仍空 |
| `tests/clawd_race_cases/test_required_wakeup_human_cases.py::test_exact_generation_steer_and_stop_do_not_reach_a_later_generation` | 正确代次 `correct` 与 `interrupt`，再 `adopt_agent(generation+1)` | steer 状态 `queued` 且只挂这一代；后一代 agent 的 `steer`/`interrupt`/`clear_interrupt` 为零；`pending_stops` 仍是原代次；再送 `generation+1` 为 `stale_generation` |

这不是 `bg-human`。`bg-human` 的触发是评估已决定 admit 之后、`_go` 进入模型之前插入更晚人话。上面的窗口在准入前，代次用例在 authority 门上。两条都不证明已发射的续跑会服从更新的人话。该发现仍未修、未验证。

## 重启恢复

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_continuation_recovery.py::test_interrupt_after_freeze_before_claim_recovers_once` | `claim` 抛真实 `JournalUnavailable`，新服务 `recover` | 中断时执行器为零；恢复后一次；再 `recover` 仍一次 |
| `tests/clawd_race_cases/test_continuation_recovery.py::test_interrupt_after_admit_before_receipt_does_not_relaunch` | 真实 `admit_continuation` 返回后抛测试 `Crash`，时钟 `advance(31)` 再 `recover` | 执行器仍一次；receipt 补上 |
| `tests/clawd_race_cases/test_continuation_races.py::test_two_consumers_recover_and_evaluate_admit_once` | 见重复唤醒 | 恢复和活评估不能各开一回合 |

`claim` 因他人活租约返回 False 时，`evaluate` 直接返回，不 `_schedule`。本目录没有把这个路径写成已修复。见下面 `bg-lease`。

## 审批代次

| nodeid | 触发 | 预期 |
| --- | --- | --- |
| `tests/clawd_race_cases/test_approval_generation.py::test_stale_request_and_generation_rejected_internal_source_cannot_bypass` | 过期 `execution_generation`、错误 `request_id`、缺字段、`source=internal`、settle 之后的迟到回调 | `invalid_params` / `stale_generation` / `approval_not_pending`；队列项 `result` 仍空 |
| `tests/clawd_race_cases/test_approval_generation.py::test_exact_generation_resolves_once_and_does_not_start_a_turn` | 同一 `request_id` 与代次 `choice=once` 两次 | 第一次 `resolved`；第二次 `approval_not_pending`；消息条数不变 |
| `tests/clawd_race_cases/test_approval_generation.py::test_waiting_approval_blocks_continuation_even_if_event_source_is_internal` | run 状态 `waiting_for_approval`，审批对象带 `source=internal`，再 `evaluate` | `approval_pending`；执行器为零；无 self-POST |

未接入、不猜签名：面板 `POST /panel-api/v1/runs/{id}/approval` 和斜杠 `/approve`。斜杠按 session key 解队列，不读 `execution_generation`。本目录不把它们算成代次门已覆盖。

## 其余已有 nodeid

不重复上面四主题，但验收发现时会一起跑：

- `tests/clawd_race_cases/test_continuation_races.py::test_cross_profile_isolation`：另一 profile 评估同一库为 `ownership_unconfirmed`；`_scope(profile)` 不互通。
- `tests/clawd_race_cases/test_continuation_recovery.py::test_panel_closed_still_continues_without_self_post`：无面板时一次 `persist_delegation_delivery`。这是单次唤醒，不是重复唤醒。

## 已知未修，本批不写成通过

下列 8 条不在本批授权修复范围。准备文档补齐主题不等于这些发现已关闭。静态结论仍是未修；本批没有运行，不能改成未通过以外的状态，也不能删掉。

| id | 边界 |
| --- | --- |
| `bg-receipt` | 回执在 admit 返回后写入，早于回合结束。`FROZEN_LAUNCH` 不是 `completed`。 |
| `bg-lease` | `claim` 失败直接返回，不排下一次评估。之后没有新事件就不会再醒。 |
| `bg-human` | admit 决定之后、模型之前的更晚人话不再被服从。窗口用例和代次用例都不覆盖这个时点。 |
| `bg-busy` | busy/draining 在真正启动前记次并按 `BACKOFF_S[0]` 退避。本目录不把它改成不计次。 |
| `bg-window` | 预检仍是 `get_messages(session_id, limit=300, latest=True)`，不走压缩谱系根。 |
| `bg-swallow` | 投递已写入后，导入或 `evaluate` 失败只 `fail_closed`，不排重试。 |
| `bg-pid` | 非终态行上仍存在的 PID 可被当成可复用旧 run。本目录没有这个用例。 |
| `bg-stop` | 对续跑 run 调 stop，无 authority 或 authority 没有对应 admission 时打不中。`interrupt` 代次用例不关闭这一条。 |

`bg-plan` 此前归并为误报，不是这 8 条，本批不升格、不修。

`grok-4.7` / `sub2api` 只是会话锁上的路由字符串。执行器输出 `test-executor-not-llm`。身份不符不改配置。
