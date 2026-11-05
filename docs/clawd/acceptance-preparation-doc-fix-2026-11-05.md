# 验收准备文档修复 — 2026-11-05

本批只做静态修复和用例准备。没有运行 pytest、构建、导入、CLI 或网络。没有改产品源码、既有测试、`scripts/clawd_acceptance`、constants、gate、生产凭据或官方目录。没有删除文件或日志，没有 commit、push、deploy，没有再派子代理。

准备文档补齐主题或「接口」字样只满足 `case_suite_status` 的交付检查。`ready` 不是测试通过。本报告不把任何 nodeid 写成 pass。

## 真实覆盖

### core-race-cases

门禁主题是审批代次、重复唤醒、取消人工输入、重启恢复。文档 `docs/clawd/race-case-preparation.md`。

已有、本批未改的 nodeid：

- 重复通知 / 并发唤醒：`tests/clawd_race_cases/test_continuation_races.py::test_duplicate_completion_events_admit_at_most_once`，`::test_two_consumers_recover_and_evaluate_admit_once`。打的是 `ContinuationService.evaluate` / `recover` 与 `ContinuationJournal.claim`，不是 `persist_delegation_delivery` 打两次。
- 取消与人话窗口：`::test_cancel_and_human_in_the_recheck_window_do_not_admit`，`::test_cancel_arriving_in_the_recheck_window_blocks`，`tests/clawd_race_cases/test_continuation_recovery.py::test_human_then_assistant_then_later_notice_is_recoverable`。不带 `execution_generation`。
- 重启恢复：`test_continuation_recovery.py::test_interrupt_after_freeze_before_claim_recovers_once`，`::test_interrupt_after_admit_before_receipt_does_not_relaunch`。
- 审批代次：`tests/clawd_race_cases/test_approval_generation.py` 的三个测试。入口是 `respond_run`、`AuthorityConnection.dispatch`、`SessionAuthority.respond` / `check_approval_generation`。

缺口因此补了 `tests/clawd_race_cases/test_required_wakeup_human_cases.py`，未运行：

- `::test_duplicate_wakeup_persisted_delivery_admits_once`：两次 `persist_delegation_delivery` 加一次 `note_persisted_delivery`，同一 `deleg_dup_wake`。
- `::test_stale_generation_cancel_and_human_steer_do_not_latch`：`SessionAuthority.interrupt` 与 `session_busy_controls.correct` 的过期代次、internal actor、多余 `source`、另一 profile。
- `::test_exact_generation_steer_and_stop_do_not_reach_a_later_generation`：正确代次挂起后，`adopt_agent(generation+1)` 不得收到 steer 或 stop。

没有 mock `evaluate`、`persist_delegation_delivery`、`interrupt` 或 `correct` 的返回值来制造通过。

### core-file-auth-cases

文档 `docs/clawd/file-auth-case-preparation.md` 现列出真实接口签名、触发、函数名 nodeid 和断言。未新建测试。既有 8 个 `test_*.py` 未改。

接口来自源码，不是猜测：`bind`、`file_roots_under_home`、`origin_allowed`、`PanelServer._authed` / `_renew` / `_proxy_sidecar`、`_artifact_roots`、`_root`、`_safe_path`、`_open_root_fd`、`_open_download`、`_upload` / `_upload_home`、`list_dir`、`Handler._dispatch` / `_artifact`。

root / fd / nofollow 只按现有测试的行为断言列出。本目录没有 Hermes profile 文件权限用例，文档写明缺失，不借用 race 的 profile 隔离。

## 未运行

- 未执行 `scripts/run_tests.sh`、pytest、收集 nodeid、构建或探针。
- 参数化用例的展开 id 没有收集。文档里的参数来自装饰器原文。
- 新 race 用例未运行。不能写成通过，也不能预先写成失败。
- 文件用例的旧「预期失败」第 1–6 条不是本次红结果。源码注释与后路静态说明称部分路径已改，但本批没有复跑，不能写成已绿。

## 遗留已知风险

8 个未授权续跑风险仍未修，本批不关闭：

- `bg-receipt`：回执仍在 admit 返回后。新唤醒用例只断言第二次不另开执行器。
- `bg-lease`：`claim` 失败仍不 `_schedule`。
- `bg-human`：admit 之后、模型之前的更晚人话仍不服从。窗口用例在准入前，代次用例在 authority 门上，都不是这个时点。
- `bg-busy`：busy/draining 仍在启动前记次。
- `bg-window`：预检仍限 300 条，不走压缩谱系根。
- `bg-swallow`：`evaluate` 失败仍只 `fail_closed`。
- `bg-pid`：活 PID 仍可能被当成可复用旧 run。本目录无此用例。
- `bg-stop`：续跑 run 的 stop 仍可能打不中。`interrupt` 代次用例不关闭它。

`bg-plan` 不是这 8 条。斜杠 `/approve` 和面板 approval HTTP 包装仍未接入 race 目录。文件侧 50MB 正边界、52MiB 代理缓冲、生图 fd/sqlite 窗口、祖先符号链接，仍未补用例。

## 改动

- 改：`docs/clawd/race-case-preparation.md`
- 改：`docs/clawd/file-auth-case-preparation.md`
- 新增：`tests/clawd_race_cases/test_required_wakeup_human_cases.py`
- 本报告：`docs/clawd/acceptance-preparation-doc-fix-2026-11-05.md`
