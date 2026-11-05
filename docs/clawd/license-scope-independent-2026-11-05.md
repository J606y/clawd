# clawd 许可作用域独立复核 — 2026-11-05

本文件是只读来源复核，不是法律意见，不是侵权或可分发结论，不是合规通过，也不是验收通过。没有运行提取器、测试、构建、安装、Nix eval、`npm audit` 或产品 CLI。没有改 LICENSE、metadata、锁、生成器或测试。没有读 git 凭据、`.git/config`、`/root/hermes-workspace/hermes-panel` 工作树、`/var/www/hermes-panel` 或 `/usr/local/lib/hermes-agent`。

复核时刻：2026-11-05 06:08:56 HKT。对照对象是 `docs/clawd/resumption-licenses-detail-2026-11-05.md` 第 13 行和第 46–52 行：仅凭四包同目录没有 LICENSE、以及提取器 sibling 规则，就写成根 MIT 不适用、必须另取版权持有人书面授权。该写法不是已核实法律结论。本次不预设可分发，也不预设必然侵权。

## 纠正

三件事被叠成了一件事，必须拆开：

| 层 | 实际含义 | 不能推出 |
| --- | --- | --- |
| metadata 未知 | 四份 `package.json` 都没有 `license` / `author` / `repository` | 不能推出根许可文本排除了这些目录 |
| 提取器同目录判定 | `generate.py` 只在同一父目录有合格 notice 时标 MIT | 不能推出法律上根 MIT 不适用，更不能推出必须新写授权书 |
| 仓库许可覆盖 | 要看许可文本有没有目录排除、文件是否属于该提交树、中间有没有更近的许可、是不是独立仓库或上游 fork | 没有这些证据时，既不能套 MIT，也不能把“未知”写成“必须额外书面授权” |

前一份记录对 **panel** 的仓库边界判断，结论方向可以保留，但理由不对：父 MIT 不附着，是因为 panel 不在父提交树里，不是因为提取器不外溢。对 **tui / shared / ink**，同目录无 LICENSE 只说明 metadata 和提取器输入不足，不能单独证明根 MIT 不适用。

无证据的部分保持未知。本文件不把四包标成 MIT，也不把“另取书面授权”写成已经成立的法律要件。书面授权是关闭未知的一条充分路径，不是本次证明了的唯一法律义务。

## 根许可文本：没有目录排除

工作树与父仓库 `HEAD` 的 `LICENSE` 字节相同。

- 路径：`/root/hermes-workspace/clawd/LICENSE`
- 固定修订：`c314285f077e91937c995d419b9fa40af9eb4981`
- git blob：`75410e73319c72cd3e991a501c5455eb78f38375`
- sha256：`821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`
- 1070 字节，21 行，非符号链接
- 公开原文：<https://raw.githubusercontent.com/NousResearch/hermes-agent/c314285f077e91937c995d419b9fa40af9eb4981/LICENSE>，sha256 与本地相同
- 页面：<https://github.com/NousResearch/hermes-agent/blob/c314285f077e91937c995d419b9fa40af9eb4981/LICENSE>
- GitHub license API（`ref` 即上述 SHA，不是浮动的 `main`）：`license.spdx_id = MIT`，`path = LICENSE`，blob 与本地相同。这是仓库级检测，不是逐路径裁定。响应不是法律意见。

正文标题是 `MIT License`，版权行是 `Copyright (c) 2025 Nous Research`。授权对象是 “this software and associated documentation files (the "Software")”。全文没有 except、excluding、subdirectory、directory 排除，也没有点名或排除 `ui-tui`、`apps/shared`、`apps/panel`。因此“根 MIT 文本自己把子目录划出去”不成立。

文本没有写明 “the Software” 的目录清单。所以“全文无排除”也不等于已经裁定每个子路径都被 MIT 覆盖。覆盖问题要看文件是否属于带这份 LICENSE 的同一提交，以及有没有更近的许可或上游许可。

同一修订的已提交 `README.md`（`git show HEAD:README.md`，不是脏工作树）第 12 行徽章链到 `blob/main/LICENSE`，第 239–241 行写 `## License` / `MIT — see LICENSE`。这是该修订的仓库自述，仍然不是逐文件裁定。工作树 `README.md` 是脏的；第 16 行和第 134–136 行仍有同一句 MIT 自述，行号不能当已提交证据。`ui-tui/README.md` 没有 License 节。

