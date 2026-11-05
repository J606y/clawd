# 浏览器夹具失败关闭 2026-11-05

未跑测试、构建、import 套件、CLI、浏览器、网络或生产读取。未改 `runs.ts`、settings、gate、manifest。未 commit/push/deploy。未 delegate。全部运行结果 **unverified**。本报告不是通过，也不是上线许可。

## 404 错误 schema

对齐 `gateway/platforms/api_server.py` `_openai_error`：

```json
{"error":{"message":"...","type":"invalid_request_error","param":null,"code":"run_not_found"}}
```

- 缺 run、路径 run_id 与夹具持有的创建记录不一致：404 `run_not_found`。禁止 `{}`。
- `/panel-` 前缀不是下表已知路由，含 `/panel-api`、`/panel-sidecar` 以外的前缀：404 `route_not_found`。禁止 `{data:[],running:0,items:{}}`。
- 已知路由方法不对：405 `method_not_allowed`。创建缺 `session_id` 或坏 JSON：400 `invalid_request`。同一 Idempotency-Key 换 session：409 `idempotency_key_conflict`。

## 身份

创建只读请求体 `session_id`。不写 `profile`/`default`，不把别的 session 的 run 记到当前路径。`run_id` 为 `run_` + 16 字节 hex。幂等键重放返回同一对 id。GET 状态、stop、events 只在路径 id 等于该记录时返回这对 id。

状态体：`{object:"hermes.run", run_id, session_id, status, created_at, updated_at}`。202 创建体仍是 `{run_id, status:"started", replayed}`，`session_id` 在状态记录上，与 `api_server_runs.py` `_set_run_status` / `_accepted_response` 一致。

活动 run 的 stop 改为 `stopping`，不是无身份的 `cancelled`。已经是 `completed|failed|cancelled|interrupted` 时，stop 原样返回该终态，并带同一对 id。其它 session 的 runtime run 查询是 `{run:null, busy:false}`，不是别人的 run。

## artifacts 已知路由

前缀 `/panel-api` 或 `/panel-sidecar` 去掉后：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| GET | `/api/sessions` | 200 列表。`source:"api_server"`。无 profile |
| GET | `/api/sessions/{id}/messages` | 200 `{data, pagination.returned}` |
| GET/PUT | `/api/sessions/{id}/effort` | GET unset，`session_id` 取路径。PUT 只接受 `{effort}` |
| POST | `/api/sessions/{id}/model` | 200 `{model, provider}`。无 profile |
| GET | `/api/model/options` | 200 fixture 提供方 |
| GET | `/api/runtime/sessions/{id}/run` | 200。只在 `session_id` 相等时带 run；key 不符则 `not_found` |
| GET | `/api/runtime/sessions/{id}/background` | 200 `{processes:[]}` |
| GET | `/api/runtime/sessions/{id}/execution` | 200。busy 只反映该 session 的活动 run。`execution:null` |
| POST | `/api/runtime/sessions/{id}/execution/stop` | 409 `unknown_execution` |
| GET | `/api/runtime/sessions/{id}/context` | 200 `source:"no-data"`，session 取路径 |
| GET | `/api/runtime/subagents` | 200 空页，含 `total`。不写 `profileKey` |
| POST | `/v1/runs` | 202。必须有 body `session_id` |
| GET | `/v1/runs/{id}` | 200 双 id，或 404 `run_not_found` |
| GET | `/v1/runs/{id}/events` | 帧带 `run_id`+`session_id`，或 404 |
| POST | `/v1/runs/{id}/stop` | `stopping` 或已有终态，双 id；缺 run 404 |
| POST | `/v1/runs/{id}/steer` | 409 `steer_not_accepted` 或 404 |
| POST | `/v1/runs/{id}/approval` `/clarify` | 409 `approval_not_pending` 或 404 |
| POST | `/v1/runs/{id}/resolve-unknown` | 409 `run_not_active` 或 404 |
| GET | `/files/artifact` | 仍代理自有 sidecar。根、密钥、MIME、字节未改 |
| GET | `/run-wait` | 200 `{state:"unknown", source:"durable", available:false}`。不是 idle 成功 |

`/unsafe-fetch` 仍 404，不进面板路由。附件根注入、选择器、视口、清理只杀自有 Chromium/sidecar，未改。完成路径原先引用未定义的 `final`，改为同一条 delta 文本，避免终态消息丢失文件名。

## admission 夹具

`react-admission-recovery-fixture.tsx` 不再对任意 `/v1/runs/` 回 `{status:"running"}` 或 `{status:"cancelled"}`。POST 用 body `session_id` 建记录；stop/status/events 只回该对 id，缺记录 404 `run_not_found`。旧请求 `old-request-01-20` 没有持有记录，resolve 仍是 `not_found`。子代理页不写 `profileKey`。未知路由 404 `route_not_found`。

## 文件

- `apps/panel/tests/artifacts-browser.mjs`
- `apps/panel/tests/react-admission-recovery-fixture.tsx`
- `apps/panel/tests/artifacts-fixture-contracts.test.mjs`（纯函数与源码断言；导入不会启动浏览器）
- 本报告

未跑上述 contract、未跑 browser/server、未构建。
