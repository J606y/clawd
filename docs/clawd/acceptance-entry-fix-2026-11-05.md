# 验收入口 phase 修复 — 2026-11-05

本文件只记录 `execute.py` 的入口修复和已准备、未运行的回归用例。不是通过，不是验收绿，不是上线许可。未跑 pytest、构建、产品导入或 `accept.py` CLI。未改 gate，未删 `artifacts/final-acceptance` 日志。

## 故障

`report_build.assemble` 只接受一个 `phase`。`_context` 固定带 `phase="report"`，`prepare` 和门禁拒绝的 `run` 又用 `assemble(**_context(...), phase=...)` 再传一次。真实日志是：

```text
TypeError: report_build.assemble() got multiple values for keyword argument 'phase'
```

prepare 与 `run --run --implementation-complete` 都因此在写报告前退出 1。阶段循环没进。这不是产品测试失败。

## 修法

`_context` 不再含 `phase` 或 `executed`。四个写报告路径都先做一份映射，再 `assemble(**映射)`。`phase` 只在 `_report_kwargs` 里赋值一次。

保留既有手递：

- `wheel_dir=_trusted_archive_handoff(layout, None)`：prepare、门禁拒绝 run、符号链接根的 report
- `wheel_dir=_trusted_archive_handoff(layout, package_result)`：全阶段收束、有存档的 report_only

没有 `wheel_dir=layout.wheel`。不列隔离目录，旧 wheel/sdist 不会被当成这次档案。`run_all` 源码不含 `run_tests.sh`。

空检查、fail、blocked 不收成 pass。存档阶段 status 为 fail/blocked 但没有对应检查行时，补一行，不回落到“未跑计划”把失败抹掉。无检查的存档 pass 记 blocked，不是 pass。`phase!="run"` 或 `executed` 为假时，assemble 不会给 `acceptance_green`。

符号链接的验收根、logs 目录、阶段 state 不读取、不写入。坏 JSON state 记 blocked，不当成已执行。

## 入口、phase、write

| 入口 | phase | executed | write | 进程/函数退出 |
| --- | --- | --- | --- | --- |
| `prepare` `execute.py:1417` | `prepare` `1426` | `False` `1427` | `prepare-plan.json` `1432`；`report.json` `1433`。计划里 `acceptance_exit` 固定 2 | 函数返回报告。CLI `accept.py:38-41` 仍返回 0，只表示计划已写，不是验收绿。符号链接根不写文件 |
| 缺 `--run` 或缺 `--implementation-complete` | 不调用 assemble | 不改旧报告 | 不写 | `run_all` `1438-1439` 返回 2。CLI `accept.py:46-48` 同样返回 2，未启动阶段 |
| 门禁拒绝 run `1444` | `run` `1449` | `False` `1450`。旧 state/旧 wheel 不读成这次 | `report.json` `1453` | 返回 2 `1454`，不是 TypeError。不调用 `execute_stage` |
| 全阶段收束 `1484` | `run` `1488` | `True` `1489`。档案只来自本次 `package_result` | `report.json` `1495` | 返回报告 `summary.exit_code` `1496`。fail 为 1；空、blocked、源/档案不完整为 2。不把这些收成 0 |
| `report_only` 回放 `1499` | `report` `1506`（符号链接根）或 `1556`（普通根） | 只有读到非链接 state 里的 result 才为真 `1537`。无记录、坏文件、符号链接均为假 | 根存在、是目录、且不是符号链接时写 `report.json` `1562-1563`。根不存在不创建 | 返回报告退出码 `1564`。回放即使检查全 pass 也不是 `phase=run`，不能绿 |
| CLI 无参数 | 不组装 | — | 不写 | `accept.py:33-36` 返回 2 |

`accept.py` 未改。gate、report_build、fingerprints、stages、许可证分类未改。

## 用例

新文件 `tests/clawd_acceptance_cases/test_public_entry_report_paths.py`。调用 `prepare`、`run_all`、`report_only` 和 `accept.main`，依赖用临时目录和 mock。禁止 `execute_stage`/`spawn` 真启动。覆盖：

- prepare 计划：`phase=prepare`，`executed=false`，计划退出 2，旧绿报告被覆盖而不是被读成这次
- 门禁拒绝 run：写 `phase=run` 且 `executed=false` 的报告，函数退出 2
- 缺旗标：不调用 assemble，旧 `report.json` 原样留下，退出 2
- report_only：失败/空/blocked/存档 pass 都不绿；未命名旧 wheel 不进 handoff；坏 state 和符号链接不读成执行
- 正常收束：空 fail 仍是 1；blocked 和空 pass 是 2；只有检查、源指纹、档案都完整时才把 assemble 的 0 传出。该完整夹具是 mock，不是本次验收

本轮没有运行这份用例，也没有运行既有验收用例、构建、产品导入或 CLI。统一复测前不能把这些断言当成已通过。

## 未运行

- 未执行 `tests/clawd_acceptance_cases/test_public_entry_report_paths.py`
- 未执行 `scripts/run_tests.sh`、pytest、构建、打包、面板或 TUI
- 未导入产品，未调用 `scripts/clawd_acceptance/accept.py`
- 未读 `/root/.hermes`，未改 gate 放行，未删 `artifacts/final-acceptance/logs`
- 15 个阶段仍未由本路启动。没有新的验收退出码，没有 `acceptance_green`
