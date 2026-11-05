# clawd 脱源码分发资源契约复核 — 2026-11-05

本文件是准备记录，不是验收通过。本轮未构建、未安装、未 eval、未跑测试、未导入产品、未做 CLI 探针、未联网。未设置 `CLAWD_DISTRIBUTION_WHEEL` 与 `CLAWD_DISTRIBUTION_SDIST`。未打开任何 wheel/sdist。旧包不得冒充新包。许可清结归许可证 owner，本路不改源码、锁、清单或测试。

## 结论

三条打包入口最终都调用同一个 `clawd_server.resources.stage_distribution`，写出同一份契约 v2。`MANIFEST.in` 的 `recursive-include` 加 pyproject 默认的 `include-package-data` 已覆盖深层和点文件。这不是缺陷，不改 `pyproject.toml` 的三层 `dist/*/*/*`，也不把 `package-data` 改成 `**`。

真实新 clawd wheel/sdist 不存在。本轮未通过。

## 当前字节

分支 `clawd`，HEAD `c314285f077e91937c995d419b9fa40af9eb4981`。下面是工作区文件的 sha256，不是已提交 blob。相对 HEAD：`pyproject.toml`、`setup.py`、`nix/lib.nix`、`nix/python.nix`、`nix/hermes-agent.nix`、`nix/packages.nix` 为已跟踪修改。其余包装、测试和 `nix/panel-resources.nix` 仍是未跟踪文件。

| 文件 | sha256 |
| --- | --- |
| `setup.py` | `6f661e44e878a3882feade9d76de0cc3be9f9092ed66315f4641149e3975a0f2` |
| `MANIFEST.in` | `8f1aacfa255d2d055fc166e79e8dda02cf1bbb8bcd2acfe57985578d7ca5c48b` |
| `pyproject.toml` | `c2c1ac07903e64aa95b8360fae5a48a75a09246392e7d30b453bf09b5d2f2264` |
| `clawd_server/resources.py` | `8bb4c2e62d93a4577dbb9ef07bc0e68db2e6fb117d26c404b593eef7a610f3a6` |
| `clawd_server/packaging_backend.py` | `e6400b0521f4a2ecb0cdbeae141f419aafe582eb74f3654f5c25d89d832487e8` |
| `clawd_server/__main__.py` | `0cc9a226c7811a0f8ee89fac7c1656a634a38ad0036e3363fafbe57d6a198b2a` |
| `clawd_server/server.py` | `ea3534ab92e054fe38c79f6533b8797c86dea07facdfb09d3badc5a060dd03ab` |
| `clawd_server/bind.py` | `a0cfe9432fbd526d11850105d040d0d0cf1e85f9ceabada2aa57ff04f20d101b` |
| `clawd_server/home.py` | `3bef1ec022dc2e3756539f1ec2d5c04713b0352e9f46959c772092e2dd3e14cd` |
| `clawd_server/cli_entry.py` | `f7e7af2dd5710211ba56fd46db461558efbb4bee3cfd5dbdac1c11f74102a88e` |
| `clawd_cli/main.py` | `2f9d863a0805165760a5a7ba73aeee9644df9ca3718f0592d7927b8888d51080` |
| `scripts/build_clawd_resources.py` | `da1ab62ffdf0f3c8af0911a9151348bd58332d2179994621d5f1229599eaff5d` |
| `nix/lib.nix` | `fdd100a4eb48e35fb4d79ef7d72c7aa5eab76c1cbbb5a77ceef66374a5019aae` |
| `nix/python.nix` | `88bf5667ffb1d7e88a80fc602e5d02a2010b04a5fbd8498d92fa277d54e353f4` |
| `nix/panel-resources.nix` | `d055b4073f4f839f0c213a882a6eea2c2e2e827694963e46c1de4426df05de3a` |
| `nix/hermes-agent.nix` | `ea65392c87123a72c13345edda8f7a35989e245fd9373ef84982f308323b30b8` |
| `nix/packages.nix` | `775f1275faf5a2f5056a0ca088b0c68df4a4b19954e975297ff6a2eda0b321c0` |
| `tests/clawd_server/test_distribution_package_manifest.py` | `636078fa43d6eb18c345e90703827c4218949f0b4c75719e163c827cca3b9a48` |
| `tests/clawd_server/test_distribution_resources.py` | `fcc3f59794e65fbe35ce696f66d1ccdb089082f7ea17bbfbe854caf7aeb19bf0` |
| `tests/clawd_server/test_verification_result.py` | `88ece5550c1664cc15891a7d32b0f3108566c336a6d239d8b5703d3a2643cd39` |
| `scripts/verify_clawd.py` | `f18000a260b5c6030d5de768542c59375364e1d26e346b0a092f106f5bba6798` |

