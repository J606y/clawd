# 迁移恢复与项目身份继承 — 只读细查 2026-11-05

状态：**未跑、未迁移、未验收通过**。本文件只静态核对源码与已有用例，不是完成声明、验收报告或上线许可。

本轮没有执行测试、build、产品 import、CLI/产品探针、网络请求，没有打开生产库，没有读生产秘密，没有改源码、测试、数据或其他报告，没有 commit/push/deploy，没有调用迁移执行或回滚。

基线与交接记录一致：核心分支 `clawd`、HEAD `c314285f077e91937c995d419b9fa40af9eb4981`；嵌套面板分支 `hk`、HEAD `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29`。本次未重跑 porcelain 全量统计，也未重跑解释器版本。下列 SHA256 是本次对工作区源文件的内容哈希，不是 git blob，也不能代替运行结果。`build/lib/` 是过期副本，不得当实现或验收对象。

## 结论

迁移回执库、`projects.db`、`state.db`、`panel_folders.db` **不是一个事务**。能称为同事务的，只有 `projects.db` 里一次 `write_txn`：`project_sessions.write_token` 与消耗表 `project_binding_tokens` 一起提交或一起回滚。

迁移绑定和条件回滚都传 `force=False`，不把 `project_id=None` 当回滚。崩溃认领只认预先写入回执的 `pending_write_token` 与读回 token、目标项目三者一致。旧 `NULL` token 不认领、不解绑。这些是源码契约，**没有本轮运行证据**。

项目身份不是一条继承链。新会话不继承；分叉/压缩/本地 branch/reset/编辑副本走 `inherit_session_project`；进程内子代理和 worker 预留子会话另有写入点，其中子代理预绑定使用 `force=True`，**不能把迁移的 idle 门禁说成覆盖全部项目写入**。

## 文件哈希

| 文件 | SHA256 |
| --- | --- |
| `hermes_cli/projects_db.py` | `57e9f8aa1214bf074f9160cdff0eed4442980af5d623a622aa113b6772b37714` |
| `hermes_cli/sqlite_util.py` | `1ffde9d5e896dee3282514c81676d00d7535c833fc6d5ef9c43a42ae3778de79` |
| `clawd_projects/migration.py` | `d00d792f2375299c23d2b71aad4270304d022d34f587795b7be1197e78e26056` |
| `clawd_projects/service.py` | `8855d47d650886c91d90440025f31ee617f662749f316ceb45c052057f174130` |
| `clawd_projects/inheritance.py` | `3cd3c63842d0ebdc229cbc7a50edc48347c377b903fa9008d44ec37de7b5db9a` |
| `hermes_state_sessions.py` | `c149236afa6f58cab44a50d1e32f0e9f9d4f7883770aa0d5d7a0d7bf9d0ada1c` |
| `hermes_state_compression.py` | `8b6e2df32259d1d1c89a6f7215c8ba23ff7a91f69ecb83bcc9f3ff9a52863670` |
| `hermes_state_mutation_branch.py` | `88f8a764edc1b02a80224b366d38d58597ff697a9aec34dafcd7ed73fcab1e61` |
| `hermes_state_mutation_reset.py` | `8cb6435c6a0b5514e40952afd5e80b3bdd445eb309cc38957068a561cfb1b2df` |
| `hermes_state_mutation_compress.py` | `a5c0f0f744479846426dea8863a93ad6a81e46d373360ed4a6f1441b11a7038e` |
| `hermes_state_api_edit.py` | `6def2fb5c09d354237da75f0e0e85685adaa4656c7d0c77be5766fceaa5d6ec5` |
| `hermes_state_worker_compression.py` | `2f366b30203dec46a2556f949dc1528817c4448d536c4cd6b8435204d239cc63` |
| `hermes_state_worker_lifecycle.py` | `06a88b97b23f4e565acacabe37b5acee7213d26c49a5e463bddb2f317db4d0be` |
| `gateway/session_worker_child.py` | `85659f5013a335ed148f8e098a4d4085db3ff42d7e0029934fc8998c31917012` |
| `gateway/platforms/api_server.py` | `b95284ed570ea9f3ed4f0218af12eff91891373a32a93f60f89658b43ba839de` |
| `gateway/platforms/clawd_project_routes.py` | `dfb354f3b45b44922b16dff29f2fcb2e3db34a123e24fafb2959ce637164856e` |
| `tools/delegate_tool.py` | `aa5bb5d981cbbcdd8438920da59b94be1ee6817bb56a23c86bd96572d484a2ad` |
| `tools/delegate_tool_child_run.py` | `8a30a7c22e28b37bd57dc4c04bce00802a9614fb8939e0b3903bd3b06896b714` |
| `agent/runtime_session_child.py` | `67e0baed5881efb23943c6ed1b1963646e25d426dcffdb5ee8aa39b41d69f3ff` |
| `agent/conversation_compression.py` | `796494d28180659f2843599420840832e1f528ecad2110b498a59f53e34e6bcb` |
| `run_agent.py` | `77228b287ca611e0fcc51fe1fa7c1efc032d148ff642165c8606530f4520d00d` |
| `apps/panel/sidecar/hermes_panel_sidecar.py` | `c8e2b0890e489e2f5c96183068f06a5d923ae67356f6fdfa2fba751288fd9bc8` |
| `apps/panel/src/react/lib/project-send.ts` | `c8b295d4cb66d9d71e0e2462c63b11c4cf08d96ce958cd557cc54da6a7bb7f22` |
| `tests/clawd_projects/test_project_migration.py` | `48a5b76ff28f395bdcb9ad26b185452dd8a8039d12f38d29d6ca35568050c570` |
| `tests/clawd_projects/test_project_binding_token.py` | `907df9d8fdebd1d80957ab462262595d3eb9331097c41707e0872838732d60d1` |
| `tests/clawd_projects/test_project_inheritance.py` | `5f0934c8366a44f8930bbb18060309a6d707d167b05b45231f53959275236013` |

