# clawd 分发许可保留说明

本文件只给分发代理留许可资源与接入建议。不改 `pyproject.toml`、`MANIFEST.in`、`LICENSE`、锁文件或 `clawd_server/resources.py`。不是安全扫描，不是合规结论，不含 CVE 判定。`scripts/clawd_inventory/generate.py` 与 `python3 scripts/clawd_inventory --offline` 本次都未执行。

## 已核对

- 核心许可文件是仓库根 `LICENSE`：标题 `MIT License`，`Copyright (c) 2025 Nous Research`。须原样保留，不得改成 clawd 版权，不得改写许可正文。
- `pyproject.toml` 发行名仍是 `hermes-agent`，`license = "MIT"`，`license-files = ["LICENSE"]`，作者 Nous Research。根 `package.json` 的 `license` 为 MIT，仓库为 `git+https://github.com/NousResearch/Hermes-Agent.git`。这些是上游归属，不是可替换品牌字段。
- 根 `NOTICE` 不存在。`plugins/security-guidance/NOTICE` 不是产品 NOTICE，未读作产品许可，不能代替根许可。
- 面板 `apps/panel/package.json` 名 `hermes-panel`，无 license 字段；`apps/panel/LICENSE` 不存在。许可未知，不得套用核心 MIT。
- TUI `ui-tui/package.json` 名 `hermes-tui`，描述已是 clawd terminal UI，无 license 字段，无 LICENSE。`@hermes/ink`、`@hermes/shared` 无 license 字段。内部包名不改，许可未知。
- 已读到的 `MANIFEST.in` 含 `include LICENSE` 与 `recursive-include clawd_server/bundled/LICENSES *`，并排除 node_modules/venv。`resources.py` 已把根 LICENSE 复制到 `clawd_server/bundled/LICENSES/LICENSE`。继续保留这条，不要改正文。实际 wheel 是否含面板未构建，标 unknown。

## 第三方与未知

锁里的 license 字符串只是元数据，不是许可判定。缺字段保持 unknown，不填 MIT。核心 MIT 不外溢到第三方、面板、TUI、`@hermes/ink`、`@hermes/shared`。这些内部包许可证仍未知，须用户确认，本路不新写 LICENSE。哪些依赖进入 wheel 或前端包，一律 unknown。

生成器已能离线读取 Python `uv.lock` 与 npm `package-lock.json` 的直接、传递、optional、dev、url、git、版本和 integrity。大锁用 stdlib 整文件解析，无网络、无随机。本次未执行，所以本文件和 `dependency-source-inventory.json` 仍没有逐条 source/integrity 行，也不用手抄或抽样充当全量。字段说明见 `scripts/clawd_inventory/SCHEMA.md`。

只读结构计数，不是生成器输出，也不是扫描：`uv.lock` 356 个 package，registry 352、editable 1、url 1、git 2；无 hash 的是 `hermes-agent@0.0.0`、`misaki@0.9.4`、`psutil@8.0.0`。根 `package-lock.json` 506 条，498 条有 license 字符串，8 条没有；integrity 497 条有、9 条没有。缺项抽样是工作区或链接。面板锁 266 条，已安装 265 条有 license 字符串和 integrity，根条目没有。面板自身许可仍未知。仓库 npm 锁未观察到 git `resolved`；解析器只按 `resolved` 前缀分类，未对仓库锁逐条执行。

`apps/panel/sidecar/requirements.txt` 声明 `PyYAML==6.0.3` 与 73 条 sha256。文件没写 registry URL，也没写 license。文件注释里的 PyPI 核验不是本次复核。optional-dependencies 全量成员未转写。执行生成器时会读 `pyproject.toml` 的 `[project.optional-dependencies]`，以及 `uv.lock` 的 `[package.optional-dependencies]` 与 `[package.dev-dependencies]`。哪些依赖进入 wheel 或前端包，一律 unknown。

## 接入建议

1. 不要新写面板或 TUI 的 MIT LICENSE，不要在许可材料里把 `hermes-panel`、`hermes-tui`、`@hermes/ink`、`@hermes/shared` 改名。
2. 建议日后把本文和生成器输出一并暂存到 `clawd_server/bundled/LICENSES/`，只作说明，不能代替 LICENSE。本次不改暂存代码。
3. 当前 MANIFEST 只收 sidecar `*.py`。建议分发代理把 `apps/panel/sidecar/requirements.txt` 随 sidecar 暂存，否则哈希钉进不了安装包。本路不改打包文件。
4. 验收日后、不是现在，在检出根运行：`python3 scripts/clawd_inventory --offline`。`--local-only` 相同。标准输出是生成器 JSON，不覆盖 `docs/clawd/dependency-source-inventory.json`。显式写法：`python3 scripts/clawd_inventory/generate.py --offline --checkout <检出> --output <新文件>`。不跑 npm audit，不联网。
5. 配套测试未运行，且不在 `pyproject.toml` 的 `testpaths`。日后显式运行 `python3 -m unittest scripts/clawd_inventory/test_generate.py`。不要把未运行当成通过。
