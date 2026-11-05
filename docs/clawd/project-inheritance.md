# 项目归属继承

会话是否属于项目，只看该 profile 的 `projects.db` 里 `project_sessions` 这一行。不读浏览器当前选中项，不按工作目录猜测，也不沿 `parent_session_id` 向祖先回绑。

## 调用链

- 普通 API 分叉：`POST /api/sessions/{id}/fork` → `APIServerAdapter._handle_fork_session` → `SessionDB.create_session(..., parent_session_id=源)` → `_insert_session_row`。新行在 state 事务提交前调用 `inherit_session_project`。已有行的再次 upsert 不重新绑定，避免事后改父项目或显式解绑被写回去。
- 编辑重发：sidecar `create_edit_branch` 在插入副本之后、`state.db` commit 之前调用同一 helper。幂等重试只返回已有会话，不按当前父项目重绑。子会话的 `parent_session_id` 仍为空，归属不靠这条外键。
- 压缩续接：`publish_compression_child` → `_publish_child_session_row`。本地 `branch_in_transaction` 和 reset 也走这个发布函数，因此同一快照规则覆盖它们。压缩调用方在 `publish_compression_child` 返回之后才把 `agent.session_id` 指到子会话；绑定失败则异常冒出，父会话不被结束，内存会话 id 不切换。

Profile 目录取 `state.db` 的父目录，不用进程的 `HERMES_HOME` / `get_hermes_home()`。同 profile 才复制。`parent_home` 与子 home 不同且父会话已绑定时拒绝，不把外 profile 的 project id 写入本库。

## 失败与恢复

这不是跨库原子提交。顺序是：先写并提交 `projects.db`，再提交 `state.db`。绑定失败抛 `ProjectInheritanceError`（编辑重发包成 HTTP 503 `project_inheritance_failed`），state 事务回滚。不返回 201，不留下可被 chat 命中的子会话行。

崩溃窗口：`projects.db` 已提交、`state.db` 未提交。结果是孤儿绑定，没有会话行，不能运行。同一 child id 重试时保留该快照，不按之后变化的父项目改写。父会话当时没有绑定行（普通模式，或已显式解除）则子会话也不写行；缺 `projects.db` 文件视为本 profile 没有绑定，不新建空库。`projects.db` 存在但读不出时失败关闭，不当成普通会话放行。

归档只把项目标成 archived，不删除会话、绑定或目录。已归档项目的 id 仍可被复制到新副本。记忆审批契约未改。