`build/lib/clawd_projects/service.py` 仍是旧的三参数 `_write_binding`，没有 token 转发。验收若从 `build/lib` 导入，会把已实现的签名看成未实现。隔离验收的 `PYTHONPATH` 必须是源码根，且要确认没有把 `build/lib` 插到前面。

## 1. 同事务 write_token / 消耗表

消耗表是 `project_binding_tokens`，不是迁移回执表。

- 表定义：`hermes_cli/projects_db.py:68-85`。`project_sessions.write_token` 可空。消耗表主键是 `write_token`，注释写明解绑后保留，防止同一 token 再创建它标识过的行。
- 事务原语：`hermes_cli/sqlite_util.py:99-113`，`BEGIN IMMEDIATE`，异常 `ROLLBACK`，成功 `COMMIT`。
- 写入：`bind_session_project`（`projects_db.py:639-706`）在同一个 `write_txn` 里 `INSERT/UPDATE project_sessions`，再 `_remember_token`（`607-616`）插入消耗表。`_remember_token` 失败会让整个 `write_txn` 回滚，正常路径不应留下“有 token 列、无消耗行”。
- 幂等重入：会话行已经是同一 session+project+token 时，仍在该事务里补消耗行（`687-693`），不另造 token，不改 `bound_at`。
- 条件释放：`_release_if`（`619-636`）只 `DELETE project_sessions`，**不删消耗行**。这是消耗语义，不是漏写。
- 无 token 且 `project_id` 空：无条件 `DELETE ... WHERE session_id=?`（`676-678`）。这是旧调用方兼容解绑。迁移回滚不走这条。

迁移不直接写这两张表。调用链：

`_bind_expected`（`migration.py:1205-1222`）→ `commit_session_binding(..., force=False, write_token=token)` → `service._write_binding`（`service.py:185-200`）→ `pdb.bind_session_project`。

回执里的 `pending_write_token` 写在另一个库 `project_folder_migrations.db` 的 `migration_receipts.body_json`（`migration.py:289-312,372-402`），并且在绑定前 `_save_receipt` 提交（`1412-1419`）。这是崩溃恢复所需的**先提交回执、再写 projects.db**，不是与消耗表同一事务。

认领侧不读消耗表。`_prepare_bind` / `_verify_binding` 只比较 `get_session_binding` 与 `get_session_write_token`。因此：

- 正常 `bind_session_project` 路径：两表同事务，静态成立。
- 若有人绕过该函数只改 `project_sessions.write_token`：当前认领会把行上的 token 当成证明，**不会**因为消耗行缺失而拒绝。这是待验收缺口，不是已证明的运行事实。

## 2. get_session_write_token 签名

已静态对齐，无新增必填参数冲突。