打包 metadata 只点名根文件，这也不是许可文本里的目录排除：

- 已提交 `pyproject.toml` 第 6、16–18 行：`name = "hermes-agent"`，`authors` 为 Nous Research，`license = "MIT"`，`license-files = ["LICENSE"]`
- 工作树只把发行名改成 `clawd`，上述 license 两行仍在第 17–18 行。改名不是版权转移，也不是新授权。
- 已提交根 `package.json` 有 `license: MIT`，仓库 URL 为 `git+https://github.com/NousResearch/Hermes-Agent.git`
- 工作树根 `package.json` 名是 `clawd`，`license` 仍是 `MIT`

父仓库没有 `NOTICE`、`REUSE.toml`、`.reuse/dep5`、`debian/copyright`、`COPYING`。这些缺失是机器可读范围声明缺失，不是排除条款。

## 固定修订与仓库边界

父检出 `/root/hermes-workspace/clawd`：

- `HEAD` = `origin/main` = `c314285f077e91937c995d419b9fa40af9eb4981`
- 提交时间：2026-11-03 00:28:04 -0800；公开 committer date：2026-11-03T08:28:04Z
- 题目：`fix(pm): judge a build's hermes-agent markers on its own interpreter`
- 父提交：`6eea8ca0c37a3d1d986a729c6837fae4786bb169`
- 页面：<https://github.com/NousResearch/hermes-agent/commit/c314285f077e91937c995d419b9fa40af9eb4981>
- 签名：unsigned。未把签名状态当成许可证据。
- 远程 URL 无凭据：`https://github.com/NousResearch/hermes-agent.git`
- 本地仓库是浅克隆：`--is-shallow-repository` 为 true，`rev-list --count HEAD` 为 1，graft 在该 SHA。本地 `git log` 不能证明引入时点。引入史全部来自下面钉死在该 SHA 的公开 Commits API，不来自本地历史，也没有 `git fetch`。
- 没有 `.gitmodules`。`ui-tui` 与 `apps/shared` 的索引模式全是 `100644`，没有 gitlink。

公开 API 使用 `sha=c314285f077e91937c995d419b9fa40af9eb4981`，因此列出的提交是该修订的祖先，不是后来的 `main`。本次没有查询浮动 `main`。

根 `LICENSE` 在该祖先链上只有一次触及：

- `9ba5d399e58fa353d7132b0e2e9533d281ff64d1`，2026-03-07T21:43:08Z
- 题目：`fix: restore missing MIT license file`
- 页面：<https://github.com/NousResearch/hermes-agent/commit/9ba5d399e58fa353d7132b0e2e9533d281ff64d1>
- 该提交只增加 `LICENSE`，blob 已是 `75410e73319c72cd3e991a501c5455eb78f38375`
- 同 SHA 原文 sha256 仍是 `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`
- “restore” 是提交说明，不是本次看到的更早 blob。祖先链上没有第二次修改。不能据此发明一份更早的、内容不同的 LICENSE。

下列路径在同一祖先链上的 Commits API 返回空列表（HTTP 200，`n=0`）：`NOTICE`、`ui-tui/LICENSE`、`ui-tui/LICENSE.md`、`ui-tui/packages/hermes-ink/LICENSE`、`ui-tui/packages/hermes-ink/license`、`apps/shared/LICENSE`、`apps/shared/NOTICE`。这是“该钉死修订的可达历史上没有这些路径”，不是全 GitHub 搜索。

`apps/panel` 在该修订的 Contents API 返回 404。父索引不包含它。`git status --untracked-files=normal -- apps/panel` 是 `?? apps/panel/`。它不是子模块。

## 提取器规则只解释 unknown

`scripts/clawd_inventory/generate.py` sha256 `26d6d3e642ad84e3f9382aead66478444cf4eafa68c0c5059be5731d109f84a1`，34282 字节。本次只读，未运行。

