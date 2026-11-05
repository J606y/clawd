# 运行事实细查 — 2026-11-05

状态：只读源码后的接线复核，不是验收通过、不是实现完成、不是上线许可。读时 `2026-11-05T05:54:22+08:00`。未运行 tests、build、import 产品、py_compile 或 CLI 探针。未读 `/root/.hermes`、`/usr/local/lib/hermes-agent` 或其他生产目录。未删文件，未改产品源码。

旧报告里的行号不能再当现行事实。`runtime-native-implementation.md` 写的侧车 `get_session_run` 约 3730、`get_run_wait` 约 4633，以及完成清单里的 `get_run_wait:4302`，都已漂移。现行定义见下文。不要沿旧两参数快照把 effort 再判成高危。

## 读时 hash

设置 owner 正在写的公共管理库，本路只哈希、不审其设置实现、不编辑。最终验收前必须重哈希；下面的值只覆盖这一读时点。

| 文件 | SHA256 | 字节 |
| --- | --- | --- |
| `clawd_server/admin_routes.py` | `eb2a2142e270a77bd21ff56fb79fa8cd996fb92da88357d6c190a07386cdb147` | 15040 |
| `clawd_server/admin_native.py` | `1117bbedc32b107e66aba60a586dd220734be1b21507f1ebb0a9a2caaf69b334` | 7991 |
| `clawd_server/admin_common.py` | `23c784e87d34dc72a7e79bbadc63ca753a3cde3348bbda4cbfa0013a2d48900d` | 7051 |
| `apps/panel/src/react/lib/admin-api.ts` | `08981346d9802048e66a2b2dd0f8fae26d35c7d69fea39ce0b207f5ce24e05b4` | 10348 |
| `apps/panel/src/react/lib/sidecar.ts` | `0b947e383a6f3c3d9f27a361c73020d7aefe8f7a5348b21facb956b84484f94b` | 23915 |
| `apps/panel/src/react/lib/queries.ts` | `3b74303666c90f0549dabcf8a22f78a59592401514877754dbc01a3dcbc5737e` | 31637 |
| `apps/panel/src/react/lib/api.ts` | `74f2e6645f64ec1f17b060f84dec15635e0d2e0e5c28cd0d15645382baf963b7` | 19329 |

品牌派发表写的 `apps/panel/sidecar.py` 不存在。真实侧车是 `apps/panel/sidecar/hermes_panel_sidecar.py`，读时 SHA256 `c8e2b0890e489e2f5c96183068f06a5d923ae67356f6fdfa2fba751288fd9bc8`，312088 字节。与 `continuation-no-five-cap.md` 记录的同一哈希一致，只说明这一读时点未变，不说明品牌或续跑 owner 之后不会再改。本路不编辑该文件。

本域核对过的源码读时哈希：

| 文件 | SHA256 |
| --- | --- |
| `gateway/session_api_turn.py` | `f03d140bf5e14f341e3bd0de6b3b5e368161ba1d69aa780e250cea001896b190` |
| `gateway/platforms/api_server.py` | `b95284ed570ea9f3ed4f0218af12eff91891373a32a93f60f89658b43ba839de` |
| `gateway/platforms/api_server_runs.py` | `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b` |
| `gateway/platforms/clawd_runtime_routes.py` | `bc479c5c767f8d0bbfcc4110135526d2b92eda574113e23f0338fa988c70c8c0` |
| `clawd_server/effort_service.py` | `8cd685880f82709654679de6e99dac558e7735b9c7dc85fa16c92fcc2aec2d98` |
| `clawd_server/runtime_native.py` | `dfbefdc9fcb8103d6c6d72ba87e4cbc3ac451825777493c410d78028b4b5132c` |
| `clawd_server/native.py` | `58a0af2c2c7208740b56a8dc6ab0d9a500c0b35e134b8c1961df22efbb68e94d` |
| `clawd_server/bind.py` | `a0cfe9432fbd526d11850105d040d0d0cf1e85f9ceabada2aa57ff04f20d101b` |
| `clawd_server/server.py` | `ea3534ab92e054fe38c79f6533b8797c86dea07facdfb09d3badc5a060dd03ab` |
| `agent/turn_facade.py` | `0c1118543afeb3c2cf01da10fcda4ecedc2476af929390a21eb3939212e7dad2` |
| `hermes_state_subagents.py` | `da2ecbe540062b8e4bbc35f55eab8c4fa791e1cb2b28c3832fc7c01e4fa619c6` |
| `tools/async_delegation.py` | `a76a78ee29576b6b40d42044666b350f63d33e8b42a792b17bcb0cbd73069082` |
| `gateway/clawd_continuation_decision.py` | `744c6a6c0f4bcee660c93180e43d9af35d1d39ad71fbfd7c62db436be8237e32` |
| `gateway/clawd_continuation_admit.py` | `57e699c2ce583fa8f1d024442a5da01be3d1524ce906d16e4357132ee0a35282` |

