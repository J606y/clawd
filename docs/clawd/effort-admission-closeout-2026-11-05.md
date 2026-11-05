# effort 准入收口 — 2026-11-05

状态：静态实现和用例准备。不是验收通过，不是上线许可。

未运行 tests、build、import 产品、py_compile、产品 CLI 或网络。未读 `/root/.hermes`、`/usr/local/lib/hermes-agent` 或其他生产目录。未删文件。未改 `gateway/clawd_continuation_decision.py`、`gateway/clawd_continuation.py`、`gateway/clawd_continuation_journal.py`。未改公共管理库和前端。未恢复成功连续 5 次硬停。

写入工具自带的语法提示不是验收，本路没有把它当成通过。

## 真实提交与冻结顺序

### 聊天：同一请求

仍在 `prepare_admitted_session_chat` 的同一把准入锁内，顺序是：

1. `profile_admission_lock`：没有带 `session_authority` / `session_authorities` 的 runner 时交出 `None`，不提交，不返回 409，也不把旧档说成已提交。有 runner 时 `require_active_authority` 要求 `adapter._ensure_session_db()` 与 `authority.db` 是同一对象，否则 `profile_mismatch`。然后持 `keyed_turn(authority._route_turns, 'clawd-admission:' + logical)`；已有活会话对象时再持 `live.mutation_lock`。
2. 有 authority 才 `commit_pending_if_any`。没有 pending 不写。有 pending 才 `commit_deferred_effort(..., authority=authority, admission_held=True)`。这一步只写本次 `reasoning_effort`（锁内 `model_options` 和根上的 `reasoning_effort`），去掉 `clawd_pending_reasoning_effort`。不替换 model、provider、`route_source`、`confirmed` 或其他档位。
3. 只有本次请求行确实 `persisted is True` 时，才把该行的已提交档放进 `committed_effort_for_lock`。谱系根的 view 不会写到另一行。
4. 然后才 `_prepare_session_chat`。若正文带了 model/provider、`require_model_lock` 为真、又不是已持久锁，`_persist_session_runtime_lock` 仍调用 `update_session_runtime_lock` 写入新的 model、provider、`route_source`、`confirmed` 和其他 `model_options` 键。`reasoning_effort` 单独处理：本次已提交档覆盖正文里的同名字段；没有本次提交时，正文省略有效档才保留锁或根上已存储的档。pending 不当成已存储档，模型锁端点因此不会偷偷提交。
5. 返回后 `apply_committed_effort` 仍只补本次内存快照的 `reasoning_effort`（以及已有 `reasoning` dict 时的 `effort`）。不覆盖路由、模型或其他档位。
6. 锁在上述写入之后才释放。这一回合的 agent 快照和下一回已确认锁读到的 `lock.model_options.reasoning_effort` 是同一档。

`POST /api/sessions/{id}/model` 不走这次提交。它没有 `committed_effort`。正文省略档位时保留已存储档；正文自己带了有效档就保留正文档。pending 留着。

无 authority 的聊天仍继续，pending 留着。这不是提交成功。

### 续跑：启动冻结前

调用方顺序没有改，因为 decision / continuation / journal 只读：

1. `ContinuationService._apply`（`gateway/clawd_continuation.py:318-333`）先 `journal.freeze`，再把 `frozen["body"]` 交给 `admit`。`decide` → `inherit_runtime`（`gateway/clawd_continuation_decision.py:342-350`）仍不读 `clawd_pending_reasoning_effort`。
2. `admit_continuation` 在 caller spec 的幂等查找之后、busy / draining 门禁之后、`reserve` / `_launch` 之前调用 `refresh_continuation_spec`。
3. 没有 authority：调用方传入的 db 上能看见 pending 就 `effort_admission_required`，不启动，不猜 profile，不把 `profile` 参数当 home。看不见 pending 才沿用 caller spec。这不是提交成功。读不到会话行是 `ownership_unconfirmed`，不当成“没有 pending”。
4. 有 authority：`db is authority.db` 才继续，否则 `profile_mismatch`。先在 `keyed_turn` 和必要时的 `mutation_lock` 内重读 `authority.db`，调用只读的 `inherit_runtime`。model、provider、`locked` 与 caller spec 不一致则拒绝，此时还没提交，pin 还在，路由不被覆盖。
5. 路由一致才 `commit_pending_if_any(..., admission_held=True)`。再重读并 `inherit_runtime`。只把最新 `model_options` 放进将要启动的 spec。model、provider、`locked`、input、`request_key` 仍是 caller 的。这不是只改内存快照。
6. 幂等指纹仍用进入 admit 时的 caller spec。journal 重试会带着同一份冻结 body 再来，这样才能找到已经 reserve 的 run，而不是因为档位变化打成 conflict。真正 `_launch` 的 kwargs 用的是提交后的最新 `model_options`。
7. 已可复用的 caller-spec 预约在提交前返回。后到的 pending 不会写进已经启动的那一回合。
8. draining、busy 仍在提交前拒绝。`effort_not_idle` 映射为 `busy`，交给现有退避，不把 pin 写成成功。取消、审批、澄清、计划、失败预算、无工作门禁都在 decision 里，本路没动。

