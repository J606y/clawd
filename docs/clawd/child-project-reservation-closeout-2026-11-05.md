# child 预留项目身份收口 — 2026-11-05

状态：**静态接线，未运行、未验收、未上线**。本文件不是完成声明，也不是跨库原子承诺。

本轮没有执行测试、build、产品 import、CLI/产品探针或网络请求。没有打开生产库，没有读生产秘密，没有删除会话或项目数据，没有 commit/push/deploy。没有改 `hermes_cli/projects_db.py`、`clawd_projects/migration.py`、`clawd_projects/service.py`、`agent/agent_init.py`、`agent/turn_facade.py`。没有碰 `/usr/local/lib/hermes-agent`、原面板主检出或线上目录。`build/lib/` 仍是旧副本，不是本轮实现。

迁移 token、条件删除和回执认领没有重做。本轮只补 worker `child.reserve` 的项目快照，并去掉已存在 child 的 `force=True` 重绑。

## 改了什么

| 文件 | SHA256 | 作用 |
| --- | --- | --- |
| `gateway/session_worker_child.py` | `dca682b9c924b5841e258a3e640b98fe5a479d9f1010d31066aa3c9a85dfb290` | 预留事务提交前，按本连接 `state.db` 快照父当前项目 |
| `agent/runtime_session_child.py` | `035d01cdf274f8f66d0de195a2a0b857ddd08bfbf8346aae178decfd60f25c82` | 不成为第二条继承实现；缺 child id 不发布 |
| `tools/delegate_tool.py` | `6053b89b4d236c264dcd09835a93d7119e2ab1128053b4cf9111b49bba343bcb` | 已预留 id 不重绑；无行时走插入事务内复制；不再 `force=True` |
| `tools/delegate_tool_child_run.py` | `466de85ba3e463fc83c1b1a3d68ab62d44876e53cf5a4afaebd490ab86218f77` | 无可信 `state.db` 且有父会话时拒绝发布，不吞成无 transcript |
| `clawd_projects/inheritance.py` | `87325bcc2aea42005930bf03484012f25c6e2e633a243f49b0eb3c115c6b718c` | 可信 `state.db` 定位；本次写入才返回可条件释放的 token |
| `tests/clawd_projects/test_child_reservation_identity.py` | `27de458c400412354632a4374530d04f3de1f3c2a3574b9499e2c63ca922cf48` | 边界用例，**未运行** |

哈希是工作区文件内容，不是 git blob，也不能代替运行结果。

## 调用链

1. Worker `child.reserve`：`worker_child_request`（`gateway/session_worker_child.py:160-172`）只把准入/限额失败收成 refusal receipt。handler 在 `apply_worker_receipt` 的 state 写回调里（`hermes_state_runtime.py:990,1005`）。回调抛错时 `_execute_write` 回滚 state（`hermes_state.py:1052-1057`），不写这条 receipt。
2. `reserve_child_execution`（`gateway/session_worker_child.py:128-145`）先要求 `db.db_path` 是已存在的 `state.db`（`:55-67`）。然后插入子行，`model_config` 仍是 `{'_delegate_from': session_id}`（`:136`），再插 `worker_executions`，最后才快照（`:144`）。快照失败变成 `RuntimeStoreError('project_inheritance_failed')`（`:45-52,92-103`），不是 `{'refused': ...}`，也不返回 child id。
3. 调度器只把 `RuntimeStoreError` 收成 JSON-RPC error frame（`gateway/session_controls.py:367-372`），不放进 `result`。客户端 `WorkerRPC` 见到 `error` 就抛 `WorkerPersistenceError`（`agent/runtime_session_store.py:202-203`）。没有 child store，就不能构建可运行 child。
4. `reserve_child_store`（`agent/runtime_session_child.py:46-70`）只取 `session_id` / `execution_id` / `generation`。不读项目字段，不绑项目，不沿祖先，不读进程 home。缺 child id 直接拒绝。
5. 进程内、尚无预留 id：`_child_session_id_for_project`（`tools/delegate_tool.py:205-249`）不再调用 `bind_session_to_project`，也不再读 `get_hermes_home()`。有预留 id 则原样返回。没有 `state.db` 路径则 `ProjectInheritanceError`，调用点（`:341-348`）释放句柄后原样抛出。有可信库且行不存在时，调用 `create_session(..., parent_session_id=...)`，由已有 `_insert_session_row` 在 state 写回调里调用 `_copy_child_project_binding`。这不是先提交 `projects.db`。
6. `_reserve_child_transcript`（`tools/delegate_tool_child_run.py:40-64`）对 worker store 直接返回，不第二遍继承。进程内无 `db_path` 且有父会话则拒绝。`create_session` 失败会 `close()` 后重抛，不把异常收成已发布。

`tools/delegate_tool_dispatch.py` 的 `child_links` 链没有改。todo / delegation 投影不在本轮文件里。

## 发布门禁

