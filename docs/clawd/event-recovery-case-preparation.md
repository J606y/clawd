# 事件恢复用例准备

身份：编写时模型为 grok-4.7，与指定一致，无不符项。未跑测试、构建、探针，未改产品源码、原测试、manifest、runner，未装依赖，未删文件，未访问 `/root/.hermes`、官方安装、原面板或现网服务。

这些文件是未运行契约，不是通过证据。父代理在实现收齐后用 `scripts/run_tests.sh tests/clawd_event_recovery_cases` 统一跑。Node 文件由同目录 pytest 包装调用，不单独改 runner。包装超时 180 秒，只为防止误监听挂死，不是产品超时。

## 新增文件

- `tests/clawd_event_recovery_cases/test_run_stream_cursor.py`：真实 `_RunStream`、`terminal_run_status`，以及 `clawd_server.contract.SSE_CURSOR`。
- `tests/clawd_event_recovery_cases/test_run_events_handler.py`：真实 `_handle_run_events`。传输是记录型 `StreamResponse`，不 `listen`、不用 `TestServer`。
- `tests/clawd_event_recovery_cases/frontend-event-recovery.test.mjs`：vite middleware 加载 `apps/panel/src/react/lib/runs.ts` 与 `sse.ts`。`server.listen` 被替换为抛错。
- `tests/clawd_event_recovery_cases/test_frontend_event_recovery.py`：上述 Node 文件的 pytest 入口。

## 冻结接口

服务端只依赖当前可见签名，不猜测 runtime 正在改的新参数：

- `_RunStream.attach(last_seq=-1) -> (queue, replay)`，回放条件是 `seq > last_seq`。
- `_RunStream.put_nowait(event)`；`None` 是终态哨兵，之后的写入丢弃。
- `_RunStream.detach(queue)`。
- `_handle_run_events(self, request, *, _api_server)`。请求只读 `match_info["run_id"]`、`headers["Last-Event-ID"]`、`query["last_seq"]`。
- 游标名是契约常量 `SSE_CURSOR == "last_seq"`。非法或负数游标按现实现回落 `-1`。
- 响应头来自真实 `APIServerAdapter._sse_headers`：`text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- 帧格式来自真实 `_sse_frame`。所有权布尔由夹具给出；404 信封是真实 `_run_not_found` / `_openai_error`。夹具不是事件成功。

前端只调用已发出的实例方法，不新造签名：`follow`、`applyRunEvent`、`reconcile`、`recover`、`observe`、`start`、`resumeAll`、`getSse`。`ctrls` 是现有 `private` 字段的编译结果，用来停掉真实订阅；若改成真正私有字段，对应用例失败，不另猜取消 API。

## 相对已有覆盖补了什么

原 `tests/gateway/test_api_server_runs.py` 已有 HTTP `TestServer` 重连、双订阅和完成事件。本目录不重复开服务。`tests/clawd_race_cases` 专管审批与续跑竞态，这里不用审批选择或续跑线程证明事件恢复。

- `last_seq`：store 不重复投递已见 seq；handler 中 `Last-Event-ID` 优先于 query；前端同一次 `follow` 断线后用已见 seq 重连。完成只来自后续终态事件，轮询体在该用例里保持 `running`。
- 重复事件：store 不按正文去重，相同正文得到新 seq；已见 seq 不回放。reducer 对同一 seq 不重复拼接，同一终态 seq 不重复结算。
- 乱序与终态重放：store 序号单调；终态哨兵后的 `run.completed` 被丢弃，handler 只写 `: stream closed`，不另造完成帧。reducer 丢弃更旧 seq，更旧的 `run.completed` 不能把 `running` 改成 `completed`。缺口仍应用正文，但 `usage.available` 必须变 false。
- 断线不是完成：`detach` 不置 `terminal`，也不写出 `run.completed`。慢订阅者收到 overflow 后被摘除，不把整段缓冲成完成。`terminal_run_status` 对 interrupted、failed、partial、`completed=False` 都不是 completed。前端流关闭且状态仍为 running 时，中止订阅不得变成 completed。
- 会话切换：一个 `follow` 中止后，迟到事件不改另一会话。`start` 先替换再中止旧 `AbortSignal`；对旧对象补 `run.completed` 不改新对象，也不发 `RUN_FINISHED`。
- run/session 错配：`reconcile` 与 `recover` 拒绝 session 或 run 不一致，且错配的 resolve 记录不得继续读状态。精确匹配仍可由真实 `reconcile` 结算，这是门禁的对照，不是模型回合。
- 实时性：handler 在下一段 delta 产生前已写出第一帧，第一块字节随后不被改写。`getSse` 在第二段入队前交出第一帧。响应头禁止代理缓冲。

## 缺失与预期失败

- 事件路由没有 `session_id` 参数。服务端不按会话错配返回单独错误码；无主 run 走现有 404 `run_not_found`。没有发明新的 query。
- `follow` 的状态轮询目前只看 `status` 字符串。`contract: a mismatched status poll...` 要求错配的 `run_id`/`session_id` 不得写成 completed。当前 reducer 不满足，运行后应失败。这是契约，不是 skip。
- `apply` 目前会抄写事件里的 `run_id` 并应用 delta。`contract: a foreign run_id event...` 要求拒绝。当前不满足，运行后应失败。禁止改成 xfail。
- `useLiveRun` 按 `RUN_CHANGED.detail === sessionId` 过滤，未启动 React，也未改出字动画。store 事件 detail 仍是源 sessionId。
- `src/lib/runs.ts` 与 `src/react/lib/runs.ts` 是否同步，本目录只加载后者。
- `clawd_server.server._proxy` 现为 `iter_chunked(65536)` 逐块写，但父代理拥有该文件。这里不构造 `PanelServer`、不监听。面板转发的实时性仍是接口依赖，不能用本目录的 handler 用例代替。
- 订阅早于 `_run_streams` 注册的 20 次 50ms 等待未覆盖，避免无意义等待。
- 空 `terminal_run_status({})` 按现实现会变成 completed。断线路径没有调用它；不要把空结果当成断线。

## 依赖

- 仓库根由 `tests/conftest.py` 放入 `sys.path`。需要已有 pytest、pytest-asyncio、aiohttp。
- Node 包装需要已有 `node` 与 `apps/panel/node_modules/vite`。不执行 npm install。
- vite 只用 `middlewareMode`，缓存放在系统临时目录，结束后删除。不写 `/root/.hermes`。
- 不读取生产 home，不调用模型，不打开 HTTP 端口。
