# 任务执行投影接线

静态核对。未跑测试、构建、探针，未访问生产库，未改验收 status，未改 `todo_tool.py` 与两份 `task-list.ts`。

## 已存在

派发把 `_clawd_todo_id` / `_clawd_delegation_id` / `_clawd_subagent_id` 写入 child 的 `model_config`，并把 `child_session_id`（`child.session_id`，不是 `subagent_id` 或 `task_index`）放进 `async_delegations.task_json.child_links`。`handle_subagents` 在 `_auth` 之后原样返回 `list_subagents`。`sidecar.ts` 的 `Subagent` / `SubagentsView` 已有可选 `delegationId` / `todoId` / `profileKey` / `executionStatus`，请求路径未改。`tasksForSession` 已按显式 id 投影：同一 id 被两项占用不匹配；全部 `returned` 才是待验收；不改验收 status。聊天页成功后才传 executions，加载和失败不传 `[]`。

## 这次补上的缺口

`list_subagents` 原先只读 `model_config`，响应没有视图级 `profileKey`，身份来自更宽的 `profile_name_for_home`。现在：

- `profileKey` 只来自这份已打开 SessionDB 的 `_own_profile_name`（`db_path`）。默认根被库证明为 `default` 才输出 `default`。证明不了就省略，不读进程 home、最后 adapter、URL，也不把空身份写成 `default`。
- 行上的 `id` 仍是 `sessions.id`。回执只按 `child_session_id` 且 `parent_session_id` 等于该行父会话来补 `delegationId` / `todoId`。`subagent_id` 和 `task_index` 不当身份。两边冲突的字段省略。同一 child session 被多条不一致回执占用时，不输出这两个 id。别的父会话的回执不覆盖本行。
- `executionStatus` 仍是：未结束 `running`/`stalled`；`agent_close` 或空 `end_reason` 为 `returned`；取消、中断、失败保持原类；其余已结束为 `unknown`。不用 `completed`。栏上 `done`/`ended` 未改。
- 视图级 `profileKey` 与行上相同，且只在库能证明时出现。请求的 parent 再滤一次，不返回别的父会话的 child。
- 聊天页在 scope 为 `unknown` 时不传 executions。行上没有真实 `profileKey`，或与 scope 不一致，不进入投影。`childId` 取行 `id`。`sessionId` 仍是当前父会话，因为 `tasksForSession` 用它做 scope；child 会话 id 在 `childId`。

`handle_subagents` 与 `sidecar.ts` 已能原样携带这些字段，未改。

## 到图标的条件

库能证明 profile，且清单项有显式 `delegation_id` 或 `child_id` 时，进行中和待验收才会出现。库证明不了身份时，图标不亮，这是拒绝猜测，不是缺类型。旧的无关联行不猜标题，验收 status 不由这条链写回。

## 品牌

`chat-page.tsx` 全文没有 Hermes。报告里的 982 行现在是 `MessageList`。用户可见的继续提示在 1078 行，已经是 clawd。未改文案、注释、协议、路径、动画和布局。`sidecar.ts` 未做品牌替换。

## 未运行

`tests/clawd_todo_native/test_projection_wiring.py` 已准备，未执行。
