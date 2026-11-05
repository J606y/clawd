# 会话档位原生化

已实现，未跑测试、typecheck、build 或真实模型。路由未挂。父代理挂载并接上准入 hook 之前，面板关闭后的 pending 不会在下一回合生效。

## 接口

同一 aiohttp app 上注册，并镜像多 profile 前缀。本模块不自挂。

- `GET /api/sessions/{session_id}/effort`
- `PUT /api/sessions/{session_id}/effort`
- `GET|PUT /p/{profile}/api/sessions/{session_id}/effort`

PUT 正文只能是 `{"effort":"low|medium|high|xhigh|max|ultra"}`。多出来的字段直接 400。

成功响应字段不变：`object`、`session_id`、`effort`、`source`、`applied`、`running`、`lock_model`、`lock_provider`、`lock_confirmed`、`persisted`。

`source`：`pending` / `lock` / `session` / `unset`。`applied`：`next_turn` / `stored` / `unset`。`running: null` 只出现在 GET，表示看不清；这时 PUT 503，不写。

失败信封与面板 `api.ts` 一致。常见码：`unauthorized`、`invalid_effort`、`unexpected_fields`、`session_not_found`、`session_db_unavailable`、`effort_run_state_unavailable`、`model_config_unreadable`、`model_options_unreadable`、`effort_admission_required`、`effort_not_idle`、`effort_transaction_unavailable`、`effort_persistence_failed`。拒绝不是成功。

## 鉴权

走 adapter.`_check_auth`。没有检查器、检查器抛错、或返回值不是 `None` 也不是 `web.Response`，都是 401。允许只认返回值 `is None`。`web.Response` 可能为假，handler 必须 `if auth_err is not None`，不能写 `if auth_err:`。

profile 只进 `adapter._profile_scope`。不改进程环境变量。

## 父代理挂载（仍未做）

不要改 `gateway/platforms/api_server.py`。在 API app 上、开始服务前：

```python
from gateway.platforms.clawd_effort_routes import register_effort_routes
register_effort_routes(app, adapter)
```

`adapter` 必须已有 `_check_auth`、`_profile_scope`、`_ensure_session_db`，且 `gateway_runner` 在当前 scope 下能解析到该 profile 的 SessionAuthority。`_ensure_session_db()` 必须就是该 authority 的库（同一对象或同一解析后的 `db_path`）。不要挂到会把第一份 SessionDB 缓存死的空 adapter 上。对不上时 GET 的 `running` 为 null，PUT 503，不把别的 profile 当成空闲。

面板请求路径仍是 `panel-api/api/sessions/{id}/effort`。

## 运行态

空闲必须同时满足，缺一则是 `unknown`，PUT 不写：

- 当前 scope 的 `authority_for_home(runner, get_hermes_home())` 存在，且其库就是这次打开的库。
- 该 authority 的 `executing` 是 dict，且会话 id、准入 owner、logical owner 都不在里面。读不到 owner 就是 unknown，不是空闲。
- 该库的谱系根租约可读且 `busy` 为 false。读失败、时间戳无效、holder 探活失败，都不是空闲。
- 这些 id 上没有 `queued`/`started` 准入，也没有 `registered`/`running` worker。出现 `unknown` 状态或查询失败，则整次 unknown。
- 活跃压缩锁无法证明过期或持有者已死时，也是 unknown。不删租约、不删压缩锁。

`adapter._run_statuses` 不参与判定。它不带 profile。同 session id 落在另一个 profile 的 status 上，不能当成这个 profile 空闲，也不能当成这个 profile 正在跑。

## 延后生效

运行中的 PUT 只写 `model_config.clawd_pending_reasoning_effort`。不改锁，不改已经记下的 `reasoning_effort`。

面板关闭后，续跑 `inherit_runtime` 仍不读 pending。下一回合要看到新档，runtime 路必须在准入锁内调用下面的函数。只靠面板回合结束后再 PUT，不够。

```python
from clawd_server.effort_service import commit_deferred_effort
commit_deferred_effort(authority.db, session_id, authority=authority, admission_held=True)
```