| 符号 | 位置 | 签名 |
| --- | --- | --- |
| `pdb.get_session_write_token` | `projects_db.py:588-597` | `(conn, session_id) -> Optional[str]` |
| `pdb.bind_session_project` | `projects_db.py:639-646` | `(conn, session_id, project_id=None, *, write_token=None, expected_write_token=None)` |
| `pdb.new_write_token` | `projects_db.py:534-536` | `() -> str`，`wt_` + 32 位十六进制 |
| `service.read_session_write_token` | `service.py:332-365` | `(session_id, hermes_home=None)`；只在参数名含 `conn`/`connection` 或 `VAR_POSITIONAL` 时调用 `reader(conn, sid)` |
| `service.commit_session_binding` | `service.py:248-257` | `(session_id, project_id, hermes_home=None, session_db=None, force=False, *, write_token=None, expected_write_token=None)` |

`None` 同时表示未绑定和旧行 `NULL`。函数注释写明这不是“已核对、可以回滚”。`read_session_write_token` 在属性或签名缺失时抛 `BindingTokenUnsupported`，不把缺失读口当成 token 不存在（`341-364`）。迁移 `_observed_write_token`（`467-474`）把该异常变成 `write_token_unsupported`，不继续绑定。

用例 `tests/clawd_projects/test_project_binding_token.py:19-26` 断言 reader 参数顺序和两个 keyword-only token。**未跑。**

## 3. 条件回滚、崩溃认领、旧 NULL、force、期望项目

### 条件回滚

`_release_expected`（`migration.py:1225-1240`）调用：

`commit_session_binding(session_id, expected_project_id, force=False, expected_write_token=token)`

位置参数是**期望当前项目**，不是新目标，也不是 `None`。`service` 没有 `expected_project_id` 这个名字；语义差在调用约定，不在签名冲突。

DB 侧 `_release_if` 要求非空 `project_id`，否则 `ValueError`，然后一条 `DELETE` 同时比较 `session_id`、`project_id`、`write_token`。`NULL` 不等于 `wt_` token，不匹配则 `applied=False`，不删除。`project_id=None` 且带 `expected_write_token` 会在无条件删除之前拒绝（`673-675` 先于 `676`）。

回滚读回（`1827-1840`）：解绑后绑定行和 reader 都必须为空才记 `restored`。token 已变记 `skipped_modified`，不解绑后来的绑定。不删目录、记忆、会话、旧文件夹行，不归档。`archive_frees_path=false`。

### 崩溃认领

绑定前 `_prepare_bind`（`1366-1420`）生成 token，写入回执的 `pending_write_token`，再返回 `ready`。绑定后必须读回同一 token 且项目 id 一致才 `_mark_committed`（`1515-1532`）。

续跑只在 `pending`、读回 token、目标项目三者一致时认领并跳过第二次绑定（`1390-1393`）。没有预先持久的 token，或已绑到目标但 token 对不上：`bind_unproven`，不认领、不重绑、不回滚、不按路径并入（`1394-1398`，`1441-1452`）。

创建侧同理：意图（slug、description、名称、目录）先写入回执（`_ensure_intent` `647-654`），创建后四处吻合才认领。只因路径相同不认领。

### 旧 NULL 不认领

- 升级不回填 token：`_ensure_binding_token_schema`（`546-550`）只加可空列。
- reader 对 `NULL` 返回 `None`（`580-584,588-597`）。
- 已绑到目标但 token 不是本次 `pending`：走 `unproven`，不覆盖。
- 回滚：已标记绑定但没有 `write_token` → `rollback_unproven`，不解绑（`1795-1802`）。未证明且当前项目碰巧相同、token 对不上 → `rollback_unproven`（`1785-1789`）。
- DB 用例 `test_legacy_schema_adds_nullable_token_and_cannot_infer_rollback` 用错误 token 做条件释放，断言行还在。**未跑。** 迁移续跑没有一条直接插入旧 `NULL` 行再恢复的用例，见验收矩阵 M7。

### 无 force 绕 idle — 只对迁移路径成立

迁移两处都是 `force=False`：`1220`、`1238`。`clawd_projects/` 内没有 `force=True`。

`force=False` 时 `commit_session_binding`（`278-290`）要求会话行存在，并在 state 写回调里调用 `require_idle`，然后才写 `projects.db`。未知 idle 错误变成 `SessionStoreCheckError`，不当空闲。这是同一把 state 写锁下的串行，**不是**两库一次提交。注释在 `261-263` 和 `289`。

`force=True`（`278-279`）跳过存在性检查和 `require_idle`。迁移不用它。子代理预绑定用它，见第 5 节。不能把迁移契约写成“所有项目写入都过 idle”。

### project_id 是期望当前项目

回滚传入的是 `written_project_id`（`1812`）。空期望在 DB 层拒绝，不会落入无条件删除。只改 `bound_at`、token 仍在，条件 `DELETE` 仍匹配，会回滚。同秒重绑会新 token，条件不匹配，记 `skipped_modified`。

