# execute 双包到开档案接线 — 2026-11-05

未运行。没有测试、构建、导入 `clawd_server.packaging_backend`、CLI、`accept.py`、网络或开包。没有设置父进程的 `CLAWD_DISTRIBUTION_WHEEL` / `CLAWD_DISTRIBUTION_SDIST`。没有删除文件，没有读生产秘密，没有访问原面板或 `/usr/local/lib/hermes-agent`。验收仍未开始。

本文件是静态接线记录，不是通过，不是上线许可。下面的退出码是代码分类，不是本次观察到的进程退出码。

## 结论

`scripts/clawd_acceptance/execute.py` 已停止私有 `setuptools.build_meta.build_wheel` 命令。package 阶段使用目录里的 `stage["argv"]`，缺省时用同一个 `package_argv`。子进程环境是 `package_child_env(direct_env(...))`：`HERMES_NIX_BUILD=1` 在 `Popen` 之前就在，父进程旧的 `CLAWD_DISTRIBUTION_*` 被丢掉。本进程不调用构建钩子。

成功标记里的两个绝对路径才会交给下一次独立的固定 venv pytest。缺任一路径不启动该测试。提取器退出 0 不再记成许可通过。

`/root/hermes-workspace/clawd/artifacts/final-acceptance/report.json` 不存在。本次没有写出验收报告，`acceptance_green` 不是真。

## 真实调用链（未执行）

工作目录 `/root/hermes-workspace/clawd`。验收根是 `Layout(ACCEPT_ROOT)`，即 `/root/hermes-workspace/clawd/artifacts/final-acceptance`。

| 步骤 | 路径 / 命令 | 本次 |
| --- | --- | --- |
| 固定解释器 | `/root/hermes-workspace/clawd/.venv/bin/python` | 未探测、未回落系统 Python |
| 隔离目录 | `/root/hermes-workspace/clawd/artifacts/final-acceptance/wheel` | 只作为子进程的 isolation 参数；本进程不 glob `*.whl`，不造档案路径 |
| 唯一输出目录 | 同上之下的 `accept-build-<pid>-<uuid>/` | 由子进程 `run_package_build` 创建。execute 不调用该函数 |
| 构建日志 | `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/package.log` | 只解析本次追加段里的 `CLAWD_ACCEPTANCE_DISTRIBUTION` |
| 开档案日志 | `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/package-manifest.log` | 未产生 |
| 许可输出 | `/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/license-inventory.json` | 未产生 |
| 开档案命令 | 见下 | 未启动 |

开档案命令只在两个路径都通过 `classify_distribution_handoff` 之后，由 `run_all` 直接 spawn。不走 `scripts/run_tests.sh`，不改它的 `env -i` allowlist，也不把这两个变量放进 `direct_env`：

```sh
CLAWD_DISTRIBUTION_WHEEL="<本次标记的绝对路径>" \
CLAWD_DISTRIBUTION_SDIST="<本次标记的绝对路径>" \
/root/hermes-workspace/clawd/.venv/bin/python \
  -m pytest -q tests/clawd_server/test_distribution_package_manifest.py
```

工作目录是 `/root/hermes-workspace/clawd`。墙钟 600 秒，不是 35 或 240。环境从 `direct_env` 新造，只注入本次两个路径。父进程 `os.environ` 里的旧路径不会进去。

核心阶段仍在 package 之前，且 `run_tests.sh` 的 `env -i` 仍不转发这两个变量。这是故意的：核心已经跑完，两个都空时那个测试的退出 0 不是开包通过。

接线位置：

- `run_package`：`execute.py` 第 265 行。签名 `run_package(repo, layout, install_prefix, stage=None)`。
- `_dispatch`：第 1152 行，`run_package(repo, layout, prefix, stage)`。
- `run_all`：第 1246–1256 行。package 之后才调用 `compare_distribution_manifest`。缺变量保持 `package-manifest-compared=not_run`。有结果则替换该 id，避免 `not_run` 在去重时压过 `pass`。
- `classify_license_inventory`：第 644 行。没有 pass 返回。

