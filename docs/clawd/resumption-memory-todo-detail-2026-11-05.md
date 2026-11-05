# 记忆改绑与任务投影接线复核 — 2026-11-05

静态只读。未运行 pytest、构建、import、CLI 产品探针、浏览器或网络。不是验收通过，不是上线许可。未改源码、测试、公共 manifest 或其他报告。未读生产凭据，未碰原面板或 `/usr/local/lib/hermes-agent`。

对照 `memory-final-wiring.md`、`todo-final-wiring.md`。`agent/turn_facade.py` 与两份已点名测试的 sha256 与那两份文档一致，说明这三份文件自该记录后未改字节。本复核按当前字节重读调用点，不以那两份自报为接通证据。

## 结论

批准范围内的两条链在源码上已接到真实入口，但都未执行验证。

- 空闲改绑在租约准入成功之后、`conversation_loop.run_conversation` 之前。面板发送走 `POST /v1/runs`，该处理函数调用 `AIAgent.run_conversation`（即 facade）。解绑只留全局；不沿祖先回绑。子代理工具写在审批门之前被只读拒绝。
- 项目记忆 HTTP 使用活动 `SessionAuthority`、profile、会话、绑定项目；带上的 `execution_generation` 必须是整数且为当前 claim。缺身份拒绝。没有记忆页面调用客户端方法，不能称 UI 已接。
- 子代理投影链是 `list_subagents` → 已注册的 `handle_subagents` → `panel-api` 字节转发 → 聊天页只在查询 `success` 且 profile 可证时投影。`returned` 不写验收 `completed`。profile 证明不了就省略字段，聊天页不传 executions，不写 `default`。

函数存在不算接通。下面按调用点区分已接到入口、已实现未验证、以及仍缺的口。

## 1. 空闲改绑与真实循环入口

`AIAgent` 的 `run_conversation` 来自 `TurnFacadeMixin`（`run_agent.py:214-217`）。`chat()`（`agent/turn_facade.py:500-502`）只是再调它。

准入成功后的顺序：

1. `admit_durable_turn_lease`（`turn_facade.py:354-357`）。`early_result` 在 358-367 返回，不改记忆。
2. 仅 `facade_owner`（349、374-376）调用 `_idle_project_memory_retarget`。嵌套入口不碰外层回合，也不清 live turn id。
3. 然后 `from agent.conversation_loop import run_conversation`，在 426 行调用。这是循环模块的函数，不是同名包装。
4. `conversation_loop.run_conversation`（1802）进入 `_run_conversation_turn`，1693 行调用 `build_turn_context`。
5. `turn_context.py:1152` 才 `_bind_turn_identity` 写入 `_current_turn_id`。1208-1209 仅当 `_cached_system_prompt is None` 才 `restore_or_build_system_prompt`。

因此刷新在本回合 turn id 和提示恢复之前。Codex app-server 在 `_turn_without_loop`（`conversation_loop.py:1611-1637`），发生在 `build_turn_context` 之后，所以也在这次刷新之后。不是绕过 facade 的第二入口。

面板产品发送不是 `POST /api/sessions/{id}/chat`。聊天页经 `apps/panel/src/react/lib/api.ts` / `runs.ts` 发 `POST v1/runs`（`api.ts:397`）。`APIServerAdapter._handle_runs`（`api_server.py:3923`）转到 `api_server_runs.py:805`，执行体在 1032 行调用 `agent.run_conversation`。注释写明该路径不经过 TurnRunner。静态上，面板回合会进 facade 刷新。

另有网关 `run_turn_runner.py:1033` 同样调用 `agent.run_conversation`。复用 agent 的空闲刷新主要靠这条和面板 `/v1/runs` 的新 agent 上仍会执行的同一 facade。

`api_server.py:3815` 的 `agent.run_conversation` 不是权威会话聊天的活路径。`_run_agent` 在 `session_authority` 存在时于 3725-3740 转入 `run_api_turn`，到不了 3815。权威 drain（`session_authority.py:1302` → `session_finite.py:71` → `session_ingress.py:277`）本复核没有一路追到 `run_conversation`。不要把 3815 说成面板或权威会话的入口。

