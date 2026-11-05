# 取消成功连续续跑次数硬停

本文件是静态核对证据，不是运行结果。未跑测试、未构建、未 `py_compile`、未部署、未改现网 Hermes 配置。源码未改。不要把本文写成「限制已取消」或「现网已生效」。

## 结论

clawd 新核心**没有**取消「连续成功自动继续 5 次后暂停、等人话」门槛。唯一活路径硬停在 `gateway/clawd_continuation_decision.py` 第 334–335 行。该文件不在本路允许名单，未改。只改允许名单里的消费点不能 admit：`streak_limit` 决策没有 spec。

用户看到的通知原文是「发一句话即可让 Hermes 继续」。本检出没有这句。隔离面板写成「让 clawd 继续」，且只统计 `[面板自动续跑]`。不要冒称现网已取消。

## 触发链

1. 子代理完成仍只写完成通知。`gateway/wake.py` `persist_delegation_delivery`（约 223–235 行）把 `display_kind=async_delegation_complete` 写入会话，日志写明 `no wake turn`，不 self-POST 普通 user。
2. 同一函数调用 `gateway.clawd_continuation.note_persisted_delivery`。没有 `gateway/clawd_continuation_wake.py`。
3. 服务已 `arm_adapter` 时，`note_persisted_delivery` 只 `watch` 并 `evaluate`。未启动则不产生回合。
4. `ContinuationService._decide` 把快照交给 `decide()`。这是唯一调度决策。
5. `decide()` 在审批/clarify/计划之后、失败次数之前执行：

```python
if streak_count(snapshot.rows) >= MAX_STREAK:  # MAX_STREAK = 5，第 18 行
    return Decision("block", "streak_limit", identities=identities)  # 第 334–335 行
```

`streak_count`（第 204–206 行）数的是最后一条真人输入之后的续跑行，不是失败次数。续跑行是 `display_kind=clawd_continuation`，或无 kind 且正文以 `[clawd自动续跑]` / `[面板自动续跑]` 开头。

6. `gateway/clawd_continuation.py` 第 294–298 行消费这个 block：写 `blocked` 事件，并对 `streak_limit` 调用 `add_receipts(..., run_id=None)`。身份被回执后，`pending` 为空，下次得到 `skip/no_trigger`。这是等人话锁，不是第二次调度器。
7. 面板展示是另一条只读链，不调度。`apps/panel/src/react/pages/chat/chat-page.tsx` 第 677、1081 行在 `continuationState().limited` 时显示停跑通知。`transcript.ts`（`src/lib` 与 `src/react/lib` 字节相同）第 134、281–297 行用 `AUTO_CONTINUE_LIMIT = 5`，且只数无 `display_kind` 的 `[面板自动续跑]`。核心续跑行带 `clawd_continuation`，这条通知不会因核心 streak 出现。核心停是静默 block 加回执。

`clawd_server/server.py` 第 145、198–204 行在 API `connect` 之后 `arm_adapter`。核心是唯一续跑 owner。

## 旧侧车不可达

`apps/panel/sidecar/hermes_panel_sidecar.py` 仍有 `AUTO_CONTINUE_LIMIT = 5`（第 4116 行）、`continuation_trigger`（第 4157–4182 行）、第 4571 行的同样比较，以及 `main()` 第 6175 行启动 `auto-continue` 线程。`CONTINUE_MAX_ATTEMPTS = 5`（第 4111 行）是 HTTP 失败重试，不是成功次数。

clawd 活路径不进入这条线程：

- `clawd_server/server.py` 第 150 行 `bind()`，第 220–228 行直接 `ThreadingHTTPServer.serve_forever`，不调用 `sidecar.main()`。
- `clawd_server/bind.py` 第 138–145 行把 `_continue_watcher`、`_continue_tick` 换成抛错函数，把 `main` 换成 `SystemExit`。
- `/clawd/ready` 固定 `legacyWatcher=disabled`（`server.py` 第 256 行）。

因此旧分支在 clawd 启动后不可达。未改侧车常数，也未恢复第二调度器。绕过 `bind` 直接跑 `sidecar.main()` 时旧上限仍在；那不是本检出的活路径，也不能用来声称现网已取消。

