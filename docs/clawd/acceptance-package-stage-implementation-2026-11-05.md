# package 阶段真实构建接线 — 2026-11-05

未运行。没有测试、构建、导入 `clawd_server.packaging_backend`、CLI 探针、网络或开包。没有设置 `CLAWD_DISTRIBUTION_WHEEL` / `CLAWD_DISTRIBUTION_SDIST`。没有删除文件，没有读生产秘密，没有访问原面板或 `/usr/local/lib/hermes-agent`。验收仍未开始。

本文件是静态实现记录，不是通过，不是上线许可。

## 结论

目录里的 package 命令已改成调用真实的 `build_wheel` 与 `build_sdist`。验收运行路径不读这条命令。`execute.py` 仍只调用 `setuptools.build_meta.build_wheel`，不构建 sdist，也不导出两个环境变量。跨阶段上下文必须改 `scripts/clawd_acceptance/execute.py`。本轮没有造档案路径，也没有假装该文件已接通。

## 真实调用链（只读核对，未执行）

声明的后端是 `pyproject.toml` 的 `build-backend = "clawd_server.packaging_backend"`。

1. `clawd_server.packaging_backend.build_wheel(wheel_directory, config_settings=None, metadata_directory=None)` 先 `_stage()`，再交给 `setuptools.build_meta.build_wheel`。返回值是写在 `wheel_directory` 里的 basename。
2. `build_sdist(sdist_directory, config_settings=None)` 同样先 `_stage()`，再交给 `setuptools.build_meta.build_sdist`，返回 basename。
3. `_stage()` 调用 `stage_distribution(仓库根)`，不传 `resource_root`。检出有 `apps/panel` 时用检出，不读一个本轮造出来的资源根。
4. `setup.py` 在导入时读取 `HERMES_NIX_BUILD == "1"`。不是 `1` 就在 `bdist_wheel` / `sdist` 的 `run` 里拒绝。守卫通过后、`super().run()` 之前再次调用同一个 `stage_distribution`。因此 PEP 517 会暂存两次。这是既有行为，本轮不改。
5. 直接 `setuptools.build_meta` 跳过 `packaging_backend._stage()`。契约只剩 `setup.py` 里那一次暂存。这不是本轮要保留的验收入口。

暂存锁和唯一工作目录仍只属于 `clawd_server/resources.py`：

- 规范槽是 `checkout/clawd_server/bundled`。
- 工作目录是本次创建的 `.{dest}.stage-<pid>-<uuid>`，模式 `0700`。
- 锁是父目录 `.{dest}.stage.lock`，`O_NOFOLLOW`，非阻塞 `LOCK_EX`。抢不到就拒绝，不删锁、不等待。
- 失败只删本次创建、名称含 `.stage-` 或 `.rejected-` 的目录。不删固定 `.partial`、`.prev`、`.kept-*` 或未知内容。

验收隔离目录仍是 `Layout.wheel`：`artifacts/final-acceptance/wheel`。没有新造 `artifacts/distribution-acceptance/dist`。

消费者仍是 `tests/clawd_server/test_distribution_package_manifest.py` 的 `compare_distribution_archive`。两个变量都空时，该测试对一个不存在的临时路径断言 `FileNotFoundError` 后返回，pytest 退出 0，不是开包通过。只设置一个会 `AssertionError`。编排必须在调用前分类。该测试本轮不改。

## 本轮写进 stages.py 的命令

`package_argv` 的子进程在导入前设置 `HERMES_NIX_BUILD=1`，再调用 `run_package_build`。后者在隔离目录下新建 `accept-build-<pid>-<uuid>`（`0700`，`exist_ok=False`），然后：

```text
build_wheel(str(outdir))
build_sdist(str(outdir))
```

不传 `resource_root` 或 `config_settings`。钩子返回的 basename 必须是本次唯一目录里的普通文件，名称以 `clawd-` 开头，wheel 为 `.whl`，sdist 为 `.tar.gz` / `.tgz` / `.tar` / `.zip`。符号链接、`hermes_agent-` / `hermes-agent-`、`entry-packaging`、目录外路径、缺文件，都不导出。

成功时只向子进程 stdout 打一行：

```text
CLAWD_ACCEPTANCE_DISTRIBUTION {"wheel": "<绝对路径>", "sdist": "<绝对路径>"}
```

路径只来自这次钩子返回值。目录构造时 `distribution_env` 是 `None`。没有把历史包 `artifacts/entry-packaging/wheel/hermes_agent-0.0.0-py3-none-any.whl` 写进命令。

子进程不能改父进程环境。父进程要导出变量，必须解析这行标记。解析函数是 `parse_distribution_marker`。不要从检查 detail 里抠路径。

输出目录名故意不含 `.stage-`、`.rejected-`、`.partial`，避免被资源暂存的失败清理当成自己的目录。本函数不 `unlink`、不 `rmtree`，失败也留下本次目录和父目录里已有文件。

这是检出上的宿主 PEP 517，证明 backend 和 `stage_distribution`。不证明 Nix `pythonSrc` 过滤，也不证明 `CLAWD_PANEL_RESOURCE_ROOT` 进了沙箱。面板 dist 不完整时钩子失败，退出 1；这里不补 `npm` 构建。