本进程未设置 `CLAWD_DISTRIBUTION_WHEEL`、`CLAWD_DISTRIBUTION_SDIST`、`HERMES_NIX_BUILD`、`CLAWD_PANEL_RESOURCE_ROOT`。`clawd_server/bundled` 与 `RESOURCE_CONTRACT.json` 不存在。`dist/` 不存在。没有可指向的新包。

只记录、未打开的既有档案：

- `artifacts/entry-packaging/wheel/hermes_agent-0.0.0-py3-none-any.whl`，15615347 字节。历史记录已写明不含 `apps/panel`。禁止当作 `CLAWD_DISTRIBUTION_WHEEL`。
- `artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz` 是第三方来源包，不是 clawd sdist。

`build/lib` 是旧副本。不删除，也不当安装包。不删除 stage、artifacts 或 `.bundled.kept-*`。本轮这些暂存路径并不存在。

## 调用链

发行名是 `clawd`。`pyproject.toml` `[project].name = "clawd"`，`uv.lock` 的 `name = "clawd"`、`source = { editable = "." }`。`nix/python.nix` 覆盖的属性也是 `clawd`。Nix 包名 `pname = "hermes-agent"` 只给外部 overlay 用，不是 Python 发行名。构建系统钉 `setuptools==83.0.0`。没有 `setup.cfg`。没有显式 `include-package-data = false`。

### PEP 517

`[build-system].build-backend = "clawd_server.packaging_backend"`。

1. `build_wheel` / `build_sdist` 先调 `_stage()`。
2. `_stage()` 调 `stage_distribution(仓库根)`，不传 `resource_root`。
3. 再交给 `setuptools.build_meta`。
4. `setup.py` 在导入时读取 `HERMES_NIX_BUILD`。不是 `1` 就在 `bdist_wheel` / `sdist` 的 `run` 里拒绝。
5. 守卫通过后、`super().run()` 之前，再次调用同一个 `stage_distribution(Path(_ROOT))`。

因此 PEP 517 会暂存两次。两次都是同一函数、同一契约。第二次只替换已经通过 v2 的规范槽。可编辑安装走 `build_editable`，不暂存。

非 Nix 的 `python -m build` 会先让 backend 暂存，再被 `setup.py` 拒绝。拒绝后可能留下 `clawd_server/bundled`。那不是发布成功。不要删，也不要把它当成新包。本轮没有触发这条副作用。

### 直接 setuptools

uv2nix 和 `tests/clawd_packaging.py`、`tests/test_packaging_build_guard.py` 直接调 `setuptools.build_meta`，不经过 `packaging_backend`。契约只靠 `setup.py` 里同一次 `stage_distribution`。没有第二套文件名单。

### Nix 过滤

`nix/lib.nix` 的 `pythonSrc` 排除 npm workspace 顶层，因此排除整个 `apps/`。另排除 `clawd_server/bundled`、`bundled.partial`、`bundled.prev` 和 `clawd_server/.bundled.`。预置 bundle 和宿主 `node_modules` 进不了 Python 源。`LICENSE` 不在排除名单，暂存仍能从检出复制。

`panelResources` 不拼进 `pythonSrc`，避免求值期读 derivation。`nix/hermes-agent.nix` 的 `mkHermesVenv` 把 `panelResourceRoot = hermesNpmLib.panelResources` 传给 `nix/python.nix`。后者只在 `clawd` 的 wheel 构建覆盖里、与 `HERMES_NIX_BUILD=1` 一起设置 `CLAWD_PANEL_RESOURCE_ROOT`。运行包装的 `agentInputsFile.env` 只有 `HERMES_NODE` 和可选的 `HERMES_REVISION`，没有这个变量。`nix build .#default` 是 `full`，`.#minimal` 是不带额外 dependency group 的同一 `hermes-agent.nix`。两条都会带上构建期依赖。editable 不跑 `bdist_wheel`，不会暂存。