`live_task_store.py` 两份副本哈希相同，构造即拒绝，没有次数门槛。未改。

## 未应用的最小差异

不改常数到 30 或 999。不要新增「任务已完成」分类器，不要把助手的 done 当成测试通过，不要加影子任务库。

### 1. 必须改，本路禁止越界

`gateway/clawd_continuation_decision.py` 删掉第 334–335 行的比较和 `return`。保留 `streak_count` 作可观测计数。`MAX_STREAK = 5` 可留着但不得再触发硬停；不要把它改大。`MAX_ATTEMPTS`、`BACKOFF_S` 不动。

删掉这两行之后，有未回执的真实完成通知、且其它闸门放行时，第 5、6 次及更长连续完成仍 `admit`。没有新通知时仍是 `skip/no_trigger`，不会空转。主代理不再派子任务，就没有新的完成通知，续跑自己停。这是现有事件驱动，不是新的完成判断。

### 2. 配套，允许名单内，本次未单独改

`gateway/clawd_continuation.py` 第 297 行从回执集合去掉 `streak_limit`，保留 `attempts_exhausted` 和 `cancelled`。单独改这里不能续跑：`decide()` 仍返回没有 spec 的 block。

### 3. 旧 counter>=5 恢复，本路禁止越界

没有单独的持久 counter 列。卡住有两层：

- 转录里已有 ≥5 条续跑行：`streak_count` 硬停。去掉第 1 项即可放行。
- 已经写入的 `run_id IS NULL` 回执：`pending` 为空，变成 `skip/no_trigger`。只删第 334–335 行仍卡住。

回执不记原因。`cancelled` 和 `attempts_exhausted` 也写 `run_id IS NULL`。`events.detail` 只有原因字符串，没有身份列表。不能按 `run_id IS NULL` 全删，否则会解开取消和失败预算锁。

安全解卡需要 `gateway/clawd_continuation_journal.py` 能按「仅 streak_limit、且该身份没有后来的取消/耗尽事件」删除回执，或给回执加原因列。该文件不在允许名单，未改。旧库在这项落地前不能保证恢复。

## 保留的停机与保护

这些都还在，本次没有删：

| 条件 | 位置 | 结果 |
| --- | --- | --- |
| 无未回执真实完成通知 | `decide` `no_trigger` | 不空转、不自激 |
| 完成通知不晚于最后助手回复或最后真人输入 | `open_notices` | 不续 |
| 插话 `display_kind=steer`、新人话 | `is_human`；`_apply` 第 312–316 行 admit 前再决策 | 不续 |
| 显式取消 | `latest_run_status == cancelled` | block，并回执 |
| 审批 `waiting_for_approval` / clarify / 计划 | `gates` 与 `transcript_plan_pending` | block，不靠提示词等待 |
| 失败重试预算 | `attempts >= MAX_ATTEMPTS`（另一个 5） | `attempts_exhausted`，保留 |
| busy 租约 | `lease_expires_at` | `defer`，`BACKOFF_S` |
| admit 时 busy / draining | `clawd_continuation_admit.py` 第 168–173 行；`_apply` 第 324–326 行 | 不回执、按 `BACKOFF_S[0]` 再排，不删 |
| 所有权不明、provider 无法继承、非 api_server、会话已结束 | `decide` 前段 | 失败关闭或 skip |
| 同身份重复通知 | 回执 + `runs_idempotency` | 不双跑 |
| 最大并发 | admit 的 busy，不是成功次数 | 保留 |
| 压缩后续接 | `wake.py` `resolve_resume_session_id` | 只改投递落点，不是次数帽 |

续跑模块里没有名为 generation / 代次 的计数器。代次保护若指「新用户话或新回合不得被旧完成通知越过」，现有实现是人话屏障和 admit 前重决策，不是另一套 5 次帽。未新造代次分类器。

