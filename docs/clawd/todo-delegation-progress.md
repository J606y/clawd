# 任务清单子代理执行指示

本文只记录静态核对。测试、构建、浏览器和探针都没有跑。现网清单不会因此自动改状态。

## 根因

核实：

- 面板清单不是子代理运行态。`apps/panel/src/react/pages/chat/chat-page.tsx` 约 573 行只调用 `tasksForSession(listedSession, sessionId, messages)`，折叠当前会话消息里的 `todo_list` 参数。
- `tools/todo_tool.py` 原来只持久化 `id/content/status/parent`。写入时丢掉未知字段，所以即使调用方带了 `delegation_id` 也不会留下。
- `delegate_task` 的任务参数没有 `todo_id`。`tools/delegate_tool_dispatch.py` 的 `_dispatched_payload` 返回批次 `delegation_id`、`units[].delegation_id/task_indexes` 和 `subagent_ids`，没有清单 id，也没有 child session id。
- `hermes_state_subagents.list_subagents` / `_page_row` 只返回会话行：`id/parentId/title/status(running|stalled|done|ended)/endReason` 等。没有 `todoId`、`delegationId`、`profileKey`。`clawd_server/runtime_native.py` 约 255 行原样返回这个结果。面板 `Subagent` 类型与此一致。
- 任务组件原先按 `item.status` 画标志。`in_progress` 已有 `◐`、`aria-label`「进行中」和现有强调色。它没有忽略进行中。
- 因此「父项 in_progress、子项 pending、子代理在做却没有进行中标志」与持久状态一致：子项状态就是 pending。不能用任务名或 goal 把 child 配到某一行。

推测，未用生产库核实：当前父代理遵守了旧说明「同时只许一项 in_progress」，只把自己标成进行中。这不是从数据库读到的事实。

现有参数在投影接线前就能让对应行显示进行中，而且不会把兄弟行一起改掉：

```text
todo_list(merge=true, todos=[{id: "<该子项 id>", content: "<原文>", status: "in_progress"}])
```

存储不拒绝多项 `in_progress`。旧说明才让模型只标父项。这次已改说明：每个真正在做的子项可以各自 `in_progress`，未关联兄弟保持原状态。child 返回后不要把验收 status 改成 `completed`。

`待验收` 无法用旧的四态表达。所以另外加了显式关联和纯投影，没有新表，也没有自动把 child 生命周期写回验收 status。

## 改过的文件

- `tools/todo_tool.py`：可选 `delegation_id` / `child_id` / `delegations`；`project_todo_execution`。
- `apps/panel/src/lib/task-list.ts` 与 `apps/panel/src/react/lib/task-list.ts`：两份必须字节相同。折叠保留关联，丢弃参数里的伪造 `execution`。
- `apps/panel/src/react/pages/chat/task-list.tsx`：只改标志、标签和可访问文案。收起、展开、布局、动画未改。
- 未运行测试：`tests/clawd_todo_delegation/test_execution_projection.py`、`apps/panel/tests/todo-delegation-progress.test.mjs`。

## 状态机

验收 status 仍只有 `pending/in_progress/completed/cancelled`，只由父代理 `todo_list` 写入。投影不改它，也不缓存。

执行指示只在该项有显式 `delegations`，且 scope 的 `sessionId+profileKey` 都非空时出现。匹配只用 `delegation_id` 或 `child_id`。`todoId` 若存在必须等于该项 id。同一 id 被两项占用则该 id 不匹配任何行。别的会话或 profile 的记录忽略。未关联行保持原样，即使本会话还有别的 child。

同一项上多条 link 的折叠顺序：

1. 任一 `running` → `running`（进行中，复用现有 `in_progress` 标志）
2. 任一 `failed` → `failed`（失败，不改成 completed）
3. 任一 `interrupted` → `interrupted`（已中断）
4. 任一 `cancelled` → `cancelled`（执行取消）
5. 缺记录或 `unknown` → `unknown`（状态不明）
6. 任一 `stalled` → `stalled`（疑似卡住）
7. 全部 `returned` → `awaiting_acceptance`（待验收，验收 status 仍是 pending/in_progress）