沙箱里没有 `apps/panel` 时，`_select_panel_tree` 接受 `CLAWD_PANEL_RESOURCE_ROOT`。`panel-resources.nix` 的 `$out` 是面板根：`dist/`、`sidecar/`、`plugins/browser-window-guard/`，不是 `apps/panel`。暂存再写成契约里的 `apps/panel/...`。环境变量是否真的进入 uv2nix 沙箱里的 `setup.py`，未 eval、未 build，不能写成已验证。通道与既有 `HERMES_NIX_BUILD` 相同，只说明写法对齐，不说明已经生效。

Nix `$out` 不是 wheel 契约本身。Python 暂存才写 `RESOURCE_CONTRACT.json`。Nix 复制 dist 时只拒绝 `node_modules` 路径段；Python `_iter_dist_files` 还跳过 `venv`、`.venv`、`__pycache__`、`.git`、点目录、`.pyc` 和 `.DS_Store`。这些差异留在输入侧。进包名单以暂存契约为准。

未验证的形状风险：Nix 用 `find dist/assets -type f -name '*.js'`，嵌套 js 也算。Python `_asset_js` 要求 `apps/panel/dist/assets/*.js` 且不再有斜杠。若真实构建只有嵌套 chunk，Nix 推导可以成功而 Python 暂存失败。没有打开构建产物，不能判定已经发生。失败应保持失败，不要加联网回退，也不要为了这一未知提前改过滤。

## 唯一暂存与锁

规范槽是 `checkout/clawd_server/bundled`。工作目录是本次创建的 `.{dest}.stage-<pid>-<uuid>`，模式 `0700`。失败只删本次创建、名称含 `.stage-` 或 `.rejected-` 的目录。不使用固定 `bundled.partial`，不删已有 `.partial` / `.prev`。

锁是父目录 `.{dest}.stage.lock`，`O_NOFOLLOW`，非阻塞 `LOCK_EX`。抢不到就拒绝，不删锁、不等待。目标或路径上的符号链接拒绝。复制不跟随符号链接。

发布是同目录 `rename`。规范槽只有现有目录已通过 v2 才换下，旧树改名为 `.{dest}.kept-<pid>-<uuid>` 并留下。其它已存在路径默认拒绝。`replace=True` 也只能交换已校验旧包，不能删未知内容。父目录不存在时不创建。检出里有 `apps/panel` 时，未构建即失败，即使旧 bundle 完整也不替换。没有 `apps/panel` 时，不把预置 `bundled/` 当成成功。

## 契约 v2

版本常量是 2。`files[]` 每项是 `path`、`type=file`、64 位小写 sha256、非负整数 `size`。完整性同时要求：路径留在 bundle 内、无符号链接、版本匹配、`license == LICENSES/LICENSE`、必要文件都在、散列和大小一致、至少一个不嵌套的 `assets/*.js`、磁盘上没有清单外残留。契约文件本身不在 `files` 里。版本 1、空列表、只有文件名、只有 `index.html` 的残留目录，都不完整。

固定必须出现：

- `RESOURCE_CONTRACT.json`
- `LICENSES/LICENSE`（仓库根 `LICENSE` 的副本）
- `apps/panel/dist/index.html`
- 至少一个 `apps/panel/dist/assets/*.js`，且该 js 不再嵌套
- 契约列出的其余 `apps/panel/dist/**`，含深于 3 段的文件和点文件
- `apps/panel/sidecar/hermes_panel_sidecar.py`
- `apps/panel/sidecar/image_studio.py`
- `apps/panel/sidecar/live_task_store.py`
- `apps/panel/plugins/browser-window-guard/` 下 `__init__.py`、`live_task_store.py`、`plugin.yaml`、`README.md`

另外两份来源许可，不代替 bundled 副本，也不构成面板或 TUI 许可：

- sdist 根 `LICENSE`
- wheel 的 `*.dist-info/licenses/LICENSE`

不得出现：路径段 `node_modules`、`venv`、`.venv`、`__pycache__`、`.git`；`*.pyc`；`.DS_Store`；`apps/desktop/`、`web/`、`hermes_cli/web_dist/`；sidecar 的 `requirements.txt`、`*.service`、`test_*.py`。