- 第 135–138 行把同名 `LICENSE`、`NOTICE`、`COPYING` 都当成 notice。
- 第 771–781 行 `_apply_notice_license`：只有 notice 的标题恰好是 `MIT License` 且含 copyright 行，才把**同一父目录**的 component 标为 MIT，`license_basis` 为 `sibling_notice_file`。其余 component 先写成 unknown。

根 `LICENSE` 的父目录是仓库根。它能成为根 `package.json` / `pyproject.toml` 的 sibling，不能成为 `ui-tui/`、`ui-tui/packages/hermes-ink/`、`apps/shared/`、`apps/panel/` 的 sibling。所以四包在提取器里保持 unknown，是这条规则的直接结果。它不读取祖先 LICENSE，不读取文件头，不识别 gitlink，也不判断 fork。退出 0 或 unknown 都不是许可裁定。

## 四包

已提交源码头检索范围只限这些树，不是全盘扫描。对 `HEAD` 的 `ui-tui` 与 `apps/shared`，`git grep` 未命中 `SPDX-License-Identifier`、`Copyright`、`MIT License`、`All rights reserved`、`licensed under`。没有把“无命中”写成“无版权”。

### tui — `hermes-tui`

| 项 | 证据 |
| --- | --- |
| 当前名 / 版本 | 工作树与 `HEAD` 都是 `hermes-tui` `0.0.1`，`private: true`。无 `license`、`author`。工作树多了 `description: clawd terminal UI`，没有别的键变化 |
| 目录 | `/root/hermes-workspace/clawd/ui-tui`。已提交 637 个路径，其中 ink 165 个，其余 472 个 |
| manifest | `HEAD` sha256 `0edb685453cf0d40d4b5ec8a572500bf5c708dc3b94c376f2757297e4a3865a9`，1475 字节。工作树 `30458fd81748d3d844fa97906efa5c2baefa1f84dd64992e5c06a17d19c48f3e`，1513 字节 |
| 来源 | 父仓库普通跟踪文件，不是子模块。引入 manifest 的提交是 `2ea5345a7b8a2cd1f47e784e9e977094cb2b8579`，2026-04-03T00:07:53Z，题目 `feat: new tui based on ink`。页面：<https://github.com/NousResearch/hermes-agent/commit/2ea5345a7b8a2cd1f47e784e9e977094cb2b8579>。该提交文件列表 13 个，低于 GitHub 单次 300 上限，其中没有 LICENSE。它晚于根 LICENSE 的 `9ba5d399` |
| 同目录许可 | 钉死修订的目录列表没有 LICENSE / NOTICE / COPYING。祖先链上 `ui-tui/LICENSE` 与 `ui-tui/LICENSE.md` 为空 |
| 作用域 | metadata 未知。提取器会给 unknown。这不证明根 MIT 排除本目录。本目录在带根 LICENSE 的同一已发布提交树里，且进入时间晚于该 LICENSE blob。这是候选仓库覆盖的来源事实，不是 MIT 裁定 |
| 仍未知 | 没有文件级 SPDX。没有权利人写明 “the Software” 是否包含本目录的单独句子。工作树另有未提交差异（`git diff --stat` 可见，含 `package.json` 的描述行和若干源文件）；这些差异没有提交作者，不在 `c314285f` 里，不能继承或否定该修订的候选覆盖 |
| 解除路径 | 不要用 sibling 规则宣布排除。若要做成可复核记录：在非浅历史或上述已钉死的公开提交上，记录“同一 commit 树 + LICENSE blob/sha256 + 无更近许可文件 + 无冲突 SPDX”。若需要权利人确认，确认书应点名 `hermes-tui`、修订 `c314285f`，并说明根 LICENSE 覆盖还是排除；不能把发行名改成 clawd 当成确认。未确认前不要在 Nix、wheel 或 NOTICE 里把本包写成 MIT |

### shared — `@hermes/shared`

