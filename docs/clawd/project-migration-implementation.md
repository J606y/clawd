# 旧文件夹显式迁移实现

身份：本次实现运行于 grok-4.7，与指定一致，未改配置。测试与构建未运行。service.py 未改。

## 契约

迁移只读当前隔离 home 的 `panel_folders.db`。home 必须是 `CLAWD_HOME` 本身，或其 `profiles/` 下的当前 profile（与现有项目路由一样，在请求处理时取 `get_hermes_home()`）。生产 `~/.hermes`、`/root/.hermes`，以及指向隔离目录外的符号链接，直接拒绝，不打开。

不猜测目录，不新建用户，不复制会话、记忆、凭据或文件。项目名称以旧文件夹记录为准。会话列表以 `assignments` 为准，请求不得增删，也不得带 `project_id`。`create_directory` 只有 JSON `true` 才创建目录。执行必须 `confirm=true`。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/projects/migrations/preview` | 只读清单。无库时 `source_missing=true`，不是迁移成功。`primary_path` 始终为 null |
| POST | `/api/projects/migrations/preview` | 只读核对显式映射。不建目录、不写回执 |
| POST | `/api/projects/migrations` | 显式执行。`confirm` 必须是 JSON true |
| GET | `/api/projects/migrations/{migration_id}` | 读回执 |
| POST | `/api/projects/migrations/{migration_id}/rollback` | 回滚本回执写入且指纹仍吻合的归属 |

路径未变。执行和核对可额外带 `reuse_migration_id` 与 `confirm_reuse`。二者缺一不可，且 `confirm_reuse` 必须是 JSON `true`。缺省不会复用任何已有项目。

成功只认 `success=true` 且 `status=applied`。预览/核对的 `success` 只表示读取完成。冲突、忙碌、缺会话在任何项目写入前拒绝。部分写入返回 `status=partial`、`success=false`，HTTP 409。`bind_unproven` 与 `create_unproven` 也不是成功。

同一 home 用线程锁加 `project_folder_migrations.lock` 串行。相同幂等键已完成则读回核对后原样返回，不第二次创建项目，也不覆盖后来改过的归属。已回滚的键不会自动再迁移。

面板在原有“未迁移”提示下增加映射入口：先核对，再确认单文件夹。不预填目录，不改出字动画，不改侧车。回滚后若预览给出 `reuse_offer`，必须另按“确认复用此回执的空项目”，再核对、再执行。默认执行不带复用字段。

## 恢复

`projects.db`、`state.db`、`panel_folders.db` 不能原子提交。已核对 `hermes_cli.projects_db.create_project`：没有 `project_id` 参数，id 在函数内部生成。因此不能预写稳定 project id。创建前把唯一意图写入回执：`intended_slug`（`mig` + 随机十六进制）和 `description` 标记 `clawd-migration-intent:<migration_id>:<folder_id>:<token>`。创建时把这两项传给现有 `create_project` 的 `slug` 与 `description`。

创建后、回执记下 `project_id` 前中断：相同幂等键续跑只在 slug、description、名称、目录四处全部吻合时认领，并记下 `ownership_proof=intent_token`。只因路径相同不会认领，也不会并入冲突项目，不会再建第二个。意图被改过或 slug 被 `_unique_slug` 改写时，状态为 `create_unproven`，不绑定。

逐条绑定前先生成 `wt_` + 32 位十六进制 `pending_write_token` 并写入回执，再调用 `commit_session_binding`。该函数在 state 写锁内执行 `require_idle`，再把 `write_token` 转给 `bind_session_project`。迁移不直接写 `project_sessions`。绑定后必须经 `read_session_write_token` 读回同一 token，且 `get_session_binding` 的项目 id 一致，才记 `applied`。`bound_at` 只作观察值，不是证明。

回执在绑定提交之后、记下 `write_token` 之前中断：续跑看到回执里的 `pending_write_token` 与读回 token、目标项目三者一致才认领，不第二次绑定。没有预先持久的 token，或读回 token 不同，返回 `bind_unproven`，不认领、不重绑、不回滚、不按路径并入。

## 回滚与 ABA

回滚只对已证明的 `write_token` 调用 `commit_session_binding(session_id, expected_project_id, expected_write_token=token)`。这里的位置参数 `project_id` 是期望项目，不是新绑定目标，也不是 `None`。`bind_session_project` 在同一条 `DELETE` 里比较 `session_id`、`project_id`、`write_token`。不先查再无条件解绑。

同秒解绑再绑或普通重绑会生成新 token，即使 `bound_at` 被改回原值也不匹配，记 `skipped_modified`。只改 `bound_at`、token 仍在，仍然回滚。旧行 `write_token` 为 NULL，或回执没有 token，不能推断可回滚，记 `rollback_unproven`，不解绑。

回滚不删除 `panel_folders.db` 行、工作目录、项目记录、记忆或会话，也不自动归档。`archive_frees_path=false`。归档项目仍被 `find_by_primary_path(include_archived=True)` 视为路径占用，不是重试办法。

## 同一目录重试

回滚后默认再次执行仍是路径冲突，不会并入该空项目，也不会并入任意其他项目。可行路径：新幂等键 + JSON `confirm_reuse=true` + `reuse_migration_id` 指向已回滚原回执。服务端重新证明该项目就是原回执创建的（意图标记、slug、名称、目录、id），且仍为空：无会话绑定、无额外文件夹、记忆目录不存在或为空、未归档、字段未改。用户必须在面板另行确认，目录仍自行填写，不预填。

原幂等键已回滚时不会自动再迁。未完成的回执用原幂等键恢复，不借复用开关并入别的项目。

## 冲突

- 会话不存在、忙碌或存储无法核实时，整批不写。绑定当时再次检查空闲，不沿用计划快照。
- 会话已属于其他项目：拒绝，不覆盖。
- 目标目录已有项目（含归档）且不能用本回执意图证明：拒绝，不并入。
- 同一请求重复文件夹或同一目录：400。请求内带 `project_id`：400。
- 幂等键对应不同未完成映射：409，不覆盖旧回执。
- 隔离范围不符：403，不读该库。
- `confirm_reuse` 不是 JSON true，或没有原回执：400，不复用。

## 未做

- 未运行 `tests/clawd_projects/test_project_migration.py` 与 `tests/clawd_projects/test_project_binding_token.py`，未做面板 typecheck/build，未做实际迁移或探针。
- 未改 `service.py`、`project_memory.py`、`inheritance.py`、`gateway/platforms/clawd_project_routes.py`、侧车和动画 CSS。
- 未退役侧车文件夹接口，未物理删除或归档旧文件夹行。
- 未提供“并入任意已有项目”开关。迁移后新加入旧文件夹的会话不会自动绑定。
- 未做生产数据迁移，未读 `/root/.hermes` 或原 hermes-panel。
- 不能称为端到端完成：service 转发已在其文件中，但本次未运行接通验证。

## service 接点（未改其文件）

已对齐的现有签名，无新增必填参数冲突：

- `clawd_projects.service.commit_session_binding(session_id, project_id, hermes_home=None, session_db=None, force=False, *, write_token=None, expected_write_token=None) -> BindingWriteResult`
- `clawd_projects.service.bind_session_to_project(...) -> bool`，只返回 `applied`。
- `clawd_projects.service.read_session_write_token(session_id, hermes_home=None) -> Optional[str]`，内部调用 `pdb.get_session_write_token(conn, session_id)`。
- `clawd_projects.service._write_binding(session_id, project_id, hermes_home, *, write_token=None, expected_write_token=None)` 用 `inspect.signature` 转发到 `pdb.bind_session_project`。缺参则 `BindingTokenUnsupported`，不丢 token。

DB 侧已实现：

- `hermes_cli.projects_db.bind_session_project(conn, session_id, project_id=None, *, write_token=None, expected_write_token=None) -> SessionBindingWrite`
- `hermes_cli.projects_db.get_session_write_token(conn, session_id) -> Optional[str]`
- `hermes_cli.projects_db.get_session_binding(conn, session_id) -> Optional[SessionBinding]`
- `hermes_cli.projects_db.new_write_token() -> str`

语义差异，不是签名冲突：条件释放时位置参数 `project_id` 是期望项目。service 没有 `expected_project_id`。迁移因此调用 `commit_session_binding(session_id, expected_project_id, expected_write_token=token, session_db=..., hermes_home=..., force=False)`，不能改成 `project_id=None`。改成 None 时 DB 拒绝且不无条件删除。绑定调用是 `commit_session_binding(session_id, target_project_id, write_token=pending_token, session_db=fence, hermes_home=..., force=False)`。`force=False` 才走 `require_idle`。

其他调用方仍可用 `bind_session_project(conn, session_id, project_id)`。未传 token 的真正绑定会在同一事务自造新 token。无 token 的 `project_id=None` 仍是兼容的无条件解绑，迁移回滚不走这条。