`package-data` 的 `dist/*`、`dist/*/*`、`dist/*/*/*` 只覆盖 1 到 3 段，且标准库 glob 不匹配点文件。深度和点文件靠 `MANIFEST.in` 的 `recursive-include ... *`，再由默认 `include-package-data` 送进 wheel。`bdist_wheel` 会在暂存之后重跑 `egg_info`。`global-exclude */node_modules/*` 去不掉 `node_modules/pkg/index.js`；源码树靠 `prune apps/panel/node_modules`，暂存靠 `DIST_SKIP_PARTS`。不要加宽 `package-data` 来代替这两条。对应静态测试是 `test_declarations_keep_manifest_as_depth_authority` 与 `test_setuptools_recursive_include_covers_depth_package_data_does_not`。未运行。

## 包内定位与脱源码入口

`detect_layout`：包目录上一级同时有真实文件 `pyproject.toml` 和 `apps/panel/sidecar/hermes_panel_sidecar.py`，且不是 `/usr/local/lib/hermes-agent`，才是 `checkout`。否则是 `installed`。符号链接不算。

- `code_root()` 是可导入 `hermes_cli` / `clawd_server` 的目录。检出是仓库根；安装后是 `site-packages`。不能改成面板资源根。
- `panel_root()` 检出是 `apps/panel`；安装后是 `clawd_server/bundled/apps/panel`。
- `panel_static_dir()` 安装布局缺 `index.html` 或 v2 不完整时抛 `ResourceError`。不在安装环境下载或构建前端。

入口：

1. 控制台脚本 `clawd = clawd_cli.main:main`。
2. `clawd panel` → `clawd_server.__main__.main`。生产导入根退出 2。`export_resource_env(require_panel=True)` 或 `resolve_serve_static(None)` 遇到 `ResourceError` 退出 2。
3. `run_serve` 在 `static is None` 时再调 `panel_static_dir()`，`ResourceError` 返回 2。显式 `--static` 会跳过 bundle 检查。脱源码验收禁止把 `--static` 指到检出的 `apps/panel/dist`。
4. `server.PanelServer.start` 先 `prepare_env`，再 `load_sidecar`，再 `bind`。`load_sidecar` 把 `panel_root()` 插入 `sys.path`，导入 `sidecar.hermes_panel_sidecar`。
5. `PANEL_LIVE_STORE_MODULE` 指向 `panel_root()/sidecar/live_task_store.py`。插件目录里的同名文件仍打进资源树，但是否被导入未验证。两份都在契约里，不是残留。

设置页是脱源码入口里必须分开看的一点。`apps/panel/sidecar/hermes_panel_sidecar.py` 的 `SETTINGS_HELPER` 用 `HERMES_PY -I -c` 跑，并把 `HERMES_SRC` 插入 `sys.path` 后导入 `hermes_cli`。`python -I` 不读 `PYTHONPATH`。`bind.prepare_env` 把 `HERMES_SRC` 设成 `home.REPO_ROOT`。今天 `home.py` 在 `clawd_server/` 内，`parents[1]` 与 `code_root()` 相同：检出是仓库根，安装后是 `site-packages`。它没有调用 `code_root()`。模块默认值仍是 `/usr/local/lib/hermes-agent` 和那里的 venv python；只有 `prepare_env` 先跑才安全。`server.start` 当前先调用它。未在安装前缀里跑过。

验收进程必须导入安装前缀里的 `clawd_server`。在检出里跑 `python -m clawd_server` 会判成 checkout，直接读 `apps/panel`，不能证明 wheel。

检出里 `apps/panel/dist/index.html` 存在，4550 字节。这只说明源码树有一个静态文件，不证明 `assets/*.js` 完整，更不证明包内哈希。未遍历 dist，未做 sha256。

## 实现缺口

不改代码。这些是未完成项，不是本轮修复授权。

