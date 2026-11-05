# 任务投影分页收口 — 2026-11-05

静态实现和接口核对。未运行本报告列出的测试，未运行 pytest、构建、import、CLI 产品探针、浏览器或网络。不是验收通过，不是上线许可。未 commit、未 push、未部署。未删文件。未读生产凭据，未碰原面板或 `/usr/local/lib/hermes-agent`。未改样式、动画、聊天页、后端、设置页或其他 lib。

公共集成人 `sa-0-e7b38dac` 已结束。其 MCP、模型、设置类型和查询接线保留，本收口没有覆盖那些函数。

## 结论

第 51 个及以后的 child 不再停在单次 `limit=50`。现有 `useSubagents` 调用仍返回原来的 `SubagentsView`，`data` 是按 child 会话 id 收齐后的完整列表。调用方不用改。

收不齐就抛错。不把半份列表、空数组或失败响应当成完整投影。profile 证明不了就不写 `profileKey`，也不写 `default`。`executionStatus: "returned"` 和栏上 `status` 原样通过，不改验收状态。

## 服务端契约（只读核对，未改）

`hermes_state_subagents.py`：

- `_MAX_LIMIT = 50`。`list_subagents` 要求 `limit` 为整数且 `1 <= limit <= 50`，`offset` 为整数且 `>= 0`。否则 `invalid_params`。
- `limit` 不截断 `running` / `stalled` / `total`。这三项是当前 parent 的全量计数。
- 页数据按 `(ended_at IS NULL) DESC, started_at DESC` 再 `LIMIT/OFFSET`。
- 响应字段：`data`、`running`、`stalled`、`total`、`now`、`limit`、`offset`。`profileKey` 只有 SessionDB 能证明时才出现。证明不了就省略，不发明 `default`。库证明的 `default` 会原样出现，客户端不剥掉。
- 行身份是会话 `id`。`delegationId` / `todoId` 可缺。`executionStatus == "returned"` 不是验收 `completed`。栏上 `status` 仍是 `done` / `ended`。

`gateway/platforms/clawd_runtime_routes.py` 的 `handle_subagents`：

- `GET /api/runtime/subagents`
- query：`parent`、`limit`（缺省 12）、`offset`（缺省 0）
- 非法分页参数 400 `invalid_limit`
- 原样返回 `list_subagents`。`running` 不是 int 或 `data` 不是数组则 503，不改字段。

面板 `request()` 走 `panel-api/`。`PanelServer` 把 `/panel-api/{tail}` 转到网关并保留 query string。本收口不改这条转发。

## 完整契约

入口仍是 `useSubagents(sessionId, limit, refetchInterval, enabled)`，返回类型仍是 `UseQueryResult<SubagentsView>`。query key 仍是 `["subagents", sessionId, limit]`。`refetchInterval` 仍由调用方传入，这里不新增定时器或自激轮询。

`queryFn` 改为 `sidecar.collectSubagents(sessionId, limit, signal)`。它按服务端页大小反复调用同一个 `GET api/runtime/subagents`：

- 页大小是调用方 `limit`，且钳到 `1..50`。聊天页传入的 50 就是服务端上限。不再发送会被拒绝的 `limit > 50`。
- `offset` 为 `0, pageSize, 2 * pageSize, ...`。0 不放进 query，与服务端缺省 0 相同。之后的页显式带 `offset`。
- `parent` 仍是原来的 `sessionId`，空字符串仍是全局列表。
- 一次查询内的每一页 `total` 必须相同。`seen` 的 child 会话 id 数等于该 `total`，且没有重复 id，才返回。
- 返回的仍是 `SubagentsView`。`data` 是按服务端顺序拼起来的完整 child，不是单页。`running` / `stalled` / `total` / `now` 取这次收齐的最后一页。`limit` 是实际页大小，`offset` 写 0，表示这是收齐后的集合，不是某一偏移页。
- `total === 0` 且第一页为空，是合法的完整空快照，不是失败抹除。
- 每一页的 `profileKey` 和每一行的 `profileKey` 都是同一个非空字符串时，才保留该字段。服务端给出的 `default` 会保留。缺省、空串或页间不一致时，响应和行上都不留 `profileKey`。聊天页现有的 `serviceProfileKey === "unknown"` 因此拒绝投影。本收口不改聊天页。
- 行上的 `executionStatus`、`status`、`delegationId`、`todoId` 不改写。`returned` 不会被写成 `completed`。

稳定身份只看 `Subagent.id`（child 会话 id）。不用 `delegationId`、`todoId`、`task_index` 或标题。成功结果里每个 id 只出现一次。

直接调用 `sidecar.subagents(sessionId, limit)` 仍是单页，给 agent 码头的 `limit=30` 查找用。它不收齐。第 51 个 child 的投影走 `useSubagents`，不走这个单页方法。

## 失败契约

不能证明收齐时抛错，promise 不解析成 `{ data: [] }` 或半份 `data`。TanStack Query 5 的失败 reducer 保留已有 `data`，并把 `status` 设为 `error`。聊天页只在 `status === "success"` 时投影，所以失败不会用空数组覆盖上一份成功投影。