## 4. 跨库失败路径不能称整体原子

涉及四个库，各自提交：

| 库 | 迁移里做什么 | 与其他库的关系 |
| --- | --- | --- |
| `project_folder_migrations.db` | 回执，`BEGIN IMMEDIATE` 后单独 `commit` | 不包住 projects/state |
| `projects.db` | 项目行、绑定行、消耗行；`bind_session_project` 自己 `COMMIT` | 在 state 回调之内提交，state 随后失败不能撤销它 |
| `state.db` | 迁移只做存在性与 `require_idle` | 不写会话归属 |
| `panel_folders.db` | 只读；符号链接越出 home 拒绝 | 不写 |

回执字段 `cross_db_atomic` 固定 `False`（`417`、`1070`、`1168`）。成功只认 `success=true` 且 `status=applied`；回滚成功只认 `success=true` 且 `status=rolled_back`。`partial`、`bind_unproven`、`create_unproven` 都不是成功。

必须分开说的失败窗口：

1. 回执已记下 `pending_write_token`，绑定未发生：续跑可以绑定。不是已成功。
2. `projects.db` 已提交，回执还没记下 `write_token`：只有三者一致才认领。对不上则 `bind_unproven`，留下可能的绑定，不自动回滚。
3. 项目已创建，回执没有 `project_id`：只在意图四处吻合时认领，否则 `create_unproven`，不按路径并入。
4. `require_idle` 通过后 `projects.db` 已提交，随后 state 写事务提交失败：绑定留下。锁只缩小竞态，不构成原子提交。
5. 继承路径：`inherit_session_project` 在调用方 state 提交前就 `COMMIT` 了 `projects.db`（`inheritance.py:112-118`）。state 回滚后留下孤儿绑定，没有可运行会话行。同一 child id 重试保留该快照，不重读父项目。
6. 面板首发：先建会话，再绑定，再锁模型，然后才 `start`（`chat-page.tsx:754-765`，`project-send.ts:55-73`）。绑定失败不得改走普通模式。清理调用 `deleteSession`。`delete_session`（`hermes_state_sessions.py:1729-1768`）只删 state 侧会话/消息，**不删** `project_sessions`。锁失败后再删除，可能留下孤儿绑定。
7. TUI 分支补偿 `discard_unadmitted_session`（`1800-1816`）只删 state 行，不释放项目绑定。
8. worker 预留先提交 state 子行，项目绑定在之后的另一进程步骤。绑定失败不能回滚已提交的子会话行。

没有会话行，和没有孤儿绑定，是两个不同保证。当前实现只保证失败时不放出可运行的未绑定副本（在异常没有被吞掉的入口上）。不保证没有孤儿 `project_sessions` 行。

## 5. 新会话 / 分叉 / 压缩 / child 的实际入口

共享 helper 不代表所有入口都经过它。归属只看该 profile `projects.db` 的 `project_sessions` 行。不读浏览器当前选中项，不沿 `parent_session_id` 向祖先回绑。Profile 目录取 `state.db` 父目录（`inheritance.py:29-36`），不用进程 `get_hermes_home()`。缺文件视为无绑定且不新建空库；文件在但读失败则 `ProjectInheritanceError`。

### 新会话：不继承

| 入口 | 调用链 | 项目行为 |
| --- | --- | --- |
| API `POST /api/sessions` | `api_server.py:2590-2658` 直接 `INSERT` | 无 `parent_session_id`，不调用 inherit，不绑定项目 |
| 面板首发 | `getPendingNewChatProjectId`（`project-send.ts:25-31`，sessionStorage 键 `hp_pending_new_chat_project`）在发送时读取；`prepareCreatedSession` 先 bind 再 lock，成功后 `chat-page.tsx:765` 才 `start` | 显式绑定所选项目。空选择是普通模式，不是绑定失败回退。回执必须 `success` 且 session/project id 一致（`84-101`） |
| 面板绑定路由 | `clawd_project_routes.py:371-394` → `bind_session_to_project(sid, project_id, session_db=sdb)` | 默认 `force=False`，不传 token，DB 自造新 token。注释已写明不是跨库原子提交 |
| TUI `session.create` | `methods_session.py:458-492` | 空草稿不落库。有父且有历史才立刻 seed。远程 cwd 的项目草稿会提前落行 |

首发成功不能证明分叉或子代理已继承。

