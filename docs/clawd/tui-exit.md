# TUI `/quit` 退出

本文件只记录隔离 PTY 的 `/quit` 核查，不是工程完成声明。未提交、未推送、未部署。模型身份为 grok-4.7，未改配置。

## 根因

`artifacts/runtime-boundaries/pty_probe.py` 只有在原始 PTY 字节里出现连续的 `ready` 时才写 `/quit`。上一份 `logs/tui-pty.log` 的画面已经画出工具/技能面板，但缓冲里没有连续的 `ready`，也没有 `/quit`（计数为 0）。探针因此没提交命令，直接对进程组发了 SIGTERM，退出码 -15。这是探针门闩错误，不是已证实的产品退出挂起。

状态字 `ready` 不能当提交条件：差分绘制会拆开字符，未进入该状态时界面也可能已经可输入。

## 改动

未改 ui-tui、hermes_cli 启动器或 `tui_gateway/server.py`。产品在命令真正提交后能退出，不改正常退出路径。

修正探针在 `artifacts/tui-exit/pty_probe.py`：

- `HOME` / `CLAWD_HOME` / `HERMES_HOME` 指向 `artifacts/tui-exit/home`，`TMPDIR` 指向 `artifacts/tui-exit/tmp`。
- `HERMES_TUI_DIR` 指向已构建的 `ui-tui`，使用仓库 `.venv` Python 3.14 与 Node 26.12.1。
- 等到画面里出现 `20 tools` 或 `/help` 再写 `/quit\r`，并要求写后缓冲回显 `/quit`。
- 应答 DA1，避免 Ink 查询 flush 空等。未回显不算已提交。
- 20 秒内进程未自行结束才记为未干净退出；探针收尾的 SIGTERM 不算产品成功。

## 实测

`artifacts/tui-exit/pty_probe.py` 退出码 0。日志：`artifacts/tui-exit/logs/tui-pty.log`。

- `quit_written=6`，`quit_echoed=True`，画面含 `/quit`。
- `clawd --tui` 退出码 0，`forced=none`，进程组残留 0。
- 画面无 `storage_unavailable`。`state.db` 会话 `source=tui`、`model=None`、`end_reason=tui_shutdown`。
- 父进程为 1 的 `gateway run --quiet --idle-exit`（pid 3488603）不是 TUI 子进程。网关日志写明无客户端后 600 秒自行退出。`GatewayClient.kill` 只断开连接，不把这个空闲网关当成 `/quit` 失败。

未跑真实模型回合，未读生产密钥。全工程、面板、生产服务不在本次范围。