## 退出码分类（未观察到）

包构建子进程不走 `_command_stage`。阶段 `exit_code` 用 `package_result_exit`，不是把子进程的 2 或 124 直接写成 fail。

| 情况 | 检查 | 阶段退出 |
| --- | --- | --- |
| 固定 venv python 缺失、argv 不是该 python、命令含 `setuptools.build_meta`、隔离目录是符号链接 | blocked，不 spawn | 2 |
| 子进程超时。spawn 原始码 124 | `package-build-wheel` / `package-build-sdist` / `package-distribution-env` blocked；不开 pytest | 2 |
| 子进程 1 | fail 优先。有 fail 就是 1，不能被 blocked 抵消 | 1 |
| 子进程 2、无标记、标记歧义、只有一个档案、历史名、目录外路径 | blocked；不开 pytest | 2 |
| 子进程 0 且标记可用 | 两个构建行和 `package-distribution-env` 可以是 pass；`package-manifest-compared` 仍是 not_run | 2 |
| 两个新档案都在，pytest 退出 0 且输出非空、不是 `no tests ran` / `collected 0` | 只把 `package-manifest-compared` 换成 pass | 见下 |
| pytest 退出 0 但输出为空或零用例 | blocked，不是通过 | 2 |
| pytest 退出 1，且不是 “No module named pytest” | fail | 1 |
| pytest 超时，或退出 2/3/4/5/124，或 venv 没有 pytest | blocked | 2 |
| 缺任一变量 | 不启动 pytest，该检查保持 not_run | 2 |
| 空检查、unknown | 不得 pass | 2 |

即使开档案检查变成 pass，下面这些仍使总账不能是 0：

- 没有隔离安装前缀时，`package-installed-panel` 保持 blocked。源码树里有面板不是安装证明。
- 标记文件不是可读 zip 时，`package-wheel-members` 是 blocked，不改去 glob 同级旧包。真正 zip 缺成员才是 fail。
- 许可三行不能 pass。见下。
- `coverage_checks` 里其余未执行的必需检查仍是 not_run。
- `fingerprints.artifact_report` 仍只 glob 隔离目录顶层 `*.whl`。唯一目录里的新 wheel 不会让 `artifacts.complete` 变真。`report_build._green` 因此仍是假。本轮不改 `fingerprints.py`。

fail 与 blocked 同时存在时，聚合是 1。没有 fail 时，blocked、not_run、unknown、空结果都是 2，不是 0。

## 许可

`run_license` 不再把提取器退出 0 记成 pass。

| 情况 | 状态 | 退出 |
| --- | --- | --- |
| 超时。原始码 124 | blocked。三行都列：`license-inventory`、`license-scope-unknown`、`license-packaged-unknown` | 2 |
| 退出 1 | fail | 1 |
| 其他非 0 | blocked | 2 |
| 退出 0，但清单缺失、不是 JSON、四包未齐、`license`/`packaged_membership` 为 unknown、`compliance_conclusion=not_determined` | blocked。unknown 不是合规通过 | 2 |
| 四包 `license` 离开 unknown、依赖行 `license`/`packaged` 离开 unknown、`packaged_membership` 离开 unknown、`compliance_conclusion` 不是 `not_determined`、`network_audit` 不是 false、MIT 缺少 `sibling_notice_file` | fail。不能当成四包已授权 | 1 |

根 component 可以是 `license=MIT` 且 `license_basis=sibling_notice_file`。这不外溢到 `apps/panel/package.json`、`ui-tui/package.json`、`ui-tui/packages/hermes-ink/package.json`、`apps/shared/package.json`，也不把阶段变成 pass。

这条检查通过也不存在。它只说明没有把退出 0 或 unknown 误写成合规。四包书面授权仍缺。

## 当前报告字段