1. 没有新的 clawd wheel/sdist。Nix 包装结果是 venv 和 wrapper，仓库里没有把 uv2nix 的 wheel 与 sdist 导出到稳定路径的 passthru。`nix build` 成功也不能自动得到 `CLAWD_DISTRIBUTION_*` 可指向的档案。
2. 宿主 `HERMES_NIX_BUILD=1 python -m build` 走检出 `apps/panel`，不证明 Nix `pythonSrc` 过滤和环境变量注入。两条都要单独验收。
3. `CLAWD_PANEL_RESOURCE_ROOT` 进入沙箱、`panelResources` 能产出 `index.html` 和非嵌套 `assets/*.js`，均未验证。
4. rolldown / lightningcss 的离线 binding 能否被 `npm rebuild` 选中，未验证。失败保持失败。
5. 脱源码设置页、侧车导入、插件发现、安装后 `hermes_cli` 导入，都没有在隔离前缀跑过。
6. `scripts/verify_clawd.py` 的 `panel-static` 比对检出 `apps/panel/dist/index.html`，不打开安装包。它不是分发验收。
7. 两个环境变量都未设置时，`test_supplied_archives_match_contract_and_missing_archives_fail_closed` 只对一个不存在的临时路径断言 `FileNotFoundError`，然后返回。pytest 退出 0 不是开包通过。只设置其中一个会 `AssertionError`，按下面的验收语义应先判阻塞退出 2，而不是让 pytest 把它当成断言失败。不改该测试；编排器必须在调用前分类。
8. `run_serve` 只把取静态目录时的 `ResourceError` 映射为 2。`serve()` 内部的导入或 bind 异常没有单独映射。不能把未捕获异常或探针杀掉进程写成通过。
9. 许可证 owner 报告不存在。资源哈希通过也不等于许可清结。

## 许可证 owner

预期报告 `docs/clawd/resumption-licenses-detail-2026-11-05.md` 不存在。调度里的 owner 是 `sa-5-d48b004f`。现有说明是 `docs/clawd/distribution-notices.md`。该文仍写发行名 `hermes-agent`，已经过时。当前 `pyproject.toml` 发行名是 `clawd`，`license = "MIT"`，`license-files = ["LICENSE"]`，作者仍是 Nous Research。根 `LICENSE` 须原样进 `LICENSES/LICENSE`，不得改成 clawd 版权。

本轮只读到的包名和许可字段：

| 包 | 名称 | license 字段 | LICENSE 文件 |
| --- | --- | --- | --- |
| Python 发行 | `clawd` | `MIT` | 仓库根有，1070 字节 |
| 根 npm | `clawd` | `MIT` | 同上 |
| 面板 | `@clawd/panel` | 无 | `apps/panel/LICENSE` 无 |
| TUI | `hermes-tui` | 无 | `ui-tui/LICENSE` 无 |
| ink | `@hermes/ink` | 无 | 未新写 |
| shared | `@hermes/shared` | 无 | `apps/shared/package.json` 无该字段 |

这四个内部包许可未知，不能套核心 MIT，也不能改名。`scripts/clawd_inventory` 与 `--offline` 未执行。哪些依赖进入 wheel 或前端包，仍是 unknown。

`resources.py` 的 `OMITTED_SIDECAR` 明确不暂存 `requirements.txt`、`*.service`、`test_*.py`。Nix `installPhase` 也不复制它们。档案核对把 sidecar `requirements.txt` 当禁入。许可证说明曾建议把 `apps/panel/sidecar/requirements.txt` 打进包，以便哈希钉随包走。这是跨 owner 的未决冲突，不是本路要改的打包缺陷。按当前契约，包里出现该文件是失败；它不在包里也不证明 PyYAML 许可或完整性。等许可证 owner 报告，不在这里改暂存名单。

## 日后验收命令

本轮不执行。隔离前缀和 home 都还没创建。禁止落到 `/usr/local/lib/hermes-agent`、`/usr/local/bin/hermes`、`/root/.hermes`、`/root/hermes-workspace/hermes-panel`、`/var/www/hermes-panel`。不要读生产凭据。不要删除已有 artifacts、`build/` 或回滚目录。

计划路径：

- 前缀：`/root/hermes-workspace/clawd/artifacts/distribution-acceptance/prefix`
- home：`/root/hermes-workspace/clawd/artifacts/distribution-acceptance/home`
- 档案目录：`/root/hermes-workspace/clawd/artifacts/distribution-acceptance/dist`

宿主 PEP 517，证明 checkout 暂存和 backend，不证明 Nix 过滤：

```bash
cd /root/hermes-workspace/clawd
npm run build:panel
HERMES_NIX_BUILD=1 python -m build --wheel --sdist \
  --outdir artifacts/distribution-acceptance/dist
```

`HERMES_NIX_BUILD` 必须在 Python 进程启动前导出。`setup.py` 在导入时读取它。

直接 setuptools，跳过 `packaging_backend`，仍是同一 `stage_distribution`：

```bash
cd /root/hermes-workspace/clawd
HERMES_NIX_BUILD=1 python setup.py bdist_wheel sdist
```

Nix 过滤路径：

```bash
cd /root/hermes-workspace/clawd
nix build --no-link .#minimal
nix build --no-link .#default
```