| 情况 | 行为 |
| --- | --- |
| 某一页请求失败、形状不对、`now` 不可用、回显的 `limit`/`offset` 与请求不符、行没有可用 `id` | 立刻抛错。不把已拿到的页当成功。不在收集器里重试传输错误。 |
| `total` 在页间变化 | 丢掉这次快照，从 offset 0 重读。最多 `SUBAGENT_MAX_ATTEMPTS`（3）次。仍不稳定则抛 `subagents_incomplete`。不合并两次快照。 |
| 空页出现在 `seen.size === total` 之前 | 同上，整表重读；用尽则抛错。不把前面的页当完整。 |
| 同一 child id 再次出现，或整页都是已见过的 id | 视为快照移动或 offset 没前进。整表重读，有界后抛错。不把去重后的脏集合当完整。 |
| 短页且已见 id 数小于 `total` | 不能证明后面没有被跳过的行。整表重读，有界后抛错。 |
| `total > SUBAGENT_MAX_CHILDREN`（2000） | 看完第一页的 `total` 后立刻抛 `subagents_incomplete`。不继续翻。 |
| `limit` 不是大于 0 的整数 | 抛 `subagents_invalid`。不发请求。 |
| 查询 signal 已中止 | 不再要下一页。不解析半份。 |
| profile 证明失败 | 不因此把 child 列表变成失败空数组。省略 `profileKey`，让现有聊天页拒绝投影。 |

没有 `while (true)`，也没有新的 `setInterval`。页数上限是 `ceil(2000 / pageSize)`。重读上限是 3。React Query 原有的查询重试没有在这里改，也没有另加一轮轮询。

活动页 `useSubagents("", 12)` 和子代理栏的现有调用也会拿到完整集合，因为它们走同一个 hook。这是“现有调用直接消费完整数据”的结果，不是新页面。栏和活动页会列出多于原来 12/50 的卡片。没有改它们的样式。全局历史超过 2000 时这次查询失败，而不是退回只显示 12 条并假装完整。

## 调用点

未改这些调用，只改它们拿到的数据：

- `apps/panel/src/react/pages/chat/chat-page.tsx`：`useSubagents(sessionId, 50, ...)`。成功且 `serviceProfileKey` 不是 `unknown` 才把 executions 交给 `tasksForSession`。加载失败、无 data、profile 不可证仍不传 `[]`。
- `apps/panel/src/react/pages/chat/subagents-bar.tsx`：`useSubagents(sessionId, sessionId ? 50 : 12, ...)`。
- `apps/panel/src/react/pages/activity/subagents.tsx`：`useSubagents("", 12, poll)`。
- `apps/panel/src/react/docks/agent-dock.tsx`：仍是 `sidecar.subagents(sessionId, 30)` 单页。不在本次投影链里。

`tasksForSession` / `projectTaskExecution` 未改。全部 `returned` 仍折成 `awaiting_acceptance`，不写验收 `status`。

## 保留的设置接线

未改这些实现，只核对它们还在：

- `sidecar.ts`：`settings`、`models`、`setDefaultModel`、`cronModel`、`mcp` 及相邻设置方法。
- `queries.ts`：`useSettings`、`useModelsAdmin`、`useMcpServers` 及相邻查询。MCP / 模型 / 设置类型没有重写。

## 用例

文件：`/root/hermes-workspace/clawd/apps/panel/tests/subagent-pagination.mjs`

未运行。退出码未知。下面是文件里准备的边界，不是执行结果。

- 源码锁定：`useSubagents` 调 `collectSubagents` 并继续把调用方的 `refetchInterval` 传下去；没有 `setInterval`。聊天页仍传 50。码头仍是单页 30。设置 / 模型 / MCP 查询还在。请求路径含 `api/runtime/subagents`，非 0 offset 会写入 query。
- 51 个 child：请求 offset 0 和 50，结果长度为 51，第 51 个 id 在列里。`executionStatus` 仍是 `returned`，`status` 仍是 `done`。
- 恰好 50 个：只请求一页，不多要一页。
- 服务端证明的 `default` 保留。缺省 `profileKey` 不补 `default`。
- 页间 profile 不一致：child 仍收齐，但视图和行上都没有 `profileKey`，投影门不能从行上找回。
- `total === 0` 的空页是成功空快照，不是失败抹除。
- `total` 变化：丢掉第一页的半份，重读成功后不包含过期 id。
- 重复页、提前空页：请求次数等于 `3 * 2`，拒绝，错误对象不是带 `data` 的成功视图。
- 第二页抛错：只请求两页，立刻拒绝，不解析空数组。
- `limit` 为 0、负数、非整数：零次请求，`subagents_invalid`。`limit=80` 按 50 翻页，第 51 个仍在结果里。
- `total` 为 2001：只看第一页就拒绝，不把那一页当全部。
- 空 id、回显 offset 不符：契约失败，不重读成一份去重成功。
- 相同 `delegationId`、不同 `id` 都保留。同一页重复 id 不发布为完整。
- 已中止的 signal 不再请求。第一页之后中止不解析那一页。
- 线路：`collectSubagents` 打到 `/panel-api/api/runtime/subagents`，第一页不带 offset，第二页 `offset=50`，结果 51 条。后一页 503 时拒绝，不是空成功。

## 未做

- 未运行 `apps/panel/tests/subagent-pagination.mjs`。未跑 `scripts/run_tests.sh`、面板类型检查、构建、浏览器或 HTTP 探针。
- 未改 `hermes_state_subagents.py`、`clawd_runtime_routes.py`、聊天页、`task-list.ts`、设置页。
- 未删除旧侧车 `GET /subagents`。它仍不是这条投影链。
- 超过 2000 个 child、分页期间列表持续变化、或某一页形状不合法时，投影失败而不是显示残缺列表。这是有界失败，不是已验证的线上容量。
- 静态注册和这次客户端接线不能代替一次真实的 `handle_subagents` + `panel-api` 请求。那仍未跑。