机器报告的字段在 `scripts/clawd_acceptance/report_build.py` 的 `assemble`，不是本次运行结果：

- `schema_version`、`kind`、`producer`、`executed`、`never_executed`、`phase`、`plan_only`
- `acceptance_green`、`final_acceptance`、`covers_cutover`
- `summary.exit_code`、`summary.status`、`summary.blocked_ids`、`summary.failed_ids`
- `blockers`、`checks`、`stages`、`artifacts`、`source_identity`、`gate`、`isolation`、`toolchain`、`identity`

`acceptance_green` 要求 `phase=run`、`executed`、无 blocker、源指纹完整、`artifacts.complete`，且全部必需检查为 pass。本次四项都不成立。

没有运行报告可核。不能把 `blocked_ids` 写成空，也不能把 `summary.status` 写成 pass。若将来跑完，blocked 必须留在 `summary.blocked_ids` 和 `blockers`，不能并进 pass。

## 单独 blocked，不是全绿

这些在接线之后仍然 blocked 或未执行。它们不是本文件的通过项：

- 验收未开始。`artifacts/final-acceptance/report.json` 不存在。
- 新测试未运行：`/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_execute_package_distribution.py`。
- 真实 wheel 与 sdist 未构建。两个分发变量未由本次导出。
- 开档案 pytest 未启动。固定 venv 能否导入该测试，未验证。
- Nix `pythonSrc` 过滤、`CLAWD_PANEL_RESOURCE_ROOT` 沙箱、面板 dist 不完整时的钩子失败，都未跑。
- 无隔离安装前缀时，`package-installed-panel` 是 blocked。
- 四包许可 scope unknown、`packaged_membership=unknown`。提取器没有 pass 路径。
- `fingerprints.py` 顶层 glob 看不见 `accept-build-*` 里的新档案，`artifacts.complete` 仍会是假。这单独阻止 `_green`。
- 核心、TUI、面板、浏览器、模型、续跑、回滚都不是本轮范围，也没有新的 pass。

## 缺口

1. `tests/clawd_acceptance_cases/test_package_stage_execution.py` 第 70 行仍断言 execute 含 `from setuptools.build_meta import build_wheel`。该字符串已删除。同函数第 71–73 行对 `run_package_build` 和 `CLAWD_DISTRIBUTION_*` 字面量的 `assertNotIn` 与当前文件一致，因为 execute 只用 `stages` 里的常量，不在本进程调用构建函数。统一跑测试时，第 70 行会失败。这是过期静态断言，不是产品失败。本轮不改那个文件。
2. `stages.py` 第 553–556 行的目录注记仍写 execute 不读 `stage["argv"]`、不导出两个变量。运行时已经读 argv，并只在标记可用后把路径交给独立 pytest。注记过期。`stages.py` 本轮只读，未改。
3. `fingerprints.py` 未改。顶层 `*.whl` glob 不是这两个环境变量的消费者，也看不见唯一目录里的新档案。
4. `package-wheel-members` 只打开标记给出的那一个路径。坏 zip 记 blocked，不回退到同级旧包。它不是契约核对；契约核对是上面的 pytest。
5. `report_only` 只回放已写入的阶段状态，不重新开档案。本次没有状态文件。
6. 统一运行仍要等实现收齐、门禁清单和六份 detail。本接线不构成 `--run --implementation-complete` 的授权。

## 修改文件

- `/root/hermes-workspace/clawd/scripts/clawd_acceptance/execute.py`
- `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_execute_package_distribution.py`（新；未运行）
- `/root/hermes-workspace/clawd/docs/clawd/acceptance-execute-package-closeout-2026-11-05.md`（本文件）

未改：`stages.py`、`isolation.py`、`scripts/run_tests.sh`、`gate.py`、`fingerprints.py`、`clawd_server/packaging_backend.py`、`setup.py`、`clawd_server/resources.py`、分发测试、许可生成器。没有 commit、push、deploy。
