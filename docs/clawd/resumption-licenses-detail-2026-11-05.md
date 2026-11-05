# clawd 许可证复核 — 2026-11-05

本文件只记录只读复核。不是许可判定，不是合规结论，不是安全扫描，不是验收通过。

本次未运行 `scripts/clawd_inventory/generate.py`、`scripts/clawd_inventory --offline`、`unittest`、构建、安装、Nix eval/build、开档案、`npm audit`，也未联网。未改包 metadata、LICENSE、锁、生成器或测试。未读生产秘密，未碰 `/root/hermes-workspace/hermes-panel`、`/var/www/hermes-panel`、`/usr/local/lib/hermes-agent`。`apps/panel/.git` 是目录；本次未读 git config，未重核嵌套仓库 HEAD。

结构计数来自本次标准库 `json` / `tomllib` 只读解析，不是生成器输出。`docs/clawd/dependency-source-inventory.json` 与 `docs/clawd/distribution-notices.md` 不得当当前全量或当前标识。

## 结论

根 Python 发行与根 npm 包可以维持既有 MIT 声明：仓库根 `LICENSE` 正文、`pyproject.toml`、根 `package.json`、根锁 `packages[""].license` 一致，版权仍是 `Copyright (c) 2025 Nous Research`。这不把版权改到 clawd，也不覆盖另外四个内部包、第三方依赖或缺失的 NOTICE。

四个内部包许可证据不足，状态 **blocked**。不能套根 MIT，不能新写 LICENSE，不能把锁里的 license 字符串或 Nix `licenses.mit` 当成授权。需要各包版权持有人的书面授权；工具不能补上。

将来提取器退出 0 只表示固定输入可读且解析成功。它不会判定这四包的许可，也不会说明哪些文件进入了 wheel。开档案与提取是两条独立检查，当前两条都未跑。仓库根和 `dist/` 没有 `*.whl` / `*.tar.gz`；`clawd_server/bundled` 不存在；`CLAWD_DISTRIBUTION_WHEEL` 与 `CLAWD_DISTRIBUTION_SDIST` 均未设置。

## 四包标识

这四包就是交接与 `distribution-notices.md` 点名、且不能默认 MIT 的内部包。当前标识以 metadata 为准，不以旧快照里的 `hermes-panel` / `hermes-agent` 为准。

| id | 当前包名 | 版本 | 目录 | metadata |
| --- | --- | --- | --- | --- |
| panel | `@clawd/panel` | `0.1.0` | `/root/hermes-workspace/clawd/apps/panel` | `private: true`。无 `license` / `author` / `repository` |
| tui | `hermes-tui` | `0.0.1` | `/root/hermes-workspace/clawd/ui-tui` | 描述已是 `clawd terminal UI`。无上述字段 |
| ink | `@hermes/ink` | `0.0.1` | `/root/hermes-workspace/clawd/ui-tui/packages/hermes-ink` | `file:./packages/hermes-ink`。无上述字段 |
| shared | `@hermes/shared` | `0.0.0` | `/root/hermes-workspace/clawd/apps/shared` | `file:../apps/shared`。无上述字段 |

四目录内，跳过 `.git`、`node_modules`、`dist`、`release`、点目录后，没有 `LICENSE`、`LICENCE`、`COPYING`、`NOTICE`、`COPYRIGHT` 及其 `.md` / `.txt`。包名不改。

根 npm workspaces 是 `apps/shared`、`ui-tui`、`ui-tui/packages/*`、`tests-js`（`package.json` 第 7–10 行；根锁第 12–16 行）。`apps/panel` 不在其中，自有 `package-lock.json`。

### 逐项证据与未知

**panel。** `apps/panel/package.json` 第 2–4 行：`name` `@clawd/panel`，`private`，`version` `0.1.0`。全文 54 行，无 license 键。sha256 `4a78ab03d54f7b96e8fd060fcb45875b8646d77ae16671144a35d7ce4c761d15`，1884 字节。旧快照同字节数但哈希是 `8d1f583311497518f87ebad2849efe68870bd8402e44f93ec1b3eba83022e21a`，且把名字记成 `hermes-panel`，已过期。面板锁顶层与 `packages[""]` 第 2–9 行同名同版本，无 license、无 integrity。锁 sha256 `79c512554a7934062d64ae73b6a9f76bff795c0bca5e0d7f606d021074bc1247`，150282 字节；旧快照 `dae066f2ed97f9051e76e4a0c03e71eebe8db0d11987475248c7842b9481a4c0` 字节数相同、哈希不同。未知：版权持有人、适用许可证、能否随根 MIT 分发。嵌套 git 的存在不能代替授权。