改绑行为（`turn_facade.py:203-286`，安装在 154-201）：

- profile 先取 `session_db.db_path` 父目录（`state_db_profile_home`，`inheritance.py:29-36`）。没有可用路径才用冻结记录，最后才是 `get_hermes_home()`（19-42）。解析期间设置 home override，结束复位。
- `projects.db` 不存在则直接返回（230-233）。已冻结时不把缺失当成解绑；无冻结时也同样返回，不会剥已有复合 store。
- `resolve_session_project`（`service.py:124-134`）只读本会话绑定，不走 `parent_session_id`。异常时保留冻结范围并继续本回合（247-249），不是拒绝。
- 绑定、home 或嵌套变了才安装。安装前剥到最内层 global，再调 `_attach_project_context`。显式解绑后再剥一次，清空 `project_id` / `project`；仍有项目 store 则抛出。
- 安装失败回滚 store、freeze、project、提示后重新抛出（283-286）。本回合不会带着半套新范围进入循环。
- 子代理：`is_subagent_memory_actor` 为真，或传入 readonly 时，`mark_memory_store_readonly`；若 attach 把 freeze 标成可写，再冻成不可写（183-196）。`memory_tool._memory_tool` 在 `evaluate_gate` 之前拒绝只读 store（`memory_tool.py:274-277`，门在 292-293）。不把子代理写成可写。
- 变化后重建提示并尽量 `update_system_prompt`。缓存不是 `None`，循环不会用旧行覆盖。持久化失败只打日志，本回合内存是新字节。

`agent/agent_init.py:1234-1240` 在 `proj is None` 时不卸复合 store，也不清 `agent.project_id`。当前调用点只有初始化（1337）和空闲路径。空闲路径在调用前后补偿。第三个直接再 attach 的调用方仍会踩这个缺口。本复核未改该文件。

HTTP 管理编辑不走 child 只读。`handle_update_project_memory` 不调用 `evaluate_gate`，也不看 `platform==subagent`。这与“管理编辑无需 agent 审批、agent 写仍审批”一致。未发现子代理产品路径去打这个 POST；没有证明子代理持有 API 密钥时也打不进去。

## 2. 项目身份：authority / profile / generation

路由已注册，不是只导出了函数。`APIServerAdapter` 启动时 `register_project_routes`（`api_server.py:4018-4019`）。`clawd_project_routes.py:619-626` 注册 `GET/POST /api/projects/{id}/memory`，并加 `/p/{profile}` 镜像。

`_memory_principal`（111-135）取 `require_active_authority`。无 runner 或 authority 不可用 → 401 `missing_identity`。`profile_mismatch` → 403。`require_active_authority`（`session_authorities.py:216-228`）没有活动 authority 或 store 不是该 authority 的库时拒绝，不填 `default`。

`authorize_user_project_memory_write/read`（`memory_authorization.py:136-222`）：

- 要 subject、profile、capability。principal profile 必须等于 authority profile。home 来自 authority 的 `state.db` 父目录，不是 URL，也不是同项目第一条会话。
- `generation is None` 允许空闲管理编辑。带上则必须是 `int`，并调用 `check_approval_generation`。非 int、不可核、或 stale → 拒绝。查询串才把纯数字解析成 int；JSON 字符串不强制转换（`clawd_project_routes.py:98-108`）。
- 会话必须绑定到路径中的项目，否则 `not_bound` / `project_mismatch`。
- 读写都会再授权一次。读授权不能被 apply。不写全局 `MEMORY.md` / `USER.md`。

面板客户端 `projectsApi.memory`（`apps/panel/src/react/lib/projects.ts:223-233`）和 `projectMemoryScope`（`project-model.ts:171-186`）会拒绝空会话，也不把非整数 generation 转成数字。全 `src/react` 没有页面调用 `.memory.read` / `.memory.update` 或 `projectMemoryScope`。没有项目记忆页。客户端函数存在不是 UI 已接。

## 3. 子代理投影链

已接通的静态路径：