| 项 | 证据 |
| --- | --- |
| 当前名 / 版本 | `HEAD` 与工作树相同：`@hermes/shared`，`private`，`0.0.0`。无 `license`、`author`、`description` |
| 目录 | `/root/hermes-workspace/clawd/apps/shared`。已提交 56 个路径，模式全是 `100644` |
| manifest | sha256 `b9ae25a2a7f088a1f2d4083f19bd10c4432c1e451022d885c59b0f1996cee85e`，1211 字节。工作树与 `HEAD` 相同 |
| 来源 | 父仓库普通跟踪文件。`apps/shared/package.json` 最早触及是 `51c68d4ab1a9e3c62fb1048fccb84144c409f0e7`，2026-05-31T22:46:56Z，题目 `Add Hermes desktop app (#20059)`。页面：<https://github.com/NousResearch/hermes-agent/commit/51c68d4ab1a9e3c62fb1048fccb84144c409f0e7>。晚于 `9ba5d399`。该提交的 files 数组正好 300，是 API 上限，不能用这份文件列表证明当时没有 LICENSE。证明来自祖先链路径查询：`apps/shared/LICENSE` 与 `apps/shared/NOTICE` 为空 |
| 同目录许可 | 钉死修订的目录内容只有 `eslint.config.mjs`、`package.json`、`src`、`tsconfig.json` |
| 作用域 | 与 tui 相同：metadata 未知；提取器 unknown 不是排除；同一提交树里的候选覆盖未被本复核裁定为 MIT |
| 仍未知 | 没有单独证明 `apps/shared/` 目录在 `package.json` 出现之前是否以别的路径存在过。没有文件级 SPDX。工作树有一处未提交差异：`apps/shared/src/json-rpc-channel.ts`。该差异不在钉死修订里 |
| 解除路径 | 与 tui 相同，点名 `@hermes/shared` 和 `c314285f`。不要因为同目录没有 LICENSE 就要求一份新的书面授权作为已成立结论。也不要补 `license: MIT` |

### ink — `@hermes/ink`

