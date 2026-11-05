# 运行契约用例准备 — 2026-11-05

状态：只准备了用例，没有运行，没有通过。写完时 `2026-11-05T06:20:13+0800`。未执行 pytest、`scripts/run_tests.sh`、构建、`py_compile`、产品 import、CLI 或网络。未读 `/root/.hermes`、`/usr/local/lib/hermes-agent`、原面板 `/root/hermes-workspace/hermes-panel`。未删文件，未改产品源码，未改其他测试。effort 文件未动。

下面的“覆盖”是断言已经写上，不是绿。

## 新增文件

| 路径 | SHA256 | 字节 |
| --- | --- | --- |
| `/root/hermes-workspace/clawd/tests/clawd_runtime_cases/test_installed_run_wait.py` | `f5e696e7409a493b4745abe7c5337d3ae645618a1521f940b681f3894f3dfb6e` | 28806 |
| `/root/hermes-workspace/clawd/tests/clawd_runtime_cases/test_child_counts.py` | `6b760b42b4d254f30952f74560f0627d6f32d13c98f375824623b5e4d99cc49c` | 18141 |

未改 `apps/panel/sidecar/test_run_wait.py`。它仍断言 `source=log`、`state=retrying`。未改 `apps/panel/tests/test_session_run.py`。这两份仍是未安装模块的语义，没有被覆盖成 installed。

## 发现路径

静态核对，未收集、未运行。

- `pyproject.toml` 只有 `testpaths = ["tests"]`，没有 `python_files` 覆盖。pytest 默认收集 `test_*.py`。
- `scripts/run_tests_parallel.py` 对根目录 `rglob("test_*.py")`。`_SKIP_PARTS` 只有 `integration`、`e2e`、`docker`。这两个文件不在跳过集里，也没有打 `integration` / `live` 标记，默认 `addopts` 不会排除它们。
- 显式文件路径会被 runner 原样纳入。全量发现根是 `tests`，一次即可，不要再把 `tests/clawd_runtime_cases` 和 `tests` 同时传一遍。

日后集中运行的形状，本次没有执行：

```text
scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> \
  tests/clawd_runtime_cases/test_installed_run_wait.py \
  tests/clawd_runtime_cases/test_child_counts.py
```

必须用仓库 `.venv` 经 `scripts/run_tests.sh`。不要把 `HERMES_HOME` / `CLAWD_HOME` 指到 `/root/.hermes`。缺解释器是阻塞，不是改用系统 `python3` 的理由。

`tests/test_tests_tree_layout.py` 要求 `tests/<目录>` 镜像源码包，或写进 `_NON_MIRROR_DIRS`。`clawd_runtime_cases` 两者都不是。同文件已经会点名一批既有 `clawd_*` 用例目录；本目录会再被点名。本路按指定路径新增，没有改那份布局测试。显式文件路径仍能发现这两个文件。全量 `tests` 里的布局断言变红，不能改写成“发现失败所以删目录”，也不能把本目录说成已被布局门禁接受。

## 静态签名

只 `ast.parse` 源码，没有 import 产品。调用点的位置参数个数和关键字都落在签名内。没有不匹配。

| 函数 | 现行签名 | 行 |
| --- | --- | --- |
| `project_run_wait` | `(db, store, session_id, turn_started_at_ms=None)` | `gateway/platforms/clawd_runtime_routes.py:122` |
| `session_run_view` | `(db, store, session_id, *, idempotency_key=None, scope=None)` | 同文件 `:96` |
| `http_routes` | `(adapter)` | 同文件 `:176` |
| `store_for_db` | `(db)` | `clawd_server/runtime_native.py:38` |
| `bind_run_store` | `(store)` | 同文件 `:30` |
| `native.install` | `(mod)` | `clawd_server/native.py:17` |
| `run_wait` | `(mod, session_id, turn_started_at_ms=None)` | `clawd_server/runtime_native.py:218` |
| `session_run` | `(mod, session_id, *, idempotency_key=None)` | 同文件 `:204` |
| `count_live_subagents` | `(db, parent_id="")` | `hermes_state_subagents.py:106` |
| `active_count` | `()` | `tools/async_delegation.py:824` |
| `active_task_count` | `()` | 同文件 `:830` |
| `collect_runtime_readiness` | 全关键字，含 `active_children` | `gateway/readiness.py:100` |
| `discover_for_session` | `(session_id, *, idempotency_key=None, scope=None)` | `api_server_run_idempotency.py:284` |
| `reserve` | `(scope, key, fingerprint, run_id, status, *, ...)` | 同文件 `:184` |