1. 派发把 todo 写入 child `model_config`（`delegate_tool.py:579`），把 `delegation_id` 再写入（`delegate_tool_dispatch.py:415`）。回执 `child_links` 用 `child.session_id`，不是 `subagent_id` 或 `task_index`（`delegate_tool.py:533-544`，`delegate_tool_dispatch.py:431`）。`patch_session_model_config` 失败被吞（`delegate_tool.py:527`）；回执仍可补 id。两边都没有则列表省略 id，图标不亮。
2. `list_subagents`（`hermes_state_subagents.py:352`）读已打开 SessionDB。`profileKey` 只来自 `_own_profile_name`（162-182，实现在 `hermes_state_sessions.py:340-355`）。默认根被库证明为 `default` 才输出 `default`。树外或异常为 `None`，响应省略字段。不读进程 home、最后 adapter 或 URL。
3. `delegationId` / `todoId` 按 `child_session_id` 且回执父会话等于行父会话合并（210-277、344-348）。冲突或别的父会话不覆盖。`subagent_id` / `task_index` 不当身份。
4. `executionStatus`（185-198）：未结束 `running`/`stalled`；`agent_close` 或空 `end_reason` 为 `returned`；`end_reason==completed` 映射为 `unknown`，不是 `completed`。栏上 `status` 仍是 `done`/`ended`。
5. `http_routes` 含 `GET /api/runtime/subagents`（`clawd_runtime_routes.py:184`）。`_http_route_table` 在适配器启动时 `add_route`（`api_server.py:1553`、1592-1593、4015-4017）。`handle_subagents`（395-418）在 `_auth` 之后原样返回 `list_subagents`，不改字段。
6. 面板 `request()` 默认 `API_BASE=panel-api/`（`api.ts:4,76`）。`sidecar.subagents` 不传 sidecar base（`sidecar.ts:471-478`），并要求 `data` 为数组且 `running`/`total` 为 number，否则抛错，查询不会变成 success。`PanelServer` 把 `/panel-api/{tail}` 字节转到 `http://127.0.0.1:{port}/{tail}`，保留 query string，不改 JSON 体（`clawd_server/server.py:158,194,277-326`）。`api_base` 无额外路径前缀，tail `api/runtime/subagents` 对得上网关路由。
7. 聊天页 `useSubagents`（`queries.ts:328-339`）→ `taskItems`（`chat-page.tsx:651-671`）。只有 `status===success` 且 `serviceProfileKey` 不是 `unknown` 才把 executions 交给 `tasksForSession`。加载、失败、无 data、profile 不可证都不传 `[]`。`serviceProfileKey`（122-136）不写 `default`。行上无 `profileKey` 或与 scope 不一致的行不进 `taskExecutions`（146-165）。结果交给 `ComposerPanels`（1095），不是死计算。

`tasksForSession`（`task-list.ts:437-448`）在 executions 或 scope 缺省时只折叠清单，不投影。`projectTaskExecution`（517-583）复制验收 `status`，只附加 `execution`。全部 `returned` 折成 `awaiting_acceptance`（471-482），不是 `completed`。同一 delegation/child id 被两项占用则 `unknown`。`taskIndicator`（593-610）在待验收时标记仍是 `pending`，不把 `item.status` 写成 `completed`。这条链没有把 `returned` 写回 todo 验收状态。

`tsconfig.react.json` 把 `@/*` 指到 `src/react/*`。聊天页用的是 `src/react/lib/task-list.ts`。`src/lib/task-list.ts` 不在这条 import 上。未逐字节比较两份副本。

旧侧车 `GET /subagents`（`apps/panel/sidecar/hermes_panel_sidecar.py:4933,6087`）仍直接读 `state.db`，响应没有 `delegationId`/`todoId`/`executionStatus`/`profileKey`。聊天页不调用它。`scripts/verify_clawd.py:184` 仍打这个旧路径。它不是投影链，也不能代替投影链。未删除。

聊天页 `limit=50`，服务端上限也是 50（`hermes_state_subagents.py:20,356`），且不跟 offset。超过 50 个子会话不会进入这次投影。

## 4. 已实现未验证 / 真实遗漏

