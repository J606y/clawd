# 续跑 effort 真实冻结收口 — 2026-11-05

状态：静态实现和用例准备。不是验收通过，不是上线许可。

未运行 tests、build、import 产品、py_compile、产品 CLI 或网络。未读 `/root/.hermes`、`/usr/local/lib/hermes-agent` 或其他生产目录。未删文件。未 commit、push、deploy。未改 `clawd_server/effort_service.py`、`gateway/session_api_turn.py`。未改面板、侧车、`build/lib` 旧副本、审查归并 JSON。写入工具自带的语法提示不是验收，本路没有把它当成通过。

## 冻结与幂等顺序

取消、审批、clarify、计划、失败预算、busy 租约、无新工作，仍在 `decide()` 里，发生在任何提交之前。`MAX_ATTEMPTS` / `BACKOFF_S` 未改。成功连续次数仍只观测，不硬停。

只有 `decide()` 已经返回 `admit` 才进入下面的顺序。

1. `compatible_inflight`：同一会话、别名已被覆盖、状态仍是 `pending` / `leased` 时，读出已冻结正文。不插入，不重写。
2. 已有这份冻结：不提交。`journal.freeze` 按原契约退回旧 body、旧 `request_key`、旧别名。新档位不能从这条别名路径写进去。
3. 没有这份冻结，且适配器已经 `busy` / `draining`：不提交。把决策 spec 冻进去，只为了失败预算有一行。然后仍把 admit 交给现有 `busy` / `draining` 路径：`record_attempt`、`BACKOFF_S[0]`、`_schedule`。不写回执。
4. 没有这份冻结，且不是 busy/draining：在 `journal.freeze` 之前调用 `refresh_continuation_spec`。它持有真实 `profile_admission_lock`：`require_active_authority` 要求 `adapter._ensure_session_db()` 与 `authority.db` 是同一对象，否则 `profile_mismatch`。然后持 `keyed_turn(authority._route_turns, 'clawd-admission:' + logical)`；已有活会话对象时再持 `live.mutation_lock`。锁内 `commit_pending_if_any` → `commit_deferred_effort(..., authority=authority, admission_held=True)`。再重读 `inherit_runtime`。只替换将要冻结的 `model_options`。model、provider、`locked`、input、`request_key` 仍是调用方的。
5. 无 authority 且调用方 db 上看见 pending：`effort_admission_required`。不冻结，不启动，不写回执，不排下一次，不把 `profile` 参数当 home，不猜另一份库。`journal.event(..., "blocked", reason)` 可观测。本进程等下去也不会长出 authority；若把它放进失败预算，五次后会把通知收成 `attempts_exhausted`。所以这条不退避。下一次只能来自已有的外部 evaluate（新通知、recover、run settled），不是自激。
6. `profile_mismatch`、路由不一致、所有权读不到，同样只记 blocked 事件，不提交，不排程。
7. `effort_not_idle` 仍映射为 `busy`。这时还没有请求行，就走第 3 步的现有失败预算，不另写一套自激循环。
8. `freeze` 之后才 `claim`，再第二次 `_decide`。第二次决策能看见已经提交进锁的档，因为它走 `inherit_runtime`，不读 pending。人话、审批、取消若在这一步出现，释放租约并按该决策处理。已经提交的档留在锁里，不回滚，也不启动。
9. 持有租约之后，若冻结正文还没有对应 reserve，且不是 busy/draining：再提交并重读。只有 `continuation_reservation == "missing"` 才 `replace_inflight_body`。这一步只改 `body_json`。不改 `request_key`、别名、attempts，不删回执、事件或请求行。用来把 draining 先冻住的旧正文，在真正启动前对齐到已提交档。store 缺失、`same`、`conflict` 都不重写。
10. `record_attempt` 仍在 admit 之前。然后设置 `FROZEN_LAUNCH`，把冻结后的 spec 交给 admit。admit 用这份 spec 做幂等指纹并启动。令牌有效时不再 `refresh`，所以后到的 pin 不会写进这一回合，启动 `model_options` 也不会离开 journal body。
11. 已 reserve 且可复用：admit 在提交和 refresh 之前返回原 `run_id`。指纹仍是冻结正文。新档位不会打成 conflict，也不会再启动一回合。
12. admit 返回后仍立刻写 `reason=admitted` 的回执。这不是回合完成。`bg-receipt` 未修。

