# 记忆作用域用例准备

本文件只记录尚未运行的静态用例。未执行 pytest、构建或探针。未读生产凭据、官方安装或原面板。未改产品、原测试或迁移实现。未新增依赖。

## 不重复的已有覆盖

`tests/clawd_projects/` 已覆盖：普通 `MemoryStore` 拒绝 `project`、复合存储的提示快照与审批暂存、子代理能读到项目事实、跨 profile 子代理不复制项目 id、归档不删工作目录和会话、fork/编辑重发/压缩的绑定行快照。迁移路由未冻结，这里不调用、不补迁移用例。

缺口是记忆读写的 profile、并发、冻结目标和缓存字节，不是再断言同一条 `project_sessions` 行。

## 新增文件

- `tests/clawd_memory_scope_cases/support.py`
- `tests/clawd_memory_scope_cases/test_profile_session_scope.py`
- `tests/clawd_memory_scope_cases/test_write_freeze_archive.py`
- `tests/clawd_memory_scope_cases/test_snapshot_subagent_cache.py`

## 新增覆盖与真实入口

| 缺口 | 入口 | 断言 |
| --- | --- | --- |
| 两 profile 相同 session id 不串项目 | `resolve_session_project`、`get_project_memory_store`、`AIAgent`；临时 `projects.db` | A/B/诱饵三库同 id，显式 home 与 context override 只见本库事实和本库记忆目录 |
| 同进程并发 scope 不依赖全局 env | 两线程；`HERMES_HOME`/`CLAWD_HOME` 指向诱饵 | 显式 `hermes_home` 与 `set_hermes_home_override` 并发解析、加载并初始化 agent，不读诱饵或对方事实。无 override 的对照调用必须落到诱饵，证明 env 回落存在但 scoped 调用不用它 |
| 普通模式拒项目写 | 无绑定会话的 `AIAgent` + `INLINE_TOOL_EXECUTORS["memory"]` | `project`/`global_memory` 及 batch 在审批前拒绝；不暂存、不落盘、不建项目记忆目录。schema 不含这两项目标 |
| 全局与项目目标冻结 | 已绑定 agent，随后 `bind_session_to_project` 改到另一项目 | 已构建 schema、`project_id` 和系统提示不变。`project` 写只可能进入原项目目录，`global_memory` 只可能进入全局 `MEMORY.md` |
| 绑定快照后父移动不改子 | `SessionDB.create_session(..., parent_session_id=)`，即插入新行时的 `inherit_session_project` | 父改绑后子 agent 仍读、仍写原项目记忆。之后新建的兄弟会话才跟随新父项目 |
| 显式解绑不回绑 | `bind_session_to_project(..., None)` 后再 `create_session` 同一 id | 已有行不再继承。新 agent 按普通模式拒绝项目写，原项目与新项目记忆都不变 |
| 子代理只读不写 | `_build_child_agent` + `validate_tool_calls` | 冻结快照含父项目事实；`memory` 不在工具面。未知工具回合在执行前返回 continue，记忆文件字节不变。父移动不改子绑定和 `project_store.memory_dir` |
| 归档不删目录记忆 | `APIServerAdapter.connect()`，`PATCH /api/projects/{id}` `archived=true`，再 `GET .../memory` | `MEMORY.md` 字节、工作目录、会话行、绑定行都还在 |
| 提示缓存与覆盖顺序 | `_restore_or_build_system_prompt`、`build_system_prompt_parts` | stable 不含记忆和 `AGENTS.md`。全文顺序是 stable、context（`PROJECT-RULE-TOKEN`）、volatile 内全局 `MEMORY`、`PROJECT MEMORY`、`USER PROFILE`。二次打开复用已持久化字节，不含缓存之后才写入磁盘的事实 |

审批不放宽：不替换 `evaluate_gate`。项目目标拒绝发生在门禁之前，必须是 `success=false` 且非 staged。允许路径若暂存，则对应磁盘不得出现该条；若落盘，只能出现在冻结时的那个存储。两种都是真门禁结果，不能把暂存当成已保存。

缓存用例在前缀已持久化后，用 `MemoryStore.add` 写入 `DISK-AFTER-CACHE-TOKEN`。这是已批准重放会调用的存储原语，用来证明重建会看见、恢复路径不得看见。它不代替工具门禁断言。

## 精确阻塞

- `API_MEMORY_WRITE_BLOCKER`：`handle_update_project_memory` 只按 project id 调用 `apply_batch`，没有可信会话身份，也不走 `memory_tool` / `evaluate_gate`。不能用它证明普通模式拒绝或审批。本目录不用该 POST 当写入证据。
- `INVOKE_TOOL_NOT_SUBAGENT_GATE`：`invoke_tool` 对 inline `memory` 不查 `valid_tool_names`。子代理不写的证据是构建出的工具面加上回合内 `validate_tool_calls`。不把直接 `invoke_tool` 说成只读门禁。
- 迁移预览/执行/回滚接口另路且未冻结，本目录没有这些用例。

## 未运行与依赖

现在不运行。集中验收时用隔离 `HOME`/`CLAWD_HOME`/`HERMES_HOME`/`TMPDIR`，工程解释器与 `scripts/run_tests.sh`，不要借生产 home。建议命令：

```sh
scripts/run_tests.sh -j 1 --file-timeout 180 tests/clawd_memory_scope_cases/
```

真实依赖：本仓库 `clawd_projects`、`hermes_cli.projects_db`、`hermes_state.SessionDB`、`run_agent.AIAgent`、`tools.memory_tool`、`tools.delegate_tool._build_child_agent`、`agent.turn_tool_validation.validate_tool_calls`、`agent.conversation_loop._restore_or_build_system_prompt`、`gateway.platforms.api_server.APIServerAdapter`。无新依赖。归档用例会在临时端口启动真实适配器，仍不读取生产数据。

根 `tests/conftest.py` 会把进程 `HERMES_HOME` 指到每测临时目录。用例在此之上再把 env 指到诱饵库，避免把夹具 home 误当成隔离已证明。