| 项 | 证据 |
| --- | --- |
| 当前名 / 版本 | `HEAD` 与工作树相同：`@hermes/ink` `0.0.1`，`private: true`。无 `license`、`author`、`description`、`repository` |
| 目录 | `/root/hermes-workspace/clawd/ui-tui/packages/hermes-ink`。已提交 165 个路径。`git diff --name-only -- ui-tui/packages/hermes-ink` 为空 |
| manifest | sha256 `9fbbbe9f3447de6a1b509e27be985b3edc14bac42c37b5f88aef5c7bd639c55b`，1492 字节 |
| 是否 vendor / fork | 是在树内的 fork，不是未修改的 registry 包，也不是 git submodule。`ui-tui/README.md` 第 356 行：`packages/hermes-ink/   forked Ink renderer (local dep)`。源码保留上游作者标记：`src/ink/components/App.tsx` 第 314、318 行指向 `https://github.com/vadimdemedes/ink/#israwmodesupported`；`src/ink/reconciler.ts` 第 28 行指向 `https://github.com/vadimdemedes/ink/issues/384`；`src/ink/events/input-event.ts` 第 66、104 行有 `TODO(vadimdemedes)`。文件头没有版权行，也没有上游 `license` 副本 |
| 引入 | `8760faf991ec13231ab790bdf3d5ab1d86850770`，2026-04-11T16:29:08Z，题目 `feat: fork ink and make it work nicely`。页面：<https://github.com/NousResearch/hermes-agent/commit/8760faf991ec13231ab790bdf3d5ab1d86850770>。files 数组 139，低于 300 上限，其中 ink 路径 118 个，没有 LICENSE / NOTICE。晚于根 LICENSE。父提交 `cb79018977850289c23d8f81aca9ed2b57d67022` 的 `ui-tui/package.json` 仍依赖 `"ink": "^6.8.0"`；fork 提交把 `@hermes/ink` 加为 `file:./packages/hermes-ink`，补丁里仍留着 `"ink": "^6.8.0"` |
| 最后一个记录到的上游针 | fork 父提交的 `ui-tui/package-lock.json` sha256 `0187de8245959709d4a1d154e70d52add1b31983eb0faccb931a1f6a5394bfaa`，225787 字节。`node_modules/ink` 为 6.8.0，`resolved` = `https://registry.npmjs.org/ink/-/ink-6.8.0.tgz`，integrity = `sha512-sbl1RdLOgkO9isK42WCZlJCFN9hb++sX9dsklOvfd1YQ3bQ2AiFu12Q6tFlr0HvEUvzraJntQCCpfEoUe9DSzA==`，锁内 `license` 字符串是 `MIT`。该字符串只是锁 metadata |
| 上游固定修订 | registry `https://registry.npmjs.org/ink/6.8.0` 的 integrity 与锁一致。`license` 字段为 `MIT`。`gitHead` = `be1b1bb6ec65056e2ed60ef3c5ae642704b82d31`。标签 `v6.8.0` 指向同一提交，2026-02-19T07:01:03Z，题目 `6.8.0`。页面：<https://github.com/vadimdemedes/ink/commit/be1b1bb6ec65056e2ed60ef3c5ae642704b82d31>。许可文件名是小写 `license`，不是 `LICENSE`；`LICENSE` 大写路径在该标签上 404。原文：<https://raw.githubusercontent.com/vadimdemedes/ink/be1b1bb6ec65056e2ed60ef3c5ae642704b82d31/license>，sha256 `de84e62e27d9a970b50446d0af1f713c1439a4180faa098adfa5da94f5158a55`，1202 字节。版权行是 Vadym Demedes 与 Sindre Sorhus，正文是 MIT，要求副本保留上述版权声明和许可声明。本次没有下载 `.tgz`，没有执行包 |
| 不是原样副本 | 钉死修订的 `src/ink/reconciler.ts` 10051 字节，sha256 `98232d5a02519ebbc344976bbbbbc6e218b70082534fb110e6384a7e0610623d`。上游 `v6.8.0` 的 `src/reconciler.ts` 8908 字节，sha256 `ebf021135d519d377e53cddb14d40d34ca580f3b0d893b426a082271f0e9bb8e`。`dom.ts`：本地 15521 字节，sha256 `3124ec439e48436582e5eb208834497ac7cba482ec2199efde1360e47800e600`；上游 5995 字节，sha256 `aa429605304781940c65caca5406dad979748fcd1a2adb4c810f439fb65fd3b0`。fork 引入时的 `reconciler.ts` 已是 13769 字节，git blob `7d50aedabecf6c5ccafbc6af7a07a6fabec20188`，当时就已经不等于上游该文件的 8908 字节。引入修订的 raw 文件对比遇到 HTTP 429，没有补做逐字节对比，不能声称当时整树等于或不等于 tarball |
| 作用域 | 三层都未裁定。第一，它在父仓库同一提交树里，根 MIT 文本没有排除它，所以不能用 sibling 规则宣布根 MIT 不适用。第二，它自述为 fork，且树里没有上游 `license` 副本；不能把根 MIT 套上去代替 Vadym Demedes / Sindre Sorhus 的声明。第三，锁和 registry 的 `MIT` 字符串、以及上游 `license` 文件，都不能直接贴到 `c314285f` 的这份已修改树。缺失的是通知保留和逐文件来源针，不是已经证明的“必须新书面授权” |
| 仍未知 | 每个文件对应的上游 blob 未逐一比对。没有证明 fork 基线就是 ink 6.8.0 tarball，只证明 fork 前的锁针到了该 tarball，且引入时至少一个文件已经不同。本地修改的版权人未在文件头写明。工作树 ink manifest 和 ink 树相对 `HEAD` 无差异；tui 其他未提交差异不在本目录 |
| 解除路径 | 先做内容鉴别，不要先写新 LICENSE。以 `be1b1bb6ec65056e2ed60ef3c5ae642704b82d31` 和 integrity `sha512-sbl1RdLOgkO9isK42WCZlJCFN9hb++sX9dsklOvfd1YQ3bQ2AiFu12Q6tFlr0HvEUvzraJntQCCpfEoUe9DSzA==` 为起点，逐文件对比上游标签与 fork 引入 blob；对不上的文件继续找别的上游修订，而不是假定 6.8.0。鉴别完成后，把**对得上的那个修订**的 `license` 全文放进本目录，并单独列出本地修改。对不上、又没有文件头的部分保持未知。权利人若要覆盖本地修改，声明必须点名 `@hermes/ink`、父修订 `c314285f`、上游修订，以及修改是否仍受上游 MIT 通知条款约束。在鉴别完成前，不要写 `license: MIT`，不要把根 LICENSE 复制进本目录冒充上游许可 |