**tui。** `ui-tui/package.json` 第 2–5 行：描述、`hermes-tui`、`0.0.1`、`private`。全文 48 行无 license。sha256 `30458fd81748d3d844fa97906efa5c2baefa1f84dd64992e5c06a17d19c48f3e`，1513 字节，与旧快照相同。根锁工作区条目第 7145–7150 行有名字、版本和 `file:` 依赖，无 license、无 integrity。`node_modules/hermes-tui` 第 3855–3858 行只有 `resolved: ui-tui` 与 `link: true`。未知同上。描述改成 clawd 不是许可。

**ink。** `ui-tui/packages/hermes-ink/package.json` 第 2–4 行：`@hermes/ink`，`0.0.1`，`private`。全文 59 行无 license。sha256 `9fbbbe9f3447de6a1b509e27be985b3edc14bac42c37b5f88aef5c7bd639c55b`，1492 字节，与旧快照相同。根锁工作区条目第 7199–7201 行无 license。链接条目第 1163–1166 行只有 `resolved: ui-tui/packages/hermes-ink` 与 `link: true`，无 version、integrity、license。本树没有上游许可副本。仓库外对 ink 的记忆不能当本目录授权。

**shared。** `apps/shared/package.json` 第 2–4 行：`@hermes/shared`，`private`，`0.0.0`。全文 38 行无 license。sha256 `b9ae25a2a7f088a1f2d4083f19bd10c4432c1e451022d885c59b0f1996cee85e`，1211 字节，与旧快照相同。根锁第 38–44 行只有名字、版本和 typescript devDependency。链接条目第 1171–1174 行指向 `apps/shared`，无 license / integrity。未知同上。

四包都没有同目录 `LICENSE` / `NOTICE` / `COPYING`。按生成器现有规则，即使将来跑提取器，这四份 manifest 的 `license` 也应保持 `unknown`，`manifest_license` 也应是 `unknown`。这是读码推断，不是本次运行输出。

### blocked 与所需授权

在下列材料落到对应目录之前，四包保持 blocked：

1. 该包版权持有人的书面授权，写明包名、版权行和许可正文。不能从根 `LICENSE` 机械复制，也不能把发行名改成 clawd 当成版权转移。
2. `@hermes/ink` 若要沿用上游许可，需要本目录内的上游许可全文，以及覆盖本地修改的说明。没有文件就不能认定。
3. `@clawd/panel` 的授权必须来自该嵌套仓库的权利人。父仓库根 MIT 不自动附着。
4. 授权未到之前，不得在 Nix `meta.license`、wheel 标签或 NOTICE 里把这四包写成 MIT。

本次没有扫描四包源码头部。头部即使有版权行，也不是包级许可，不能解除 blocked。

## 根许可：够用，且不外溢

| 证据 | 位置 | 内容 |
| --- | --- | --- |
| 许可正文 | `LICENSE` 第 1、3 行 | 标题 `MIT License`；`Copyright (c) 2025 Nous Research` |
| Python metadata | `pyproject.toml` 第 6、16–18 行 | `name = "clawd"`；作者 Nous Research；`license = "MIT"`；`license-files = ["LICENSE"]` |
| 根 npm metadata | `package.json` 第 2、27–31 行 | `name` `clawd`；`license` `MIT`；仓库仍是 `git+https://github.com/NousResearch/Hermes-Agent.git` |
| 根锁镜像 | `package-lock.json` 第 8–11 行 | `packages[""]` 名 `clawd`，版本 `1.0.0`，`license` `MIT` |

`LICENSE` sha256 `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`，1070 字节，21 行，非符号链接。与旧快照一致，正文未改。根 `NOTICE` 不存在。`plugins/security-guidance/NOTICE` 不是这四包的同目录文件，不在默认输入里，不能代替产品 NOTICE。

当前文件哈希，旧快照已不能对上的：

