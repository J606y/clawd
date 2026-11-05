# CLI / TUI 品牌改名

静态核对。未运行 CLI、TUI、测试、build 或探针。未提交、推送、部署。`python -m py_compile` 只查了已改 Python 的语法，不是产品验收。

## 入口

产品入口仍是 `clawd_cli/main.py`：先 `isolate_inherited_hermes_env()`，再进 `hermes_cli.main`。默认数据根是 `CLAWD_HOME`，未设置则为 `~/.clawd`。这条隔离在 `hermes_constants.py`，本路未改该文件。没有把未设置的家目录自动指到生产 `~/.hermes`。

`HERMES_BRAND=1` 只恢复顶层 `--help` 措辞、横幅版本和版本回退行。它不选择数据目录。理由：共享 CLI 仍可能被旧入口调用，需要显式桥接，不能当默认。

## 已改

- `hermes_cli/_parser.py`：默认 `prog`/简介/短示例是 clawd。聊天描述和 `setup` 建议跟随产品名。未知命令仍用内部键 `hermes` 查找，展示改成 clawd。
- `hermes_cli/banner.py`、`_startup_fast.py`：版本标签默认 clawd。无皮肤 logo 时用文字 `clawd`，不重画 HERMES ASCII。Nous Research 署名保留。
- `hermes_cli/main.py`：进程标题、PID1 前缀、配置/会话/doctor 建议改为 clawd。官方 dashboard 注册仍只在未设 `CLAWD_CLI` 时挂上。
- 子命令 `--help`、setup/status/console/completion/卸载横幅、skills/logs/config/kanban/doctor 等用户可见命令建议：命令词改为 clawd，产品名改为 clawd。框线只补空格，宽度仍是 58。
- `service_manager.py`：监督进程执行 `clawd gateway run`。`s6-setuidgid hermes` 是系统用户名，未改。
- TUI 在 `ui-tui/`（仓库没有 `apps/tui/`）。欢迎艺术字已是 CLAWD，未重画。补了 `~/.clawd/.env`、`clawd -p`、演示标题。`apps/shared` 只改了 RPC 失败回退文案。
- 运行路点名的残留，只改用户可见产品名：`gateway/session_ingress.py`、`session_settlement_recovery.py`、`relay/adapter.py`、`agent/estop.py`、`agent/shell_hooks.py`、`hermes_state.py`、`hermes_cli/web_routers/local_models.py`。

## 未改及原因

- 导入、`HERMES_*` 环境变量名、`hermes-cli` OAuth client id、`hermes.service` / `hermes-gateway` 单元名、`@hermes` 对等别名、`hermes-peer-dm`、`/hermes discard`（Slack 协议命令）、Hey Hermes 唤醒词。
- 上游署名与安装 URL：Nous Research、`NousResearch/hermes-agent`、`hermes-agent.nousresearch.com`。门户页名 “Hermes Agent page” 是上游页面，不是本产品名。
- `update` / `dashboard` / `desktop` / `gui` / `webapp` 的解析器仍写官方 Hermes。`clawd` 入口在进解析器前拒绝这些命令，避免把官方产品说成本产品。
- `hermes_constants.py` 归路径所有人。`hermes_state_user_copy.py` 的 doctor/gateway 建议仍写 hermes，不在本次点名文件里。
- `locales/en.yaml` 的关机/重启通知仍是 “Hermes is shutting down / restarting”。因此未改 `tests/gateway/test_gateway_shutdown.py`、`test_grid_sweep_gateway_diagnostics.py`。`tests/agent/test_cross_process_turn_lease.py` 断言来自未授权的 `agent/turn_facade_lease.py`，未改。
- 侧车、tools 提示、`package.json`、根 README、`build/lib`、run-wait（`runtime_native.py`、`clawd_runtime_routes.py`）未动。
- `clawd-tui-work` 工作树同时改着 `ui-tui`。本路改的是主检出。合并时以主检出用户可见文案为准，不要覆盖已是 clawd 的艺术字。

## 未验证

未启动 `clawd --help`、`--version`、TUI，也未跑测试。子命令帮助和生成建议的测试快照可能仍期望 `hermes`。验收前不要把本文件当成通过记录。
