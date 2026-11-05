# clawd 分发裁剪修复 — 2026-11-05

不是通过，不是上线许可。未跑 pytest，未构建 wheel/sdist，未安装，未跑 CLI，未做 Nix eval。未删 `build/`、`*.egg-info`、验收日志或旧档案。未读 `/root/.hermes`，未读生产面板，未读 `/usr/local/lib/hermes-agent`。

## 根因

失败档案（只读打开，未改）：

- `/root/hermes-workspace/clawd/artifacts/final-acceptance/wheel/accept-build-320184-6978a4cb34f949158ec4cd590a044863/clawd-0.0.0-py3-none-any.whl`（2882 个成员）
- 同目录 `clawd-0.0.0.tar.gz`（2955 个成员）

两个档案各有同一组 37 个 `plugins/web/**`。pytest 先比较 wheel，所以日志停在 wheel。sdist 会以同样的 `forbidden pruned tree` 失败。`MANIFEST.in` 的 `prune web` 没有挡住它们。

入包路径不是资源契约，也不是把裁剪 UI 加回允许表。`RESOURCE_CONTRACT.json` 未改。

1. `[tool.setuptools.packages.find]` 的 `include` 含 `plugins` 与 `plugins.*`。setuptools 83 因此发现 `plugins.web` 和 `plugins.web.*`，把它们当 Python 包。
2. 验收走 `packaging_backend.build_wheel` → `setuptools.build_meta` → `bdist_wheel`。`package.log` 首行是 `running bdist_wheel`。`build_py` 按发现结果从源码树拷贝，不看 `MANIFEST.in`。证据行：`copying plugins/web/__init__.py -> build/lib/plugins/web`（以及同树其余模块）。`package-data` 的 `plugins = ["**/plugin.yaml", ...]` 再把 yaml 拷进同一棵 `build/lib`（日志约 2769 行起）。
3. `include-package-data` 默认真。`manifest_maker.add_defaults` 在 `SOURCES.txt` 已存在时会 `read_manifest()`，把旧清单加回文件列表，然后才读 `MANIFEST.in`。`clawd.egg-info/SOURCES.txt` 第 2277–2313 行、`hermes_agent.egg-info/SOURCES.txt` 第 2211–2247 行列出了这 37 个文件。
4. `FileList.prune("web")` 的模式是 `web/**`。它去掉顶层 `web/`，不去掉 `plugins/web/**`。所以 sdist 的 prune 不等于 wheel 的包发现，而且这条 prune 对 `plugins/web` 两边都无效。
5. Nix `pythonSrc` 原先整棵保留 `plugins/`。官方 `web/` 与 `apps/` 已因 JS workspace 被排除，挡不住 `plugins/web`。

契约把路径组件 `web` 当成裁剪树（`forbidden_reason` 用 `/web/` 子串）。这是搜索插件树，不是官方 Web 面板，但仍是禁入成员。没有把它写进允许表。

其它插件仍在 include 里。`tui_gateway`、`clawd_server`、`hermes_cli/web_routers`、`tools/bot_desktop`、browser vendor `LICENSE`/`NOTICE` 不在这条排除上。`namespaces` 未改成 false。

## 泄漏成员（wheel 与 sdist 相同的 37 个）

`plugins/web/__init__.py`、`_common.py`、`keyless_mcp.py`，以及 `brave_free`、`ddgs`、`exa`、`firecrawl`、`keenable`、`openai_native`、`parallel`、`perplexity`、`searxng`、`tavily`、`xai` 下的 `__init__.py` / `provider.py` / `plugin.yaml`。`ddgs` 另有 `_search_worker.py`。sdist 成员前缀是 `clawd-0.0.0/`。

## 改了什么

三条收录路径用同一份排除，不靠删当前 `build/lib`：

- `pyproject.toml` 只动打包段：`packages.find.exclude` 为 `plugins.web`、`plugins.web.*`、`hermes_cli.web_dist`、`hermes_cli.web_dist.*`。`exclude-package-data` 用 fnmatch（`*` 跨分隔符）丢掉父包 glob 仍会扫到的 `plugins/web/*` 与 `hermes_cli/web_dist/*`。
- `MANIFEST.in` 增加 `prune plugins/web`。原有 `prune web`、`prune apps/desktop`、`prune hermes_cli/web_dist` 保留。
- `setup.py` 的 sdist / bdist_wheel 在 `super().run()` 前仍调用 `stage_distribution`。`finalize_options` 调用 `apply_distribution_boundary`：滤掉裁剪包，把 `build_base` 与 `egg_base` 指到本次新建的 `.clawd-dist.stage-*`，不读仓库 `build/` 或 `*.egg-info/SOURCES.txt`。已 finalized 的默认 `build` 直接拒绝。`build_py` 子类再滤模块和 data 文件。
- `packaging_backend.py` 的 wheel/sdist 钩子在调用 setuptools 前设置 `CLAWD_DIST_BUILD_BASE`，钩子返回后只删这个 stage。editable 不走这条。直接 `setuptools.build_meta`（uv2nix）不经过本模块，由 `setup.py` 自己建 stage。
- `nix/lib.nix` 的 `pythonSrc` 丢掉路径组件 `web` / `web_dist`、`apps/desktop`，以及顶层 `build` 与 `*.egg-info`。`web_routers` 的组件不是 `web`，保留。`flake.nix` 本身没有过滤表达式，只加了指向 `nix/lib.nix` 的注释。

`scripts/clawd_resources/` 不存在。真实资源脚本是 `scripts/build_clawd_resources.py`。它只暂存面板 dist/sidecar/`browser-window-guard`，不负责 `plugins/web`。未改。

`tests/clawd_server/test_distribution_backend.py` 不存在，未新建。

`tests/clawd_server/test_browser_vendor_distribution.py` 只同步了 `MANIFEST_LINES` 里新增的 `prune plugins/web`。`tests/clawd_acceptance_cases/test_current_contract_registration.py` 要求这份字面量与 `MANIFEST.in` 全等。未改 vendor 收录规则，未把 `browser_harness_0_1_13` 写进 `nix/lib.nix`、`setup.py` 或 `resources.py`。

新测试 `tests/clawd_server/test_pruned_python_packages.py`：声明仍含其它 plugins / TUI / vendor 许可；`prune web` 去不掉 `plugins/web` 而 `prune plugins/web` 可以；已 finalized 的仓库 `build` 被拒绝且旧文件还在；旧 `SOURCES.txt` 与 `build/lib` 不进入本次 wheel 和 sdist；两个档案的必需成员、sha256、禁入 `plugins/web`。未运行。

## 列出、未删除

- `/root/hermes-workspace/clawd/build/lib/plugins/web/`（37 个文件，与 wheel 成员同名）
- `/root/hermes-workspace/clawd/clawd.egg-info/SOURCES.txt`（第 2277–2313 行）
- `/root/hermes-workspace/clawd/hermes_agent.egg-info/SOURCES.txt`（第 2211–2247 行）
- 上面两个验收档案

下次构建应使用隔离 stage，而不是删这些路径来假装已修复。本次没有重打 wheel，所以上面的档案仍然含 `plugins/web`。

## 未运行

未执行 `tests/clawd_server/test_pruned_python_packages.py`，未执行 `test_distribution_package_manifest.py`，未 `python -m build`，未 `setup.py bdist_wheel`，未 Nix eval。开包事实来自只读 zip/tar 列表和 `package.log` / `package-manifest.log`。修复是否让新档案通过契约，要等下一次隔离构建后再开包，不能用这次旧 wheel 代替。