已接到入口、未运行，不能当通过：

- facade 刷新顺序、解绑不抄祖先、子代理只读、session db home 胜过进程环境：`tests/clawd_memory_native/test_turn_rebind_context.py`。该测试把 `agent.conversation_loop.run_conversation` 换成假函数（70 行），所以它不执行真实循环体。真实入口是上面的静态调用，不是这条测试跑过。
- 归档/跨 profile/错项目/严格整数 generation：`tests/clawd_memory_scope_cases/`、`tests/clawd_projects/test_project_api.py:218-348`。未跑。
- `list_subagents` 的 id、冲突省略、别的父会话、`executionStatus != completed`、不可证不发明 `default`、库证明的 `default`：`tests/clawd_todo_native/test_projection_wiring.py`。它直接调 `list_subagents`，不经过 `handle_subagents`、代理或聊天页。

真实遗漏，不是“文件里有函数”：

- 无项目记忆页面。`projectsApi.memory` 无页面消费者。
- 聊天页不翻页，第 51 个及以后的 child 不投影。
- `db_path` 缺失时改绑回落到冻结 home，再回落到进程 home，不是拒绝。无 `projects.db` 时有复合 store 且无冻结也不解绑。`resolve_session_project` 失败保留旧范围并继续回合。
- `agent_init._attach_project_context` 的不解绑缺口仍在，只是当前两个调用点里空闲路径做了补偿。
- 权威会话聊天（`run_api_turn`）未追到 `run_conversation`。不要用 3815 行冒充该入口。
- 旧侧车 `/subagents` 仍在，且不含投影字段。聊天投影不依赖它。
- 没有覆盖 `handle_subagents`、`panel-api` 转发和聊天页 success/unknown 门的测试。静态注册不能代替请求。

## 5. 若以后修，精确文件

本复核不改这些文件。

| 缺口 | 文件 |
| --- | --- |
| 无 `db_path` 时拒绝而不是进程 home；无 `projects.db` 时是否解绑无冻结复合 store | `agent/turn_facade.py` |
| 第三个 attach 调用方也会不解绑 | `agent/agent_init.py` 的 `_attach_project_context`；空闲补偿仍在 `turn_facade.py` |
| 超过 50 个 child 要翻页 | `apps/panel/src/react/lib/sidecar.ts`、`chat-page.tsx`；列表侧已有 offset，不必先改 `hermes_state_subagents.py` |
| 记忆页面 | 面板页面 owner。不要把 `projects.ts` 的方法当成页面已接。公共路由已注册，不要再改 `clawd_project_routes.py` 来“补 UI” |
| 旧 `/subagents` | `apps/panel/sidecar/hermes_panel_sidecar.py`。不是聊天投影链。未授权不删除 |
| 补测试 | 只扩展既有 `tests/clawd_memory_native/test_turn_rebind_context.py`、`tests/clawd_todo_native/test_projection_wiring.py`。聊天页门目前没有对应用例，新增前要单独定 owner |

## 6. 最终验收命令与依赖

未执行。退出码未知。空结果、未跑、阻塞都不是通过。

隔离 `HOME`、`CLAWD_HOME`、`HERMES_HOME`、`TMPDIR`。不要指向生产 Hermes home，不要读生产 `state.db` 或凭据。

```sh
scripts/run_tests.sh -j 1 --file-timeout 180 \
  tests/clawd_memory_native/test_turn_rebind_context.py \
  tests/clawd_memory_scope_cases/ \
  tests/clawd_projects/test_project_api.py \
  tests/clawd_todo_native/test_projection_wiring.py
```

这组命令不覆盖 `handle_subagents`、`panel-api` 代理和聊天页 success/unknown 门。那些需要面板类型检查/构建和浏览器或 HTTP 用例，依赖已构建的面板静态资源和 loopback 上的 clawd API，且必须用隔离 home。缺这些依赖时应标 blocked，不能用模拟响应冒充端到端。

外部依赖：统一验收时的隔离 Python 环境与 `scripts/run_tests.sh`。不需要生产模型、MCP 或生产浏览器才能先跑上面的库内用例。面板浏览器验收另需构建产物和隔离服务，本复核没有启动。

