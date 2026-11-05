# 运行事实原生化

状态：本路已补 CLI 停止投影、profile store 绑定、旁白消费者、用量快照、编辑序号规范化、子代理计数和 effort 注册。随后收口了当前回合档位消费、跨进程幂等 run store、run-wait 持久事实、派发回执计数和清单关联投影。未运行测试、未构建、未提交。未改 `native.py`、effort 服务实现、`runs.ts`、chat-page、附件路由、`todo_tool.py`、task-list。

## 3. 谱系根租约 / 按 session 查 run / CLI 停止

shim 带幂等键时不再因为 sidecar 进程没有内存 bind 就 503。`store_for_db` 只打开该 `state.db` 同目录已存在的 `runs_idempotency.db`，按 home 登记，不使用进程最后 adapter，也不在文件缺失时新建影子库。文件不存在时带幂等键仍是不可用，不是 not_found。

未改 `hermes_panel_sidecar.py`。`bind` → `native.install` → `runtime_native.install` 会替换 `get_session_run`。没走这条安装的进程仍用侧车 3730 行旧实现；那是待调度接点，本路不抢。

## 10. 用量 / 上下文 / run-wait

`run-wait` 在安装替换后读持久 run/lease。能证明终态且租约不忙才是 idle。缺事实是 `state=unknown`、`available=false`，不写剩余秒数 0，不刮 `agent.log`。

侧车文件未改。精确接点：`get_run_wait`（约 4633 行）和 `_route` 的 `/run-wait`（约 5778 行）。只有 `runtime_native.install` 替换了 `mod.get_run_wait` 的进程走新投影。

## 11. 子代理

`active_count()` 未改，仍是完成单元。面板 `active_children` 改读 `count_live_subagents`：入库未结束 child，加上回执里尚未入库的 `child_session_id`，并去掉回执已结束的 child。没有 session id 的回执项单独加，不用批次索引当身份。读失败仍是 null，不是 0。

`active_task_count()` 只减本进程回执里已结束的 child，不是全局人数，也不触发 drain。

`_page_row` 增加 `profileKey`、`executionStatus`，有落库值才给 `delegationId`/`todoId`。`status` 的 done/ended 语义未改。`runtime_native.session_subagents` 和 `handle_subagents` 仍原样返回。

## 12. effort 注册与消费

路由仍只注册一次，未再挂。

两参数 `commit_deferred_effort(db, session_id)` 已从 `turn_facade` 移除。它现在必 409，不能当已提交。

可信路径在 `_prepare_session_chat` 和 `/v1/runs` 冻结 `model_options` 之前，持有 `keyed_turn(clawd-admission:<logical>)` 以及已注册会话的 `mutation_lock`，调用 `commit_deferred_effort(authority.db, session_id, authority=authority, admission_held=True)`。`_ensure_session_db()` 必须是该 `authority.db`。成功后只把 `reasoning_effort` 写进本次 agent 快照，不覆盖整把模型锁。

`effort_not_idle` / `effort_run_state_unavailable` / `effort_admission_required` 返回对应 HTTP 错误，不继续把旧档当成功。没有 session authority 的进程不伪称持锁。租约释放前仍 executing 时不再调用提交，拒绝是合理的，不伪完成。

没有 pending 时不调用提交，避免忙会话上的空提交把普通聊天打成 409。

## 13. 复用 agent 的项目记忆

网关 `_resolve_turn_agent` 会复用缓存 agent。无 authority 的 `_run_agent` 每次 `_create_agent`，init 已挂项目记忆，不强制重建。

复用路径在 `run_conversation` 取得租约之后、`conversation_loop` 设置 `_current_turn_id` 之前调用 `_attach_project_context`。只在解析出的 project id 与冻结范围不同时换 store 并丢掉缓存提示。嵌套进入不改外层冻结。

## 14. 清单关联

`DELEGATE_TASK_SCHEMA.tasks` 增加可选 `todo_id`，禁止从 goal 推断。`_dispatched_payload.children[]` 回显 `todo_id`、`delegation_id`、`subagent_id`、`child_session_id`。`subagent_id` 不是 session id。同一链接写入 child 的 `_session_init_model_config` 和 `patch_session_model_config`，并进入 `async_delegations.task_json.child_links`。不改验收 status，child 返回不自动 completed。未改 `todo_tool.py`、两份 `task-list`、`chat-page.tsx`。

## 未改边界

未访问生产 home，未跑测试，未构建，未提交。审批体未改。`absPath` 未改。

## 1. 服务端可信来源

现状不变。`apps/panel/src/lib/api.ts` 不在 Vite 构建入口（`index.html` 与 `@` 都指向 `src/react`）。测试仍 `ssrLoadModule('/src/lib/...')`，按死代码保留，未删。

## 2. 准入观察 interim / reasoning

未改。`want_interim_messages` 关闭时核心仍不把 interim 接到 agent。