- `pyproject.toml`：`c2c1ac07903e64aa95b8360fae5a48a75a09246392e7d30b453bf09b5d2f2264`，47378 字节。快照 `f4f054a31e188587c0abe88f284aef4f1280b2ecf3fe6645025cee416bf9c703`，47223 字节。快照仍写发行名 `hermes-agent`。
- `package.json`：`34eb0dd183cdf84d5da97d6566db3d4611365820329fd93f8e793b2a09d7b77f`，2784 字节。快照 `800b554712d93509cea3c4b09e078733e51ecfa84d8d2ce837253f1e0d8b85a4`，2791 字节。
- `uv.lock`：`f9aa7c8ee28bf7a8dffb1e22df70cdcdbb77a95ce9b40d02c1bc847f9c1a7019`，1244645 字节。快照 `e7314b3489730fd1832e53d79979bb01986246e657058766ed09372fb30fbf70`，1244771 字节。
- `package-lock.json`：`cbd82812443c8c689f8ace98755287e26434d244fd624a600061cb6d03f4bf4c`，249942 字节。快照 `e70767480951f0747ddb0005cf2d3a4de4b58fa48ae65e56f3ace63a0dffd6aa`，249956 字节。

`distribution-notices.md` 第 8、10 行仍写 `hermes-agent` 与 `hermes-panel`。那是过期叙述。当前根发行名是 `clawd`，面板名是 `@clawd/panel`。

## 锁覆盖：本次结构计数，不是全量提取

`uv.lock` 356 个 package：registry 352、editable 1、url 1、git 2。整个文件 `license` 字段出现 0 次。无 hash 的是 `clawd@0.0.0`（第 2296–2298 行，`source = { editable = "." }`，无 license）、`misaki@0.9.4`（第 3658–3660 行，git）、`psutil@8.0.0`（第 5069–5071 行，git；同名 `7.2.2` 在第 5038 行是 registry）。旧快照把无 hash 项写成 `hermes-agent@0.0.0`，计数碰巧相同，标识已过期。提取器若运行，Python 锁行的 `declared_license` 仍会是 `unknown`，因为锁里没有可抄的 license 字符串。不要补 MIT。

根 `package-lock.json` lockfileVersion 3，506 条。498 条有非空 license 字符串，8 条没有；integrity 497 有、9 没有。这 8 个缺 license 的键是：

- `apps/shared`（第 38 行）
- `ui-tui`（第 7145 行）
- `ui-tui/packages/hermes-ink`（第 7199 行）
- `tests-js`（第 7103 行）
- `node_modules/@hermes/ink`（第 1163 行）
- `node_modules/@hermes/shared`（第 1171 行）
- `node_modules/hermes-tui`（第 3855 行）
- `node_modules/@hermes/root-tests`（第 1167 行）

缺 integrity 的是这 8 个再加 `packages[""]`。4 个 `link: true` 都没有 version、integrity、license。旧快照说 8 缺只打印了 6 个；本次补全的就是上面 8 个，没有发现额外第三方缺项。这仍不是生成器逐行输出，也不能把 498 条 lock 字符串当成许可判定。

面板锁 266 条。265 条同时有 license 字符串和 integrity。唯一缺的是根条目 `""`（第 7–9 行，名 `@clawd/panel`）。面板自身许可仍未知。其依赖的 lock 字符串只是 metadata。

`apps/panel/sidecar/requirements.txt` sha256 `228960abe2055602b6f3c3b947751cbf1aee793133834dae06cd0489181cea4b`，6347 字节，76 行，与旧快照相同。第 3 行是 `PyYAML==6.0.3`，73 个 `--hash=sha256:`。无 URL，无 license 词。第 1–2 行注释里的 PyPI 核验不是本次复核。

生成器默认输入是 `generate.py` 第 23–36 行：根 `LICENSE`、`NOTICE`、`pyproject.toml`、`uv.lock`、根 `package.json`、根 `package-lock.json`、面板 manifest 与锁、sidecar `requirements.txt`、TUI / ink / shared 三份 manifest。不扫描 `node_modules` 或 venv。不包含、因此一次默认运行也不会盘点：

- `apps/bootstrap-installer/package.json`：`@clawd/bootstrap-installer` `0.0.0`，无 license，无 LICENSE。sha256 `2204e78115a4d166258722af3826de786927b2caac09862b47c66c0a510c6d9f`
- `tests-js/package.json`：`@hermes/root-tests`，无 license
- `website/package.json`：`website` `0.0.0`，无 license
- `plugins/platforms/photon/sidecar/package.json`：`@hermes-agent/photon-sidecar` `0.4.0`，无 license
- `scripts/whatsapp-bridge/package.json`：`hermes-whatsapp-bridge` `1.0.0`，无 license
- `pm/uv.lock`

