# clawd 分发包数据声明

本文件只记录静态核对。未打 wheel、未打 sdist、未安装、未跑测试、未做 Nix eval 或构建。`pyproject.toml` 与 `MANIFEST.in` 原样保留，不是因为没看，而是两条收录路径合在一起已经盖住契约 v2。不要把三层 `dist/*/*/*` 改成 `**` 来“补深度”：那会扩大 `package-data` 的命中面，且仍漏点文件。

## 结论

不是遗漏。wheel 与 sdist 收录任意深度普通文件，靠的不是 `package-data` 的星号层数，而是 `MANIFEST.in` 的 `recursive-include`，再由 pyproject 默认的 `include-package-data` 送进 wheel。`stage_distribution` 仍在 `setup.py` 两个守卫里、`super().run()` 之前调用。这条接线不改。

## 证据

钉的是 `setuptools==83.0.0`（`[build-system].requires` 与 dev 组）。语义来自该标签源码，不是按 glob 层数推断。

- `setuptools/command/build_py.py` 的 `find_data_files` 使用标准库 `glob`，调用是 `partial(glob, recursive=True)`。标准库里 `*` 只匹配一个不含分隔符的路径段；`**` 只有在模式段恰好是 `**` 且 `recursive=True` 时才递归。`include_hidden` 默认 `False`，且这里没有传入，所以 `*` 和 `**` 都不匹配点文件。因此现有 `dist/*`、`dist/*/*`、`dist/*/*/*` 只覆盖 dist 下 1 到 3 段，第 4 段和点文件都不走这条。
- `setuptools/command/egg_info.py` 的 `FileList.recursive_include` 把目录收成 `dir/**/<pattern>`，再用 `setuptools.glob.glob(..., recursive=True)`。`**` 列出该目录下全部分段，随后的 `*` 用 `fnmatch` 匹配文件名。`setuptools.glob` 明确不忽略隐藏文件，`fnmatch` 的 `*` 匹配点文件。所以 `recursive-include clawd_server/bundled/apps/panel/dist *` 覆盖任意深度的普通文件，包括点文件。sidecar `*.py`、plugins `*`、`LICENSES *` 同理。
- `setuptools/config/pyprojecttoml.py` 在未显式设置时把 `include-package-data` 默认成 `True`。本仓库没有 `setup.cfg`，`setup.py` 的 `setup()` 不传 `include_package_data=False`。`build_py.analyze_manifest` 在该值为真时把 egg_info 文件清单里、位于包目录内的文件并进 wheel。`existing_egg_info_dir` 只被 editable 安装设置；`bdist_wheel` 不设它，所以会在暂存之后重新跑 `egg_info`。wheel 的 `RECORD` 由实际写入安装树的文件生成，不沿用暂存前的清单。
- `global-exclude */node_modules/*` 不是任意深度排除。`translate_pattern` 里的 `*` 不跨目录分隔符，`global-exclude` 只是在前面加 `**/`。它能去掉 `node_modules` 的直接子文件，去不掉 `node_modules/pkg/index.js`。源码树靠 `prune apps/panel/node_modules`（模式是 `dir/**`）。暂存靠 `DIST_SKIP_PARTS`，根本不复制 `node_modules`、`venv`、`.venv`、`__pycache__`、`.git`，也不复制以 `.` 开头的目录。不要用加宽 `package-data` 来代替这两条。

`package-data` 仍有用：它不依赖清单，能补上暂存后、且不在版本库里的浅层文件。它不是深度的唯一入口，所以三层写法不必改。改成 `bundled/**/*` 不会补上标准库 glob 漏掉的点文件，却会把清单排除规则盖不住的额外文件再收一遍。

## 最终打包验收要逐项打开的文件集

验收必须实际构建并打开 wheel 与 sdist。以包内 `clawd_server/bundled/RESOURCE_CONTRACT.json` 的 `files[].path` 为清单，相对 `clawd_server/bundled/` 逐项核对：每条都在，类型为普通文件，大小与 sha256 一致；bundled 下没有清单外残留。清单本身不在 `files` 里，但两个档案都必须有这个文件。

固定必须出现：

- `RESOURCE_CONTRACT.json`
- `LICENSES/LICENSE`（暂存自仓库根 `LICENSE` 的副本；安装布局读这个路径）
- `apps/panel/dist/index.html`
- `apps/panel/dist/assets/*.js` 至少一个，且该 js 不再嵌套子目录
- 契约列出的其余 `apps/panel/dist/**` 普通文件，含深于 3 段的文件和点文件
- `apps/panel/sidecar/hermes_panel_sidecar.py`
- `apps/panel/sidecar/image_studio.py`
- `apps/panel/sidecar/live_task_store.py`
- `apps/panel/plugins/browser-window-guard/__init__.py`
- `apps/panel/plugins/browser-window-guard/live_task_store.py`
- `apps/panel/plugins/browser-window-guard/plugin.yaml`
- `apps/panel/plugins/browser-window-guard/README.md`

另外两份来源许可，不代替上面的 bundled 副本：

- sdist 根上的 `LICENSE`（`include LICENSE`）
- wheel 的 `*.dist-info/licenses/LICENSE`（`license-files = ["LICENSE"]`，setuptools 83 写到 dist-info 的 `licenses/`）

不得出现：路径段 `node_modules`、`venv`、`.venv`、`__pycache__`、`.git`；`*.pyc`；`apps/desktop/`、`web/`、`hermes_cli/web_dist/`；sidecar 的 `requirements.txt`、`*.service`、`test_*.py`。

核对函数是 `tests/clawd_server/test_distribution_package_manifest.py` 的 `compare_distribution_archive`。它只读调用方给出的档案，缺失就抛 `FileNotFoundError`，不合成成员名单。设置 `CLAWD_DISTRIBUTION_WHEEL` 与 `CLAWD_DISTRIBUTION_SDIST` 后跑该测试，才是这次打包验收。未设置时测试只证明缺档案不会被当成通过。

## 未运行

未执行该测试，未构建，未安装，未生成 stage，未改 Nix、锁文件、许可正文或 `resources.py`。