## 五次门槛

`gateway/clawd_continuation_decision.py:336` 仍是“Success streak is observational via streak_count / MAX_STREAK. It is not a gate.” 下一行仍是 `attempts >= MAX_ATTEMPTS` → `attempts_exhausted`。该文件读时 SHA256 `744c6a6c0f4bcee660c93180e43d9af35d1d39ad71fbfd7c62db436be8237e32`，与 `resumption-runtime-detail-2026-11-05.md` 的读时哈希相同。本路没有改 decision、continuation、journal，没有把 `streak_limit` 加回 admit，也没有清回执。

## 改过的文件

| 文件 | SHA256 |
| --- | --- |
| `gateway/platforms/api_server.py` | `e653b26b4bcb8e816cc673c62304727b511bbb24b980cfff8024d35beeab7a5c` |
| `gateway/session_api_turn.py` | `297b746965d2946829e729cb05c9671fe9692271da4ae06f8a37816ed2c21df4` |
| `gateway/clawd_continuation_admit.py` | `75c7ff6466068948decce5cfda8b614a9a7daadd2cf5d04933e952843a77c908` |
| `clawd_server/effort_service.py` | `4e2699372896c2ce45a76d7e5ed842ad0e016ec49d14f25786d146f753fac619` |
| `tests/clawd_effort_cases/test_admission_model_lock.py` | `12e8f56a33c7bfc16c0dd74bda5a7d721eda3f7ab71598c4062f5897df3c440d` |
| `tests/clawd_effort_cases/test_continuation_pending_effort.py` | `cb658dc538e7f45d61ab6390c319d6e26072f29256d5dc6dd19e8c1f35779a9d` |

只读、未改：`gateway/clawd_continuation.py` `5b0945c10bef7609140f545454589e9be40a2bc43e85d856c489f643c18adefe`；`gateway/clawd_continuation_journal.py` `4ccff5c301a4509dc20fc42107d91da97a518b46c422ba5e162ac3372d2af1e6`。

## 剩余接口阻塞（转交，不越界）

1. **journal 正文仍是提交前的 spec。** 要让冻结 body 本身带上最新档，continuation owner 必须在 `gateway/clawd_continuation.py:319` `journal.freeze` 之前，用真实 `authority.db`、`keyed_turn`、必要时的 `mutation_lock`、`admission_held=True` 调用 `commit_pending_if_any` 或 `refresh_continuation_spec`，再把重读后的 spec 交给 `freeze` 和第二次 `_decide`。本路不能改这个文件。`journal.freeze`（`gateway/clawd_continuation_journal.py:352-367`）对仍处于 `pending` / `leased` 且别名已被覆盖的请求会原样返回旧 body，不会重写。admit 现在能让这一次启动用到最新 `model_options`，但不能改 journal 里已经冻住的正文。状态读如果只看 journal body，仍可能看到旧档。
2. **`inherit_runtime` 仍不读 pending。** `gateway/clawd_continuation_decision.py:283`：已确认锁的 `model_options` 只要是 dict，就不回退到根上的 `reasoning_effort`，也不读 `clawd_pending_reasoning_effort`。本路在 admit 里先提交再调用这个函数，所以启动能看到锁内档位。若要在 `decide()` 里不先提交就看见 pending，必须改 decision。不要在 `inherit_runtime` 里只读 pending 而不提交，那会把未提交的 pin 放进 spec，下一回锁定路径仍可能丢掉。不要恢复 `streak_count >= MAX_STREAK` 硬停。
3. **无 authority 的续跑拒绝不会自动退避。** `effort_admission_required` / `profile_mismatch` 不在 `continuation.py` 的 `busy` / `draining` 延迟集合里。本路不启动、不猜 profile。若产品要求这种拒绝也像 draining 一样稍后重试，要改 `gateway/clawd_continuation.py:337-339`，并仍不得在没有 authority 时提交。
4. **`POST /api/sessions/{id}/model` 不提交 pending。** 它只在正文省略档位时保留已存储档。若这个端点也必须在准入锁内提交 pending，需要同一套 authority 证明。本路没有扩到那条入口。
5. **run-wait / 计数用例仍缺 owner。** 不在本路。不要把 `apps/panel/sidecar/test_run_wait.py` 的 `source=log` 改成安装后契约。

## 集中测试命令

下面没有执行。文件存在不是 pass。

```text
scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> tests/clawd_effort_cases/test_admission_model_lock.py tests/clawd_effort_cases/test_continuation_pending_effort.py
```

不要把 `HERMES_HOME` 指到 `/root/.hermes`。不要用系统 `python` 代替仓库 `.venv`。

## 结论

聊天在有 authority 时，于新模型锁整段替换之前提交 pending，并把本次已提交的 `reasoning_effort` 写进新锁，不覆盖 model、provider、route 或其他档位。续跑在 busy/draining 之后、reserve/launch 之前，用真实 `authority.db`、`keyed_turn`、`mutation_lock`、`admission_held` 提交 pending，启动用重读后的 `inherit_runtime` 结果，不只改快照。无 authority 不伪成功、不猜 profile。五次成功硬停没有恢复。

未运行测试。本报告不是验收 pass。