### 分叉 / 编辑 / 本地 branch / reset：经过 inherit，但入口语义不同

共同底层：新行才复制。`_insert_session_row`（`hermes_state_sessions.py:430-439`）仅当 `parent_session_id` 且该 id 原先不存在时调用 `_copy_child_project_binding` → `inherit_session_project`。已有行重写不重新绑定。

| 入口 | 实际调用 | 失败时是否阻止发布 |
| --- | --- | --- |
| API `POST /api/sessions/{id}/fork` | `api_server.py:2818-2824` `create_session(..., parent_session_id=source_id)` | 异常会冒出，不返回 201。**没有**把 `ProjectInheritanceError` 映射成 sidecar 那种 503 `project_inheritance_failed`。创建成功后的 `end_session`、`replace_messages`、改标题是后续独立调用，不是与绑定同一个提交 |
| 侧车编辑重发 | `create_edit_branch` → `_persist_edit_branch_project`（`sidecar/hermes_panel_sidecar.py:1725-1756`）在 state `commit` 前调用 inherit | 失败变 HTTP 503，回滚 state。幂等重试返回已有行，不按当前父项目重绑 |
| 核心 API 编辑 | `hermes_state_api_edit.py:244-251` 走 `_publish_child_session_row` | `ProjectInheritanceError` 包成 `RuntimeStoreError("project_inheritance_failed")` |
| 本地 branch | `hermes_state_mutation_branch.py:77-82` → `_publish_child_session_row` | 异常留在调用方 state 事务里，子行不提交 |
| 本地 reset | `hermes_state_mutation_reset.py:32-35` 同一发布函数 | 同上 |
| TUI/桌面分支 | `methods_session.py:249-251` `create_session`；`474-479` 捕获后 `_refuse_unreleased_session` | 继承失败不把 sid 交给客户端。其他异常在 `_seed_branch_row`（`306-310`）被记日志并回落懒创建；补偿删除不清理 `projects.db` |

`inherit_session_project`（`inheritance.py:58-125`）不走 `commit_session_binding`，因此**不调用 `require_idle`**。它直接 `bind_session_project(conn, child_id, parent_pid)`，由 DB 自造新 token，并在同一 `write_txn` 写消耗表。子行已存在则原样返回，不重读父项目，也不清绑定。跨 profile 且父已绑定：拒绝且不写。父未绑定：子也不写行。

### 压缩

两条发布都进 `_publish_child_session_row`（`hermes_state_compression.py:372-380`），在 state 提交前 inherit：

- 自动压缩：`conversation_compression.py:3290-3318`。`publish_compression_child` 返回之后才 `agent.session_id = new_session_id`。发布抛错则不切换内存会话，父会话不被这条赋值结束。
- 本地压缩动词：`hermes_state_mutation_compress.py:17-21`。`in_place` 只归档，不建子行，也不继承。非原地才 `publish_on_connection` → `_publish_compression_child_on_conn` → 同一子行发布。
- worker 压缩包装：`hermes_state_worker_compression.py:216-224` 只转发到上述函数，没有第二条绑定实现。

压缩调用方在 `publish` 返回后才换 `agent.session_id`。这不能证明 projects.db 与 state.db 一起提交。崩溃窗口仍是孤儿绑定。

已有用例 `test_compression_and_branch_publisher_snapshot_before_release`、`test_compression_bind_failure_keeps_parent_and_does_not_switch`。**未跑。**

### child：两条写入点，只有一条过 inherit

| 路径 | 实际入口 | 是否 inherit | idle |
| --- | --- | --- | --- |
| 进程内子代理，尚无预留 id | `delegate_tool.py:203-225` `_child_session_id_for_project` 在 `AIAgent()` 之前 `bind_session_to_project(..., force=True)` | 否。稍后 `_reserve_child_transcript`（`delegate_tool_child_run.py:41-44`）或 `run_agent._ensure_db_session`（`336-345`）若插入新行，inherit 会看到已有子行并**保留 force 写入的快照**，不重读父项目 | **绕过**。`force=True` 不检查会话是否存在、是否空闲 |
| 进程内，父 home 与子 home 不同 | 同上，`215-216` 直接返回，不写 | 不复制外 profile 的 project id | 不适用 |
| worker `child.reserve` | `gateway/session_worker_child.py:61-64` `insert_session_row_in_transaction(...)` | **不调用 inherit**。state 行和 `worker_executions` 已在 owner 事务提交 | 预留本身不写项目 |
| worker 子 store | `agent/runtime_session_child.py:27-33` `create_session` 不传 `parent_session_id` | 即使以后再 upsert，也不会因为 parent 触发 inherit | 不适用 |
| worker 子代理真正绑项目 | 仍是 `_build_child_agent` → `_child_session_id_for_project`。预留 id 已存在时对该 id `force=True` 绑定 | 绑定发生在 owner 事务之外 | **绕过 idle**，尽管 state 行已经存在 |
| `run_agent` 懒创建 | `create_session(..., parent_session_id=self._parent_session_id)` | 仅新行 inherit | 继承本身仍无 idle |
| 继承失败 | `run_agent.py:347-354` 重新抛出，不把失败当成可重试的瞬时错误 | 阻止本回合把子会话当成已创建 | 其他异常仍被吞掉并留待下回合重试（`355-357`）。这是刻意区分，不能把所有存储错误都改成同一语义 |

