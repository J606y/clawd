# 取消成功连续续跑次数门槛 — 实现记录 2026-11-05

静态实现记录，不是运行结果、验收或上线许可。未自行运行测试、构建、import、py_compile 或 CLI。未 commit、push、deploy。未读生产，未改现网，未删数据文件，未启第二调度器。编辑工具自带的 lint 状态不是验收。

journal 精确文件名是 `gateway/clawd_continuation_journal.py`，不是另名，无需扩范围。

归并报告 `docs/clawd/core-audit/final-findings-2026-11-05.json`、`final-coverage-2026-11-05.json`、`final-summary-2026-11-05.md` 未改。其中 `gateway/clawd_continuation.py` 与 `gateway/clawd_continuation_decision.py` 的旧哈希已因本实现过期，不能再当这两份文件的当前最终结论。后续独立复核负责更新。本路不修审查项。

## 变更文件

| 文件 | 做了什么 |
| --- | --- |
| `gateway/clawd_continuation_decision.py` | 去掉 `streak_count >= MAX_STREAK` 硬停。`MAX_STREAK = 5` 与 `streak_count` 留下作观测，不再返回 `streak_limit`。`MAX_ATTEMPTS = 5`、`BACKOFF_S` 未改。 |
| `gateway/clawd_continuation.py` | 决策前按会话释放可归因的旧次数锁。block 回执只留给 `cancelled` 与 `attempts_exhausted`，并写入原因。准入回执原因是 `admitted`。不再为 `streak_limit` 写回执。 |
| `gateway/clawd_continuation_journal.py` | 回执增加可空 `reason` 列。打开时只加列，不改旧行。`reconcile_legacy_streak_receipts` 只删能证明的次数锁。 |
| `tests/gateway/test_clawd_continuation.py` | 原 `test_streak_limit_blocks_without_asking_the_model` 改为第 5 次仍 admit；同批行上的失败预算和取消仍 block。 |
| `tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py` | 同步次数、旧回执恢复，以及空回执不得整批清除、旧库迁移、取消重启、新回执原因、draining 不写回执。未执行。 |

未改：`clawd_continuation_admit.py`、`clawd_continuation_gates.py`、`wake.py`、侧车、两份 `transcript.ts`、`chat-page.tsx`、`build/lib` 旧副本、审查归并文件、`docs/clawd/continuation-no-five-cap.md`（该文仍是改前核对，不要把它读成当前实现）。

## 恢复规则

新回执必须带原因：`cancelled`、`attempts_exhausted`，或准入后的 `admitted`。省略原因只用于模拟旧行。`admitted` 表示 admit 已返回并排队，不是模型回合成功。

旧 `run_id IS NULL` 不得按空值整批删除。一行只有同时满足才释放：

1. `run_id` 为空。有 run_id 的去重回执不删。
2. `reason` 为空或正好是 `streak_limit`。已记录的取消、耗尽、`admitted` 或其他原因都留着。
3. 空原因还要有同一会话、同一 `accepted_at` 的 `blocked/streak_limit` 事件，且该时刻没有 `cancelled` 或 `attempts_exhausted` 事件。
4. 更晚的取消或耗尽事件若对不上自己的回执，视为无法绑定身份的安全停止，更早的次数锁也不释放。对得上（同时刻有 `reason` 相同的回执，或旧空原因且该时刻事件只有这一个安全原因）才不挡住更早的次数锁。

不删 events，不删 requests，不删数据库文件。无法归因的裸空回执、同时刻含糊、以及后续未绑定的安全停止保持停止。events 仍没有身份列表，所以不能把「后来有一条取消事件」自动理解成「这个身份被取消了」；对不上就不恢复。

`open()` 对旧库只 `ALTER TABLE receipts ADD COLUMN reason TEXT`。旧行原因保持 NULL，不回填，不借迁移清锁。

## 调用链

1. 子代理完成仍只写完成通知。`gateway/wake.py` `persist_delegation_delivery` 调用 `note_persisted_delivery`。本路未改 wake。
2. 服务已 `arm_adapter` 时，`note_persisted_delivery` 只 `watch` 并 `evaluate`。未启动不产生回合。
3. 重启 `recover` 仍只扫 pending、watched 和 2 小时内完成通知，然后 `evaluate`。没有第二调度器。侧车线程仍被 `bind` 替换，本路未改侧车。
4. `evaluate` → `_decide`。`_decide` 先 `reconcile_legacy_streak_receipts(session_id)`，再读回执，再 `decide()`。
5. `decide()` 在审批、clarify、计划之后不再比较成功次数。无未回执真实完成通知仍是 `skip/no_trigger`。取消、审批、clarify、计划、失败预算、busy 租约、所有权与路由门禁仍在。
6. `_apply` 对 `cancelled` / `attempts_exhausted` 写带来源的空 run_id 回执。`streak_limit` 不再进入这个集合。busy/draining 仍 `record_attempt` 并按 `BACKOFF_S[0]` 再排，不写回执。claim 失败仍直接返回，不排下一次。admit 返回后仍立刻写回执，现多了 `reason=admitted`。

