# 核心八路审查独立复核摘要

模型 grok-4.7。只读源码与工作区 SDK，未运行测试、构建、导入或探针，未改产品。不宣布全内核安全。第三方未覆盖不等于安全。计数来自 `reviewed-findings.json`，不是心算。

## 计数

| 桶 | 数量 |
|---|---|
| 活跃 | 27 |
| 活跃高 / 中 / 低 | 11 / 15 / 1 |
| 活跃确定 / 可能 | 23 / 4 |
| 已修复 | 1 |
| 误报 | 2 |
| 需验证 | 33 |
| 原始映射 | 63 |
| 复核后不新开 | 1 |

活跃高危 11 项全部为确定。活跃可能 4 项都是中：面板端口、TUI 任意 choice、environ 留存触发面、跨 profile 的 session_id 碰撞。

## 最高已复核风险

1. Anthropic 兼容客户端跟随重定向并带走 `X-Api-Key`。位置 `agent/anthropic_adapter.py:385-397`，SDK `anthropic/_base_client.py:909`、`httpx/_client.py:552-556`。触发：配置的 base_url 返回跨源 3xx。置信度确定。
2. 不带 `request_id` 的同意按 FIFO 放行另一条命令。位置 `tools/approval.py:165-166` 及 Telegram 等按钮、`/approve`。触发：同一会话两条待决审批。置信度确定。
3. 压缩提交失败仍换提示并继续打模型。位置 `agent/conversation_compression.py:4198-4239` 与 `turn_preflight.py:147-191`。触发：提交在打上成功标记前抛错。置信度确定。
4. 续跑回执写在 `create_task` 返回时。位置 `gateway/clawd_continuation_admit.py:122-133`。触发：回执后进程退出或回合未完成。置信度确定。

## 已修 / 误报 / 不新开

- 已修复：execution 报告的 effort 两参数调用。当前 `turn_facade.py`（`3adf4fab…`）无该调用。提交在 `session_api_turn.py:618-619`，带 `authority` 与 `admission_held=True`，锁为 `keyed_turn(clawd-admission:<logical>)` 加 `mutation_lock`。无 authority 时不提交也不把回合打失败。该文件可能再被改，结论绑定此哈希。
- 误报：aiohttp `Response.__bool__` 恒为真，`if auth_err` 不会放行 401。未闭合 tool 字符串不会被补成缩短命令执行。
- 不新开：`projects_db.py` 条件释放的位置参数是期望项目，空项目拒绝，NULL token 不匹配。缺 token 走 `bind_unproven` / `rollback_unproven`。service 仍不是跨库原子事务。

## 未覆盖与未运行

需验证 33 项没有逐行重读，不计活跃。包括 ctypes `c_void_p` 调用约定、滴流总时限、计划闸门、300 条窗口、存储 S8/S9、生图重开。`turn_facade_lease.py`、`project_memory.py`、`hermes_state_subagents.py`、`clawd_runtime_routes.py` 与 sidecar 投影可能正在改，未用旧行号升格。未审第三方原生轮子、生产库、官方安装。未运行任何测试或请求。