直接调用 `admit_continuation` 且没有设置 `FROZEN_LAUNCH` 时，旧契约还在：先按调用方 spec 查幂等，可复用就返回；busy/draining 在提交前拒绝；否则才提交。指纹仍是进入 admit 时的调用方 spec，所以旧测试里的重试找得到已 reserve 的 run。生产续跑不走这条分叉，因为服务在调用 admit 前已经设了令牌。

## 幂等别名

`journal.freeze` 对仍处于 `pending` / `leased` 且别名已被覆盖的请求，仍原样返回旧 body，不重写。本路没有改这个返回。

已 reserve 的 run 用的是那份旧正文的指纹。重试必须把这份正文交给 admit，不能先提交再拿新档位去 lookup。否则同一 `request_key` 会 conflict，或再启动一回合。

没有 reserve 的 in-flight 正文可以在持有租约、确认 `missing` 之后替换 payload，使 journal 与即将启动的 spec 一致。替换失败就启动原来的冻结正文，不另造一份。

窄竞争，不掩盖：peek 认为没有 in-flight，本进程已经提交，随后 `freeze` 却退回别人刚冻住且已经 reserve 的旧正文。本进程不重写、不按新档位再启动。已 reserve 的 run 不被新档位拆开。pin 可能已被本进程提交，但不会进入那次已 reserve 的启动。两库没有跨库事务，不能把这次提交收成未发生。

## 保留未动

- `MAX_ATTEMPTS = 5`、`BACKOFF_S`、`MAX_STREAK` 只观测。没有把 `streak_count >= MAX_STREAK` 加回。
- 取消、审批、clarify、计划、无新通知 `no_trigger`、busy 租约 defer，仍在提交之前。
- 回执恢复仍只删能证明的次数锁。`run_id` 为空不是删除条件。取消、耗尽、`admitted`、无法归因的空回执不批删。`open()` 仍只加列。
- `inherit_runtime` 仍不读 `clawd_pending_reasoning_effort`。先读 pin 而不提交，会把未提交档冻进 spec。本路在冻结前提交，所以第二次决策看见的是锁内档。

## 文件

| 文件 | 做什么 | SHA256 |
| --- | --- | --- |
| `gateway/clawd_continuation.py` | 冻结前提交并重读；in-flight 不提交；无 reserve 才对齐正文；令牌下启动冻结 spec；无 authority 只记事件 | `6c2be349953a7b1044549e322b5443d6e34847084bec600c46728eeae2c56d4f` |
| `gateway/clawd_continuation_admit.py` | `FROZEN_LAUNCH` 时不再二次 refresh；导出指纹与 reservation 查询。直接调用的旧指纹保留 | `a8e6b2f1c8595708186c6aba4ee034efefec107f8cd0b2bcc8a7b1e9cc017f3a` |
| `gateway/clawd_continuation_journal.py` | `compatible_inflight`、`replace_inflight_body`。`freeze` 仍不重写已覆盖别名。不删回执 | `fe3d64ff1af9d3f241e37522828e0d2a8e6107271055eb38a71c654ca50205d9` |
| `gateway/clawd_continuation_decision.py` | 只加注释。逻辑未改，不读 pending，不恢复五次硬停 | `fa244674385cc0b216ca29227d32025fb51848a9a7d43a5c34a4f77cd9b97dbd` |
| `tests/clawd_effort_cases/test_continuation_pending_effort.py` | 服务路径冻结前持锁提交、journal 与启动一致、已 reserve 别名、无 authority、profile 不猜、draining 后再对齐、门禁前提交 | `f9da010f1c79643ceb568f0a75b597ca8f6c2538c71fc4c58a71586dd280b915` |
| `tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py` | 无 authority 拒绝不批删取消/空回执，不自激，不写成 `streak_limit` | `f98b2f8ec53f3960d959b6344a43f7a744da8d34dd1586ac902c09bff4078392` |

未改，读过：

- `tests/gateway/test_clawd_continuation.py` `f81420e9a3ed42de203030263f9ac8e5d1b7767345319bf14f2bbf0a34f394c5`。无 pin 时 refresh 原样返回决策 spec，注入的 admit 仍收到这份 spec。
- `clawd_server/effort_service.py` `4e2699372896c2ce45a76d7e5ed842ad0e016ec49d14f25786d146f753fac619`。与上一份 effort 收口相同。
- `gateway/session_api_turn.py` `297b746965d2946829e729cb05c9671fe9692271da4ae06f8a37816ed2c21df4`。与上一份 effort 收口相同。调用了 `commit_pending_if_any` 和 `profile_admission_lock`，没有改它们。