安装后的模块全局是 `get_run_wait(session_id, turn_started_at_ms=None)` 和 `get_session_run(session_id, idempotency_key=None)`。用例按这个形状调用，幂等键是关键字。

工作树里 `gateway/readiness.py`、`tools/async_delegation.py`、`gateway/platforms/api_server.py` 等已有他人未提交改动。上面的行号只对这一读时点。最终开跑前要重核对，不能沿用本文件当现行事实。

## 覆盖矩阵

全部未运行，全部未通过。

### 安装后 run-wait 与同 home 幂等库

| 用例 | 锁什么 | 夹具 | 状态 |
| --- | --- | --- | --- |
| `test_projection_signatures_are_keyword_stable` | 上面四个入口的参数形态，幂等键仅关键字 | 无 I/O | 未运行 |
| `test_gateway_runtime_table_has_no_run_wait_route` | 网关 `http_routes` 没有 run-wait；有 `/api/runtime/sessions/{session_id}/run` | 不发 HTTP | 未运行 |
| `test_http_entries_are_listed_and_not_called_here` | 只列出集成入口，本文件不调用服务器或 `urlopen` | 不是端到端 | 未运行 |
| `test_uninstalled_sidecar_log_case_keeps_original_semantics` | 未安装 `test_run_wait.py` 仍是 `source=log` / `retrying`；`test_session_run.py` 未改打 install | 只读那两份测试 | 未运行 |
| `test_missing_session_is_not_found_not_unknown` | 会话不存在是 `not_found`，不是 unknown 字典 | 真实 `SessionDB` | 未运行 |
| `test_missing_run_and_lease_stay_unknown_without_zero_seconds` | 无 run、无租约行：`unknown`、`available is False`、`source=durable`、`busy is False`，无 `remainingSeconds`；种下的 `agent.log` 不得被打开 | 真实库 + 日志诱饵 | 未运行 |
| `test_lease_read_failure_stays_unknown_and_does_not_invent_idle` | 租约读失败保持 unknown，且没有 `busy`；`session_run_view` 不吞成 idle | 模拟租约读，会话行真实 | 未运行 |
| `test_non_bool_lease_stays_unknown` | `busy` 不是布尔则 unknown | 模拟租约载荷 | 未运行 |
| `test_discover_failure_stays_unknown` | run 发现抛错保持 unknown，不变成 idle | 模拟索引 | 未运行 |
| `test_non_dict_run_is_not_idle` | 非 dict run 不是 idle，无 `runStatus` | 模拟索引 | 未运行 |
| `test_terminal_and_idle_lease_is_the_only_available_state` | 仅 `completed/failed/cancelled/interrupted` 且租约不忙才 `idle` 且 `available is True`；仍无剩余秒、无 `httpStatus`、不读日志 | 真实库 + 真实 `RunIdempotencyStore`，四状态 | 未运行 |
| `test_terminal_run_with_busy_lease_is_not_available` | 终态但租约忙仍 unknown | 真实租约行 + 真实 run | 未运行 |
| `test_live_run_status_stays_unknown_when_lease_is_idle` | `queued/running/waiting_for_approval/stopping/unknown` 即使租约空闲也不可用 | 真实 run | 未运行 |
| `test_illegal_stored_status_stays_unknown` | 库里的 `success` 被发现层拒绝，投影仍 unknown，不写出 `runStatus` | 真实库 | 未运行 |
| `test_returned_unlisted_status_is_not_available` | 若发现层已经交出未列入的 status，仍不可用 | 模拟发现结果 | 未运行 |
| `test_other_sessions_terminal_run_does_not_free_this_session` | 别的会话终态 run 不让本会话 available | 真实库 | 未运行 |
| `test_session_run_view_missing_store_with_key_is_unavailable_not_not_found` | store 缺失且带键（含空字符串）是 `storage_unavailable`，不是 `not_found`；无键只回租约，不造文件 | 真实库 | 未运行 |
| `test_session_run_view_reads_a_real_store_without_inventing_a_row` | 真实发现：同 scope 命中，错 scope 是 not_found 且 `run is None` | 真实库 | 未运行 |
| `test_missing_idempotency_file_does_not_construct_a_shadow_store` | 缺文件返回 `None`，不调用会建表的构造器，不产生 `runs_idempotency.db` 及 wal/shm | 真实目录；构造器是会炸的模拟 | 未运行 |
| `test_same_home_registered_store_wins_and_still_does_not_create_a_file` | 同 home 已登记 store 优先，缺文件仍不创建 | 模拟登记对象，查找是真的 | 未运行 |
| `test_existing_same_home_file_is_opened_and_another_home_is_not_created` | 已有文件才打开且 `durable`；另一 home 缺文件仍是 `None` | 真实文件 | 未运行 |
| `test_non_durable_constructor_is_not_registered` | 构造后 `durable` 不为真则不登记 | 模拟构造器 | 未运行 |
| `test_is_file_oserror_returns_none_without_creating` | `is_file` 抛错返回 `None`，不建文件 | 模拟 `Path.is_file` | 未运行 |
| `test_native_install_global_missing_facts_are_unknown_and_ignore_logs` | `native.install` 后真实全局：缺事实 unknown，不刮种下的日志，不写剩余秒 0，不建影子库 | 模拟侧车表面，替换函数是真的 | 未运行 |
| `test_native_install_global_terminal_idle_is_available_without_log_seconds` | 安装后全局：终态且租约空闲才 available，`source=durable`，日志诱饵无效 | 真实 home + 真实 run 文件 | 未运行 |
| `test_installed_global_missing_session_is_404_not_unknown` | 安装后缺会话是 404 `session_not_found`，不是 unknown | 隔离 home | 未运行 |
| `test_installed_global_rejects_bad_session_and_turn_time` | 非法会话 400；nan/inf/负值/过远回合时间 400，不进库 | 模拟侧车表面 | 未运行 |
| `test_installed_global_keyed_lookup_does_not_create_a_shadow_db` | 安装后带键（含空串）503，无键不发明 run，两种都不建影子库 | 隔离 home | 未运行 |