`build/lib/` 是旧树，不是现行导入根。`build/lib/gateway/platforms/api_server.py` SHA256 `ffabfe4338d3ed5746fdac2c59233b5eb84efe2981b51acf0721f2645e503fe9`，其聊天处理仍直接调用 `_prepare_session_chat`，没有准入包装。不能用它重判现行源码，也不要删除。

## 1. effort：两入口在冻结前提交，且只改本次档

现行提交点不在 `turn_facade`。全库 `commit_deferred_effort(` 只有定义和一处调用：

- 定义：`clawd_server/effort_service.py:469-543`
- 调用：`gateway/session_api_turn.py:618-619`，关键字 `authority=authority, admission_held=True`

`commit_deferred_effort` 仍接受两个位置参数，但 `authority` / `admission_held` 是仅关键字参数，默认不构成持锁证明。`admission_held is not True`、`authority is None` 或库对象不是该 authority 的库时，抛 `effort_admission_required`（409），不写。这不是旧的两参数成功提交。不能把“函数还能被两参数调用”说成仍会改档。

持锁包装在 `gateway/session_api_turn.py:625-650` `profile_admission_lock`：

1. `_trusted_runner`（573-579）要求 runner 上有 `session_authority` 或 `session_authorities`。没有则 `yield None`，不伪称持锁。
2. `require_active_authority`（`gateway/session_authorities.py:216-228`）要求活动 authority，且 `adapter._ensure_session_db()` 与 `authority.db` 是同一对象。不是则 `RuntimeStoreError('profile_mismatch')`。
3. 包装内再比一次 `authority.db is not adapter._ensure_session_db()`（640-642），不符同样 `profile_mismatch`。
4. 持 `keyed_turn(authority._route_turns, 'clawd-admission:' + logical)`（645；`keyed_turn` 在 `gateway/session_authority.py:117-132`）。
5. 仅当 `authority.sessions.get(logical)` 已有活会话对象时，再嵌套 `live.mutation_lock`（646-650）。没有活对象时只持 keyed_turn，不持 mutation_lock。

`commit_pending_if_any`（599-622）先 `describe_session_effort`。`source != 'pending'` 不调用提交，避免空提交把普通聊天打成 409。有 pending 才提交。请求行与谱系根不是同一行时，两行分别提交；没有 pending 的那行跳过。

`_commit_config`（384-394）与 `_with_effort`（353-363）只写 `browser_model_lock.model_options.reasoning_effort`、根上的 `reasoning_effort`，并去掉 `clawd_pending_reasoning_effort`。不替换 model、provider、`route_source`、`confirmed`。锁不是 dict 时 409，不写。

### 聊天

`POST /api/sessions/{id}/chat` 与 `/chat/stream` 不直接进 `_prepare_session_chat`。

- `_handle_session_chat`：`gateway/platforms/api_server.py:3087`
- `_handle_session_chat_stream`：同文件 `3128`
- 两者都调用 `_prepare_admitted_session_chat`（2909-2913）→ `prepare_admitted_session_chat`（`session_api_turn.py:677-698`）

顺序，且全程仍在上面的锁内：

