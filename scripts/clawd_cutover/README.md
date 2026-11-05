# clawd 切换准备

离线准备工具。操作说明在 `docs/clawd/cutover-runbook.md`，清单形状在 `docs/clawd/cutover-manifest.schema.json`。

```text
python3 scripts/clawd_cutover/cli.py gates
python3 scripts/clawd_cutover/cli.py plan --allow-root DIR --source DIR --target DIR --migration-allowed no
```

没有默认数据目录。变更命令缺少精确确认词时只出计划。确认词不能越过生产路径。不安装单元或站点。真实迁移模块是 `clawd_projects/migration.py`，本工具不调用。apply 不接受短 pass JSON。

本次交付未运行工具、未运行 `tests/`、未构建。输出里的 `verification.tool_tests` 保持 `not_run`。
