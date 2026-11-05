# 记忆作用域收口 — 2026-11-05

静态实现与接口核对。不是验收通过，不是上线许可。未运行 pytest、构建、import、py_compile、CLI 产品探针、浏览器或网络。未新建项目记忆页。未改 `clawd_projects`。未读生产凭据，未碰原面板、`/var/www/hermes-panel` 或 `/usr/local/lib/hermes-agent`。未 commit、push、deploy。

## 实现

可证明的会话身份只来自 `agent._session_db.db_path` 经 `state_db_profile_home` 得到的 profile。没有这条路径时，不再用冻结 home，也不再调用 `get_hermes_home()`。

- 已有项目 store、冻结项目或 `project_id`：拒绝本回合，不带着旧项目进入循环。
- 普通无项目全局模式：不阻断，不查进程 home 的 `projects.db`，不改已缓存提示。
- `resolve_session_project` 抛错：拒绝本回合，不再保留旧项目继续。
- `projects.db` 不存在且冻结与 store 一致：不把缺文件当成解绑，保留已封存范围，不重建提示。
- `projects.db` 不存在但复合 store 没有匹配冻结：拒绝，不继续使用该 store，也不把它降成全局。
- `_attach_project_context` 在解析结果为 None 时自己剥到最内层全局 store，并清空 `project_id` / `project`。门面事后校验仍在，但不再是正确性的唯一来源。
- 直接 attach 时，若已有项目身份而 `projects.db` 不存在，先拒绝，避免 `connect()` 建出空库再被读成解绑。
- 活回合、子代理只读、嵌套剥离、先租约后刷新、范围变化才重建提示、安装失败回滚均保留。未改动的绑定不重写提示。

## 权威 `run_api_turn` 调用链

有 `session_authority` 时，非代理路径会进入本域，再进入真实循环。行号是本次只读核对的当前源码。

1. `gateway/platforms/api_server.py:3725-3740` `APIServerAdapter._run_agent` 调用 `run_api_turn`。到不了 `api_server.py:3815`。
2. `gateway/session_api_turn.py:315` `run_api_turn` → `admit_api_turn_async`（129）→ `observe_api_turn`（340）→ `authority._schedule(ref)`（368）。
3. `gateway/session_authority.py:483` `_schedule` 创建 `_drain`（489）。`_drain`（1302）在 1336 调用 `_execute_claimed`（1265）。
4. `_execute_claimed` 在 1279 调用 `execute_finite_admission`。`gateway/session_finite.py:71` 在 81 调用 `execute_admission`。
5. `gateway/session_ingress.py:277`。API 源在 320-341 准备 `api_execution`，353 把事件标成 internal，354 调用 `authority.runner._handle_message`。
6. `gateway/run_inbound.py:1264` `_handle_message`。internal 事件不满足 `_hm_authority_admission`（1220）的再准入条件，继续到 1351 的 `_handle_message_with_agent`。
7. `gateway/run_turn.py:199` → 251 `GatewayRunner._run_agent` → 834 → 841 `_run_agent_inner`（2132）。
8. 无代理 URL 时，2215 启动 `TurnRunner.run_sync`。`gateway/run_turn_runner.py:1210` → 1227 `_run_sync_scoped` → 1354 `run_classic_cli_turn`。
9. `gateway/session_cli_turn.py:211` 在 215 或 224 调用传入的 `run`。那是 `run_turn_runner.py:973` 的 `_run_conversation_with_approval`，1033 调用 `agent.run_conversation`。
10. 该方法是 `agent/turn_facade.py` 的 `TurnFacadeMixin.run_conversation`。410 先 `admit_durable_turn_lease`。early result 在 414-423 返回，不改记忆。门面所有者在 431 调用 `_idle_project_memory_retarget`，然后 432 才标记 entered。482 才调用 `agent.conversation_loop.run_conversation`。
11. `agent/conversation_loop.py:1802` 在 1837 进入 `_run_conversation_turn`（1640）。1693 调用 `build_turn_context`。Codex 的 `_turn_without_loop` 在 1739，位于这次刷新之后，不是绕过门面的第二入口。

面板 `POST /v1/runs` 在有 authority 时走 `api_server_runs.py:935-938` 的 `_admit_run_to_authority`（779 同样是 `admit_api_turn_async`）。`_execute_run`（1117）在 1162 看到 `run.admission` 后只 `observe_api_turn`（1182），不走 1032。1032 是没有 authority 时的直接 `agent.run_conversation`，同样进门面，但不是 `run_api_turn`。

## 不经过本域

这些不是猜测，是上述调用点的旁路：

- `gateway/run_turn.py:2148-2160` `_run_agent_via_proxy`。有代理 URL 时不创建本地 agent，不调用 `run_conversation`。远程回合的项目记忆不由这次刷新保证。
- `gateway/run_turn_runner.py:1298-1307`。模型或凭据解析失败时返回失败结果，不到 1354。
- `api_server.py:3815` 不是 authority 存在时的活路径。
- `session_ingress.py:356-367`。`_handle_message` 没有留下 TurnRunner 结果时，drain 记失败或通知，没有另一条循环。

## 仍阻塞

- 上述用例和原有 `tests/clawd_memory_native/test_turn_rebind_context.py` 都未运行。退出码未知。准备好的用例不是通过。
- 没有项目记忆页面。本次按约束不新建。客户端函数存在不等于 UI 已接。
- `clawd_projects` 另有 owner，未改。`resolve_session_project` 的库语义未变。普通初始化没有项目身份时，attach 仍可能经 `connect()` 打开 `projects.db`；这是既有 init 契约，不是空闲改绑去猜进程 home。
- 代理路径、记忆页、聊天页第 51 个及以后的 child、旧侧车 `/subagents` 不在本文件范围。
- `projects.db` 与 `state.db` 仍不是一个原子事务。
- 已进入的 facade 回合仍不改绑，也不因身份缺失中途拒绝。这是保留的活回合保护。

## 文件

- 改：`/root/hermes-workspace/clawd/agent/turn_facade.py`
- 改：`/root/hermes-workspace/clawd/agent/agent_init.py`
- 改：`/root/hermes-workspace/clawd/tests/clawd_memory_native/test_turn_rebind_context.py`
- 本报告：`/root/hermes-workspace/clawd/docs/clawd/memory-scope-closeout-2026-11-05.md`

未运行。
