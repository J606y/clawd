# 验收运行时修复 — 2026-11-05

静态修复加回归用例。未跑 tests、构建、产品 CLI、许可提取器。未改 `generate.py`、`frontend-common.mjs`。未删日志，未读 `/root/.hermes`，未 commit/push/deploy。失败/blocked 聚合未改。

## 1. core `--basetemp` 丢值

根因：`run_tests_parallel.py` 只把 `-k/-m/-p/-o/-c/-r/-W` 的下一 token 当值。`--basetemp <路径>` 的路径被当成发现根，pytest 只看到孤立 `--basetemp`，于是 `argument --basetemp: expected one argument`。这不是产品断言失败。

调用链：`isolation.core_argv` 仍传独立 token `--basetemp`、`-j 1`、唯一 `tests`（`run_tests_argv_ok` 要这个 token，且 `"--basetemp"` 不在 `OUR_FLAGS` 后 800 字内）。`run_tests.sh` 在 `env -i` 之前把路径选项收成 `--basetemp=<path>`，不吃 `tests`。`split_forwarded_argv` 再收一次，直接调用 python runner 也不丢值。两层都不把 `tests` 当成 basetemp。

## 2. tui-build 落在仓库内 TMPDIR

根因：`npm run build` 无额外参数时走 `buildDeveloperTui`，`mkdtemp(tmpdir(), 'hermes-tui-')` 用验收 `TMPDIR=artifacts/final-acceptance/tmp`。该目录在源码树内，`productOutput` 拒绝。守卫未删，执行器未改。

调用链：`execute.run_tui_build` 只在调用侧追加 `--source <repo> --out <唯一目录>/tui`，并把 `TMPDIR/TEMP/TMP` 设到 `/root/hermes-workspace/clawd-acceptance-tui/<token>/tmp`。目录在工程外、非 `/tmp`、不预建 out、不覆盖已有路径、不删未知目录。若 token 与检出重叠则 blocked，不回落系统 tmp。

## 3. 许可入口误拒

根因：`generate.py` 真实 argparse 有 `--checkout/--output/--offline/--input`，但在约 32KB 处。旧门禁 `source_text` 只读 20KB，所以启动前报“无 --checkout/--output”，提取器没跑。不是法律结论。

调用链：不改提取器，不发明参数。`stages.license_argv` 与 `execute._license_argv` 同一条：`--checkout <repo> --output <logs>/license-inventory.json`。`license_signature_ok` 读完整文件核对签名。相对路径或未知 flag 仍 blocked。写出的 JSON 即使退出码非 0 也交给 `classify_license_inventory`。解析成功时 `legal_review` 仍是 blocked，unknown 不是通过。

## 文件

- `scripts/run_tests.sh`
- `scripts/run_tests_parallel.py`
- `scripts/clawd_acceptance/isolation.py`
- `scripts/clawd_acceptance/stages.py`
- `scripts/clawd_acceptance/execute.py`
- `tests/clawd_acceptance_cases/test_execute_package_distribution.py`
- `tests/clawd_acceptance_cases/test_runner_argument_forwarding.py`
- `tests/clawd_acceptance_cases/test_license_cli_contract.py`
- 本报告 `docs/clawd/acceptance-runtime-fixes-2026-11-05.md`

未跑：pytest、npm build、`generate.py`、验收 `run`。旧日志与 `phase` 单次 mapping、`TrustedArchiveHandoff` 保留。