## 退出码

`package_result_exit` 复用 `classify.aggregate_exit`：

| 结果 | 退出码 |
| --- | --- |
| 必需检查非空且全部 pass | 0 |
| 任一 fail；fail 与 blocked 混合仍是 1 | 1 |
| 空结果、无必需检查、blocked、unknown、not_run、超时 | 2 |

钩子抛错是 1。没有新档案、不在隔离目录、依赖缺失、隔离目录不存在或是符号链接、禁止路径，是 2，不是 pass。只缺一个 `CLAWD_DISTRIBUTION_*` 也是 blocked/2，不能交给 pytest 变成 AssertionError/1。

`package-manifest-compared` 在本模块里保持 `not_run`。因此即使两个钩子都返回了文件，聚合也不能是 0。路径就绪不是契约核对通过。

新增的计划检查也是 `not_run`：`package-build-wheel`、`package-build-sdist`、`package-distribution-env`、`package-manifest-compared`。`run_all` 会把执行结果里没有的 id 从 `coverage_checks` 补上。所以在 `execute.py` 发出这些行之前，验收聚合不能是 0。这不表示新命令已经被运行。

## 无法接通点

`execute.py` 的 `_dispatch` 在 package 上调用 `run_package`，不读 `stage["argv"]`。`run_package` 自己的命令仍是：

```text
from setuptools.build_meta import build_wheel; print(build_wheel(sys.argv[1]))
```

它只构建 wheel，写到 `layout.wheel`，再用 `*.whl` 数量是否为 1 判断。已有的一个 wheel 会被当成这次的产物。它不调用 `build_sdist`，不解析标记，不设置两个环境变量。

`run_all` 只把 `real-model-mcp` 的 payload 传给 `continuation`。没有把 package 输出传给后续阶段。`direct_env` 不包含这两个变量。核心阶段在 package 之前，且 `scripts/run_tests.sh` 的 `env -i` 不转发它们。把变量加进 allowlist 也接不通：核心已经跑完，而且两个都空时那个测试的退出 0 不是开包通过。

`_command_stage` 把任何非 0 都记成 fail。不能把 package 子进程的 2 丢给它。

`fingerprints.artifact_report` 仍只 glob `*.whl`。它不是这两个环境变量的消费者。本轮不改它。

## 接通所需的精确范围

只缺这一处实现文件：

`/root/hermes-workspace/clawd/scripts/clawd_acceptance/execute.py`

- `run_package`（约 214–254 行）：停止私有的 `setuptools.build_meta` argv。用目录里的 `stage["argv"]`，或同样的 `package_argv`。子进程环境用 `package_child_env(direct_env(...))`，让 `HERMES_NIX_BUILD=1` 在进程启动前就存在，并丢掉父进程里旧的 `CLAWD_DISTRIBUTION_*`。固定 venv python 缺失仍是 blocked/2，不回落系统 Python。
- 不要走 `_command_stage`。子进程 0 才解析日志里的 `CLAWD_ACCEPTANCE_DISTRIBUTION`；1 是 fail；2 和超时是 blocked。不要 glob `*.whl`。
- 只在 `parse_distribution_marker(...)["may_start_manifest_test"]` 为真时，把返回的 `env` 交给下一次独立 pytest。不要在验收进程里直接调用 `run_package_build`，以免 `setup.py` 已经在没有该变量时被导入。
- `_dispatch`（约 697–699 行）：把 `stage` 传进 `run_package`。
- `run_all`（约 785–793 行）：package 之后、且仅当两个路径都是这次的新档案时，才运行：

```sh
CLAWD_DISTRIBUTION_WHEEL="$WHEEL" \
CLAWD_DISTRIBUTION_SDIST="$SDIST" \
/root/hermes-workspace/clawd/.venv/bin/python \
  -m pytest -q tests/clawd_server/test_distribution_package_manifest.py
```

缺任一变量就不要启动它。用这次 pytest 的结果替换 `package-manifest-compared`；没有这次运行就保持 `not_run`。检查 id 必须由执行器发出，否则 `coverage_checks` 会补上 `not_run`。

不需要改，也不要为了接通去改：

- `isolation.py`：不要把分发变量放进 `direct_env`。
- `scripts/run_tests.sh`：不要加进 `env -i` allowlist。
- `tests/clawd_server/test_distribution_package_manifest.py`
- `clawd_server/packaging_backend.py`、`setup.py`、`clawd_server/resources.py`
- `gate.py`、清单、资源契约、`accept.py`
- `fingerprints.py`：可选的后续指纹问题，不是导出两个变量所必需。

`spawn` 已把 stdout 写入阶段日志。解析日志即可，不必改它的返回签名。

## 修改文件

- `/root/hermes-workspace/clawd/scripts/clawd_acceptance/stages.py`
- `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_package_stage_execution.py`（新；未运行）
- `/root/hermes-workspace/clawd/docs/clawd/acceptance-package-stage-implementation-2026-11-05.md`（本文件）

用例覆盖注入钩子的实际调用、钩子失败、缺文件、历史名、符号链接、缺依赖、混合状态和退出码。本轮没有执行该文件，也没有执行构建。