`is_file()` 为真之后文件被删、构造器仍可能建空文件。那不是缺文件的设计路径。缺文件的设计路径是返回 `None`。本路没有把那场窄竞争写成“保证不建文件”。

### child 计数

| 用例 | 锁什么 | 夹具 | 状态 |
| --- | --- | --- | --- |
| `test_count_signatures_stay_distinct_from_the_panel_headcount` | 三个计数函数签名分开；readiness 的 `active_children` 是关键字 | 无 I/O | 未运行 |
| `test_http_child_count_entry_is_listed_not_executed` | `GET /health/detailed` 只列入口，本文件不发 HTTP | 不是活动页 | 未运行 |
| `test_open_subagent_is_counted_and_ended_or_other_source_is_not` | 未结束 `source=subagent` 计入；已结束和其他 source 不计；父过滤 | 真实 `SessionDB` | 未运行 |
| `test_unstored_receipt_is_counted_and_duplicate_id_is_one` | 未入库 `child_session_id` 计入；同一 id 在行和多条回执里只算 1；`finalizing` 也算 | 真实库 | 未运行 |
| `test_finished_receipt_removes_child_even_when_the_session_row_is_open` | `completed/success/error/failed/interrupted/cancelled` 把该 id 从集合去掉，即使会话行未结束 | 真实库，六状态 | 未运行 |
| `test_non_terminal_receipt_status_does_not_remove_the_child` | 回执 status `running` 不移除 | 真实库 | 未运行 |
| `test_missing_session_id_counts_extra_and_batch_index_is_not_identity` | 无会话 id 的回执项各 `+1`；`task_index` / `subagent_id` 不是身份；非 dict 不加；整数 id 不移除字符串 child | 真实库 | 未运行 |
| `test_terminal_delegation_row_does_not_add_links` | 非 `running/finalizing` 的单元不把链接算进人数 | 真实库 | 未运行 |
| `test_parent_filter_ignores_other_parent_receipts` | 另一父会话的持久 child 和未入库回执不计到本父 | 真实库 | 未运行 |
| `test_empty_database_is_zero_but_unreadable_receipt_is_not_zero` | 空库是合法 0；坏 `result_json` / `task_json` 抛 `storage_unavailable`，不返回 0 | 真实库 | 未运行 |
| `test_simulated_read_failure_raises_and_is_not_zero` | `_read_ctx` 失败抛 `RuntimeStoreError`，不是 0 | 模拟库，已标明 | 未运行 |
| `test_invalid_parent_is_invalid_params_not_zero` | 过长 parent 是 `invalid_params`，不是 0 | 真实库 | 未运行 |
| `test_readiness_preserves_null_and_does_not_coerce_it_to_zero` | `active_children=None` 保持 null；真实 0 仍是 0 | 隔离 home，不发 HTTP | 未运行 |
| `test_health_handler_source_maps_child_read_failure_to_null_not_suppress` | `_handle_health_detailed` 读失败写成 `None` 再传给 readiness；这段不用 `suppress`。`_readiness_work_counts` 才用 `suppress` 包 `active_count` | 静态源码，不是 HTTP | 未运行 |
| `test_drain_still_uses_completion_units_not_child_headcount` | `GatewayShutdownMixin._scale_to_zero_has_live_background_work` 点名 `active_count`，不点 `count_live_subagents` / `active_task_count` | 静态源码 | 未运行 |
| `test_active_count_is_one_completion_unit_when_children_are_many` | 1 个 running 单元加 3 个 child 仍是 `active_count()==2`（含 stalling），`active_task_count()==3`；completed 单元不计。不能为活动页改 `active_count` | 本进程 `_records`，home 隔离 | 未运行 |
| `test_durable_children_do_not_change_process_completion_units` | 库里 3 个活 child 时，本进程两函数都是 0 | 真实库 + 空 `_records` | 未运行 |
| `test_active_task_count_subtracts_only_this_process_finished_children` | 只减本进程回执里已结束的 `child_session_id`；账本读失败人数不变成 0 或 null，只是少减 | 真实行 + 模拟 `_connect` 失败 | 未运行 |

