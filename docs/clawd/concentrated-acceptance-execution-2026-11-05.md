# clawd 集中验收执行索引 — 2026-11-05

唯一入口已执行。阶段循环未启动。没有 `report.json`。不是通过，不是上线许可。未改产品代码、测试、gate、manifest。未提交、未推送、未部署。未读 `/root/.hermes`，未调用 `/root/panel-deploy.sh`，未碰 `/usr/local/lib/hermes-agent`。

观察时间：2026-11-05T18:45–18:47+0800。树 HEAD `c314285f077e91937c995d419b9fa40af9eb4981`，执行前已有 6245 行 porcelain，本路未改源码。脚本未写出 `source_identity`，不能把 HEAD 当成报告指纹。

## 机器与工具链（开跑前）

- RAM 3.8Gi，可用约 1.7Gi；Swap 已用 910Mi；CPU 2；load 0.23。磁盘 `/` 可用 2.0G（90%）。无同名活验收进程。
- Python `/root/hermes-workspace/clawd/.venv/bin/python` 3.14.9；pytest 9.1.1。
- Node `/root/hermes-workspace/clawd/.dev-home/tools/node-26.12.1-linux-x64/bin` v26.12.1；npm 11.20.0。
- `/usr/bin/chromium` 存在（156.0.8078.73）。仓库、`ui-tui`、`apps/panel` 的 `node_modules` 已在，未重装。
- `artifacts/final-acceptance` 原先不存在，无 `*.state.json`，不会 skip/refuse 旧阶段。
- 未传 `--provider-config`、`--rollback-entry`、`--install-prefix`。没有授权的非生产凭据、MCP、隔离 journal、可用安装前缀。

隔离打算交给脚本：`HOME=artifacts/final-acceptance/account-home`，`CLAWD_HOME`/`HERMES_HOME`=`account-home/.clawd`，`TMPDIR=artifacts/final-acceptance/tmp`。父进程用 `env -i`，不继承生产家目录。

## 命令与退出码

1. `prepare` 退出 **1**。日志 `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/accept-prepare.stdout.log`。
2. `run --run --implementation-complete` 退出 **1**。日志 `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/accept-run.stdout.log`。

解释器均为 `/root/hermes-workspace/clawd/.venv/bin/python`，入口 `/root/hermes-workspace/clawd/scripts/clawd_acceptance/accept.py`。进程已退出，无残留验收进程。

## 最早阻塞

两处都在写报告前抛出，阶段循环未进入：

```text
TypeError: report_build.assemble() got multiple values for keyword argument 'phase'
```

- prepare：`execute.py:1362`，`assemble(**_context(...), phase="prepare", ...)`。`_context`（`execute.py:1317`）已带 `phase="report"`。
- run：`execute.py:1379`。`gate["run_allowed"]` 为假，走拒绝分支，同样把 `phase="run"` 和 `_context` 的 `phase` 一起传入。`write_json(report.json)` 在 `execute.py:1385`，未执行。

因此没有 `prepare-plan.json`、没有 `report.json`、没有 `logs/*.state.json`、`wheel/` 无文件。不能把这次退出 1 说成产品测试失败，也不能说成验收退出 2。

只读门禁探针（导入 `gate_view`，未启动阶段）说明：即便没有这个 TypeError，`run_all` 也会在阶段循环前返回，且本来应写报告后退出 2。

- 契约：`schema_ok=true`，`blocking_not_implemented=[]`，`implementation_complete` 旗标为真，契约 `run_allowed=true`。
- 总 `run_allowed=false`，因为用例套件未就绪：
  - `core-race-cases` blocked：`docs/clawd/race-case-preparation.md` 缺主题「重复唤醒」「取消人工输入」。
  - `core-file-auth-cases` blocked：`docs/clawd/file-auth-case-preparation.md` 无「接口」/interface。
  - `core-memory-scope-cases`、`core-event-recovery-cases` ready。ready 不是测试通过。
- inventory 声明离线，探针未执行提取器。

## 程序计数

阶段状态文件 0。档案文件 0。报告不存在。

| 口径 | pass | fail | blocked | 未跑 |
| --- | --- | --- | --- | --- |
| 脚本阶段（15） | 0 | 0 | 0 | 15 |
| 脚本检查 | 无报告，不计为通过 | | | |

15 个阶段都未启动：core、tui-check、tui-build、tui-pty、panel-typecheck、panel-build、panel-node、panel-python、panel-browser、package、license-inventory、verify-integration、real-model-mcp、continuation、rollback。

无失败 nodeid。无 traceback 产品测试行。无 wheel、无 sdist。

## 未验，不是已 blocked 的阶段结果

脚本没跑到这些检查，不能写成阶段 blocked：

- 核心 pytest（单次 `tests`、`-j 1`、每文件 300 秒）未启动。
- TUI check/build/PTY 未启动。Chromium 在，但 panel-browser 未启动。
- 面板 typecheck/build/node/python 未启动。
- package 未建本次唯一 wheel/sdist，两路径未接，`RESOURCE_CONTRACT` 开包 pytest 未跑。无可用隔离安装前缀，未传 `--install-prefix`。
- 许可提取器未跑。解析成功不等于法律通过。
- 未提供非生产 `--provider-config`。没有真实模型回合，不用 `test-not-llm` 填。
- 无隔离 cutover journal，未调用回滚。
- 源指纹与报告无法对照：报告未生成。
