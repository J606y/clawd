# effort 路由接线 — 2026-11-06

## 结论

GET/PUT 已接到真实 `resolve_execution_state(db, session_id, authority, adapter)`。authority 缺失不再直接 503；能证明 idle/running 就按证明读写，unknown 仍拒绝且不写。缺会话在 unknown 拒绝之前返回 404。执行器 worker 计数这个真实漏项已补上，读失败不能当成 idle。

未跑 pytest / node / build，未提交，未部署，未向现网发写入探针。线上进程仍是旧代码，不能说已经修好。

## 目标

- 机器：`list_servers` 8 台，`HUAWEI CLOUD SG SERVER` id `beqCprBT`，online。不是 HK。
- 源码：`/root/hermes-workspace/clawd`，HEAD `a3c447d2d297a724219edc62c88b1802e4cb3f75`。脏工作区其他改动未动。
- 现网进程：pid `68692`，2026-11-06 04:39 由 `/root/clawd-migrate/serve_panel.py` 拉起，`sys.path` 指向这份源码，但进程未重启。未读取其内存里的 adapter。

## 改动

| 文件 | 角色 | SHA256 | 字节 |
| --- | --- | --- | --- |
| `gateway/platforms/clawd_effort_routes.py` | 已改 | `7e8be3841afb579940934ddfb7e5c2cf5cd47082b602d2ccbfa5930cd2123616` | 10022 |
| `clawd_server/effort_service.py` | 已改，保留先前 owner | `a14b018809d8fda6363e4ade03434f9eaf823cd6b5b1b077686a07c78a4d2a2a` | 35295 |
| `tests/clawd_effort_bug/test_effort_http_routes.py` | 新增 | `5f6cc030e0c898a3463154769b6247986f90a22721b26f36c25f67a05fca1526` | 22129 |
| `tests/clawd_effort_bug/test_unbound_adapter_state.py` | 未改 | `4e4a483dce9b5630bb303eaaab6989eaf9e76a7044a028752ee805f53f23b851` | 9645 |

`tests/clawd_effort_cases/` 没有改。那两个文件不导入路由，也不断言「authority 缺失即 503」，没有受这次接线影响的 fixture。

未改：API server 主文件、state 消息、项目资源、面板前端、nginx、登录鉴权、任何服务。`api_server.py` 里原有的 `register_effort_routes` 调用没动。

## 备份

修改前逐文件原始字节，远端与本机同一路径，哈希一致。未覆盖已有备份。

`/root/hermes-workspace/clawd-bugs-backup-effort-integration-20261106T051721-e4b1/`

| 备份文件 | SHA256 | 字节 |
| --- | --- | --- |
| `gateway/platforms/clawd_effort_routes.py` | `ddb968815d6c9a9f033eecdb9178cd6789f28d0cb71da63d53476597e8a9a3b8` | 8863 |
| `clawd_server/effort_service.py` | `575d4f5cc7b1e8c3a9838d5cb15effb378abc9f2d05265a523e00b190b39ebbc` | 34011 |

`effort_service.py` 备份哈希与交接一致。

## 静态证据

路由：

- import `resolve_execution_state`。`_run_state` 调用 `(db, session_id, authority, adapter)`。
- PUT 不再因 `authority is None` 直接 503。先校验 effort（非法仍 400），再查会话（没有则 404），再 resolve。unknown 或不在 `running`/`idle` 的结果 503 且不写。`write_session_effort` 传 `adapter` 和 `running=state == "running"`。
- 会话读取抛错是 503，不是 404，也不是 idle。鉴权、draining、session id、JSON、字段、profile 校验仍在写之前。
- 远端源码已无 `or authority is None`。`.venv/bin/python -m py_compile` 三个改动文件退出 0。这只是语法解析，不是测试。

`APIServerAdapter` 运行字段对照（只读 `api_server.py` / `api_server_runs.py`，未改）：

- 已覆盖：`_run_statuses`（live 与 `_active_structured_run_count` 同为 queued/running/waiting_for_approval/stopping；终态与 `TERMINAL_STATUSES` 一致）、`_active_run_agents`、`_active_run_tasks`、`_shutdown_interruptible_agents`、`_inflight_agent_runs`、`_pending_agent_requests`、`_ensure_session_db`、`gateway_runner`。
- `gateway_runner` 已设置但 `authority_for_home` 回 None 时，resolver 仍返回 unknown。这是先前 owner 的规则，保留。`run_serve` 构造的 adapter 默认 `gateway_runner is None`；独立面板这条路接线后才能 idle。未对活进程做属性内省。
- 不应当成执行证明：`_run_streams`、`_run_approval_sessions`、`_run_owners`。`_stopping_run_ids` 与 status `stopping` 或 durable admission 成对出现。`_run_admission_aliases` 是 run id 到 admission id，会话忙闲以 `session_admissions` 行为准。
- 真实缺口：`_inflight_agent_runs` 在 handler `finally` 里下降，executor 线程仍可能在跑（`api_server_runs.py` 对 #116535 的说明）。补了 `api_worker_live_count()`。比已归因 agent 多的 worker，或计数读失败/类型不对，都是 unknown，不是 0，也不是 idle。

压缩 lineage 与 durable 状态（只读，与现有 proof 一致）：

- `SessionDB.get_compression_lineage` / `get_compression_tip` 存在。普通会话返回 `[id]`，不存在返回 `[]`。所以缺会话若先 resolve 会变成 unknown；路由必须先 404。
- lease key 沿压缩父链走到 conversation root。admission/worker/compression lock 按 lineage id 查。表字段与查询一致：`session_admissions.status` 含 queued/started/unknown；`worker_executions.status` 含 registered/running/unknown；`compression_locks` 为 session_id/holder/expires_at。
- 活压缩锁仍是 unknown，不写。委托活状态与 `_LIVE_STATES`（running/stalling/finalizing）一致。
- 运行中只写 `clawd_pending_reasoning_effort`，不改当前 `browser_model_lock` 的 model/provider/其他 option。提交仍在 `SessionDB._execute_write` 的 `BEGIN IMMEDIATE` 里读改写。

## 待父代理在 SG 上集中跑

工作目录必须是 `/root/hermes-workspace/clawd`。不要在 Hermes 本机跑，这里没有这份检出。

```bash
cd /root/hermes-workspace/clawd && scripts/run_tests.sh tests/clawd_effort_bug tests/clawd_effort_cases
```

新 HTTP 用例没有 mock `resolve_execution_state`。覆盖：unbound idle GET/PUT、running 只 pending、压缩子会话、lease、worker 行、queued admission、unknown 不写（无主 inflight、压缩锁、multiplex、有 runner 无 authority）、worker 计数缺口与读失败、缺会话 404、非法 effort 仍 400、鉴权拒绝、输入校验、跨 profile 404、跨库 503、同事务读改写与并发写不丢 model/provider。

## 未验证

- 上述 pytest 未执行。py_compile 不能代替它。
- 未 build，未重启 pid 68692，未对 `/api/sessions/.../effort` 发读写探针。现网 GET `running=null` / PUT 503 仍可能存在。
- 前端无存档自动 PUT 的显示失败未改面板，也不在本范围。重启并跑通测试之前，不能说界面已恢复。
- 活进程内存中的 `gateway_runner` 未读。若它不是 None 且 authority 解析不到，按保留规则重启后 PUT 仍会 503。
