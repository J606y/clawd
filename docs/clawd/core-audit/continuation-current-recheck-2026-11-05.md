# 续跑 9 ID 当前重核 — 2026-11-05

静态只读。不是验收通过，不是上线许可，不是修复。未跑 tests、build、import、py_compile、产品 CLI、网络、生产读取或攻击验证。未改源码、归并 JSON、面板或官方安装目录。未 commit、push、deploy。

不与 `final-findings-2026-11-05.json` 的 63 相加。作者自报 8 条仍成立，不是本结论。本结论来自当前调用点。可达有风险不写成已利用。

观察时刻：`2026-11-05T18:07:38+08:00`。

## 计数

| 类 | 数 |
| --- | --- |
| 范围 ID | 9，唯一 9，遗漏 0 |
| 结论相同 | 9 |
| 结论改变 | 0 |
| active | 8 |
| false_positive | 1 |
| fixed | 0 |
| needs_verification | 0 |

改变结论的 ID：无。

## 哈希

| 文件 | SHA256 | 相对归并 |
| --- | --- | --- |
| `gateway/clawd_continuation.py` | `6c2be349953a7b1044549e322b5443d6e34847084bec600c46728eeae2c56d4f` | 变。旧行号作废 |
| `gateway/clawd_continuation_admit.py` | `a8e6b2f1c8595708186c6aba4ee034efefec107f8cd0b2bcc8a7b1e9cc017f3a` | 变。旧行号作废 |
| `gateway/clawd_continuation_decision.py` | `fa244674385cc0b216ca29227d32025fb51848a9a7d43a5c34a4f77cd9b97dbd` | 变。见 `bg-plan` |
| `gateway/clawd_continuation_journal.py` | `fe3d64ff1af9d3f241e37522828e0d2a8e6107271055eb38a71c654ca50205d9` | 归并未引用。只核 `claim` |
| `gateway/clawd_continuation_gates.py` | `6d2fa59a5c89ecf198799fb41e51fd7c3c6e671a0e23d2008ea0324ea1511ae9` | 相同 |
| `gateway/wake.py` | `7fe985e4606b769355c942ba137e5d52037cd96d61c1a6072a2e837f782b1a32` | 相同 |
| `gateway/platforms/api_server_runs.py` | `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b` | 相同 |
| `hermes_state_compression.py` | `8b6e2df32259d1d1c89a6f7215c8ba23ff7a91f69ecb83bcc9f3ff9a52863670` | 相同 |
| `hermes_state_messages.py` | `b81c7d272f768a548daa91642bf8283dfad2f592528fbe95c029038a13e7f574` | 只核 `include_ancestors` 默认 |

## 逐 ID

`bg-receipt` active，高，结论相同。触发：回执已写，随后进程退出，或结果字典 `completed` 为假。`admit.py:156-172` 不读 `completed` 就标完成。`174-180`、`356-357` 创建任务后返回。`315-316`、`342-343` 复用也直接返回。`continuation.py:479-482` 随后写 `admitted`。`FROZEN_LAUNCH` 不是回合完成。

`bg-lease` active，高，结论相同。触发：通知已在，claim 因他人活租约或不可领返回 False，之后无新事件。`journal.py:454-472` 只返回 False。`continuation.py:442-443` 与 `353-354` 都不 `_schedule`。未把租约秒数当成已复现丢失。

`bg-human` active，高，结论相同。触发：第二次 admit 决策之后、模型之前出现更晚人话。`continuation.py:444-448` 只再决策一次，`468` 后不再决策。`admit.py:141-172` 不比较人话。`479-482` 不撤回收据。缺检查确定。未跑交错。

`bg-busy` active，中，结论相同。触发：连续 busy 或 draining。`360-363`、`451-458`、`463-474` 在真正启动前记次，并按 `BACKOFF_S[0]` 退避。`decision.py:343-344` 的 `MAX_ATTEMPTS` 是失败预算。成功次数不是闸门。未修。

`bg-window` active，中，结论相同。触发：压缩后通知落到子会话而租约在谱系根，或完成行之后超过 300 条新活动消息。`continuation.py:501-503` 仍是 `limit=300, latest=True`，不传祖先。`hermes_state_messages.py:1372` 默认 False。`gates.py:68-73` 用传入 id。`compression.py:746` 写入谱系根。`wake.py:215-220` 先解析尖端。后三文件哈希未变。

`bg-swallow` active，低，结论相同。触发：投递已写入，随后导入或 evaluate 失败，且没有新事件。`wake.py:231-235` 只记日志。`continuation.py:293-301` 只 `fail_closed`，不排重试。

`bg-pid` active，高，结论相同。触发：非终态行的原进程已死，pid 属于无关进程，评估走到 reused。`admit.py:191-192` 只要求进程存在。`337-341` 存了 `owner_started` 但复用未读。`315-316`、`342-343` 返回旧 run，调用方写回执。再分配未跑。

`bg-stop` active，高，结论相同。触发：对续跑 run 调 stop，无 authority，或 authority 没有对应 admission。`admit.py:137-180` 不登记 agent，不传 `request_id`；task 字典存在时会登记。`api_server_runs.py:1556-1580` 哈希未变：有 authority 且无 `delivery_id` 走 `stop_run`；否则只在 agent 非空时打断，不取消 task。新登记不关闭本条。双执行未跑，不升格。

`bg-plan` false_positive，中，结论相同，不升格。`gates.py:47-51` 哈希未变，缺探针返回 False。产品源码没有该属性赋值，也没有 `plan_approval` / `plan_mode` 写入方。`decision.py:340-341` 仍先于 `343` 的次数比较。`342` 的注释不放行。decision 哈希已变；没有归并时字节，不能证明差异只有注释。这不构成可触发输入。

## 结论

8 条 active 与 1 条 false_positive 都与归并结论相同。行号已按当前字节更新。没有改成 fixed 或 needs_verification。未运行。本文件不是 pass。