1. 有 authority 时 `commit_pending_if_any`。
2. 然后才 `_prepare_session_chat`（2838-2907）。它在 2864 取 `runtime_request`，2880-2881 或 2894-2903 把 `model_options` 抄进 `agent_overrides` / `run_kwargs`。
3. 仅当返回的 view `persisted is True`，再 `apply_committed_effort` 到本次 `run_kwargs` 和 `runtime_request`（689-691）。`merge_reasoning_effort`（582-596）只写入 `reasoning_effort`，以及已有 `reasoning` dict 时的 `effort`。不覆盖整把模型锁。

无 authority 时不调用提交、不返回 409，请求继续，pending 留着。这不是“已提交成功”。effort 文档里“没锁就 409”指的是调用了 `commit_deferred_effort` 却没有证明；这两条入口在没有 runner/authority 时选择不调用。面板适配器是否总能带上 authority，本路没有运行探针，不能从源码调用形状宣称每个进程都走 409。

`EffortServiceError` 原样变成对应 HTTP 码，不把旧档当成功（693-694）。`profile_mismatch` / `runtime_draining` 映射 `effort_admission_required` 409；其他 `RuntimeStoreError` 映射 `effort_run_state_unavailable` 503（695-698）。

同一请求里若正文带了 model 或 provider，且 `require_model_lock` 为真、又不是已持久锁，`_prepare_session_chat:2868` 会调用 `_persist_session_runtime_lock`（1911-1931）。该函数把正文 `model_options` 整段交给 `update_session_runtime_lock`（`hermes_state_sessions.py:830-848`），替换整个 `browser_model_lock`。这发生在 effort 提交之后、仍在同一把准入锁内。本次内存快照随后会被 `apply_committed_effort` 补上档位，所以这一回合的 agent 快照仍只多了本次 effort。下一回若走已确认锁，读到的是被整段换掉的 `lock.model_options`，不会回退到根上的 `reasoning_effort`。这是同一请求新锁的残留顺序问题，不是旧两参数提交。常见路径（无新锁，或 `persisted_lock`）不会走这个写入。

### `/v1/runs`

`gateway/platforms/api_server_runs.py:805` `_handle_runs`。`agent_overrides` 在 845 行从正文生成，此时还没有提交。已解析出 `selected_session_id` 时，879-891 调用 `commit_trusted_pending_effort(self, session_id, agent_overrides)`。该函数（659-674）在锁内提交，并在释放锁之前把档位写进传入的快照。919-925 才把 `model_options` 从这份已补丁的 dict 抄进 `agent_kwargs`。没有选中会话时不提交；新 `run_id` 没有 pending 行可交。失败同样 409/503，不继续用新档自称成功。无 authority 时函数返回 `None`，run 继续，不伪称已提交。

`/v1/runs` 这段没有调用 `_persist_session_runtime_lock`。

### 未接入的第三入口

续跑不走上面两条。`inherit_runtime`（`gateway/clawd_continuation_decision.py:272-295`）从已确认锁的 `model_options`，或未锁定时的根 `reasoning_effort`，冻结 spec。它不读 `clawd_pending_reasoning_effort`。`clawd_continuation_admit.py:91-114` 把这份 spec 直接交给 `_run_agent`，不调用 `commit_trusted_pending_effort`。面板关闭后的 pending，不会因续跑准入而提交。这是尚缺接线，不是已修高危的复发。

## 2. turn_facade 旧签名

`agent/turn_facade.py`（读时哈希见上）没有 `commit_deferred_effort`、`reasoning_effort`、`admission_held`。`build/lib/agent/turn_facade.py` 同样没有这些字符串。旧的回合门面两参数提交已经不在现行源码里。不要再按那份快照升格。

## 3. run-wait：安装后缺事实是 unknown

面板 `sidecar.runWait`（`apps/panel/src/react/lib/sidecar.ts:480`）打侧车 `run-wait`，不是网关 `/api/runtime/*`。聊天消费点：`apps/panel/src/react/pages/chat/turns.tsx:601`。网关 `clawd_runtime_routes.http_routes`（176-187）没有 run-wait 路由。