真正停机（不新增判断器）：没有新的未回执完成通知；或取消、插话、审批/clarify/计划等待、所有权或路由无法确认、失败预算耗尽。需要新授权时停。已验证交付不是本模块的分类结果：主代理在续跑回合里不再派子任务，通知链自然停止。助手一句 done 不算测试通过。

## 现网与隔离

| 项 | 隔离 clawd 活路径 | 用户看到的现网通知 |
| --- | --- | --- |
| 调度 | 核心 `decide()`，侧车线程被 `bind` 替换 | 未核现网进程。原文「Hermes」不在本检出 |
| 成功 5 次 | 仍硬停 `streak_limit` | 不能声称已取消 |
| 展示句 | chat-page 写「clawd」，且不数核心续跑行 | 用户原文是「Hermes」 |
| 部署 | 未部署 | 未改现网配置 |

## 品牌核对（未改）

`docs/clawd/rebrand-overview.md` 点名的侧车展示句仍在，owner 为 web，本路不改：第 63 行「Hermes 网关」；第 473、515、516、823、2833、3279、5047、5051、5242、5354 行的用户可见 Hermes/hermes 文案。服务 id 与 `hermes update` 调用未动。未做全盘品牌。

## 哈希（sha256，未改字节）

- `gateway/clawd_continuation_decision.py` `9bd9fd9d65e78a7f9d9ebd240b63d10b5262c4e1c82557c0110bad53285751e1`
- `gateway/clawd_continuation.py` `300f059641a218a78bf8be30fe7a1e4e162f44053b3105efe0bfc78341bbe476`
- `gateway/clawd_continuation_admit.py` `57e699c2ce583fa8f1d024442a5da01be3d1524ce906d16e4357132ee0a35282`
- `gateway/clawd_continuation_journal.py` `c256bb89bf4868424e4c0e845e6f78804f39a4adbe5a2f023bbec7748c94ca8e`
- `gateway/clawd_continuation_gates.py` `6d2fa59a5c89ecf198799fb41e51fd7c3c6e671a0e23d2008ea0324ea1511ae9`
- `gateway/wake.py` `7fe985e4606b769355c942ba137e5d52037cd96d61c1a6072a2e837f782b1a32`
- `clawd_server/bind.py` `a0cfe9432fbd526d11850105d040d0d0cf1e85f9ceabada2aa57ff04f20d101b`
- `clawd_server/server.py` `ea3534ab92e054fe38c79f6533b8797c86dea07facdfb09d3badc5a060dd03ab`
- `apps/panel/sidecar/hermes_panel_sidecar.py` `c8e2b0890e489e2f5c96183068f06a5d923ae67356f6fdfa2fba751288fd9bc8`
- 两份 `live_task_store.py` `81046175f34ab56e8f13fd8e265a72e9578e7be6a091be533f17683ab60744c1`
- `apps/panel/src/react/pages/chat/chat-page.tsx` `a209be6367d8f1d679fabf59300b51e5c09dbfbd9d1bbcbf8c69ee3cf750d029`
- 两份 `transcript.ts` `c36cf1943d1f96d6745e08a17d28ddafda285490951beb6ac1636b4e0145cbe4`

前五个续跑模块哈希与 `docs/clawd/core-audit/background.md` 所记一致，说明这次没有改它们。

## 测试

已写 `tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py`，调用真实 `decide` 与 `ContinuationService`，未运行。次数与旧回执用例对当前字节会失败。失败表示门禁还在，不是产品已取消限制。

落地第 1 项后，还要改 `tests/gateway/test_clawd_continuation.py` 第 123–131 行：它仍断言 `streak_limit` block。该文件不在本路允许名单，未改。侧车 `apps/panel/tests/test_auto_continue.py` 的 streak cap 断言对应已退役分支，本次未动侧车，那些断言仍描述死代码。

## 仍阻塞

- 活路径硬停未移除：`clawd_continuation_decision.py:334-335`。
- 旧 `streak_limit` 回执无法安全删除：`clawd_continuation_journal.py` 不在允许名单。
- 展示句在禁止修改的 `chat-page.tsx` 与未授权的两份 `transcript.ts`。核心停并不产生用户原文。
- 现网未核对、未部署。
