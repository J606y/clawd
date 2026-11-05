# clawd 分发搜索能力保全 — 2026-11-05

不是通过，不是上线许可。未跑 pytest，未构建 wheel/sdist，未安装，未跑 CLI，未做 Nix eval，未 import 产品模块。只对改过的 Python 文件做了 `ast.parse` 语法检查。未删 `build/`、`*.egg-info`、验收日志或旧档案。未读 `/root/.hermes`，未读生产面板，未读 `/usr/local/lib/hermes-agent`。未 commit / push / deploy。

上一份 `distribution-prune-fix-2026-11-05.md` 把 `plugins/web` 写成禁入成员，是功能分类错误，不是批准范围内的官方 Web/Desktop 裁剪。旧 wheel/sdist 里的 `plugins/web/**` 不是“被批准裁剪后的泄漏”。本批撤回那条误排除，不靠放宽任意 `/web/` 子串来骗绿。

## 身份

只读核对，未改插件源码。

`plugins/web` 是捆绑网页搜索提供商，不是官方面板：

- `plugins/web/__init__.py`：`kind: backend`，经 `ctx.register_web_search_provider()` 写入 `agent.web_search_registry`。
- `brave_free/plugin.yaml` 声明 `provides_web_providers: brave-free`；`__init__.py` 注册 `BraveFreeWebSearchProvider`。
- `ddgs/plugin.yaml` 声明 `ddgs`；`__init__.py` 注册 `DDGSWebSearchProvider`。`ddgs/_search_worker.py` 是子进程入口，按 `python plugins/web/ddgs/_search_worker.py` 运行。
- 发现：`hermes_cli/plugins_discovery.py` 扫捆绑插件，注释写明类别键如 `web/firecrawl`。`web` 不在跳过集合里。捆绑 `kind: backend` 默认启用（`hermes_cli/plugins_cmd.py` `_BUNDLED_DEFAULT_ON_KINDS`）。
- 注册表：`hermes_cli/plugins_registrars.py` 把 `register_web_search_provider` 接到 `agent.web_search_registry`。
- 消费者：`tools/web_tools.py`（`plugins.web.firecrawl.provider`）、`tools/web_tools_extract.py`（`plugins.web._common`）、`tools/web_tools_rescue.py`（`plugins.web.keyless_mcp`）、`agent/web_search_registry.py`（`plugins.web.keyless_mcp`）。`hermes_cli/tool_availability_notices.py` 要求提供商名存在于 `plugins/web/`。

官方 UI 根（本检出里 `web/`、`apps/desktop/`、`hermes_cli/web_dist/` 已经不在树上，规则仍按这些身份裁，防止再入包）：

- 顶层 `web/`：`hermes_cli/source_build.py` 的 `build_source_web` 读 `web/package.json`，用 `scripts/build/web.mjs` 写到 `hermes_cli/web_dist`。
- `hermes_cli/web_dist/`：建成的官方 dashboard。
- `apps/desktop/`：官方 Desktop，含 `dist-webapp`（`hermes_cli/webapp.py`）。

不是官方 UI，必须留：`hermes_cli/web_routers/`、`tools/bot_desktop/`、browser vendor 的 `LICENSE`/`NOTICE`、以及 `plugins/web` 下其余搜索提供商。任意内层 `web`（`skills/web`、`docs/.../web`、`website/.../web`）也不按官方 UI 处理。不用搜索路径 allowlist 吞掉未知 `web` 路径。

## 入包 / 裁剪身份

| 身份 | 相对路径 | 决定 |
| --- | --- | --- |
| Brave 免费搜索 | `plugins/web/brave_free/{__init__.py,provider.py,plugin.yaml}` | 必须入包 |
| DDGS 搜索 | `plugins/web/ddgs/{__init__.py,provider.py,plugin.yaml,_search_worker.py}` | 必须入包 |
| 其余捆绑搜索提供商 | `plugins/web/<vendor>/` | 入包。不是 allowlist，只是不在官方前缀上 |
| API 路由 | `hermes_cli/web_routers/` | 保留 |
| Bot Desktop | `tools/bot_desktop/` | 保留 |
| browser vendor 许可 | `tools/vendor/browser_harness_0_1_13/LICENSE`、`NOTICE` | 保留 |
| 官方 dashboard 源 | `web/` | 裁 |
| 官方 Desktop | `apps/desktop/`（含 `dist-webapp`） | 裁 |
| 建成的官方 dashboard | `hermes_cli/web_dist/` | 裁 |
| 资源包里的上述三棵树 | `clawd_server/bundled/{web,apps/desktop,hermes_cli/web_dist}/` | 裁。这是嵌套资源形式 |
| sdist 根下的同一相对路径 | `clawd-<version>/web/` 等 | 先去掉已知档案根再裁 |
| wheel data 巢 | `clawd-<version>.data/{purelib,platlib,data,scripts,headers}/` 下的官方前缀 | 裁 |
| node_modules / venv / .venv / __pycache__ / .git / *.pyc / .DS_Store | 任意位置，包括 `plugins/web/**` | 仍禁 |
| sidecar `test_*`、`*.service`、`requirements.txt` | `apps/panel/sidecar/` | 仍禁 |

