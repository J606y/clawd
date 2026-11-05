# 运行时失败边界

本文件只记录本次失败边界修复，不是工程完成声明。

## 根因

隔离 TUI 启动时，canonical `session.create`（`source=tui`）在配置里没有模型名时把本地 policy 的 `model` 写成 JSON `null`。这不是凭据检查：`LocalSessionPolicy.model` 允许 `None`，`build_policy` 在客户端未提交 model 时也不拒绝。`restore_policy` 把非空字符串之外的 model 一律当成 `invalid policy`（原 1007 行），`session_local_recovery` 再包成 `storage_unavailable`。上路 PTY 的 `errors.log` 最后一行就是这条链。Nous 未登录是另一条 auxiliary 警告，不是这条拒绝的原因。

`ProjectInheritanceError` 会回滚子会话行。`AIAgent._ensure_db_session` 把所有 `Exception` 记成可重试警告；`agent.turn_context._persist_under_lock` 再吞掉，压缩和模型调用仍会开始。TUI 的行创建、seed、`prompt.submit` 和 compute host 的 `suppress(Exception)` 也会把绑定失败当成瞬态存储错误，或在失败后仍 `_run_prompt_submit`。

## 改动

- `restore_policy` 接受显式 unset model（JSON null），不从当前默认重建模型。缺字段、空白或非字符串仍然是 `storage_unavailable`。
- `_ensure_db_session` 继续警告并重试普通瞬态错误；`ProjectInheritanceError` 原样抛出。
- `_persist_under_lock` 不吞绑定失败，因此回合设置在压缩和模型之前停止。
- TUI seed / 行创建 / prompt / compute host 对绑定失败返回明确错误或 `turn.error` `project_inheritance_failed`，不启动模型或 tool，也不改写成 `storage_unavailable`。

## 实测

`scripts/run_tests.sh -j 1` 会 `env -i`，不转发 `HERMES_HOME`/`CLAWD_HOME`/`TMPDIR`，只转发 `HOME`。本次把 `HOME` 和 pytest `--basetemp` 放在 `artifacts/runtime-boundaries`。3 文件 7 测试通过，退出码 0。日志 `artifacts/runtime-boundaries/logs/regression.log`。

隔离 PTY：`HERMES_TUI_DIR` 指向已构建 `ui-tui`，避免在源码树内的 `TMPDIR` 上重构建。画面出现工具/技能面板，没有 `storage_unavailable`。`state.db` 留下 `source=tui`、`model=None` 的会话；进程结束后 `restore_policy` 仍能读回该收据。`errors.log`/`gateway.log` 没有 `invalid policy` 或 `storage_unavailable`。`/quit` 在 20 秒内没有退出，探针发了 SIGTERM，退出码 -15。这不是干净退出。

模型未配置，真实模型回合阻塞。未读生产密钥。

## 父代理复验

父代理回读了 run_agent、agent/turn_context、TUI prompt/compute/seed 的错误传播分支，并重跑新增三类边界测试、全部项目测试及 API server/runs：10 文件、219 测试通过，退出 0。证据：artifacts/parent-review/runtime-boundaries-regression.log。测试包括真实 SessionDB 与 projects.db 下绑定失败回滚，随后控制流不得执行；不等同于真实模型或工具端到端。

PTY启动结果仍为子代理探针证据；父代理未独立重做PTY。/quit 超时及 SIGTERM 退出没有通过正常生命周期验收，不能将该项标为完成。未提交、推送、部署。

## 未覆盖

未配置模型时不能跑真实模型回合，标为阻塞，不读取生产密钥。全工程、面板、生产服务不在本次范围。