这五项不是那四个内部包，同样不能套 MIT。它们是默认提取覆盖缺口。

生成器对锁的实际行为，读码，未执行：

- 依赖行的 `license` 与 `packaged` 被强制写成 `unknown`（`generate.py` 第 192–193 行）。lock 字符串只进 `declared_license`。
- npm 锁键不含 `node_modules/` 的工作区条目被跳过（第 710–711 行）。所以 `apps/shared`、`ui-tui`、`ui-tui/packages/hermes-ink`、`tests-js` 不会成为锁行。链接条目会成为 `relation=first_party`，`install_class`、`integrity`、`license` 均为 `unknown`（第 720–736、760–762 行）。测试 `test_npm_direct_dev_and_link` 第 309–323 行用夹具锁住了这个形状。
- 面板锁与根锁按锁文件父目录匹配 manifest，不共用（`test_npm_locks_do_not_share_manifests` 第 685–741 行）。面板根键 `""` 被跳过，面板身份只来自 manifest。
- 走不到的 Python 锁包是 `unknown`，不叫 transitive（`classify_python` 第 594–599 行）。
- `packaged_membership` 恒为 `unknown`（第 875 行）。生成器不读 `MANIFEST.in`、`resources.py` 或 Nix。

## 归属 / NOTICE 与资源契约

生成器不写 NOTICE，也不把说明文件放进包。根 NOTICE 缺失不是错误（`_read_bytes` 第 128–129 行；缺文件不置 `errors`）。提取器退出 0 不等于归属完整。

同目录判定在 `_apply_notice_license` 第 771–781 行：只有标题恰好是 `MIT License` 且含 copyright 行的 notice，才给同一父目录的 component 标 MIT，`license_basis` 为 `sibling_notice_file`。`_kind` 第 135–138 行把 `LICENSE`、`NOTICE`、`COPYING` 都当 notice。`SCHEMA.md` 第 42 行只写 NOTICE，窄于代码。根文件名是 `LICENSE` 不是 `NOTICE`；若将来运行，根 `pyproject.toml` 与根 `package.json` 会被标 MIT，四包父目录不同，不会被标上。`test_sibling_notice_does_not_cover_other_package` 第 79–97 行锁的是这个不外溢，夹具名仍是 `hermes-panel`。`test_manifest_license_field_alone_is_not_determination` 第 72–77 行：仅有 `license: MIT` 不够。

资源契约实际收录的许可只有仓库根 `LICENSE` 的副本：

- `clawd_server/resources.py` 第 659–660 行把 `checkout/LICENSE` 复制到 `LICENSES/LICENSE`。第 491、674 行要求契约 `license` 等于该相对路径。第 732–733 行缺根 LICENSE 就拒绝暂存。
- 同一暂存纳入面板 `dist/`、三个 sidecar `.py`（第 46–50 行）和 `browser-window-guard` 选定文件。`requirements.txt` 在 `OMITTED_SIDECAR` 第 58–62 行，不进包。
- `MANIFEST.in` 第 1、3、5 行：`include LICENSE`，`recursive-include clawd_server/bundled/LICENSES *`，sidecar 只收 `*.py`。sha256 `8f1aacfa255d2d055fc166e79e8dda02cf1bbb8bcd2acfe57985578d7ca5c48b`。
- `nix/panel-resources.nix` 第 10 行写明该推导不发出 LICENSE，由 Python 暂存复制仓库根 LICENSE。第 8–9 行 `$out` 只含 `dist/`、sidecar、插件；`node_modules` 不进 `$out`。
- `nix/lib.nix` 第 115–129 行把 npm workspace 顶层目录排除出 Python 源过滤。`apps/` 与 `ui-tui` 因此不进 Python 源；面板构建产物另经 `CLAWD_PANEL_RESOURCE_ROOT` 注入。TUI、ink、shared 不在 wheel 资源契约文件表里。面板构建产物在表里，但没有面板自己的 LICENSE。
- `nix/hermes-agent.nix` 第 454 行 `license = licenses.mit` 是整份推导的 Nix 元数据，推导会带上面板资源。这是断言，不是四包授权，本次未 eval。`nix/desktop.nix` 第 216 行同样断言 MIT，描述仍是 Hermes desktop shell；它不是四包证据，也未 eval。
- wheel 另有 `license-files = ["LICENSE"]`，只把根 LICENSE 送进 `*.dist-info/licenses/LICENSE`。