### panel — `@clawd/panel`（已提交名 `hermes-panel`）

| 项 | 证据 |
| --- | --- |
| 当前名 / 版本 | 工作树 `package.json`：`@clawd/panel` `0.1.0`，`private: true`。无 `license`、`author`、`repository`、`description`。sha256 `4a78ab03d54f7b96e8fd060fcb45875b8646d77ae16671144a35d7ce4c761d15`，1884 字节。已提交名是 `hermes-panel`，其余键相同；唯一变化的键是 `name`。已提交 sha256 `8d1f583311497518f87ebad2849efe68870bd8402e44f93ec1b3eba83022e21a`，同样 1884 字节 |
| 目录 | `/root/hermes-workspace/clawd/apps/panel`。父仓库不跟踪。嵌套仓库 `HEAD` = `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29`，分支 `hk`，跟踪 `origin/hk`。提交时间 2026-11-03 22:15:38 +0800，题目 `[hk] 螃蟹当作滑块拖动`。非浅克隆，本分支 328 个提交，375 个已跟踪路径 |
| 来源 | 独立 git 仓库，不是父仓库子模块，也不是父修订 `c314285f` 的树对象。父 Contents API：`https://api.github.com/repos/NousResearch/hermes-agent/contents/apps/panel?ref=c314285f077e91937c995d419b9fa40af9eb4981` 返回 404。嵌套远程 URL 是本地路径 `/root/hermes-workspace/hermes-panel`；按范围没有读那个工作树，也没有读其 git 凭据 |
| 自身许可史 | 根提交 `aca020d3050d6e3ccd10fca2ea8cb02945931bf8`，2026-10-06 18:28:37 +0800，作者元数据 `hermes-panel <panel@localhost>`，题目 `Segment 2: scaffold, login, shell, session list, deploy pipeline`。根树没有 LICENSE。`git log --all` 对 `LICENSE` / `LICENCE` / `COPYING` / `NOTICE` / `COPYRIGHT` 无增加记录。全部新增路径里，名字命中只剩 `tests/transcript-notice.test.mjs`，不是许可文件。`git log -S '"license"' -- package.json` 为空：已检查历史上 `package.json` 没有出现过 JSON 键 `"license"`。已提交全文也没有该键。源码检索无 `SPDX-License-Identifier`、`Copyright (c)`、`MIT License` |
| 不能当成包许可的一行 | `docs/hermes-skill/hermes-panel-engineering/SKILL.md` 第 6 行 frontmatter 是 `license: MIT`。同一文件第 1–8 行把这行放在技能自身的 name/description/version/author 下，author 是 `Hermes Shadow Architect`。描述提到 GitHub `J606y/hermes-panel`。这是一个 Markdown 技能文件的 YAML，不是仓库 `LICENSE`，不是 `package.json` 的 license 字段，不能外溢到 375 个路径 |
| 公开仓库 | 未认证的 `https://api.github.com/repos/J606y/hermes-panel`，以及该修订的 commit、license、`contents/LICENSE`，均为 404。404 对私有仓库和仓库不存在是同一响应。本次不用凭据区分，也不把 404 写成“公开许可不存在” |
| 作用域 | 父根 MIT 不因目录嵌套而附着。这是仓库边界，不是提取器 sibling 规则。嵌套仓库已检查的 `hk` 历史上没有找到许可授予。状态是未知，不是 MIT，也不是已证明的侵权。未知也不等于已经证明“必须新做一份书面授权”这一法律要件；没有找到授予时，不能把分发预设为已获许可 |
| 仍未知 | `origin` 指向的本地检出未读，可能有本嵌套克隆没有的分支。公开仓库是否私有未分辨。未提交差异除 `name` 改为 `@clawd/panel` 外还有其他脏文件；那些差异没有单独的许可字段。版权持有人不能从 `panel@localhost` 或技能 author 行认定 |
| 解除路径 | 父 `LICENSE` 不能复制进本目录来关闭缺口。关闭方式只有两类，本次都不实施：一是在未读的远程分支或权利人提供的固定修订上找到真实许可文件，并核对它覆盖 `c28f2f8` 与当前工作树；二是权利人书面授予，点名仓库、包名 `hermes-panel` 与 `@clawd/panel`、修订 `c28f2f8`，并附上许可正文。技能 frontmatter 的 `license: MIT` 不能充当第二类。未关闭前不要写 Nix `licenses.mit` 或包 license 字段 |

