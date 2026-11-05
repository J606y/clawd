# 浏览器 vendor 分发收录 — 2026-11-05

本文件是静态收录规则，不是开包验收，不是上线许可。

未构建 clawd wheel/sdist，未安装，未跑测试，未 import `browser_harness`，未启动 daemon，未做 CLI/CDP 探针。未打开任何新的 clawd 档案，未设置也未读取 `CLAWD_DISTRIBUTION_WHEEL` / `CLAWD_DISTRIBUTION_SDIST`。未改 `uv.lock`、`browser-harness==0.1.13` pin、Nix 过滤、`setup.py`、`clawd_server/resources.py` 或其它 stage 脚本。未改上游版权，未删文件，未读生产安装、原面板或 `/usr/local/lib/hermes-agent`。

vendor 树在本路写完声明之后出现。本路没有轮询等待，也没有改这棵树。只读核对结果：16 个上游成员的 sha256 与锁定 sdist 一致，没有符号链接，没有 `mcp_server.py`、egg-info 或 sdist 根元数据。`NOTICE` 不存在。`daemon.py` 与 `helpers.py` 仍是未打补丁哈希。缺补丁和缺 `NOTICE` 都不是已收录。

## 结论

不需要改 `packages.find`。setuptools 83 的 `[tool.setuptools.packages.find]` 不写 `namespaces` 时，`expand.find_packages` 默认 `namespaces=True`，走 `PEP420PackageFinder`。现有 `tools` / `tools.*` 能发现

`tools.vendor.browser_harness_0_1_13.src.browser_harness`

中间目录没有 `__init__.py` 也发现。把 `namespaces` 设成 false，或改用只认 `__init__.py` 的 `PackageFinder`，会在 `tools/vendor/` 停住，不往下走，整份 Python 包从 wheel 里消失。那是漏包。本路没有改发现配置，也没有用 package-data 的 `*.py` 冒充模块收录。

非 Python 文件不靠发现。`tools` 的 package-data 写了四条精确路径。sdist 靠 `MANIFEST.in` 的 `recursive-include`，并且这条写在 `global-exclude *.pyc` 之前。

发行名仍是 `clawd`。`license-files` 仍只是仓库根 `LICENSE`，没有改成这份第三方许可。extras、editable、Nix 过滤都没动。

## 收录规则

钉的是 `setuptools==83.0.0`。下面是该版本源码，不是按星号层数推断。

| 入口 | 规则 | 本路 |
| --- | --- | --- |
| 包发现 | `pyprojecttoml.py` 把 `packages.find` 原样传给 `expand.find_packages`。未写 `namespaces` 时默认 `True`。`PEP420PackageFinder._looks_like_package` 恒为真。目录名含 `.` 才跳过且不下降。`tools.*` 的 `fnmatch` `*` 跨点号，匹配嵌套名 | 不改 `packages.find` |
| wheel 模块 | 发现到的包由 `build_py` 从磁盘收集 `.py`。未跟踪也进 wheel。不要求先能 `import` | 靠现有 `tools.*` |
| wheel 数据 | `tools` package-data 用标准库 `glob`，`recursive=True`，不匹配点文件。四条都是精确相对路径，没有 `**`，没有点文件 | 已加 |
| sdist | 未跟踪文件不会自动进 sdist。`recursive-include tools/vendor/browser_harness_0_1_13 *` 用 `setuptools.glob`，含点文件、任意深度 | 已加，且在 `global-exclude *.pyc` 之前 |
| wheel 再收清单 | 默认 `include-package-data=True`。`analyze_manifest` 把清单里、位于已发现包目录内的文件并进 wheel | 不改这个默认值 |
| 无关文件 | 清单后部排除上游 sdist 残留：`PKG-INFO`、`README.md`、上游 `pyproject.toml`、`setup.cfg`、`src/mcp_server.py`、`*.egg-info/*`、`__pycache__/*`，并 prune 已知 `src/browser_harness.egg-info` | 已加。见下方漏洞 |
| 项目许可 | `license-files = ["LICENSE"]` 只复制仓库根 Nous 许可到 `*.dist-info/licenses/LICENSE` | 不改。第三方 MIT 只作为 vendor 文件随包 |

`video.py` 与 `video_render.py` 都是 `TEMPLATE = Path(__file__).with_name("video-template.html")`。模板必须跟模块在同一目录。安装后的路径是

`site-packages/tools/vendor/browser_harness_0_1_13/src/browser_harness/video-template.html`

子进程要把 `.../browser_harness_0_1_13/src` 放在 `PYTHONPATH` 最前，再执行已有的 `python -m browser_harness.daemon`。不要改成 `python -m tools.vendor...browser_harness`。父进程继续用已安装、未改协议的 `browser_harness`。

