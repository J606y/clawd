# 项目记忆写入授权

未运行 pytest、构建或探针。未改 `projects_db.py`、`migration.py`、`agent/turn_facade.py`、`agent/turn_facade_lease.py`、`gateway/session_api_turn.py`。迁移路由未改。

## 已核实

`POST /api/projects/{id}/memory` 原先只校验 Bearer，再按 URL 中的 project id 调用 `apply_batch`。没有 profile 会话权威、没有绑定行、没有 generation。这是代码事实，不是未跑用例的结论。

用户显式编辑项目长期记忆的原契约不走 `memory.write_approval` / `evaluate_gate`。那道门只约束 agent 的 `memory` 工具。给管理接口加暂存会让用户改不了自己的项目记忆，因此没有加。

`invoke_tool` 对 inline `memory` 不查 `valid_tool_names`。子代理只读的真实门禁是 `DELEGATE_BLOCKED_TOOLS` 去掉工具面，以及回合内 `validate_tool_calls`。工具实现另在只读 store 上拒绝写入，避免把直接调用当成门禁。

## 已接入

`gateway/platforms/clawd_project_routes.py` 只改了两个记忆 handler：

- `GET /api/projects/{id}/memory?session_id=` 调用 `authorize_user_project_memory_read` 和 `load_authorized_project_memory`。
- `POST` 体需要 `session_id`、`operations`，可选整数 `execution_generation`。调用 `authorize_user_project_memory_write` 和 `apply_authorized_project_memory`。
- 两者仍先走原 `_auth`。再取 `adapter.gateway_runner` 的 `require_active_authority`。主体是现有 `Principal("api", authority.profile_id, {session:read, session:control})`，不是新 token。
- 缺 runner、错 profile、会话不在该 profile、未绑定、项目不符、generation 不是 int 或不是运行代次，都拒绝。不读 body 里的 home。
- 不调用 `evaluate_gate`。

导出：`authorize_user_project_memory_write`、`authorize_user_project_memory_read`、`apply_authorized_project_memory`、`load_authorized_project_memory`、`MemoryAuthorizationError`、`authorization_error_body`。

## 审批语义

- 用户管理写：`approval=not_required`。人就是编辑者。空闲可不带 generation；带了就必须是 `check_approval_generation` 的当前运行代次。
- agent 工具写：仍走 `evaluate_gate`。只读 store 在暂存之前拒绝，不把子代理写入送进 pending。
- 运行中 `_current_turn_id` 已设置时，再次 `_attach_project_context` 不替换 store、project id。schema 封印后同一 turn id 不重建。提示快照第一次 `load_from_disk` 后不再被第二次 load 替换。
- 没有进程级项目记忆缓存。`profile_home_key` 只标记该 store 的 home。

## write_token

`service.py` 仍只消费已有 token 接口，不铸造 token。当前 `hermes_cli/projects_db.py`（只读核对，未改）已经是约定签名：

- `bind_session_project(conn, session_id, project_id, *, write_token=None, expected_write_token=None)` 返回 `SessionBindingWrite(applied, write_token)`。
- `get_session_write_token(conn, session_id)` 存在。`None` 表示未绑定或旧行 NULL，不是“读口还没有”。

`commit_session_binding` / `_write_binding` 按 `inspect.signature` 转发这两个关键字。读回调用 `get_session_write_token(conn, session_id)`。属性缺失或签名不是这个契约时仍 `BindingTokenUnsupported`，不把缺读口说成 token 为空。不是 pending。未跑读取用例。

## 消费者

面板没有项目记忆页面，也没有调用 `/api/projects/{id}/memory` 的组件。未新造 UI，未改样式、布局、动画。普通模式全局记忆的入口不在这两个文件里，未改。

现有客户端是 `apps/panel/src/react/lib/projects.ts` 的 `projectsApi.memory`，身份判断在 `project-model.ts` 的 `projectMemoryScope`：

- 必须由调用方传入当前 `sessionId`。不读 `sessions()` 的第一条，不用 URL 项目 id 当会话，缺会话不发请求、不创建、不绑定。
- `execution_generation` 只有已经是整数才放进 POST JSON 数字。字符串不 `Number()`、不丢弃后当空闲写入。
- 拒绝原样带上服务端 `error` / `message`。`401 missing_identity` 不广播登录失效。`401 unauthorized` 仍广播。
- 409 不走 `projectActionError`（那句文案只表示不能改归属）。

记忆 HTTP handler 已挂身份门，本轮未改 `clawd_project_routes.py`。

## 未验证

本文件描述的测试没有执行。`tests/clawd_projects/test_project_api.py` 的记忆用例已改为真实 `SessionAuthority`、实际绑定和 `session_id`，并保留未授权、`profile_mismatch`、过期代次、JSON 字符串代次拒绝。未 skip，未放宽门禁。未运行。

`tests/clawd_memory_scope_cases` 仍有不带 `session_id` 的 `GET /api/projects/{id}/memory`，且 `API_MEMORY_WRITE_BLOCKER` 仍写“只按 project id 写入”。该目录不在本路，未改。跑验收前会失败，不能当成记忆门已覆盖。

未把 turn 开始时的空闲重绑接进 `turn_facade.py`。API 按现有注释每回合新建 agent，会重新 attach。长期复用同一个 agent 对象时，空闲改绑要等下次构造，或由 turn 文件主人在设置 `_current_turn_id` 之前调用 `_attach_project_context`。本路不改那三个文件。