因此：提取器能看见 sidecar pin 和四包 manifest，但 `packaged` 永远是 `unknown`；资源契约则会把面板构建产物和根 MIT 副本放进同一个 wheel，同时不收录四包许可文件。开档案即使看到 `LICENSES/LICENSE` 与根文件字节相同，也只证明根 MIT 副本在包内，不能证明面板、TUI、ink、shared 已被授权。当前没有档案可开。

`tests/clawd_server/test_distribution_package_manifest.py` 的 `compare_distribution_archive` 第 204–209 行只检查 wheel 是否有 `*.dist-info/licenses/LICENSE`、sdist 是否有根 `LICENSE`，并按档案内契约核对哈希。它不把这些字节与仓库根 `LICENSE` 比较。夹具用的是 `b"MIT\n"`（第 230 行附近），不是真实 LICENSE。未设置两个环境变量时，第 345–349 行只证明缺档案抛 `FileNotFoundError`，不是打包通过。本次未跑该测试。

`scripts/clawd_acceptance/execute.py` 第 280 行把提取器退出 0 记成 `pass`。按当前生成器，退出 0 与四包 `unknown`、`packaged_membership=unknown` 可以同时成立。该 pass 不能当作四包许可清除，也不能代替开档案。

## 生成器与测试

| 文件 | sha256 | 字节 | 行 |
| --- | --- | --- | --- |
| `scripts/clawd_inventory/generate.py` | `26d6d3e642ad84e3f9382aead66478444cf4eafa68c0c5059be5731d109f84a1` | 34282 | 916 |
| `scripts/clawd_inventory/test_generate.py` | `b4e7c375ec57e974ae1f8682a38e81ff229fda84f3f7b592455107bc916d8352` | 34072 | 760 |
| `scripts/clawd_inventory/__main__.py` | `430505ab1604252a63e3580d37ed64057ca61bf299d5dfa459c2430dc7c0ae34` | 566 | 22 |
| `scripts/clawd_inventory/SCHEMA.md` | `14219a2f4fe89d1c68ac56aff728f6a800dddd8067c05236c635cc94b93e4e0d` | 3053 | 42 |

`pyproject.toml` 第 768 行 `testpaths = ["tests"]`。库存测试不在默认测试发现里。`__pycache__` 里有 cpython-314 字节码，只说明先前被导入过；不是本次运行，也不是通过。

`test_generate.py` 有 20 个测试，全部用临时夹具，不打开仓库锁或真实档案：

- 第 29 行 `test_default_inputs_are_explicit_files`
- 第 37 行 `test_missing_field_is_not_filled_with_mit`
- 第 72 行 `test_manifest_license_field_alone_is_not_determination`
- 第 79 行 `test_sibling_notice_does_not_cover_other_package`
- 第 102 行 `test_non_mit_heading_is_not_relabeled`
- 第 112 行 `test_directory_is_not_scanned`
- 第 123 行 `test_node_modules_path_is_refused`
- 第 131 行 `test_python_relation_scope_and_integrity`
- 第 260 行 `test_lock_without_manifest_is_unknown_not_transitive`
- 第 269 行 `test_npm_direct_dev_and_link`
- 第 325 行 `test_requirements_hashes_and_unknown_source`
- 第 340 行 `test_parse_error_invents_nothing`
- 第 352 行 `test_names_normalize`
- 第 357 行 `test_source_has_no_network_imports`
- 第 369 行 `test_main_refuses_forbidden_output`
- 第 377 行 `test_pyproject_optional_reaches_lock_transitive`
- 第 418 行 `test_python_lock_optional_dev_git_url_and_missing`
- 第 577 行 `test_npm_optional_dev_git_url_file_and_missing`
- 第 685 行 `test_npm_locks_do_not_share_manifests`
- 第 743 行 `test_offline_input_does_not_scan_checkout`

未覆盖：当前四包真实路径、真实 `uv.lock` / 两份 npm 锁、档案字节与根 LICENSE 的对比、Nix `meta.license` 过宽断言、默认输入清单外的内部 manifest。夹具通过也不能解除 blocked。