## 7. 当前哈希

`sha256sum`，工作目录 `/root/hermes-workspace/clawd`。不是测试结果。

```
0c1118543afeb3c2cf01da10fcda4ecedc2476af929390a21eb3939212e7dad2  agent/turn_facade.py
bc1a44a42e254c9500cee099aa0d81590534aecb0ae6986f0f5bce6f24849d49  agent/conversation_loop.py
812d5019ecff07906e72f8baf8450cf9d6701046203f9519c17a21aa728ab213  agent/turn_context.py
679879b4b12a63b25073536b8229bc29050efa4017eaa5c95a145c3ec8c62af5  agent/agent_init.py
3cd3c63842d0ebdc229cbc7a50edc48347c377b903fa9008d44ec37de7b5db9a  clawd_projects/inheritance.py
ddaf2bfdeeed107f34e86be5a23134e625e950b56ee6609416afb10a3116c93c  clawd_projects/memory_authorization.py
dfb354f3b45b44922b16dff29f2fcb2e3db34a123e24fafb2959ce637164856e  gateway/platforms/clawd_project_routes.py
bc479c5c767f8d0bbfcc4110135526d2b92eda574113e23f0338fa988c70c8c0  gateway/platforms/clawd_runtime_routes.py
b95284ed570ea9f3ed4f0218af12eff91891373a32a93f60f89658b43ba839de  gateway/platforms/api_server.py
554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b  gateway/platforms/api_server_runs.py
90c986818dabf6dffc94dc45c98f309a50cd1aa8121f2101bb6eda2e39f840b6  gateway/run_turn_runner.py
da2ecbe540062b8e4bbc35f55eab8c4fa791e1cb2b28c3832fc7c01e4fa619c6  hermes_state_subagents.py
c149236afa6f58cab44a50d1e32f0e9f9d4f7883770aa0d5d7a0d7bf9d0ada1c  hermes_state_sessions.py
ea3534ab92e054fe38c79f6533b8797c86dea07facdfb09d3badc5a060dd03ab  clawd_server/server.py
6d0d7bbb853fafffacebff8ee246a364aa24f45b23aba2108975bd6a1f754a97  tools/memory_tool.py
aa5bb5d981cbbcdd8438920da59b94be1ee6817bb56a23c86bd96572d484a2ad  tools/delegate_tool.py
bdcb2fd7fc50f4c69102dc13a74f2038c585a9e8dcc8c2729c5504727ecf41fc  tools/delegate_tool_dispatch.py
a209be6367d8f1d679fabf59300b51e5c09dbfbd9d1bbcbf8c69ee3cf750d029  apps/panel/src/react/pages/chat/chat-page.tsx
78eee2c693b40d7df246dc85cdab98bd2d40f7a8d608d6a9c33636b850e296f0  apps/panel/src/react/lib/sidecar.ts
19f9dbc00320ff67c0391eca1e5147a7a8817a9d4118b7cc4065c64243c5fa40  apps/panel/src/react/lib/task-list.ts
542ce9bb436bd44ca86f06644ae38dd954ccdf87b7bf13a4abb009b4af775b0a  apps/panel/src/react/lib/queries.ts
8f5d636859312304f1f3efae64f5cbd136f0a6a308364592f7d4672fc816df00  apps/panel/src/react/lib/api.ts
ee9a8b4e90bd7e85b1fd7edcc08a8564c4ad2b8688c6d35368bf422b0dd07c88  tests/clawd_memory_native/test_turn_rebind_context.py
b4cde9c6c32f75858c03298b007c5b76aa170854dd76fe98b5431e01ce6ab466  tests/clawd_todo_native/test_projection_wiring.py
b8167a4356b39664872f5732eb7b2c64867caff42a4bb7a90afe9879b464c113  tests/clawd_projects/test_project_api.py
6fa2b1edca5e9c578a8038b2b9be6d899972b9832b1f05e7ee190175128280da  tests/clawd_memory_scope_cases/test_write_freeze_archive.py
```
