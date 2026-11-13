# clawd 安装包资源契约

本文件是实现说明，不是验收通过声明。未打 wheel、未安装、未跑 `tests/clawd_server/test_distribution_resources.py`、未做 Nix eval 或构建。setup.py 已在两个 Nix 守卫通过后调用 `stage_distribution`。Nix 构建期已把 `panelResources` 传入 wheel 环境；未 eval、未 build、未验证。运行包装不设置该变量。

## 两种布局

- `checkout`：`clawd_server` 的上一级同时有真实文件 `pyproject.toml` 和 `apps/panel/package.json`（符号链接不算），且该目录不是 `/usr/local/lib/hermes-agent`。面板资源直接用检出里的 `apps/panel`。可编辑安装也走这条。
- `installed`：上述标记不存在。面板资源在 `clawd_server/bundled/apps/panel/`。缺 `index.html`，或 bundle 未通过版本 2 完整性检查时抛 `ResourceError`。不在安装环境下载或构建前端。残留目录即使有 `index.html` 也不算完整。

`code_root()` 是能导入 `hermes_cli` / `clawd_server` 的目录。面板核心设置页用 `python -I` 并把该路径插入 `sys.path`。它不能改成面板资源根。

## 包内含什么

`scripts/build_clawd_resources.py` 把已构建产物暂存到规范槽 `clawd_server/bundled/`（gitignore，不是源码）：

- `apps/panel/dist/**`（必须有 `index.html` 和 `assets/*.js`；跳过 `node_modules`）
- 面板核心：`clawd_server/panel_core.py`（文件/日志/live 状态/生图接口）、`clawd_server/image_studio.py`、`clawd_server/live_task_store.py`
- `plugins/browser-window-guard` 的 `__init__.py`、`live_task_store.py`、`plugin.yaml`、`README.md`
- 仓库 `LICENSE` 原样复制到 `LICENSES/LICENSE`。不生成许可清单，不改 `license-files`。

不复制：`node_modules`、`venv`、`.venv`、`__pycache__`、`.git`、`apps/desktop`、`web`、`hermes_cli/web_dist`。

契约版本是 2。`RESOURCE_CONTRACT.json` 的 `files` 是 `{path,type,sha256,size}`。`type` 只能是 `file`。完整性同时要求：路径留在 bundle 内、无符号链接、版本匹配、必要文件都在清单里、散列和大小一致、磁盘上没有清单外的残留文件。版本 1 或只有文件名的旧 bundle 不算完整。

## 暂存安全

- 工作目录是本次创建的 `.{dest}.stage-<pid>-<uuid>`。失败时只删除这个本次创建的目录。
- 不使用固定 `bundled.partial`，也不删除已经存在的 `.partial` / `.prev`。
- 并发用 `.{dest}.stage.lock` 的非阻塞独占锁。抢不到就拒绝，不删锁、不等待。
- 目标路径上的符号链接拒绝。复制时不跟随符号链接。
- 发布是同目录 `rename`。规范槽 `checkout/clawd_server/bundled` 是打包计划内的替换位：仅当现有目录已通过版本 2 检查时才换下，旧树改名为 `.{dest}.kept-<pid>-<uuid>` 并保留。失败则把旧树改回原位，不删除。
- 其它已存在路径默认拒绝。`--replace` / `replace=True` 也只能交换已校验旧包，不能删除未知内容。
- 父目录不存在时不创建。不清理用户另行放置的数据。

检出里有 `apps/panel` 时，未构建即失败，即使旧 bundle 完整也不替换。没有 `apps/panel` 时，不把预置 `bundled/` 当成成功。必须传入 `resource_root` 或环境变量 `CLAWD_PANEL_RESOURCE_ROOT`（面板根，或含 `apps/panel/` 的树）。本模块不下载、不构建前端。

## Nix

`nix/lib.nix` 的 `pythonSrc` 仍按 npm workspace 排除整个 `apps/`，并额外排除 `node_modules` 与 `clawd_server/bundled`。不能把宿主 `apps/panel/node_modules` 或 gitignore 的预置 bundle 送进 Python 源。把构建产物拼进 `pythonSrc` 会让 `uv2nix.loadWorkspace` 在求值期读 derivation 输出（import-from-derivation），因此没有这么做。

