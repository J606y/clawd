# 核心续跑验证

调度证据和外部模型验收分开。本文只记录已执行的隔离检查，不把模块存在写成已启动，也不把测试执行器写成真实模型回合。

## 状态

- 面板未打开时，API adapter `connect()` 经 `finish_api_connect` 调用 `arm_adapter`。`gateway/run.py` 没有另写挂载：它只要走 `APIServerAdapter.connect()` 就会臂上续跑。本次未改 `gateway/run.py`。
- `/clawd/ready` 的 `continuation` 只在服务 `running` 时为 `started`。`continuationModule=present-not-started` 只表示模块可导入，不能当成启动。
- 完成通知仍走 `persist_delegation_delivery`，不 self-POST 普通 user。审批、取消、人话在 admit 之前由代码拒绝。无标记的执行器钩子直接拒绝，不会落到 `_run_agent` 或 self-POST。
- 没有模型配置时，测试注入 `clawd_executor_marker=test-not-llm`。这只证明调度、账本和 run 状态，不证明外部模型回合。

## 身份

系统提示标识是 grok-4.7。隔离面板日志广告模型是 `hermes-agent`，不是 grok-4.7。生命周期测试把 `grok-4.7` / `sub2api` 当作会话锁上的路由字符串继承，执行器输出固定为 `test-executor-not-llm`。没有提供方往返。身份不符，不能把这次结果写成 grok-4.7 回合通过。

## 已执行

解释器：`/root/hermes-workspace/clawd/.venv/bin/python`（3.14.9）。

`scripts/run_tests.sh -j 1` 对三份续跑测试退出 0，25 通过。日志 `artifacts/continuation-review/pytest.log`，退出码文件 `pytest-exit-code.txt`。该脚本用 `env -i`，不转发 `CLAWD_HOME` / `HERMES_HOME` / `TMPDIR`；测试在用例内把这三项和 `HOME` 指到临时目录，并拒绝生产 home。

隔离探针另用 `env -i` 启动 `python -m clawd_server serve --port 0`。`ready.json`：

- `continuation=started`
- `continuationRunning=true`
- `continuationModule=present-not-started`
- `legacyWatcher=disabled`

探针进程自身退出 0。子进程退出码 -15 是探针发出的 SIGTERM，不是崩溃。停止后监听关闭、进程消失，见 `listener.txt`。日志没有 `/root/.hermes`。隔离 `gateway_state.json` 的 `hermes_home` 是探针目录。未读生产密钥，未改外部模型配置。

## 覆盖

- 无面板：真实 `APIServerAdapter.connect`、真实 SessionDB、`persist_delegation_delivery`，标记执行器被调用一次。
- 重复通知：同一 `delegation_id` 不再次 admit。
- 重启：服务停止期间写入的完成通知，下次 `connect` 的 recover 只 admit 一次；再次启动不再 admit。
- 人话插入、显式取消、`waiting_for_approval`：执行器不被调用。
- 无标记执行器：事件 `unmarked_executor`，钩子不运行。
- 启动关闭：面板探针与测试的 `disconnect` 后 `service_for` 为空，端口不再监听。

## 未完成 / 父代理

- `scripts/verify_clawd.py` 仍把 continuation-core 写成 blocked，且没有执行完成回合。该文件不在本路。ready 现在会返回 `started`，但父代理探针仍未跑真实完成回合，不能因此改判通过。
- `tests/clawd_server/test_binding.py` 约 188 行仍断言 `continuation in {missing, present-not-started}`。本路未改该文件。面板实际启动后该断言会失败，应改为 `started`，并单独断言 `continuationModule == present-not-started`。
- 外部模型、MCP、浏览器未验收。没有模型配置，不能补成端到端通过。
- 未提交、未推送、未部署。
