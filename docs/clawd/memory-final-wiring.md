# 空闲改绑下一回合记忆接线

未运行 pytest、构建或导入探针。未改 `agent/agent_init.py`、`run_agent.py`、service、`projects_db`、迁移、UI、锁文件。没有项目记忆页面，未新造。下面的哈希是改完后的 `sha256sum`，不是验收。

## 调用链

复用 agent 的下一回合从 `TurnFacadeMixin.run_conversation` 进入。

1. `admit_durable_turn_lease` 成功后才刷新。未拿到租约就返回，不改记忆。准入可能把 `agent.session_id` 指到压缩后的 tip，刷新读的是这个 id。
2. 只有 `facade_owner`（`_turn_facade_entered` 为假）才调用 `_idle_project_memory_retarget`（`agent/turn_facade.py` 374–376）。嵌套入口不碰外层回合。
3. 然后才调用 `conversation_loop.run_conversation`（同文件 426 行）。
4. 循环里的 `build_turn_context` 才 `_bind_turn_identity` 写入 `_current_turn_id`（`agent/turn_context.py` 1152），之后若 `_cached_system_prompt is None` 才 `restore_or_build_system_prompt`（1208–1209）。

因此刷新发生在本回合 turn id 和提示快照之前。已进入的 facade 直接返回，不清除 live turn id，不换 store。

API 每回合新建 agent，构造时 `_init_memory` 会 attach。TUI/Desktop/网关缓存复用同一个对象，才走这条空闲刷新。Codex 回合也从 `conversation_loop` 进去，排在这次刷新之后。

## 本回合做了什么

旧实现会调用 attach，但有三个缺口，所以不是“已经接好”：

- 用 `get_hermes_home()`，多 profile 时可能读到进程环境，不是会话库所属 profile。
- 已有 `ProjectMemoryStore` 再 attach，会把旧项目包成新 store 的 global。
- 绑定变化时把 `_cached_system_prompt` 设成 `None`。继续会话会从 `state.db` 恢复旧提示，新项目记忆进不了本回合。

现在：

- profile 取 `session_db.db_path` 的父目录（`state_db_profile_home`）。没有可用路径才看冻结记录，最后才是进程 home。解析和 attach 期间设置 context override，结束即复位，不把进程环境改掉。
- 没有 `projects.db` 且已经冻结了项目时，不把缺失当成解绑。
- 改绑前先剥到最内层 global store，再调用现有 `_attach_project_context`。不沿 `parent_session_id` 回绑。
- 显式解绑后 store 只留 global，`project_id` / `project` 清空。普通模式不广告 `project` / `global_memory`。
- 项目模式仍是 global store 加该项目 store。
- 子代理 `memory_writes_allowed` 保持假；若 attach 把 freeze 标成可写，再冻成不可写。不把子代理写成可写。
- 绑定、home 或嵌套确实变了，才重建提示并尽量 `update_system_prompt`。缓存不是 `None`，循环不会拿旧行覆盖。未变化不重建，避免无故打断前缀缓存。
- 安装失败会回滚 store、freeze、project 和提示，再抛出。本回合不会带着半套新范围跑。

`agent/turn_facade_lease.py` 未改。租约只决定这一回合能不能开始；记忆刷新在准入成功之后、循环之前。

`clawd_projects/project_memory.py` 未改。新的复合 store 自己 `load_from_disk` 就会封新快照，不需要改存储或授权。

## 未改的禁止文件

`_attach_project_context` 仍在 `agent/agent_init.py`。它在 `proj is None` 时不卸复合 store，也不清 `agent.project_id`；已有复合 store 时会把它当成 global 再包一层。当前只有两处调用：初始化（此时还不是复合 store）和本文件的空闲刷新。空闲路径在调用前后补偿，所以没有改 `agent_init.py`。以后若有第三个调用方直接再 attach，这个缺口还在。

## 用例

`tests/clawd_memory_scope_cases/test_profile_session_scope.py` 和 `test_snapshot_subagent_cache.py` 不打记忆 HTTP，未改。跨 profile、错项目、严格整数 generation、运行冻结仍在 `tests/clawd_projects/test_project_api.py`，本路未改那个文件，也没有 skip。

归档用例改为：无 `session_id` 时期望 401 `missing_identity`；真实 `SessionAuthority` 下错项目 403 `project_mismatch`；带 `session_id` 的 GET 仍要读到原事实。归档不删目录、会话和绑定的断言还在。POST 仍不当普通模式拒绝的证据。

新文件 `tests/clawd_memory_native/test_turn_rebind_context.py` 覆盖：下一回合在 turn id 赋值前刷新且不嵌套；已进入回合不换；绑定不变保留提示；普通模式只全局；解绑不抄祖先；子代理改绑仍只读；session db 的 home 胜过进程环境。都未运行。

## 哈希

```
0c1118543afeb3c2cf01da10fcda4ecedc2476af929390a21eb3939212e7dad2  agent/turn_facade.py
1addcf9f0e9b6ae286799b429731f577a976a763317d428125e47969e2cf49bf  tests/clawd_memory_scope_cases/support.py
6fa2b1edca5e9c578a8038b2b9be6d899972b9832b1f05e7ee190175128280da  tests/clawd_memory_scope_cases/test_write_freeze_archive.py
ee9a8b4e90bd7e85b1fd7edcc08a8564c4ad2b8688c6d35368bf422b0dd07c88  tests/clawd_memory_native/test_turn_rebind_context.py
```

## 仍未验证

统一验收前不要把这些用例当成通过。建议只在隔离 `HOME` / `CLAWD_HOME` / `HERMES_HOME` / `TMPDIR` 下跑：

```sh
scripts/run_tests.sh -j 1 --file-timeout 180 tests/clawd_memory_native/test_turn_rebind_context.py tests/clawd_memory_scope_cases/
```

提示持久化失败时，本回合内存里是新字节，但日志会写明后来的新 agent 可能恢复旧行。那不是已验证的持久化成功。