侧车 `_route_admin`（`hermes_panel_sidecar.py:6095-6096`）调用模块全局 `get_run_wait`。`runtime_native.install`（67-77）把 `mod.get_run_wait` 换成 `run_wait`（218-240）→ `project_run_wait`（`clawd_runtime_routes.py:122-165`）。Python 在调用时查全局，所以安装之后这条 HTTP 命中替换函数，不是定义处的旧函数对象。

`project_run_wait` 初始就是 `state=unknown`、`available=false`、`source=durable`。不写 `remainingSeconds`，不读 `agent.log`。

- 会话不存在：`not_found`，经 `runtime_native._api` 成 404，不是 unknown。
- 租约读取失败、租约不是带布尔 `busy` 的 dict、run 发现抛错：保持 unknown。
- store 缺失或没有 run 行：没有终态 status，不能变成 idle，仍是 unknown。
- 租约 `busy` 或 status 属于 `queued/running/waiting_for_approval/stopping/unknown`：显式 unknown。
- 只有 status 属于 `completed/failed/cancelled/interrupted` 且租约 `busy is False` 才是 idle，`available=true`。

旧函数仍在 `hermes_panel_sidecar.py:4817-4899`。它刮 `agent.log` / `agent.log.1`，缺日志时返回 `state=idle`，并可能写 `remainingSeconds=max(0, ...)`、`source=log`。这是未安装时的行为，不是安装后的投影。

## 4. 幂等库：只打开同 home 已有文件

`store_for_db`（`runtime_native.py:38-64`）用 `state.db` 的解析后父目录做 home，不用进程里最后一个 adapter。已登记的同 home store 优先。否则只在该目录 `runs_idempotency.db` 已是文件时打开 `RunIdempotencyStore`。文件不存在返回 `None`，不调用会 `CREATE TABLE` 的构造器，不造新库。构造后 `durable` 不为真也不登记。

`session_run_view`（96-115）：store 为 `None` 且带了幂等键，抛 `storage_unavailable`，不是 `not_found`。没键则只返回租约 busy，不发明 run 行。侧车替换 `session_run`（204-215）走这条。旧 `get_session_run`（3915-3950）同样：文件不存在且带键则 503，不创建文件。

网关 `handle_run`（240-272）不走 `store_for_db`。它用请求 adapter 自己的 `_run_idempotency_store`；store 缺失是 503 `session_run_unavailable`。adapter 构造自己的库时，`RunIdempotencyStore.__init__`（`api_server_run_idempotency.py:116-149`）会对路径 `sqlite3.connect` 并 `CREATE TABLE IF NOT EXISTS`。那是网关进程自己的库，不是侧车影子库。`bind_run_store` 的现行调用点是 `api_server.py:1089`。侧车进程不共享网关内存登记表，所以侧车查找落到同目录已有文件，或不可用。

窄竞争：`is_file()` 为真之后文件被删，构造器仍可能建出空文件。这不是缺文件时的设计路径。缺文件的设计路径是返回 `None`。

## 5. 计数

`tools/async_delegation.py:824-827` `active_count()` 仍是完成单元：活着的 delegation record 数，不是 child 人数。drain 仍用它（`gateway/run_shutdown.py:334` `active_count() > 0`）。不要为活动页人数改这个函数。

活动页「进行中的子代理」不读 `active_count`。`GET /health/detailed`（`api_server.py:1558` 注册，2277 处理）在 2300-2309 调用 `count_live_subagents`。异常或没有 db 时 `active_children=None`，不是 0。`gateway/readiness.py:117-121` 保留这个 null。面板 `activity-page.tsx:40-46,101` 非数字显示「不可用」。`queries.ts:160-165` 可见时 10 秒拉一次 `api.healthDetailed()`（`api.ts:400`，同源 `panel-api/health/detailed`）。活动列表 `useActivity` 也是可见时 10 秒（178-183），走侧车 activity，不是这个人数。刷新间隔是静态接线，未实测。`background_queues` 整对象缺失时卡片不渲染，不是「不可用」文案。这留待统一验收，不能当人数已验收。

