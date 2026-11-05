# 事件身份与订阅等待

身份：编写时模型为 grok-4.7，与指定一致。未跑测试、构建、探针，未改 gateway、api.ts、queries.ts 请求路径、turns、原 `tests/clawd_event_recovery_cases`。该目录未放宽、未 skip。

这些改动是静态实现，不是通过证据。父代理收齐后仍跑 `scripts/run_tests.sh tests/clawd_event_recovery_cases` 与 `tests/clawd_event_native`。

## 文件

- `apps/panel/src/react/lib/runs.ts` 与 `apps/panel/src/lib/runs.ts`：改后 `diff -q` 仍相同。`follow` 遇错配状态体立即返回，不读其 status/error/正文。
- `apps/panel/src/react/pages/chat/chat-page.tsx`：只加了错配 follow 的页面重订阅，以及清单执行投影的消费。首发仍是绑定、锁模型、再发送。出字样式和收起/展开未改。
- `clawd_server/server.py`：只改 `_proxy` 的 SSE 响应头和读取方式。`sock_read=None`、认证、home 解析未动。
- `apps/panel/src/react/lib/sidecar.ts`：只给 `Subagent` / `SubagentsView` 加可选字段，请求路径未改。
- 没有新增 useLiveRun 文件，也没有改 `stream.ts`。

## 错配后的重订阅

错配状态体仍结束这一次 `follow`，避免原用例里的 8 次退避超过 10 秒。回合保持 `running`，不发 `RUN_FINISHED`，不设 `streamGone`，不清思考，不标 `completed`。

可见会话的 effect 随后调用 `resubscribe(sessionId, runId, startedAt, cancel)`。绑定的是当前 session、run、`startedAt`，以及该 run 的 `idempotencyKey`。最多再跟 3 次，每次先等 250ms。页面切走、槽位隐藏、中止信号、终态、run 被替换，或幂等键变化，都停止后续尝试。已经用该 run 自己的 AbortSignal 接上的 follow 不因页面 effect 重跑而拆掉，事件仍只写回原来的 run 对象。

3 次仍错配且回合还在跑时，只写一条暂停说明。思考和正文保留。刷新仍走原来的 `resumeAll`。没有 `session_id` 查询，也不猜 profile。

同一会话的另一个 run 仍由现有 `observe` 接上。`run.started` 不能把终态改回 `running`。重订阅若自己收到匹配的终态，会补一次 `finish`，因为当时原来的 `consume` 已经退出。

## 清单投影消费

子代理查询 `status !== "success"` 时不传 executions，避免用 `[]` 把已关联行打成状态不明。成功后才传，且只传 `parentId === sessionId`、有 child session id 或 `delegationId`、profile 与 scope 相同的行。`childId` 用行上的会话 id，不用 title。`executionStatus` 只接受规范枚举；栏上的 `done/ended` 不翻译成 `returned`。`returned` 交给已有投影变成待验收，不写验收 status。

`profileKey` 只读子代理响应的视图字段；没有视图字段时，仅当各行给出的 profile 完全一致才用那一个值。否则 scope 是 `unknown`。不写死 `default`，不从 URL 猜，也不用 profiles 列表的进程档代替 SessionDB。缺这个字段时，真实 profile 的行不会被送进投影。请 runtime owner `sa-0-8c9a5a62` 在现有 `list_subagents` 响应给出 `profileKey`，并在行上给出 `delegationId` / `todoId` / `profileKey` / `executionStatus`。

## 代理

静态核对的是项目 venv 里的 aiohttp，不是探针：

- `StreamReader.read` / `readany` 在缓冲非空时就返回已有字节，不等满 n，也不等 EOF。`AsyncStreamIterator` 在空字节处停止。
- `StreamResponse.write` 调用 `transport.write`。`write_eof` 在循环之后。`StreamResponse._send_headers_immediately` 为 True，首块不跟头攒在一起等 EOF。
- SSE 现在用 `iter_any()`，并自己写 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`。非 SSE 仍是 `iter_chunked(65536)`，不加这两个头。`Content-Length` 本来就在 hop 头里，不会转发。
- `iter_chunked` 这个名字不能证明下游没有缓冲。内核写缓冲、反向代理和浏览器都没有探针。不能声称缓冲问题已复现或已消失。

## 品牌

本范围没有旧 `@hermes` 第一方包 import。自动继续那句用户可见文案里的产品名改成了 clawd，其余语义、布局和出字未动。`runs.ts` 里「Hermes 并行执行」是注释，不是界面文案，未改。`server.py` 的 `/root/.hermes` 是兼容路径，不在 `_proxy`，未改。包名留给品牌分发路。

## 仍依赖后端

- 事件路由仍只有 `run_id`、`Last-Event-ID`、`last_seq`。跨 profile 必须由 runtime 在状态或事件里给出 profile。本路不猜。
- 子代理行的 `delegationId` / `todoId` / `profileKey` / `executionStatus` 仍要 runtime 写入。没有这些字段时，投影不能把进行中点亮，也不能把 `returned` 显示成待验收。
- 代理下游是否真的不缓冲，仍是未测疑点。