`tests/clawd_projects/test_project_core.py` 的 `test_child_subagent_inherits_project` 走 `_build_child_agent`，覆盖的是 force 预绑定这条，不是 worker `reserve_child_execution`。**未跑。** 没有用例证明 worker 预留行在绑定失败时不会被后续执行捡起。

跨 profile 的进程环境变量不能用来定位项目库。worker 预留用的是 owner 的 state 连接；随后 force 绑定用 `get_hermes_home()`。两者不一致时，子行可以带着 `parent_session_id` 发布，而 `projects.db` 没有快照。这是待实现点，不是已完成的身份继承。

## 6. 已有用例与覆盖缺口

全部下列用例都已写在树上。**本轮未执行，不能记 pass。**

### 迁移 `tests/clawd_projects/test_project_migration.py`

| 用例 | 覆盖的恢复条件 | 未覆盖 |
| --- | --- | --- |
| `test_apply_is_idempotent_and_rollback_keeps_source` | 成功绑定读回 token；回滚后源文件夹仍在 | 消耗表是否仍留有该 token |
| `test_rollback_restores_only_unchanged_binding` | 未改绑定才回滚 | 并发改 token |
| `test_aba_same_project_id_is_not_rolled_back` | 同项目重绑新 token，回滚 `skipped_modified` | 消耗表拒绝旧 token 复用 |
| `test_bound_at_change_alone_still_rolls_back` | 只改 `bound_at` 仍回滚 | 无 |
| `test_unproven_bind_is_not_adopted_or_rolled_back` | 清掉回执 token 后不认领、`rollback_unproven` | 库内旧 `NULL` 行 |
| `test_crash_after_bind_claims_persisted_token_only` | 回执保留 pending、列上 token 一致才认领 | 消耗行缺失仍会被认领 |
| `test_crash_after_create_adopts_only_proven_intent` | 创建后崩溃只认意图 | 不测 state.db |
| `test_cleared_intent_is_not_adopted_by_path` | 改过的意图不按路径认领 | 无 |
| `test_partial_failure_is_not_success_and_resume_does_not_duplicate` | 中途失败不是成功，续跑不建第二个项目 | 不是杀掉进程的真实崩溃 |
| `test_bind_rechecks_idle_not_plan_snapshot` | 计划后变忙，绑定时拒绝 | 不证明 `force` 没被其他入口使用 |
| `test_retry_reuses_only_confirmed_empty_receipt_project` | 新幂等键 + `confirm_reuse` 才复用空项目 | 无 |
| `test_reuse_refuses_nonempty_project` | 非空不复用 | 无 |
| `test_forbidden_and_foreign_homes_do_not_write` | 隔离范围外不写 | 未对生产路径做探针；也不应做 |

### token `tests/clawd_projects/test_project_binding_token.py`

覆盖签名、旧 schema 的 `NULL` 不能被错误 token 释放、重绑 ABA、供应 token 幂等且不可复用、条件释放比较项目且拒绝时间戳和双 token。**没有**“`_remember_token` 在 `INSERT` 之后抛错，两表都回滚”的注入。

### 继承 `tests/clawd_projects/test_project_inheritance.py`

覆盖缺库不创建、跨 profile 拒绝、不可读失败关闭、已有子快照不重绑、编辑分支崩溃快照、压缩/branch 发布前快照、绑定失败不切换父会话、归档不删会话且 fork 仍复制 id。**没有** worker `child.reserve`，**没有** API fork 的 HTTP 错误码，**没有** `discard_unadmitted_session` 后的孤儿绑定。

## 7. 精确待实现点

不要重写已有的 token、条件删除和回执认领。下面是当前字节上仍然缺的接线或证明，不是授权现在改代码。