`count_live_subagents`（`hermes_state_subagents.py:106-159`）：未结束的 `source='subagent'` 行，加上回执 `child_links` 里尚未结束的 `child_session_id`，用集合按 `child_session_id` 去重。回执已结束的 id 会从集合去掉。没有 session id 的回执项单独 `extra += 1`，不用批次索引当身份。读失败抛 `RuntimeStoreError`，不返回 0。API 层把这个异常变成 null。

`active_task_count`（830-886）只数本进程 `_records` 里 `running/finalizing` 的 goal，再减去本进程这些 delegation 回执里已结束的 `child_session_id`。账本读失败返回空集合，人数不变成 0，也不变成 null，只是少减已结束 child。它不是跨进程全局人数，也不驱动 drain。`hermes_state_subagents.py:3-8` 的模块说明仍写它会把已结束 child 算进去，和现行实现不一致；以 `async_delegation.py` 为准。

就绪卡片里的 `active_delegations` 来自 `active_count()`，但 `_readiness_work_counts`（1188-1193）用 `suppress(Exception)`，抛错时留下 0。那是完成单元卡片的显示吞掉，不是 drain 语义被改成 0。child 计数没有这个吞掉。

## 6. 未安装旧分支是否可达，退役界线在哪

clawd 面板进程的现行启动：

1. `clawd_server/server.py:149-152` `load_sidecar()` 后 `bind()`，然后 `_start_sidecar()`。
2. `bind.py:147-149` 调用 `native.install`。
3. `native.py:38-42` 在 `_clawd_runtime_installed` 未置位时调用 `runtime_native.install`，替换 `get_session_run` 和 `get_run_wait`，然后置位。
4. `_start_sidecar`（220-228）用同一个已绑定模块起 `ThreadingHTTPServer`。

因此，经 `PanelServer.start` 成功 bind 之后，`/sessions/run`、`/sessions/run/resolve`（5982-5985）和 `/run-wait`（6095-6096）命中替换函数。旧日志刮取和旧 SQL 发现不在这条 HTTP 上。

`admin_routes.install`（106-140）包装 `Handler._route`，未命中的路径回落到原 `_route`。包装不重绑 `get_run_wait` / `get_session_run`。设置 owner 仍可能改这些文件；上面的 hash 过期后，这条结论要重核，不能沿用。

旧函数仍然真实可达，界线是「没有执行 `native.install` / `runtime_native.install` 的模块对象」：

- `hermes_panel_sidecar.py:6168-6183` `main()` / `__main__` 直接 `ThreadingHTTPServer`，不 bind、不 install，并启动 `_continue_watcher`。`python apps/panel/sidecar/hermes_panel_sidecar.py` 会打到旧 `get_session_run` / `get_run_wait`。`bind` 把 `mod.main` 换成拒绝函数，只影响已经 bind 的那个模块对象，挡不住直接执行源文件。
- `apps/panel/sidecar/test_run_wait.py` 与 `apps/panel/tests/test_session_run.py` 直接 import 模块并调用函数，不 install。`test_run_wait.py:33-39` 仍断言 `source=log`、`state=retrying`。这些测试证明的是未安装模块，不是面板进程里的替换投影。
- `apps/panel/tests/artifacts-browser.mjs` 也直接起 `Handler`，不 install。那是夹具，不是产品服务器。

退役界线：不要现在删除旧函数。删除会打断未安装测试和 `__main__`，而且不能靠删除制造“已接线”。应退役的是产品服务路径上的旧行为，该路径在 bind 成功后已经替换。真正退役还要：产品启动只走 `PanelServer.start`；面板测试改打 `project_run_wait` 或先 install 再请求；确认没有启动器调用未绑定的 `main()`。在此之前，旧函数是未安装分支的活代码，不是死代码，也不是 clawd 面板 HTTP 的现行实现。

## 尚缺接线