package-data 的 glob 没有命中时，setuptools 不报错。缺 `NOTICE` 或 `SKILL.md` 的构建不会自己失败。测试是失败关闭门禁，不是开包证明。

Nix `pythonSrc` 不排除顶层 `tools/`。vendor 放在 `tools/` 下不必改过滤。顶层 `skills/` 排除不影响 `tools/vendor/.../SKILL.md`。本路未改 `nix/lib.nix`。

editable 不跑 `bdist_wheel`，直接用检出。声明不改变 editable。检出里现在有未打补丁的原件，没有 `NOTICE`。这不能当成安装包已包含补丁。

## 精确资源

允许进入 clawd sdist/wheel 的 vendor 相对路径只有下面 18 个。没有的不算收录。多出来的是失败，尤其不要复制上游 sdist 根上的 `PKG-INFO`、`README.md`、`pyproject.toml`、`setup.cfg`、`src/mcp_server.py`、`src/browser_harness.egg-info/`。

锁定来源是 `artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz`。本路复核文件 sha256 为 `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3`，114905 字节，与 `uv.lock` 的 `browser-harness` 0.1.13 sdist 哈希一致。只列了成员并计算哈希，没有把它当成 clawd 包打开，也没有从网络重下。

除 `daemon.py`、`helpers.py` 外，vendor 副本必须与下表逐字节相同。这两份必须存在，且不得仍是未打补丁哈希。`_ipc.py` 必须仍是下表哈希。

| vendor 相对路径 | 角色 | sha256 |
| --- | --- | --- |
| `LICENSE` | 第三方 MIT 全文。版权行必须仍是 `Copyright (c) 2026 Browser Use`。不得换成仓库根 Nous 许可 | `5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631` |
| `NOTICE` | vendor owner 新写。不是 sdist 成员 | 无上游哈希。见接线 |
| `src/browser_harness/__init__.py` | 原件 | `b159e84a2701448a4905041f48b27bf6d26cc420b597b82e4fdeabbd7fe26c2f` |
| `src/browser_harness/_ipc.py` | 原件，禁止改 | `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3` |
| `src/browser_harness/admin.py` | 原件 | `d2d21c753f06e9275ce32291ca7e2c20acb83fab50486fffdbbfbb0fb1a7da94` |
| `src/browser_harness/auth.py` | 原件 | `e71acf31c28524f4b26f87770333d154c2182b70a9412be5276d982fa6c2cf9d` |
| `src/browser_harness/daemon.py` | 只许这一份打补丁。未打补丁哈希不得出现 | 未打补丁 `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97` |
| `src/browser_harness/helpers.py` | 只许这一份打补丁。未打补丁哈希不得出现 | 未打补丁 `5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3` |
| `src/browser_harness/macos.py` | 原件 | `e7fb08ba92af6976a58c6abc91e36eb625b978102ba03fee8f9f5911c88f5a74` |
| `src/browser_harness/mcp_cli.py` | 原件 | `11d66e3168436bf9bf49c2ddd301c19ac7bed9e717fc907fbb5ff61c7c160ece` |
| `src/browser_harness/paths.py` | 原件 | `ba3c6e4c91a8e5f8add837eedd51a1aa391f47a7d11c79db47ac8b8146267ad7` |
| `src/browser_harness/recorder.py` | 原件 | `ccf987870ab88cff7dbefe586165eaac122bba53dc73813e8202d65c840814e8` |
| `src/browser_harness/run.py` | 原件 | `cf2f5e52596c45686f0b4d606b89bce10a153293d2205523bfb246d161dc07b7` |
| `src/browser_harness/telemetry.py` | 原件 | `8d48b95afe4b149c2be115e0df18dd9f62496148a194277702d74829455cfa94` |
| `src/browser_harness/video.py` | 原件 | `6112a7582cfc71f7a8db9d8c0f703cbbf6ec47b5c5464564725154ffc3e41142` |
| `src/browser_harness/video_render.py` | 原件 | `96a2a69d3653d47f8a2de1b4b51d1b7deca4a12d163e03d7f4afcc262b2e4e04` |
| `src/browser_harness/SKILL.md` | 原件，package-data | `2155ce650490e214734b2ea2f3b23f9dd12addedd0382e048a8e9d805ab507a7` |
| `src/browser_harness/video-template.html` | 原件，package-data | `e237f67c2636e6946fd853c286fba0df0e7b10cfa2c95d4a9bb08fdd9d24e933` |

`tools` package-data 现为：

- `bot_desktop/launcher.sh`
- `bot_desktop/wallpaper.png`
- `vendor/browser_harness_0_1_13/LICENSE`
- `vendor/browser_harness_0_1_13/NOTICE`
- `vendor/browser_harness_0_1_13/src/browser_harness/SKILL.md`
- `vendor/browser_harness_0_1_13/src/browser_harness/video-template.html`

