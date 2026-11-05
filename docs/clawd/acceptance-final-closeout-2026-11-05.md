# 验收执行链最终收口 — 2026-11-05

未运行。没有测试、构建、`py_compile`、导入 `clawd_server.packaging_backend`、CLI、`accept.py`、网络或开包。没有设置父进程的 `CLAWD_DISTRIBUTION_WHEEL` / `CLAWD_DISTRIBUTION_SDIST`。没有删除文件，没有读生产秘密，没有访问原面板或 `/usr/local/lib/hermes-agent`。没有 commit、push、deploy。

本文件是静态接线记录，不是通过，不是法律意见，不是可分发结论，也不是上线许可。下面没有观察到的 sha256、进程退出码或 `acceptance_green`。

纠正对象是 `docs/clawd/acceptance-execute-package-closeout-2026-11-05.md` 第 73、91、117、122–124 行，以及该文件把提取器规则写成“四包书面授权仍缺”。来源对照是 `docs/clawd/license-scope-independent-2026-11-05.md`。那份独立复核只读，本轮未改。

## 真实产物到指纹

调用链已接上。`report_build.py` 未改。它没有单独的路径参数；`assemble` 第 47 行把 `wheel_dir` 原样交给 `artifact_report`。`execute` 传入的不是隔离目录 `Path`，而是 `TrustedArchiveHandoff`。

| 步 | 位置 | 本轮静态事实 |
| --- | --- | --- |
| 本次标记 | `run_package` 只读本次追加日志里的唯一 `CLAWD_ACCEPTANCE_DISTRIBUTION` | 不列目录。父进程旧 `CLAWD_DISTRIBUTION_*` 不进构建子进程 |
| 可信路径 | 阶段结果 `distribution_env`，且已过 `classify_distribution_handoff` | 缺一、歧义、历史名、符号链接、非 `accept-build-*`、隔离目录外，都不会进入该字典 |
| 传参 | `execute.py` 第 1400–1402 行保存 package 结果；第 1426 行 `wheel_dir=_trusted_archive_handoff(layout, package_result)` | `prepare` 与门禁拒绝的 `run_all` 传 `None` 结果，不列目录 |
| 转发 | `report_build.py` 第 47 行 | 未改。不调用 `is_dir()`，不列目录 |
| 再保护 | `fingerprints._trusted_archives` 再次调用 `classify_distribution_handoff` | 历史前缀、`.partial` / `.stage-` / `.rejected-`、符号链接文件、隔离目录外，不读字节 |
| 额外符号链接 | `fingerprints._archive_row` 拒绝档案自身或父目录是符号链接 | 防止 `accept-build-*` 名字指向旧目录后再哈希 |
| 哈希 | `_sha256` 读文件字节 | `content_hashed` 与 `sha256` 同时为真才算该行。只存在文件不是 complete |
| 回放 | `report_only` 第 1432 行起，用已写入的 `package.result.distribution_env` | 不重新解析 `package.log`，不列目录 |

`fingerprints.py` 没有 `.glob(` 或 `rglob`。把普通 `Path` 传进 `wheel_dir` 时，返回 blocker `no trusted stage archive paths`，不读取隔离目录里的旧 `*.whl`。

`artifacts.complete` 为真还要检出树这两行也有内容哈希：`ui-tui/dist/entry.js`、`apps/panel/dist/index.html`。它们不从 wheel 成员读取。缺它们时，两个档案即使已哈希，`complete` 仍为假，`report_build._green` 仍为假。这是既有合取，不是本批漏接的调用。

`package-wheel-members` 仍只打开标记给出的那一个 wheel 做 zip 成员检查。坏 zip 记 blocked，不回退同级旧包。它不产生报告 sha256。报告哈希不走这条检查。

## 许可分类

三层拆开。分类器不造授权，也不把根 MIT 外溢到四包。

| 层 | 含义 | 本分类器 |
| --- | --- | --- |
| 提取器解析 | JSON 可读，四包 `license` 字段是字符串，`network_audit` 为 false，`compliance_conclusion` 为 `not_determined`，`errors` 为 false | `license-inventory` 可以是 pass。detail 写明这不是法律合规通过。`extractor_parse=pass` |
| 提取器契约 | `network_audit` 不是 false，或 `compliance_conclusion` 离开 `not_determined` | 阶段 fail/1。这是离线契约或禁止写出合规结论，不是法律审查通过，也不是已证明的额外授权义务 |
| 法律合规 | 本函数不做 | `legal_review` 固定 `blocked`。`license-scope-unknown` 与 `license-packaged-unknown` 固定 blocked。阶段状态没有 pass |

未知不是 pass。四个内部包的 unknown 或缺失各有一条 `unknown_scopes`，`status=blocked`。这是提取器同目录规则或字段缺失的结果，不是法律排除，也不是已经成立的额外授权义务。

已核许可字段离开 unknown 不直接 fail，也不当授权。`recorded_license_fields` 每条仍是 blocked。依赖行的 license 字符串只记 metadata，不是裁定。`packaged_membership` 离开 unknown 只记字段，不是入包证明，不因此 fail。