可运行 child 只有在下面都成立时才离开这些函数：

- 身份是正在写的 `state.db` 父目录。文件名不是 `state.db`、文件不存在、或没有 `db_path`，都拒绝。不读进程 home、URL、`parent_session_id` 祖先。
- 父会话不在这个 `state.db` 里，worker 预留拒绝，不插子行。
- 父当前 `project_sessions` 行被复制。父没有行（普通模式或显式解绑）则子也不写行。子已经有项目行则保持，不重读父。
- 继承失败、读失败、写入后读回不一致、或本次写入没有可证明 token，都抛错。worker 路径是 `project_inheritance_failed`，不是容量类 refusal。进程内路径是 `ProjectInheritanceError`。两边都不返回可运行 child。
- 已存在 child，包括预留 id，不再 `force=True` 重绑。也不走 `force=False` 重绑：那仍会覆盖已有快照或撤销显式解绑。交接里“行已存在必须 force=False”的下限因此收紧为“已发布行不重绑”。新行的复制发生在插入事务内，不在行出现后再 force。

准入失败、深度和并发上限仍是 refusal receipt（`:162-166` 只包住这段）。继承失败没有并进这段。

## 失败恢复

不是原子事务。`projects.db` 在自己的 `write_txn` 里提交，state 事务随后才提交。成功只表示本函数返回了 child id 或项目 id；不表示两库一起提交。

补偿只针对本次调用写出的 `write_token`：

- `ChildProjectCopy.write_token` 只在本次 `bind_session_project` 写入时设置。已有快照、父未绑定、跨 profile 普通父，都是 `wrote=False` 且 token 为空。这些行不能拿来释放。
- `release_owned_child_binding`（`clawd_projects/inheritance.py:97-124`）把 `project_id` 当期望当前项目，带 `expected_write_token` 做三字段条件删除。不传 `project_id=None`，不无条件按 session 删除，不删消耗行，不删其他会话。
- 读回不一致或后续异常时，先按该 token 条件释放，再抛错（`inheritance.py:215-235`，`session_worker_child.py:92-103`）。释放本身失败会再抛，不会改成成功返回。
- handler 已经返回之后，state `commit` 失败并重试回调：第一次的 child id 可能留下孤儿 `project_sessions` 行，重试会铸造新 id。这发生在 `hermes_state.py` 的 `_execute_write`，不在本轮可改文件里。没有在那里做补偿，也不把这个窗口叫原子提交。孤儿行不是可运行会话。
- 不自动删除已存在会话的项目数据。条件释放匹配失败则行还在。

## 超范围阻塞

没有改这些文件。下面不是本轮已修：

1. **`agent/agent_init.py:1231-1233`** 仍用 `get_hermes_home()` 读项目。worker 进程 home 与 owner `state.db` 父目录不一致时，持久绑定可以在正确的 `projects.db`，内存初始化仍可能看不见。不能改 `agent_init` / `turn_facade`。这是记忆消费缺口，不是本轮快照没写。
2. **`tests/clawd_projects/test_project_core.py` 的 `test_child_subagent_inherits_project`** 父代理没有 `session_db`。新门禁会拒绝，不再用进程 home `force=True` 预绑。该测试文件不在本轮可改名单。预期以后运行失败，不能改断言放行。
3. **`build/lib/tools/delegate_tool.py:224`** 仍是 `force=True`。`build/lib/gateway/session_worker_child.py` 仍没有快照。不手改 `build/lib`。从那里导入会把已改源码看成未改。
4. **state commit 失败后的重试窗口** 要改 `hermes_state.py` 的 `_execute_write` 才能在回调返回后条件释放。未改。
5. **API fork 的 HTTP 错误码、`delete_session` 不释放项目行、迁移认领不读消耗表** 仍是交接第 7 节的其他项，不在本轮文件里。

## 准备但未运行的用例

`/root/hermes-workspace/clawd/tests/clawd_projects/test_child_reservation_identity.py`

覆盖：预绑定源码不再 `force=True`；缺 `state.db` 不猜 home；预留 id 保持已有快照和显式解绑；进程内插入按 state 文件 profile 快照并忽略进程 home；不把新 child 写到已有 session id；worker 预留保留 `_delegate_from`；不沿祖父项目回绑；绑定失败回滚子行且不是 refusal；错误 token / 空项目不删；读回不一致只释放本次 token；`projects.db` 已提交而 state 尚无子行时，条件释放只删本次行。

未执行。准备用例不是 pass。旧 `tests/hermes_state/test_runtime_worker_children.py` 也未跑；父未绑定且没有 `projects.db` 时，新路径按普通模式返回，不把缺库当成失败。这是源码契约，没有本轮运行证据。

## 本次未做

- 未运行任何测试、构建、导入、探针。
- 未把静态接线标成已验收。
- 未声称 `projects.db` 与 `state.db` 一次提交。
- 未删除已有会话、项目、记忆或消耗行。