`nix/panel-resources.nix` 是范围内的离线构建。源只取 `apps/panel`，排除 `node_modules`、`dist`、`release`、`.git`、`tests`。依赖只用该目录自己的 `package-lock.json`，经 `importNpmLock.importNpmLock` 按锁内 integrity 取回；没有第二套 hash，没有失败后改走 registry 的回退。`$out` 只含 `dist/`、`browser-window-guard` 选定文件。不安装 `node_modules`，不写 `LICENSE`（许可仍由 Python 暂存从仓库 `LICENSE` 复制）。`lib.nix` 导出 `panelResources`，并由 `nix/hermes-agent.nix` 的 `mkHermesVenv` 传给 `nix/python.nix`。此推导未 eval、未 build。

rolldown / lightningcss 的平台包在面板 lock 里有 resolved URL 与 integrity。esbuild 只是 vite 的可选 peer，不是构建必需。`npm rebuild` 在离线模式下能否选中预置 binding，未验证。失败应保持失败，不要加联网回退。

### 已写入的构建期接线（未 eval、未 build，不能称为已验证）

消费链：

1. `nix/hermes-agent.nix` 的 `mkHermesVenv` 把 `panelResourceRoot = hermesNpmLib.panelResources` 传给 `nix/python.nix`。`let` 递归，可引用后面的 `hermesNpmLib`。不把该 derivation 拼进 `pythonSrc`，避免求值期 IFD。
2. `nix/python.nix` 增加 `panelResourceRoot ? null`，只在 hermes-agent 的 wheel 构建 derivation 上、与现有 `HERMES_NIX_BUILD` 同一覆盖里转发 store 路径：

```nix
  pythonSrc,
  panelResourceRoot ? null,
  dependency-groups ? [ "all" ],
```

```nix
            hermes-agent = prev.hermes-agent.overrideAttrs (
              _old: {
                HERMES_NIX_BUILD = "1";
              }
              // lib.optionalAttrs (panelResourceRoot != null) {
                CLAWD_PANEL_RESOURCE_ROOT = panelResourceRoot;
              }
            );
```

```nix
    callPackage ./python.nix {
      inherit uv2nix pyproject-nix pyproject-build-systems;
      pythonSrc = hermesNpmLib.pythonSrc;
      panelResourceRoot = hermesNpmLib.panelResources;
      dependency-groups = lib.unique ([ "all" ] ++ groups);
    };
```

`CLAWD_PANEL_RESOURCE_ROOT` 只存在于 wheel 构建环境。`nix/hermes-agent.nix` 的 `agentInputsFile.env` 和 `makeWrapper` 不设置它。安装运行时走 wheel 内 `clawd_server/bundled/apps/panel/`，不把 store 路径或构建沙箱路径写进包装。`stage_distribution` 的契约只记相对路径、`type`、`sha256`、`size`。

`panelResources` 的 `$out` 是面板根（`dist/`、`plugins/`）。没有 `apps/panel` 时，`_select_panel_tree` 接受这个形状，再暂存成契约 v2 的 `apps/panel/...`。Nix 的 `pythonSrc` 排除了 `apps/panel`，所以构建期必须靠这个环境变量，不能靠预置 `bundled/`。

`mkHermesVenv` 也被 icons 的 sealed venv 和 dev 的 editable venv 调用，因此这两条也会带上同一构建期依赖。editable 不调用 `bdist_wheel`，不会暂存。运行包装仍不导出该变量。

未 eval、未 build。不能说 `panelResources` 能产出 `index.html`，也不能说环境变量已实际进入 `setup.py`。未验证。

## 已观察到的其它接线（本路未改这些文件）

- `setup.py`：两个 Nix 守卫通过后调用 `stage_distribution(Path(_ROOT))`。
- `server.py`：`run_serve` 在 `static is None` 时用 `panel_static_dir()`，`ResourceError` 返回 2。
- `__main__.py`：`resolve_serve_static(None)` 调用 `panel_static_dir()`。
- `bind.py`：直接导入 `clawd_server.panel_core`。`PANEL_LIVE_STORE_MODULE` 指向 `clawd_server/live_task_store.py`，不是插件目录里的同名文件。插件副本仍打进资源树，但是否被导入未验证。
- `native.py`：`install` 调用 `browser_native.install`。本路未改。

直接 `setuptools.build_meta` 会走 `setup.py`，不会走 `packaging_backend`。可编辑安装不暂存。