合法空库的 0 和读失败的 null 分开断言。没有把失败改成“允许 0”。

## 标明模拟，以及未冒充的集成入口

模拟夹具类名带 `Simulated`，文档写明不是 sqlite、不是侧车进程、不是 HTTP。持久事实走隔离 `tmp_path` 上的真实 `SessionDB` / 已有 `runs_idempotency.db`。路径解析后若落在 `/root/.hermes`、`/usr/local/lib/hermes-agent` 或原面板目录，用例自己拒绝。

需要隔离环境以后再打的真实 HTTP，本文件没有调用，也不是端到端：

| 入口 | 前提 | 不能当成 |
| --- | --- | --- |
| `GET /panel-sidecar/run-wait`，侧车路径 `/run-wait` | `PanelServer.start` 成功 bind，已 `native.install`，隔离 home | 聊天回合、浏览器、未安装日志刮取 |
| `GET /panel-sidecar/sessions/run` | 同上 | `/v1/runs` 准入成功 |
| `GET /panel-sidecar/sessions/run/resolve` | 同上 | 跨进程重放 |
| `GET /health/detailed`，面板同源 `GET /panel-api/health/detailed` | 网关进程、Bearer、隔离 home，不打开生产 `state.db` | 活动页、10 秒刷新、浏览器里的「不可用」 |

活动页「进行中的子代理」读 `readiness.checks.background_queues.active_children`，非数字显示「不可用」（`activity-page.tsx` 的 `countText`）。`useHealthDetailed` 可见时 10 秒拉一次（`queries.ts:185-191`）。`useActivity` 也是 10 秒，走侧车 activity，不是这个人数（`queries.ts:203-208`）。刷新间隔未实测。`background_queues` 整对象缺失时卡片不渲染，本路没有把它写成已验收。

网关 `http_routes` 没有 run-wait。聊天消费点仍是 `sidecar.runWait`（`sidecar.ts:498`，`turns.tsx:601`）。未安装的 `python apps/panel/sidecar/hermes_panel_sidecar.py` 和未 bind 的测试仍打旧日志函数。本路不删旧函数。

## 明确没做

- 没有运行上述任何用例，没有通过数。
- 没有构建、没有 import 产品模块来探活，没有 CLI，没有网络，没有起 HTTP。
- 没有改 effort、续跑、公共 API 实现或其他测试。
- 没有把未安装日志用例改成 `source=durable`。
- 窄竞争建库、活动页 10 秒刷新、真实面板 HTTP，都还没跑。准备好不是 pass。
