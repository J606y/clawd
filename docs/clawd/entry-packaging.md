# clawd 入口与打包

本文件只记录入口与独立包范围，不是工程完成声明。

## 已核对并修复

- `pyproject.toml` 的 `packages.find.include` 原先只有 `clawd_cli`，独立 wheel 不会带上 `clawd_server` 与 `clawd_projects`。现已列入，不改依赖版本，不改锁文件。
- TUI 启动包装在 `CLAWD_CLI=1` 时不再把 `HERMES_BIN` 传给子进程，退出码 42 不再 exec `hermes update`。用户可见的启动/恢复提示在 clawd 入口下使用 `clawd`。内部模块名 `hermes_cli` 未改。
- 已构建的 `ui-tui/dist/entry.js` 与源码 `externalCli.ts` 在 clawd 启动时调用 `HERMES_PYTHON` + 仓库 `clawd`，不回落到 `hermes` 命令。
- `scripts/bundles/desktop*.py`、`scripts/desktop-update/*` 及对应的 `build-bundle`、`scripts/ci/desktop_*.py`、`dump_desktop_slash_registry.py` 直接执行立即拒绝，不构建、不更新、不重开官方 Desktop。`apps/shared` 未删除。未跑 Desktop 重构建。

## 验证方式

相关测试用工程解释器与 `scripts/run_tests.sh`，HOME/CLAWD_HOME/HERMES_HOME/TMPDIR 隔离在 `artifacts/entry-packaging/`。

`tests/clawd_core_entry.py`、`tests/clawd_core_paths.py`、`tests/clawd_packaging.py`：3 文件、30 测试通过，退出码 0。日志 `artifacts/entry-packaging/logs/core-packaging.log`。

实际 wheel `artifacts/entry-packaging/wheel/hermes_agent-0.0.0-py3-none-any.whl`（15615347 字节）含 `clawd_server/__init__.py`、`clawd_server/__main__.py`、`clawd_projects/__init__.py`、`clawd_projects/service.py`、`clawd_cli/main.py`。无 `apps/desktop`、`apps/panel` 成员。构建退出码 0。

真实 PTY：`clawd --tui` 在隔离目录启动，进程树出现 `ui-tui/dist/entry.js` 与内部 `hermes_cli.main gateway`，没有名为 `hermes` 的可执行文件。画面出现 `storage_unavailable` 后到 `ready` 提示符；探针在约 20 秒时结束仍在运行的 TUI（wait status 15）。这不是完整会话验收。日志 `artifacts/entry-packaging/logs/tui-pty-3.log`。

## 未覆盖

上游 Desktop 手测与 `tests/scripts/desktop_update`、桌面 CLI 契约测试未改；那些直接入口现已拒绝，再跑会失败。未做生产安装、未改面板、未改 `clawd_projects` 业务、未改 gateway。网关横幅仍打印 Hermes Gateway，属 gateway 包，本次未改。`pm/runtime.py` 的 “Preparing the isolated Hermes runtime” 有既有测试锁定，未改。工程 `.dev-home` 工具链曾丢失，已按锁文件校验重新放入 Python 3.14.9 与 Node 26.12.1，未改模型配置。
