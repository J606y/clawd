# clawd 集中验收复测 — 2026-11-05

marker `rerun-20261105T193153`。不是通过，不是上线许可。未改产品源码、测试、gate、stage。未删首次日志。未读 `/root/.hermes`，未调用 `/root/panel-deploy.sh`，未提交、未推送、未部署。

## 证据同步

独立对当前字节算 `clawd_server/runtime_native.py` sha256：`367e0ee31af8001fcffb2e308f18054ab2d37596428f5f05d602dd41e7fa54cb43`（17107 字节）。只改 `docs/clawd/implementation-completion-manifest.json` 这一条证据，以及 `observed_at=2026-11-05T19:31:53+0800`。`implementation_complete` 仍为 true。blocking 未实现仍为空。其余证据未动。

## 开跑前

RAM 可用约 1.7Gi，Swap 已用约 907Mi，CPU 2。磁盘 `/` 可用 2035Mi。无同名活验收。Python 3.14.9、Node v26.12.1、`/usr/bin/chromium` 与既有 `node_modules` 在，未重装。空间够完成本次写入，未清盘，未连用户 i9。

隔离按脚本：`HOME=artifacts/final-acceptance/account-home`，`CLAWD_HOME`/`HERMES_HOME` 为其 `.clawd`，`TMPDIR=artifacts/final-acceptance/tmp`。父进程 `env -i`。未传 `--provider-config`、`--install-prefix`、`--rollback-entry`。

## 命令与退出码

1. `prepare` 退出 **0**。stdout `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/accept-prepare.rerun-20261105T193153.stdout.log`。stderr 同名空文件。计划快照 `logs/prepare-report.rerun-20261105T193153.json`。
2. `run --run --implementation-complete` 退出 **1**。入口 stdout/stderr：`logs/accept-run.rerun-20261105T193153.stdout.log`、`logs/accept-run.rerun-20261105T193153.stderr.log`（入口本身无输出）。阶段日志在同一 `logs/`。

首次 `accept-prepare.stdout.log`（658）与 `accept-run.stdout.log`（677）未覆盖。进程已退出。

prepare 报告：`phase=prepare`，`executed=false`，计划验收退出 2。四组用例 ready，inventory ready，工具链在。`run_allowed=false` 只因 prepare 固定不带完成旗标。随后的 run 门禁 `run_allowed=true`，阶段循环已进入。

## 程序计数

报告 `/root/hermes-workspace/clawd/artifacts/final-acceptance/report.json`（副本 `logs/run-report.rerun-20261105T193153.json`）。`phase=run`，`executed=true`，`acceptance_green=false`。`summary.exit_code=1`。必需唯一检查：pass 5，fail 16，blocked 9，not_run 0。

15 阶段（报告 `stages`）：pass 1，fail 9，blocked 5，not_run 0。

| 阶段 | 状态 | 退出 |
| --- | --- | --- |
| core | fail | 2 |
| tui-check | fail | 1 |
| tui-build | fail | 1 |
| tui-pty | pass | 0 |
| panel-typecheck | fail | 1 |
| panel-build | fail | 1 |
| panel-node | fail | 1 |
| panel-python | fail | 1 |
| panel-browser | fail | 1 |
| package | fail | 1 |
| license-inventory | blocked | 2 |
| verify-integration | blocked | 2 |
| real-model-mcp | blocked | 2 |
| continuation | blocked | 无 |
| rollback | blocked | 2 |

必需 pass 只有 `tui-pty`、`package-build-wheel`、`package-build-sdist`、`package-distribution-env`、`package-wheel-members`。构建检查通过不是阶段通过。

## 最早故障

core，收集前退出。无 pytest nodeid。`logs/core.log`：

```text
run_tests_parallel.py: error: argument --basetemp: expected one argument
```

脚本 argv 是 `--basetemp <路径>`。runner 只把 `-k/-m/-p/-o/-c/-r/-W` 的下一个 token 当值，路径被当成位置参数，pytest 只看到孤立 `--basetemp`。这不是产品断言失败。入口没有可改的 basetemp 参数，未改脚本，未第二次调用。

随后唯一 pytest nodeid：

`tests/clawd_server/test_distribution_package_manifest.py::test_supplied_archives_match_contract_and_missing_archives_fail_closed`

`ArchiveMismatch`：wheel 含被标为 pruned 的 `plugins/web/**`。日志 `logs/package-manifest.log`。

## 其余失败（最小根因）

- tui-check：`slashAttachmentRenderer.test.tsx` 3 失败 / 1908 通过。日志 `logs/tui-check.log`。
- tui-build：输出落在 `artifacts/final-acceptance/tmp/hermes-tui-*/tui`，构建拒绝“必须在源码外或支持的生成目录”。
- panel-typecheck / panel-build：同一组 TS：`admin-api.ts:418` TS2345，`sidecar.ts:532` TS2322，`config-tab.tsx:38` TS2430。
- panel-node：未按 35/240 杀掉。17 个失败标题在 `logs/panel-node.log` 的 `✖ failing tests`，首条 `tests/approval-contract.test.mjs:379`（期望 completed，实际 running）。
- panel-python：日志两个页脚：341 tests（68 fail / 40 error / 10 skip）；61 tests（6 fail / 9 error）。名单在 `logs/panel-python.log`，不把重复 traceback 行当 nodeid。
- panel-browser：真实 Chromium。13 FAIL / 13 PASS。最早门 `artifacts-browser`。其余：`folders-browser`、`image-preview-layer-browser`、`subagent-card-size-browser`、`test-react-admission-recovery-browser`、`agent-close-browser`、`folder-offpage-browser`、`mcp-path-browser`、`subagents-session-frame-browser`、`task-popup-mutex-browser`、`subagent-clock-browser`、`subagent-spin-browser`、`stream-perf-react-browser`。
- package 阶段 fail 来自开包契约，不是没建出档案。`package-installed-panel` blocked：无隔离安装前缀。

## blocked，且未冒充已跑完

- license-inventory：启动前拒绝，`generate.py` argparse 无 `--checkout/--output`。提取器未跑。解析通过不等于法律通过。
- verify-integration：探针跑了，blocked 行保留。`verify:model-run` 接受后 status failed；`verify:mcp` 空 data；无隔离浏览器 daemon。不升成通过。
- real-model-mcp：无非生产 `--provider-config`。未读生产凭据。无真实模型回合，无活 MCP。
- continuation：无真实委派完成回合。`test-not-llm` 不是通过。
- rollback：无隔离 journal。cutover cli 未调用。未调用生产部署脚本。
- deploy-support：未验证。

## 本次档案

存在，sha256 与报告一致：

- `/root/hermes-workspace/clawd/artifacts/final-acceptance/wheel/accept-build-320184-6978a4cb34f949158ec4cd590a044863/clawd-0.0.0-py3-none-any.whl`
- `/root/hermes-workspace/clawd/artifacts/final-acceptance/wheel/accept-build-320184-6978a4cb34f949158ec4cd590a044863/clawd-0.0.0.tar.gz`

未 glob 旧 wheel。

## 未验

核心 `tests` 未收集，故 race、file-auth、memory、event、usage-close、公共入口用例都未执行。真实模型、活 MCP、许可提取、回滚、安装后面板布局、部署支持未验。`bg-receipt` `bg-lease` `bg-human` `bg-busy` `bg-window` `bg-swallow` `bg-pid` `bg-stop` 仍只审查，未修。`tui-pty` 程序记 pass：日志有 `/quit` 回显，无 SIGKILL/SIGTERM；这不是全验收通过。