面板三层 `dist/*/*/*` 没改。不要把 vendor 收成 `**`。

## 发现核对

先在临时目录、后在真实 `tools/vendor/` 上调用了 setuptools 83 的 `find_packages`。两次都不是 clawd 构建，也没有 import `browser_harness`。

真实树：

- `namespaces=True`：发现 `browser_harness_0_1_13`、`browser_harness_0_1_13.src`、`browser_harness_0_1_13.src.browser_harness`。对应仓库根下的包名是 `tools.` 加这些名字，能被现有 `tools.*` 匹配。
- 只认 `__init__.py` 的 `PackageFinder`：结果为空。唯一的 `__init__.py` 在 `src/browser_harness/`，中间目录没有，所以传统发现不下降。
- 临时目录上 `namespaces=False` 同样只剩带 `__init__.py` 的 `tools`，嵌套包不在结果里。

因此现有 `tools.*` 足够，条件是构建系统继续用 setuptools 83 的默认命名空间发现。不要加 `where = ["src"]`，那会把 clawd 自己的包发现根改掉。不要把 `browser_harness` 加进顶层 include，那会和已安装的依赖抢名字。仓库根现在没有 `browser_harness/` 或 `src/browser_harness/`。

`src/mcp_server.py` 若被放进 vendor 树，会成为已发现命名空间包 `tools.vendor.browser_harness_0_1_13.src` 的模块，从而进 wheel。`MANIFEST.in` 的 `exclude` 只影响 sdist 和 `include-package-data`，挡不住 `find_all_modules`。要挡住它必须改 `packages.find` 的 exclude。那超出本路范围，所以不要复制这个文件。测试在它出现时失败，而不是静默收下。

## 仍需额外接线

1. vendor owner 还要写 `NOTICE`，并真正改 `daemon.py`、`helpers.py`。树已经在，16 个原件哈希正确，但这两个文件仍是未打补丁哈希 `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97` 和 `5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3`。`NOTICE` 要写明上游 Browser Use、MIT、sdist sha256、tag `v0.1.13`、改了这两个文件、clawd 补丁不移除上游版权。测试认这些字面量：`Browser Use`、`MIT`、上述 sha256、`v0.1.13`、`daemon.py`、`helpers.py`，以及 `Copyright` 或 `版权`，以及 `不移除` / `保留` / `not remove` / `retain` 之一。不要用仓库根 `LICENSE` 覆盖第三方 `LICENSE` 或充当 `NOTICE`。本路不改这两个补丁文件。
2. `tests/clawd_server/test_distribution_package_manifest.py` 的 `MANIFEST_LINES` 仍是改前的 14 行。该文件只读，本路不能改。跑到 `test_declarations_keep_manifest_as_depth_authority` 时会因整份 `MANIFEST.in` 不一致而失败。需要那个测试的 owner 把 `MANIFEST_LINES` 换成现在的 23 行。新测试 `tests/clawd_server/test_browser_vendor_distribution.py` 已经钉住这 23 行。面板深度契约本身没有改。
3. CLI owner 把安装后的 `tools/vendor/browser_harness_0_1_13/src` 加到子进程 `PYTHONPATH` 前面，site-packages 仍留在后面，以便 `cdp_use` 还能找到。不要改启动 argv 里的 `-m browser_harness.daemon`。本路不改 `tools/browser_use_cli.py`。
4. 验收日必须实际构建并打开新的 clawd wheel 和 sdist，按上表路径核对存在、类型、大小和 sha256。源码树可导入、package-data 写了、本报告、未跑的测试，都不是包内已包含。旧的 `hermes_agent` wheel 不能冒充。本路没有这样的新档案。
5. 不要为了让传统发现成功而给 `vendor/`、`browser_harness_0_1_13/`、`src/` 加 `__init__.py`。当前默认命名空间发现已经覆盖。加了会让测试的允许名单失败，也会让父进程多一条 `tools.vendor...` 导入路径。运行 daemon 仍必须是顶层 `browser_harness`。

## 测试

`tests/clawd_server/test_browser_vendor_distribution.py` 只做静态声明和逐档案哈希。它读锁定 sdist 和 vendor 树，不构建，不安装，不 import `browser_harness`，不读 `CLAWD_DISTRIBUTION_*`。树缺失、缺 `NOTICE`、或两个补丁仍是原件哈希时失败，不跳过。本路没有运行它，也没有运行其它测试。

## 未实际构建

没有 clawd wheel。没有 clawd sdist。没有安装前缀。没有打开新包。准备完成不是 pass。