根 component 可以是 `license=MIT` 且 `license_basis=sibling_notice_file`。这只覆盖那个 component。它不外溢到 `ui-tui/package.json`、`apps/shared/package.json`、`ui-tui/packages/hermes-ink/package.json`、`apps/panel/package.json`。

对照独立来源核证，不把同目录提取规则当法律边界：

- 根 `LICENSE` 文本没有目录排除。无排除也不等于已经裁定每个子路径都被 MIT 覆盖。
- tui `hermes-tui`、shared `@hermes/shared`：在带根 LICENSE 的同一已发布提交树 `c314285f077e91937c995d419b9fa40af9eb4981` 里，是普通跟踪文件，不是子模块。同目录无 LICENSE 只说明 metadata 和提取器输入不足。候选覆盖未被裁定为 MIT。未确认前不要写成 MIT。
- ink `@hermes/ink`：树内 fork，树里没有上游 `license` 副本。不能用根 MIT 代替 Vadym Demedes / Sindre Sorhus 的声明。缺的是通知保留和逐文件来源针，不是已经证明的“必须新书面授权”。
- panel `@clawd/panel`：不在父提交树。父 MIT 不附着是仓库边界，不是因为提取器不外溢。嵌套 `hk` 已检查历史没有找到许可授予。未知不等于 MIT，也不等于已经证明必须新做书面授权这一法律要件。没有找到授予时，不能把分发预设为已获许可。技能 frontmatter 的 `license: MIT` 不能充当授予。

书面授权是关闭未知的一条充分路径，不是本文件证明了的唯一法律义务。本分类器不把四包标成 MIT，也不把“另取书面授权”写成已经成立的结论。

`acceptance_green` 仍要求全部必需检查为 pass。`license-scope-unknown` 保持 required 且 blocked，所以解析行 pass 不能让总账变 0。

## 旧断言

`tests/clawd_acceptance_cases/test_package_stage_execution.py` 不再要求 execute 含 `from setuptools.build_meta import build_wheel`。替换后的契约没有放宽：该导入字符串必须不在；拒绝字符串必须在；父进程不得调用 `run_package_build`；不得内嵌两个环境变量字面量；必须读 `stage["argv"]`；`compare_distribution_manifest` 必须先丢掉父进程的两个键，再写入本次 `handoff["env"]`，并用固定 venv pytest，不走 `run_tests.sh`。后端签名、资源暂存名、`setup.py` 的 `HERMES_NIX_BUILD`、钩子调用形参，原断言仍在。

`stages.py` 包阶段 note 与 `planned_checks` 第 643–646 行已改成：execute 读 catalog argv；路径只在本次标记分类之后交给独立 pytest 和指纹；构建子进程不接收父进程旧路径。这些行在运行前仍是 not executed，不是 pass。

## 待运行命令

工作目录 `/root/hermes-workspace/clawd`。本次没有执行，没有结果。

```sh
scripts/run_tests.sh \
  tests/clawd_acceptance_cases/test_package_stage_execution.py \
  tests/clawd_acceptance_cases/test_execute_package_distribution.py \
  tests/clawd_acceptance_cases/test_artifact_handoff_fingerprint.py
```

这三份文件不构成 `--run --implementation-complete` 的授权，也不构成验收通过。核心、TUI、面板、浏览器、模型、续跑、回滚、Nix 过滤、开档案 pytest、真实 wheel/sdist 构建，本轮都没有新的 pass。

## 静态接线

没有未接上的指纹调用点。上面的 `distribution_env` → `_trusted_archive_handoff` → `assemble(wheel_dir=...)` → `artifact_report` 是完整链。

不是漏接、但会继续挡住全绿的具体条件：

1. `fingerprints.artifact_report` 第 56–60 行仍哈希检出树 `ui-tui/dist/entry.js` 与 `apps/panel/dist/index.html`。缺文件时 `artifacts.complete` 为假。不从本次 wheel 内读取这两份文件。
2. `report_only` 只消费状态文件里的 `distribution_env`。当前 `execute_stage` 会写入该键，`_refresh_package_stage` 保留它。没有该键的旧状态不会从 `package.log` 补路径，也不会列目录。
3. `report_build.assemble` 仍无独立的 `wheel_path` 参数。当前第 47 行原样转发 `wheel_dir`，所以 `TrustedArchiveHandoff` 能到达指纹函数。若以后在转发前对 `wheel_dir` 调用目录方法，这条链会断。当前没有这个调用。

## 修改文件

- `/root/hermes-workspace/clawd/scripts/clawd_acceptance/fingerprints.py`
- `/root/hermes-workspace/clawd/scripts/clawd_acceptance/execute.py`
- `/root/hermes-workspace/clawd/scripts/clawd_acceptance/stages.py`
- `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_package_stage_execution.py`
- `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_execute_package_distribution.py`
- `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_artifact_handoff_fingerprint.py`（新；未运行）
- `/root/hermes-workspace/clawd/docs/clawd/acceptance-final-closeout-2026-11-05.md`（本文件）

未改：`report_build.py`、`isolation.py`、`scripts/run_tests.sh`、`gate.py`、`clawd_server/packaging_backend.py`、`clawd_server/resources.py`、`setup.py`、`scripts/clawd_inventory/generate.py`、原先两份证据报告。