人话、steer、无新通知、draining 无工作空转，都没有改成放行。主代理不再派子任务时没有新完成通知，续跑自己停。没有新的完成分类器。

## 对 7 个审查 ID 的实际影响

父代理核对 74 份时，只有 `gateway/clawd_continuation.py` 与 `gateway/clawd_continuation_decision.py` 相对归并哈希变了，这是本实现，不是审查修复。下面 7 项都未按建议代码修改。旧报告不可再当这两份文件的当前最终结论；ID 结论本身本路不改写。

| ID | 归并状态 | 本实现的实际影响 |
| --- | --- | --- |
| `bg-receipt` | 仍成立，高 | 无关修复。回执仍在 `admit` 返回后立刻写入，现为 `clawd_continuation.py:344-347`。`admit.py` 未改。新原因 `admitted` 只表示已排队，不表示回合 `completed`。崩溃后通知仍会被这条早回执消费。 |
| `bg-lease` | 仍成立，高 | 无关修复。`claim` 失败仍在 `clawd_continuation.py:323-324` 直接 `return`，不 `_schedule`。busy/draining 的再排路径还在 `337-340`。没有补「抢不到租约就再评估」。 |
| `bg-human` | 仍成立，高 | 无关修复。claim 后仍只再决策一次（`325-329`），然后 `await admit`。`admit.py` 的 `_go` 未改，发射后仍不再看更新的人话。释放旧次数锁发生在 `_decide` 里，人话屏障仍由 `open_notices` / `is_human` 挡住，不会借恢复绕过人话。 |
| `bg-busy` | 仍成立，中 | 无关修复，且按批准范围保留。`record_attempt` 仍在 admit 前（`330-331`）。busy/draining 仍加次并退避（`337-340`）。这是故障预算，不是已取消的成功次数门槛。未采用「busy/draining 不计次」的审查建议。 |
| `bg-plan` | 归并为误报 | 无关。未改 `clawd_continuation_gates.py`。`transcript_plan_pending` 函数体未改，现起始于 `clawd_continuation_decision.py:211`，只因上文注释下移。计划闸门仍在已删除的次数比较之前（`334-335`）。去掉次数硬停不会制造计划放行，也没有补计划探针。本路不改这条误报结论。 |
| `bg-window` | 仍成立，中 | 无关修复。预检仍是 `get_messages(session_id, limit=300, latest=True)`（`clawd_continuation.py:364-365`），租约仍用传入的 `session_id`（`290`），不走压缩谱系根。`wake.py` 与压缩模块未改。 |
| `bg-swallow` | 仍成立，低 | 无关修复。`wake.py` 未改。`evaluate` 的 `JournalUnavailable` 与宽 `except` 仍只 `fail_closed`，不排重试（`261-267`）。reconcile 失败走同一条既有失败关闭，不是新的吞异常，也不是修复。 |

## 未完

- 上述 7 项都未修。不要把本实现写成审查收口。
- 界面仍未改。`apps/panel/src/react/pages/chat/chat-page.tsx:677,1081` 在 `continuationState().limited` 时显示「已连续自动继续 5 次」。`apps/panel/src/lib/transcript.ts` 与 `apps/panel/src/react/lib/transcript.ts` 第 134、281-297 行仍有 `AUTO_CONTINUE_LIMIT = 5`，且只数无 `display_kind` 的 `[面板自动续跑]`。核心续跑行带 `clawd_continuation`，这条提示不会因核心 streak 出现。需要改展示时交给 chat-page 与两份 transcript 的后续 owner。本路不改。
- 侧车 `AUTO_CONTINUE_LIMIT = 5` 仍在死分支。`bind` 已替换 watcher/main。未恢复第二调度器。
- `build/lib/gateway/` 下的旧副本未改，不是活导入根。不要删这个目录来制造干净。
- 无法归因的旧空回执保持停止。2 小时窗外且未被 watch 的旧通知，重启仍不会被 `recover` 扫到；这是原窗口，不是新门禁。
- `docs/clawd/continuation-no-five-cap.md` 是改前证据，顶部结论已过期。未改该文。
- 交接清单 `clawd-continuation-remove-five-cap` 不在本路文件名单，未勾。
- 用例已按新契约写好，本阶段未运行。最终验收不豁免。

## 明确未运行

未运行 `tests/gateway/test_clawd_continuation.py`、`tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py`，也未运行其他测试、构建、import、py_compile、产品 CLI、浏览器或迁移。准备好的用例不是通过。