入口：`generate.py` 第 887–912 行。`--offline` 与 `--local-only` 相同。未给 `--input` 时只读默认相对路径。退出 0 是解析成功；路径被拒、不可读或解析失败为 2。标准输出或 `--output` 都不应覆盖 `docs/clawd/dependency-source-inventory.json`。生成器输入哈希带 `sha256:` 前缀（第 104–105 行）；本报告哈希是裸十六进制。

## 最终验收命令

以下命令本次都未执行。不要把旧快照、本文计数或提取器退出 0 当成全量许可通过。不要跑 `npm audit`，不要联网，不要用系统 `python3` 代替仓库 `.venv`。

库存测试，不在 `testpaths` 里，必须点名：

```text
cd /root/hermes-workspace/clawd
.venv/bin/python -m unittest scripts/clawd_inventory/test_generate.py
```

离线提取。输出必须是隔离文件，不能是 `docs/clawd/dependency-source-inventory.json`，也不能落在 `node_modules` 或 venv：

```text
cd /root/hermes-workspace/clawd
.venv/bin/python scripts/clawd_inventory/generate.py \
  --checkout /root/hermes-workspace/clawd \
  --output <隔离目录>/license-inventory.json
```

等价标准输出入口是 `.venv/bin/python scripts/clawd_inventory --offline`。退出 0 之后仍须检查 JSON，而不是停在退出码。四包 `license` 与 `manifest_license` 必须仍为 `unknown`；任一依赖行的 `license` 不得离开 `unknown`；`packaged_membership` 必须仍为 `unknown`；`network_audit` 必须为 false；`compliance_conclusion` 必须为 `not_determined`。根 component 若被标 MIT，只接受 `license_basis=sibling_notice_file`，且不得外溢到四包。这条检查通过也只说明生成器没有误填，不说明四包已获授权。

开档案要等真实 wheel 与 sdist 存在。当前两个环境变量都未设置，根目录与 `dist/` 没有档案，所以这条现在会阻塞，不能当成通过：

```text
cd /root/hermes-workspace/clawd
CLAWD_DISTRIBUTION_WHEEL=<真实.whl> CLAWD_DISTRIBUTION_SDIST=<真实.tar.gz> \
  .venv/bin/python -m pytest \
  tests/clawd_server/test_distribution_package_manifest.py::test_supplied_archives_match_contract_and_missing_archives_fail_closed
```

该测试不比较许可字节。档案出现后还要用标准库核对，本次没有档案，所以没有执行：

- 仓库根 `LICENSE` 的 sha256 必须等于 wheel `*.dist-info/licenses/LICENSE`、wheel 内 `clawd_server/bundled/LICENSES/LICENSE`、sdist 根 `LICENSE`。期望值是 `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`。
- 两份档案都不得含 `apps/panel/LICENSE`、`ui-tui/LICENSE`、`ui-tui/packages/hermes-ink/LICENSE`、`apps/shared/LICENSE`，也不得含产品 NOTICE。缺这些文件是未知，不是 MIT。
- 契约要求的面板 `dist`、三个 sidecar `.py`、插件文件必须在；`requirements.txt` 必须不在 bundled sidecar 里。
- 根 LICENSE 字节一致不能解释成面板产物已被 MIT 覆盖。四包授权未到时，开档案结果对许可仍是 blocked，不是 pass。

编排器 `scripts/clawd_acceptance/stages.py` 第 146–154 行与 `execute.py` 第 284–292 行会调用同一提取器。`execute.py` 第 280 行的 pass 必须降级理解：它不是四包许可通过。没有真实档案时，打包阶段保持 blocked。

## 外部依赖与未运行

- 仓库 `.venv/bin/python` 存在且是符号链接。本次未执行版本命令。交接里的 3.14.9 只是历史记录。
- Node 路径存在。许可提取不需要它。不要用它跑 `npm audit` 或 `npm ci` 来补许可。
- 真实 wheel、sdist、`clawd_server/bundled`、两个 `CLAWD_DISTRIBUTION_*` 环境变量：均缺。开档案 blocked。
- 隔离输出目录：验收时自备，不要覆盖快照。
- 四包书面授权：缺失。这是人工/法律输入，提取器和开档案都不能生成。
- Nix eval/build：未做。`licenses.mit` 不是证据。
- 第三方许可：uv 锁无 license 字段；npm 锁字符串未提升为判定。将来提取器跑完后仍应保持 `license=unknown`。不是本次已提取的全量清单。