1. **认领不证明消耗行。** 若契约要求“认领必须看到 `project_binding_tokens` 中同一 session/project/token”，待改 `clawd_projects/migration.py` 的 `_prepare_bind`、`_verify_binding`、回滚读回。当前只读 `project_sessions`。写入侧同事务不能代替这个复核。
2. **worker 子会话不在 owner 事务里快照项目。** 待改 `gateway/session_worker_child.py:61-64`，在 `insert_session_row_in_transaction` 之后、该 state 事务提交之前调用与 `_copy_child_project_binding` 相同的失败关闭复制。失败必须让预留事务回滚，不能先提交子行再指望 `force=True`。配套：`agent/runtime_session_child.py` 不应成为第二条继承实现。
3. **子代理预绑定绕过 idle。** `tools/delegate_tool.py:224` 的 `force=True` 是服务注释允许的“行尚未存在”用途，但 worker 路径在行已存在时仍用它。待改该函数：行已存在必须 `force=False` 并传入该 profile 的 `session_db`；行尚未存在只能走第 2 点的事务内复制，不能再先提交 `projects.db`。`hermes_home` 必须来自 child state 文件的父目录，不能只用调用时的 `get_hermes_home()`。
4. **API fork 没有结构化继承失败。** 待改 `gateway/platforms/api_server.py:2818-2836`：捕获 `ProjectInheritanceError`，不返回 201，不 `end_session`。消息复制失败也不能把已创建的 fork 报成完整成功。这不是把 fork 改成跨库原子。
5. **清理会话不释放项目行。** `discard_unadmitted_session` 与 `delete_session` 不写 `projects.db`。若产品要求首发/分支补偿不留孤儿绑定，待改调用方在删除 state 行之前做条件释放；失败要同时报告两个错误。不能在 `delete_session` 里悄悄无条件解绑。
6. **中途失败两表回滚没有用例。** 待补 `tests/clawd_projects/test_project_binding_token.py`，注入 `_remember_token` 失败。不改产品也能先写这个证明。
7. **迁移续跑缺少旧 `NULL` 行用例。** 待补 `test_project_migration.py`：目标项目上已有 `write_token IS NULL` 的行，续跑必须 `bind_unproven` 或冲突，不得覆盖，回滚不得删除。
8. **build/lib 过期。** 不手改 `build/lib`。验收和安装必须从当前源码构建；用旧 `build/lib` 不能证明签名已对齐。

不在本域待办、不要顺手改：侧车文件夹接口退役、并入任意已有项目、生产数据迁移、记忆页、动画。

## 8. 隔离故障注入验收矩阵

全部用新建临时目录。设置 `HOME`、`CLAWD_HOME`、`HERMES_HOME`、`TMPDIR` 指向该目录。不要设置到 `~/.hermes`、`/root/.hermes`、`/usr/local/lib/hermes-agent`、原面板或 `/var/www`。不要打开现有 `projects.db` / `state.db`。mock 只用于把失败注入到指定语句；持久化断言必须读临时库的真实行。

进程杀掉用测试替身：在 `_remember_token`、`_save_receipt` 之后、`commit_session_binding` 返回前抛异常。这不是生产崩溃演练，也不能称为已做崩溃恢复验收。