| 缺口 | 要改的文件 | 本路为何不动 |
| --- | --- | --- |
| 续跑冻结 `model_options` 时不提交 pending | `gateway/clawd_continuation_decision.py`、`gateway/clawd_continuation_admit.py`，以及其现有用例 | 续跑 owner 的文件；本路只读 |
| 同一聊天请求新模型锁可能在提交后整段替换 `browser_model_lock` | `gateway/platforms/api_server.py` `_persist_session_runtime_lock` 的调用点；不要让 effort 路私自改 `update_session_runtime_lock` | 公共 API 文件不在本路可写范围。本次快照补丁已挡住这一回合，下一回锁定路径未证 |
| 无 authority 时聊天和 `/v1/runs` 继续且不 409 | 若产品要求这种进程也拒绝，才改 `session_api_turn.py` 的 `held is None` 分支 | 现行代码是“不伪称持锁”，不是假成功。未运行，不能证明面板适配器是否总会有 authority |
| run-wait / session-run 的安装后契约没有测试 | 新增核心用例，调用 `project_run_wait` / `session_run_view` / install 后的模块全局。不要把 `test_run_wait.py` 的 `source=log` 改成安装后契约而不分开未安装夹具 | 本路不写测试、不跑测试 |
| `count_live_subagents` 去重与失败 null 没有命中测试 | 新增核心用例。活动页 10 秒刷新留到统一验收 | 同上 |
| 旧 `__main__` 仍启动未安装侧车和 auto-continue | 产品入口保持 `PanelServer.start`；未绑定 `main()` 的退役要单独授权，不能删文件充数 | 禁止删除 |

## 集中测试命令与依赖

本路没有执行下列任何命令。准备好或文件存在都不是 pass。

最终验收入口仍是 `python3 scripts/clawd_acceptance/accept.py`。无 `--run --implementation-complete` 不得开跑。本域没有把清单改成 implemented。

核心，固定单线程，隔离 basetemp，不读生产 home：

```text
scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> tests/hermes_state/test_runtime_projection_contracts.py
```

该文件只断言 `store_for_db` 按 home 分开，以及没有登记、没有文件时返回 `None`。它用假 store，不打开 sqlite，不覆盖 effort、run-wait、计数。全量核心发现根是 `tests`，一次发现，不要把子目录再传一遍。

依赖：仓库 `.venv` 经 `scripts/run_tests.sh` 进入隔离测试环境；`env -i` 不转发 `CLAWD_HOME`。不要用系统 `python`。不要把 `HERMES_HOME` 指到 `/root/.hermes`。

面板 Python，验收编排里的形状（`scripts/clawd_acceptance/execute.py:170-195`），工作目录 `apps/panel`，解释器必须是仓库 `.venv/bin/python`，去掉 `PYTHONPATH`：

```text
.venv/bin/python -B -m unittest discover -s tests -p 'test*.py'
.venv/bin/python -B -m unittest discover -s sidecar -p 'test*.py'
```

缺 `.venv/bin/python` 是 blocked，不能改用系统 `python3`。这两组会 import 未安装的 `hermes_panel_sidecar`。`sidecar/test_run_wait.py` 仍期望日志刮取。它们通过也不能证明安装后的 unknown 投影；失败也不能单独当成安装后回归，除非先证明测的是替换后的全局。

当前没有任何 `test_*.py` 调用 `commit_deferred_effort`、`prepare_admitted_session_chat`、`commit_trusted_pending_effort`、`project_run_wait` 或 `count_live_subagents`。effort 准入、run-wait 持久事实和 child 去重，在现有命令里没有对应断言。统一验收前要先有这些用例，再跑；不能把现有绿或未跑说成本域通过。

## 结论

聊天和 `/v1/runs` 在有 authority 时，于冻结本次 `model_options` 之前，在 keyed_turn、必要时的 mutation_lock、同一 `authority.db` 和 `admission_held=True` 下只提交 pending effort。`turn_facade` 旧提交已消失。安装后的 run-wait 缺 run/lease 事实是 unknown，不刮日志，不写剩余秒数 0。幂等查找不造同目录影子库。`active_count` 仍是完成单元；child 加回执按 `child_session_id` 去重，读失败是 null；`active_task_count` 只是本进程。未安装的旧 `get_session_run` / `get_run_wait` 仍可经直接 `main()` 和未 bind 的测试打到，但不是 `PanelServer.start` 成功 bind 之后的 HTTP 实现。

未运行测试。本报告不是验收 pass。