## 3. 谱系根租约 / 按 session 查 run / CLI 停止

现状：`GET /api/runtime/sessions/{id}/execution` 用 `project_session_execution`。只有谱系根租约、`platform=cli`、本 pidns、cmdline 含 `hermes_cli.main`、非网关 cgroup、不与其他租约共 pid 时才返回可停止描述符。未知 holder 保持 busy 且 `execution=null`，不发信号。

`POST .../execution/stop` 复用 pidfd，代次用 execution id。进程未死返回 504，不删租约。API run 仍走 `POST /v1/runs/{id}/stop` → `stop_run` 的 admission generation。

`bind_run_store` 按 store 文件的 profile home 登记。shim 的 `session_run` 只取当前 `state.db` 同一 home 的 store；没有登记且带幂等键时 503。HTTP `handle_run` 仍用请求 adapter 自己的 store 加 scope，不读这个登记表。内存 store 不登记，不能当跨 profile 兜底。

剩余：sidecar 进程与网关不是同一进程，shim 在 sidecar 进程里通常没有 bind，带幂等键仍 503。React 已改打 API 路由。旧 `src/lib/sidecar.ts` 仍打侧车停止路径，构建不包含它。

## 4. 编辑分支

缺 `display_order` 时先在同一写事务调用 `_reconcile_display_orders`，再要求严格递增。规范化后仍不能唯一定位则 409，不改用时间戳，不降低空闲/内容校验。会话未修改（事务回滚）。

剩余：规范化本身写源会话展示序号；编辑成功才会提交。无法证明严格顺序的历史行仍然不能编辑。

## 8. 历史旁白

`useCommentary` 不再调用 `sidecar.commentary`，只从已缓存的消息 `display_commentary` 投影。React `loadCommentary` 仍读消息字段，未改出字顺序。`sidecar.commentary` 对直接调用仍拒绝；构建内已无调用点。`src/lib/commentary.ts` 仅测试加载，未删。

## 9. 后台进程

未改。非 host pid 不列出。

## 10. 用量 / 上下文

`update_token_counts` 在同一写事务把 `last_api_call` 写入 `state_meta` `session_last_api_call.v1.{session_id}`。写入点：`agent/turn_usage.py`、`agent/codex_runtime.py` 有 usage 的路径。无 usage 的 Codex 调用不造快照。

`GET /api/runtime/sessions/{id}/context` 读该快照。窗口只读配置 `model.context_length` 或 `get_cached_context_length`，不探测、不刮 `agent.log`。没有快照返回 `source=no-data`，不是 0。React `sidecar.context` 已改打该路由。

剩余：网关 absolute 累计写入不带 last call，不会覆盖已有快照，也不会补一条。`run-wait` 仍刮 `agent.log`，不在本路。

## 11. 子代理

`active_count()` 语义未改，仍是完成单元，drain/续跑继续用它。`active_task_count()` 未替换：活着的 batch 在单元结束前仍按全部 goal 计数，部分 child 已在 `sessions` 结束时会多计。

活动页「进行中的子代理」用 `background_queues.active_children`。读失败是 `null`，页面显示「不可用」，不显示 0。列表走 `GET /api/runtime/subagents`，`running`/`total` 不受 `limit` 限制。页面可见时 0 也 15 秒刷新。默认折叠、计时和动画类名未改。

剩余：尚未写入 `sessions` 的刚派发 child 不在这个计数里。完成单元卡片仍是会被异常吞成 0 的 `active_delegations`。

## 12. effort 注册

`api_server.connect` 在 `register_project_routes` 之后、`/p/{profile}/{tail:.*}` 之前调用一次 `register_effort_routes(self._app, self)`。未改 effort 服务和路由实现。

`commit_deferred_effort` 在 `TurnFacadeMixin.run_conversation` 取得租约之后、模型循环之前调用；循环返回、释放租约前再调用一次。没有租约不调用。循环前失败会中止本回合并仍释放租约；循环后失败只记日志。

精确缺口：`_prepare_session_chat` 在准入前把 lock 的 `model_options` 拍进 `run_kwargs`。同一次请求里，围栏内提交赶不上这一拍。上一回合释放租约前的提交，下一次 prepare 能看见。不需要新 effort API，除非要让已经拍好的同请求 runtime_request 改档；那会改当前回合，本路拒绝。

## 父代理安装顺序

1. 不要再改 `native.py`。现有 `browser -> runtime -> admin` 会装上本路替换的 `get_session_execution` / `get_context` / `get_subagents` / `stop_session_execution`。
2. 不要再挂第二遍 `register_effort_routes`。`connect` 已挂。
3. 不要改 `runs.ts`、chat-page、effort 服务、附件路由。
4. 集中测试前不要把 409/503 当成这些项已完成。

## 未改边界

未访问生产 home，未跑测试，未构建，未提交。审批体未改。`absPath` 未改。