## 若提取器要识别祖先许可：来源条件

只列条件，不改 `generate.py`，不改测试。现有 sibling 规则应继续把四包标成 unknown，直到这些条件被实现并复测。实现前，unknown 不得被解释成排除，也不得被解释成 MIT。

祖先 `LICENSE` 最多记成 `candidate_repo_grant`，basis 必须带提交 SHA、blob、sha256。输出仍应保持 `compliance_conclusion = not_determined`。全部条件同时成立才可记录候选，缺一条就保持 unknown：

1. 同一提交树。文件 blob 与 LICENSE blob 都在同一个钉死的 commit 里（`git ls-tree` / `git rev-parse <rev>:<path>`）。工作目录碰巧挨着不算。
2. 中间没有 gitlink，目录不是未跟踪的嵌套仓库。`apps/panel` 因此永远不能吃父 LICENSE。
3. 对 LICENSE 字节做 sha256，并解析排除句。出现覆盖该路径的 except / excluding / 目录清单时，不得套用。本次这份 MIT 文本没有这种句子，但检查本身不能省。
4. 从文件目录向上、直到但不包括该 LICENSE 的每一层，在同一 commit 里都没有更近的 `LICENSE` / `LICENCE` / `COPYING` / `NOTICE` 及其 `.md` / `.txt`。更近文件优先，且不得改写成 MIT，除非该文件自身满足现有标题规则。
5. 文件头若有 `SPDX-License-Identifier` 或明确的另一份许可名称，祖先不得覆盖。只有版权行、没有许可名称时，不构成许可，也不足以拒绝祖先候选；它只是未知的版权通知。
6. 路径被 README、提交说明或依赖针标成 fork / vendor 时，祖先候选不能取代上游。必须另有固定上游修订：标签或 `gitHead`，加上 lock integrity。上游许可字节单独哈希。树里没有上游许可副本时，记通知缺口，不要把它改写成“需要新书面授权”，也不要改写成祖先 SPDX。
7. 浅克隆或 graft 不能提供引入史。引入时点只接受非浅本地历史，或与产品修订相同的公开 URL。提取器不得为了补历史而 `git fetch`，也不得联网。
8. 未提交 diff 不属于该 commit 的候选覆盖，单独标 unknown。
9. `package.json` 的 `license` 字段、锁里的 license 字符串、Nix `meta.license` 继续只做 metadata。现有测试已拒绝把字段当成裁定；祖先识别不得打开这个口子。
10. 候选记录不得写进 NOTICE、wheel 标签或 Nix。那些输出仍要等权利人陈述或上游许可副本落地。

## 未运行、未读、未做

- 未运行 `scripts/clawd_inventory/generate.py`、unittest、pytest、构建、安装、Nix、`npm audit`、产品导入或 CLI 探针。
- 未改任何许可、metadata、锁、生成器、测试。本文件是本次唯一写入。
- 未读 git config、凭据、生产目录、官方安装目录、panel 远程工作树。
- 未下载 ink 6.8.0 的 tarball，未执行任何上游包。fork 引入修订的两个 raw 文件请求返回 HTTP 429，所以没有做引入时的逐字节对比。
- 未全盘扫描。源码头检索只覆盖上述四包的已提交树和 panel 嵌套历史。
- 未把 `docs/clawd/dependency-source-inventory.json` 或 `distribution-notices.md` 当当前证据。
- 开档案、提取器退出码、Nix `licenses.mit` 都不在本次范围内，也不能用本文件倒推为通过。

## 不构成的结论

- 不构成法律意见。
- 不构成“四包已被根 MIT 覆盖”。
- 不构成“根 MIT 已被证明不适用于 tui、shared、ink”。
- 不构成“必须另取书面授权”已经成立。书面授权只是关闭未知的充分路径之一。
- 不构成可分发，也不构成侵权。
- 不构成验收通过。上面的最终验收命令本次都没有执行。