这两条不自动留下可赋值的 `.whl` / `.tar.gz`。构建后必须从该次构建的真实输出定位新的 `clawd` 档案。名字仍是 `hermes_agent-0.0.0`、或路径在 `artifacts/entry-packaging/` 的，一律拒绝。

只有两个路径都是该次新档案时才导出：

```bash
export CLAWD_DISTRIBUTION_WHEEL=<新 wheel 的绝对路径>
export CLAWD_DISTRIBUTION_SDIST=<新 sdist 的绝对路径>
```

然后用 `tests/clawd_server/test_distribution_package_manifest.py` 的 `compare_distribution_archive` 对两个档案只读核对。以包内 `clawd_server/bundled/RESOURCE_CONTRACT.json` 的 `files[]` 为名单，相对 `clawd_server/bundled/` 逐项核对类型、大小和 sha256；bundled 下除契约文件外不得有残留；全档案不得有禁入路径。缺档案、空 `files`、未设置变量，都不是通过。

隔离安装与运行，必须用安装后的模块，不能把检出放进 `PYTHONPATH`：

```bash
CLAWD_HOME=/root/hermes-workspace/clawd/artifacts/distribution-acceptance/home \
HERMES_HOME="$CLAWD_HOME" \
  /root/hermes-workspace/clawd/artifacts/distribution-acceptance/prefix/bin/clawd \
  panel serve \
  --home /root/hermes-workspace/clawd/artifacts/distribution-acceptance/home \
  --host 127.0.0.1 --port 0
```

通过条件是：布局为 `installed`，静态文件来自 `clawd_server/bundled/apps/panel/dist`，设置子进程的 `sys.path` 插入的是安装后的代码根而不是面板根，且不是 `/usr/local/lib/hermes-agent`。资源缺失应退出 2。进程一直监听不是回合成功；探针杀掉进程不是通过。不声称模型、MCP 或浏览器可用。

测试运行器日后是 `scripts/run_tests.sh`，不是裸 pytest 把未设置变量的那条用例当成绿。本轮不跑。

## 退出码

全部必需检查通过才是 0。断言失败是 1。只有阻塞、未知、未跑或空结果是 2。有失败时失败优先，即使同时有阻塞也是 1。

`scripts/verify_clawd.py` 的 `verification_exit_code` 已是这个规则。`tests/clawd_server/test_verification_result.py` 覆盖了全通过 0、通过加阻塞 2、失败加阻塞 1、空结果 2、unknown 2。未覆盖 `not_run`，但函数对任何非全 `pass` 都返回 2。该函数没有分发档案检查。未运行。

分发门禁要单独编排，不能把上面的面板验收退出码当成开包结果：

| 状态 | 退出 |
| --- | --- |
| 两个新档案都逐项匹配，且许可、脱源码入口、Nix 注入都已单独通过 | 0 |
| 哈希或大小不符、残留、禁入、契约不是 v2、`files` 为空、符号链接、把面板根当代码根 | 1 |
| 变量未设置、只设置一个、档案不存在、指向旧 `hermes_agent` wheel、许可证报告缺失、Nix 未 eval、隔离前缀未跑、结果集为空 | 2 |
| 失败与阻塞同时存在 | 1 |
| 未设置变量时那条 pytest 自己退出 0 | 不是本门禁的 0 |

`scripts/build_clawd_resources.py` 自身是 0 完整、2 缺输入或不安全，没有退出 1。未捕获异常不算已分类。`__main__` 对生产根、空密码、`ResourceError` 返回 2。这些准备脚本的 0 只表示暂存或进程启动分类，不是分发验收通过。

## 阻塞依赖

- 真实新 wheel 与 sdist，以及把它们导出给两个环境变量的路径。
- Nix eval/build，含 `panelResources` 的 `index.html`、非嵌套 js，和 `CLAWD_PANEL_RESOURCE_ROOT` 是否进入 `setup.py`。
- 离线 npm binding。不允许失败后改走 registry。
- 隔离前缀、非生产 `CLAWD_HOME`，以及从该前缀启动的脱源码进程。
- 许可证 owner 报告。四个内部包许可未知。清单生成器未跑。
- 非生产模型、MCP、浏览器不在本门禁的通过条件里。缺它们不把资源契约写成通过，也不假装端到端已通。

准备完成不是 pass。测试通过以后也不构成 commit、push、deploy 或迁移授权。