## 仍成立的审查 ID

本路不修这些。旧报告里的行号已因本实现移动，不能再当当前行号。ID 本身未关闭。

| ID | 状态 | 本路实际影响 |
| --- | --- | --- |
| `bg-receipt` | 仍成立，高 | 未修。回执仍在 admit 返回后、回合结束前写入。`FROZEN_LAUNCH` 不表示回合 `completed`。 |
| `bg-lease` | 仍成立，高 | 未修。`claim` 失败仍直接返回，不 `_schedule`。冻结前的预算路径也是抢不到租约就返回。 |
| `bg-human` | 仍成立，高 | 未修。claim 后仍只再决策一次，然后 await admit。`_go` 发射后仍不看更新的人话。 |
| `bg-busy` | 仍成立，中 | 未修，且按批准范围保留。`record_attempt` 仍在 admit 前。busy/draining 仍计入 `MAX_ATTEMPTS` 并按 `BACKOFF_S[0]` 退避。 |
| `bg-plan` | 归并为误报 | 未改 `clawd_continuation_gates.py`。计划闸门仍在次数比较之前。去掉次数硬停不会制造计划放行。 |
| `bg-window` | 仍成立，中 | 未修。预检仍是 `get_messages(session_id, limit=300, latest=True)`，不走压缩谱系根。 |
| `bg-swallow` | 仍成立，低 | 未修。`evaluate` 的 `JournalUnavailable` 与宽 `except` 仍只 `fail_closed`，不排重试。 |
| `bg-pid` | 仍成立，高 | 未修。`_reusable` 仍把别的活 PID 当成可复用旧 run。本路没有改这个判断。 |
| `bg-stop` | 仍成立，高 | 未修。不在本路文件的停止入口。 |

## 互相依赖，未越界

- `tests/clawd_race_cases/test_continuation_recovery.py` 不在本路文件名单，未改。它的崩溃包装在服务调用 `admit` 时再调 `admit_continuation`。服务此时已设置 `FROZEN_LAUNCH`，内层不再 refresh。该文件的会话没有 pending，启动 spec 与决策 spec 相同，恢复仍用同一份冻结正文找已 reserve 的 run。若以后给这条用例加 pending，提交必须发生在包装调用之前；本路的无 in-flight 路径已经如此。不要在包装里再 refresh，否则会和 journal 分叉。
- `tests/clawd_race_cases/test_continuation_races.py` 只导入 `admit_continuation`，搜索未见调用。未改。
- 界面 `AUTO_CONTINUE_LIMIT = 5` 与「已连续自动继续 5 次」仍在 chat-page 和两份 transcript。不在本路。核心没有恢复五次硬停。
- `build/lib/gateway/` 旧副本未改，不是活导入根。不要删目录来制造干净。
- `docs/clawd/continuation-no-five-cap.md` 与审查归并 JSON 未改。其中 continuation / decision 的旧哈希已过期。

## 集中测试命令

下面没有执行。文件存在不是 pass。

```text
scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> tests/clawd_effort_cases/test_continuation_pending_effort.py tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py tests/gateway/test_clawd_continuation.py
```

不要把 `HERMES_HOME` 指到 `/root/.hermes`。不要用系统 `python` 代替仓库 `.venv`。

## 结论

新冻结在没有 in-flight 别名时，于 `journal.freeze` 之前用真实 `authority.db`、`keyed_turn`、必要时的 `mutation_lock`、`admission_held=True` 提交 pending 并重读。冻进 journal 的就是随后启动的 spec。已覆盖的 pending/leased 别名仍退回旧正文；已 reserve 的 run 用这份正文的指纹复用，新档位不拆开它，也不把后到的 pin 写进那一回合。没有 reserve 的旧正文，在持锁确认 missing 之后才替换 payload，避免 journal 与启动分叉。无 authority 的 pending 拒绝记成 blocked 事件，不猜 profile，不自激。busy/draining 仍走现有失败预算。五次成功硬停没有恢复。旧空回执没有批删。

未运行测试。本报告不是验收 pass。