| ID | 注入 | 必须观察到 | 不能观察到 |
| --- | --- | --- | --- |
| M1 | `bind_session_project` 正常供应 `wt_` token | `project_sessions.write_token` 与 `project_binding_tokens` 同一 token、同一 session、同一 project；一次提交后两表都在 | 只写其中一表 |
| M2 | `_remember_token` 在会话 `INSERT` 之后抛错 | 两表都没有该 token；会话行不存在或保持注入前的值 | 会话行留下新 token 而消耗表没有，或相反 |
| M3 | 条件释放，三字段匹配 | `project_sessions` 行消失；消耗行仍在；同一 token 再绑定抛 `not reusable` | 无条件按 session_id 删除其他项目的行 |
| M4 | 条件释放，项目 id 错误、token 为空、或 `project_id=None` 加 `expected_write_token` | 不删除；`None` 路径是 `ValueError`，不是无条件解绑 | `applied=True` |
| M5 | 同秒解绑再绑，或只改 `bound_at` | 新 token 不回滚；只改 `bound_at` 仍回滚 | 用 `bound_at` 当证明 |
| M6 | 回执有 pending，绑定已落库，回执未记 `write_token`，三者一致 | 认领，不第二次绑定，不新建项目 | 再插一行或覆盖别的项目 |
| M7 | 目标上是旧 `NULL` token，回执无匹配 pending | `bind_unproven` 或冲突；行仍在；回滚不删 | 认领、覆盖、或因项目 id 相同而解绑 |
| M8 | 读口缺失或签名不是 `(conn, session_id)` | `BindingTokenUnsupported` / `write_token_unsupported`，不写 | 把缺失读成 `None` 后继续 |
| M9 | 计划时空闲，绑定前回合租约或压缩锁出现 | `session_busy`，不写绑定 | `force=True` 或沿用计划快照 |
| M10 | 迁移调用栈上搜索实际参数 | 两处都是 `force=False`；回滚位置参数是回执中的项目 id | 出现 `project_id=None` 的回滚调用 |
| M11 | 创建后、回执记下 `project_id` 前失败；续跑时 slug/description/名称/目录之一被改 | `create_unproven`，不绑定 | 按 `primary_path` 并入 |
| M12 | `projects.db` 提交函数已返回，state 回调之后抛错 | 绑定或孤儿行可以留下；调用方不得返回成功；同一 child id 重试不改写已有快照 | 文档或响应写“整体原子提交” |
| M13 | 父已绑定，inherit 在 state 提交前失败 | 子 state 行回滚；API/侧车/压缩不切换到子 id；不返回 201 | 未绑定子会话被 chat 命中 |
| M14 | `projects.db` 已提交、state 未提交后用同一 child id 重试；其间父项目已变 | 子行保持第一次快照 | 重读父项目并改绑 |
| M15 | worker `reserve_child_execution` 后、force 绑定前失败 | 当前代码会留下无项目绑定的 state 子行。验收在第 7.2 点落地前应把它标为 **fail/未实现**，不能标 pass | 把 `parent_session_id` 列当成项目身份 |
| M16 | 子代理 `force=True` 写入后父会话变忙，再走普通 `commit_session_binding` | 预绑定已经绕过 idle。若第 7.3 点未改，此项是已知缺口，不是迁移用例通过 | 用迁移测试的 `force=False` 声明子代理也过了 idle |
| M17 | 面板首发 bind 回执项目 id 不符，或 lock 失败且 remove 失败 | 不 `start`，不改普通模式；两个错误和会话 id 都在 | 只报其中一个错误 |
| M18 | remove/delete 之后读 `projects.db` | 记录孤儿绑定是否仍在。当前 `delete_session` 不删它 | 把 HTTP 删除成功写成绑定已回滚 |
| M19 | 生产路径、符号链接指向隔离目录外的 `panel_folders.db` | 403/拒绝，函数不打开目标库 | 为了让用例变绿而去读 `~/.hermes` |

M15、M16 在代码改前预期就是缺口。不要把“测试失败”修成放宽断言。

## 9. 验收命令与依赖

未执行。准备命令不等于 pass。退出码约定：全部必需检查通过才 0；断言失败 1；阻塞、未知、未跑或空结果 2。

依赖：

- 源码树 `/root/hermes-workspace/clawd`，不要用 `build/lib`。
- 项目规定的隔离 Python。交接里的 `.venv` 3.14.9 只是历史记录，本次未复核。
- 临时 `HOME` / `CLAWD_HOME` / `HERMES_HOME` / `TMPDIR`。核对 `scripts/run_tests.sh` 是否用 `env -i` 清掉外层变量；清掉则用例自己建临时 home。
- 不需要网络、模型、MCP、浏览器、生产 secret、生产 `state.db`。
- 面板类型检查/构建是另一层，不能代替本矩阵。

核心命令（隔离环境，限制并行）：

```sh
scripts/run_tests.sh -j 1 --file-timeout 90 \
  tests/clawd_projects/test_project_binding_token.py \
  tests/clawd_projects/test_project_migration.py \
  tests/clawd_projects/test_project_inheritance.py \
  tests/clawd_projects/test_project_core.py \
  tests/clawd_projects/test_project_isolation.py
```

面板绑定顺序（同样未跑）：

```sh
node --test tests/project-panel.test.mjs
```

通过这些命令仍不等于端到端迁移验收，更不等于可以迁生产。

## 10. 本次未做

- 未运行任何测试、构建、导入、探针。
- 未打开生产数据库，未读生产秘密，未碰 `/usr/local/lib/hermes-agent`、原面板主检出和线上目录。
- 未执行迁移或回滚，未搬历史会话。
- 未改源码、测试或其他文档。
- 未把已有实现标成已验收。M15/M16 和认领不读消耗表，是待实现或待补证明，不是本轮失败的运行结果。