## 构建与安装（本路不执行）

```bash
cd /root/hermes-workspace/clawd
npm run build --workspace apps/panel
python scripts/build_clawd_resources.py --check
python scripts/build_clawd_resources.py
```

面板不是根 workspace 成员。上面的 workspace 脚本若不能选中 `apps/panel`，应使用仓库已有的 `npm run build:panel`。本路两种都没跑。暂存脚本本身不构建前端。

PEP 517 / Nix wheel 都未跑。不要对生产前缀安装。隔离安装目录不能落在 `/usr/local/lib/hermes-agent` 或 `~/.hermes`。

若规范槽已有版本 1 或未校验内容，下一次暂存会拒绝并且不会删除它。需要操作者先把该目录移走。本路不移动、不删除它。成功替换后留下的 `.bundled.kept-*` 是上一版合法 bundle，回滚失败时必须还在；不要把它当成垃圾删掉，除非另行授权。`.gitignore` 已加入 `/clawd_server/.bundled.kept-*/`、`/clawd_server/.bundled.stage-*/`、`/clawd_server/.bundled.stage.lock`、`/clawd_server/.bundled.rejected-*/`。不忽略源码。本路扫描仓库时，`clawd_server/bundled`、`.bundled.kept-*`、`.bundled.stage-*`、`.bundled.stage.lock`、`.bundled.rejected-*` 和 `RESOURCE_CONTRACT.json` 都不存在。未移动、未删除，也未执行生成器或 stage。

## 三种资源定位的实际调用点

静态核对，未运行。不能写成 implemented+pass。

- checkout：`resources.detect_layout` 要求包目录上一级同时有真实文件 `pyproject.toml` 和 `apps/panel/package.json`。`panel_root` 返回该 `apps/panel`。`code_root` 返回包目录上一级，不是面板根。消费者：`home.panel_resource_root`、`home.panel_static_dir`、`bind`（直接导入 `clawd_server.panel_core`）、`server.run_serve` 在 `static is None` 时调用 `panel_static_dir()`、`__main__.resolve_serve_static(None)`、`scripts/build_clawd_resources.py --print-layout`。后四个文件本路未改。
- wheel：`setup.py` 的 `_GuardedSdist.run` 与 `_GuardedBdistWheel.run` 调用 `stage_distribution(Path(_ROOT))`，不传 `resource_root`，因此读 `CLAWD_PANEL_RESOURCE_ROOT`。`packaging_backend._stage` 被 `build_wheel` / `build_sdist` 调用，不被 `build_editable` 调用；uv2nix 直接走 `setuptools.build_meta`，所以 Nix wheel 不经过 `packaging_backend`。`scripts/build_clawd_resources.py:main` 可传 `--resource-root`，否则同样读环境变量。这三个调用点都未在本路执行。
- Nix：`lib.nix` 的 `panelResources` → `hermes-agent.nix` `mkHermesVenv` 的 `panelResourceRoot = hermesNpmLib.panelResources` → `python.nix` 构建 derivation 的 `CLAWD_PANEL_RESOURCE_ROOT = panelResourceRoot`。安装后的 `panel_root` 只读 `clawd_server/bundled/apps/panel`，不读该环境变量。运行包装的 `agentInputsFile.env` 只有 `HERMES_NODE` 和可选的 `HERMES_REVISION`。环境变量是否真的进入 uv2nix 沙箱里的 `setup.py`，未验证；通道与既有 `HERMES_NIX_BUILD` 相同，但本路没有 eval 或 build。

## 未解依赖

- 两处 Nix 接线已写入，未 eval、未 build。不能说 `panelResources` 能产出 `index.html`，也不能说 `CLAWD_PANEL_RESOURCE_ROOT` 已进入 `setup.py`。运行包装故意不设置该变量。未验证。
- 未打开 wheel，未做隔离安装，未验证安装后的 `hermes_cli` 导入、侧车 YAML 和插件发现。
- `pyproject.toml` / `MANIFEST.in` 的 `dist/*/*/*` 深度是否盖住更深的静态文件，未核对产物，也未改这两个文件。
- 安装后无 `.git`，侧车版本显示未改。
- `apps/panel` 没有独立 LICENSE，只复制仓库 MIT `LICENSE`。
- 测试文件已改，未运行。