调用前提：

- 调用方正持有该 profile、该会话的准入锁，并且本调用返回前不释放。新 run 不得在调用期间读取或快照档位。
- `authority` 是这个 profile 的 SessionAuthority，`db` 是它的库。
- 每次准入都调，不要等面板声称有 pending。没有 pending 时原样返回，`persisted: false`，不写。
- 必须在新 run 复制 `browser_model_lock.model_options` 或根上的 `reasoning_effort` 之前调用。

失败时不改当前运行档，pending 保留：

- 没拿锁、authority 缺失或库不是该 profile：`effort_admission_required`（409），不写。两参数旧调用现在也会这样拒绝。
- `executing`、租约、准入或 worker 仍活：`effort_not_idle`（409），不写。
- 上述读取失败或压缩锁仍活跃：`effort_run_state_unavailable`（503），不写。
- 会话不存在：`session_not_found`（404）。
- 没有 `_execute_write`：`effort_transaction_unavailable`（503），不写。

`admission_held=True` 只是调用方声明。没持锁却传入，是调用方错误；事务内仍会拒绝数据库能看见的运行中会话，但看不见的内存执行必须由锁和 `authority.executing` 挡住。

HTTP 空闲 PUT 会在同一写事务里再查租约和准入。期间若已有回合，只改记 pending，不改锁。这不能代替准入 hook。

## 持久化

不新库，不新锁字段。不调用 `update_session_runtime_lock`。

不把事务外拼好的 `browser_model_lock` 交给 `patch_session_model_config`。该函数的事务只按顶层 key 替换；调用方传入的整把锁会盖掉并发写进的 model/provider。写完再读只能发现破坏，不能撤销。

本路使用已有的 `SessionDB._execute_write`：`BEGIN IMMEDIATE` 内读取当前 `model_config`，只改 `model_options.reasoning_effort`、根上的 `reasoning_effort` 和 pending。model、provider、`route_source`、`confirmed` 和其他 key 留在这次读到的文档里。异常回滚。

核心要求（本路不改 `hermes_state*`）：保留 `_execute_write(fn)`，fn 在 `BEGIN IMMEDIATE` 内执行，提交由 helper 拥有，异常回滚。若删除该入口，需新增公开方法：同一事务内按精确 session id 读取 `model_config`，对整份 dict 做纯函数回调，再 UPDATE 同一行。回调必须看见前一个写者已提交的锁，且不能要求调用方传入预制的 `browser_model_lock`。不要在本路私加这个方法。

## 前端

选中已有会话就 GET。服务器有档则覆盖滑块。无会话 id 的默认档仍只留在 `hp_effort_next`。

新会话首发顺序不变：冻结项目、创建、绑定并核对、锁定模型、再发送。档位恢复不插在绑定或锁定之前，也不等待写入成功。尚无持久行（404 / `session_not_found`）不阻断发送，不改走普通模式，不清理已绑定会话。本地档仍随发送请求带走。写入失败只提示，草稿不因此退回。

已有会话发送前仍向服务器确认。确认失败则消息不发出。

滑块要等 PUT 成功才动。运行中成功则提示下一回合生效。Ultra 的 id、动画和布局没改。

`ui.ts` 仍不在本路。

## 未改的依赖

- `pin_reasoning_effort` 只改内存 policy。本路由不绕过它。
- `api_server._persist_session_runtime_lock` 仍可能用请求体整段覆盖 `lock.model_options`。未改占用文件。Argon/Flash 发送仍可能把滑块 id `ultra` 写成线路值 `xhigh`。Grok 滑块仍把 `xhigh` 显示成 Ultra。
- Argon/Flash 后缀留在 `model-effort.ts`。核心 gemini profile 的 `thinkingLevel` 不服务 `sub2api-gemini`。去掉后缀会让代理按基础模型名路由，所以不删。
- 续跑不读 pending。面板关闭后的生效取决于 runtime 路在准入锁内调用 `commit_deferred_effort`。该 hook 尚未接入。
- 路由尚未注册。未运行配套测试。