`RESOURCE_CONTRACT.json` 未改。`excluded_prefixes` 已是 `apps/desktop`、`web`、`hermes_cli/web_dist`，语义本来就是相对前缀，不是路径组件。`files[]` 的 path/size/sha256 未动。不改这份已暂存契约来假装分类已纠正。

## 源 → 规则 → 测试

1. `pyproject.toml` 打包段：`packages.find.exclude` 只留 `hermes_cli.web_dist` 与 `hermes_cli.web_dist.*`。去掉 `plugins.web` / `plugins.web.*`。`exclude-package-data` 去掉 `plugins = ["web/*"]`（fnmatch 的 `*` 跨分隔符，会丢掉搜索 `plugin.yaml`）。`hermes_cli = ["web_dist/*"]` 保留。`namespaces` 仍不设 false。
2. `MANIFEST.in`：删除 `prune plugins/web`。保留 `prune web`、`prune apps/desktop`、`prune hermes_cli/web_dist`。`FileList.prune("web")` 的模式是 `web/**`，只去顶层 `web/`，不去 `plugins/web`。
3. `clawd_server/resources.py`：`PRUNED_PACKAGE_ROOTS` 只剩 `hermes_cli.web_dist`。`merge_pruned_exclude_package_data` 不再加 `plugins = ["web/*"]`。`distribution_path_is_pruned` 在去掉已知 sdist 根（`name-version/`）、wheel `.data/<scheme>/` 和资源根 `clawd_server/bundled/` 之后，精确匹配官方相对前缀。`..` 先解析，避免 `plugins/web/../../web/` 躲过。任意内层 `/web/` 不算官方 UI。`apps/desktop` 与 `hermes_cli/web_dist` 按组件序列匹配，所以绝对路径和资源嵌套也裁；`bot_desktop`、`web_routers` 不匹配。
4. `nix/lib.nix` `pythonSrc`：删除 `builtins.elem "web" components`。改为 `web/`、`hermes_cli/web_dist/`、`apps/desktop/` 前缀。顶层 `web` 当前不在 npm workspace 里，这条前缀才是裁剪，不能靠 workspace 推导代替。`web_routers` 保留。`flake.nix` 只改注释。
5. 测试：`forbidden_reason` 走上面的精确函数，不再用 `f"/{prefix}" in f"/{norm}"`。clawd wheel（`clawd-*.dist-info/METADATA`）和 clawd sdist（`clawd-*/PKG-INFO`）缺 `REQUIRED_SEARCH` 即失败。面板契约夹具不是 clawd 发行物，不因此被要求带搜索文件。契约 size/sha256 检查未放宽。

## 保留的隔离构建

未恢复复用仓库 `build/` 或 `*.egg-info/SOURCES.txt`。

- `setup.py` 未改。`finalize_options` 仍先 `apply_distribution_boundary`，`run` 仍在 `super().run()` 前 `stage_distribution`，`finally` 只 `release_distribution_boundary`。它本来就不点名 `plugins.web`。
- `packaging_backend.py` 行为未改，只补了一句注释。wheel/sdist 钩子仍建本次 `.clawd-dist.stage-*`，返回后只删这个 stage。不删已有 `build/`、`*.egg-info`、验收档案。editable 仍不走这条。
- 已 finalized 的仓库 `build` 仍拒绝，且不删除旧文件。

## 文件

改了：

- `clawd_server/resources.py`
- `clawd_server/packaging_backend.py`（注释）
- `pyproject.toml`（只打包段）
- `MANIFEST.in`
- `nix/lib.nix`
- `flake.nix`（注释）
- `tests/clawd_server/test_distribution_package_manifest.py`
- `tests/clawd_server/test_pruned_python_packages.py`
- `tests/clawd_server/test_browser_vendor_distribution.py`（`MANIFEST_LINES` 与 `MANIFEST.in` 对齐）
- `tests/clawd_acceptance_cases/test_current_contract_registration.py`
- 本报告

未改：`setup.py`、`RESOURCE_CONTRACT.json`、其它源码和测试。旧 `build/lib/plugins/web`、两份 `SOURCES.txt`、验收档案 `artifacts/final-acceptance/wheel/accept-build-320184-6978a4cb34f949158ec4cd590a044863/` 都还在，未删。那些旧档案仍含搜索插件；那是当时的包内容，不是本批批准裁剪的证据，也不能代替下一次隔离构建后的开包。

## 未运行

未执行 `tests/clawd_server/test_distribution_package_manifest.py`，未执行 `test_pruned_python_packages.py`，未执行 `test_browser_vendor_distribution.py`，未执行 `test_current_contract_registration.py`。未 `python -m build`，未 `setup.py bdist_wheel`，未安装，未跑 CLI，未 Nix eval。语法检查不是开包通过。修复是否让新档案同时带上 `plugins/web/brave_free`、`plugins/web/ddgs` 并排除官方 UI，要等下一次隔离构建后再开包。