父代理已经写成 `completed` 或 `cancelled` 的行不再盖执行指示。`done/ended` 不是成功返回；调用方没送规范 `executionStatus` 时按 unknown，不能当成待验收。

关联写入：`delegations` 出现就整组替换，空数组清空。只给一个标量时，已有多条 link 不猜是哪一条，保持不动；只有一条时更新该字段；空字符串清该字段。两边标量都空则清空。省略这些键则保留。

## 仍未接入的派发入口

当前运行中的父代理和现网页面都还不会自动点亮子项。下面三处都没接，缺一处投影就不会出现：

1. 派发 owner：`tools/delegate_tool.py` 的 `DELEGATE_TASK_SCHEMA.tasks.items.properties` 增加可选 `todo_id`。说明写明只接受调用方显式传入，禁止用 goal 文本推断。`tools/delegate_tool_dispatch.py` `_dispatched_payload` 按 child 回显，不要只回批次一个 id：

```text
children[]: { task_index, todo_id|null, delegation_id, subagent_id, child_session_id }
```

`child_session_id` 取 `child.session_id`。`subagent_ids` 不是会话 id，不能直接当 `child_id`。把 `todo_id` 记到该 child 已有的 delegation/registry 记录上。不要新建清单库。本路不改 `tools/async_delegation.py`。

2. 运行 owner `sa-0-8c9a5a62`：`hermes_state_subagents.py` `_page_row` 增加可选字段，仅当记录里真有这些值。`runtime_native.py` 已原样返回 `list_subagents`，那里不必再包一层。`gateway/platforms/clawd_runtime_routes.py` `handle_subagents` 也是原样返回。不要从 title 填 id。

```text
delegationId: string|null
todoId: string|null
profileKey: string
executionStatus: running|stalled|returned|failed|cancelled|interrupted|unknown
```

`executionStatus` 与子代理栏的 `status` 分开，栏上的 `running/stalled/done/ended` 不要改语义。建议映射：未结束且未停滞 → `running`；未结束且停滞 → `stalled`；`end_reason` 为 `agent_close` 或空 → `returned`；取消类 → `cancelled`；中断/杀死/停止 → `interrupted`；错误/失败 → `failed`；其余已结束 → `unknown`。`profileKey` 用这份 SessionDB 所属 profile，不用浏览器当前选中项。

3. 聊天页 owner：`chat-page.tsx` 约 573 行。子代理查询还没返回时不要传空数组，否则已关联行会变成状态不明。加载完成后再传，且只传 `parentId===sessionId` 且 profile 相同的行。不要改 `queries.ts` / `sidecar.ts` 的请求路径；类型上给 `Subagent` 加可选 `delegationId/todoId/profileKey/executionStatus` 即可。

```text
tasksForSession(listedSession, sessionId, messages, executions, { sessionId, profileKey })
```

`executions[]` 使用 `delegationId/childId/todoId/sessionId/profileKey/executionStatus`。`childId` 是 child session id，不是标题。没有 id 的行不要送进投影。

## 聊天页消费（已接线，未跑测试）

`chat-page.tsx` 在子代理查询 `status === "success"` 之后才把 executions 传给 `tasksForSession`。加载中和失败都不传 `[]`。只送 `parentId === sessionId`、有会话 id 或 `delegationId`、且 profile 与 scope 相同的行。`childId` 取行 id，不取 title。栏上的 `done/ended` 不译成 `returned`。验收 status 仍不由页面写入。

给 runtime owner `sa-0-8c9a5a62`：现有 `list_subagents` 响应没有可信 `profileKey`。聊天页不读 URL，不写死 `default`，也不用 profiles 列表的进程档代替 SessionDB。缺字段时 scope 保持 `unknown`，带了别的 profile 的行不会进入投影。请在现有响应上给出视图级 `profileKey`，以及行上的 `delegationId`、`todoId`、`profileKey`、`executionStatus`。没有这些字段，进行中和待验收都不会出现。
