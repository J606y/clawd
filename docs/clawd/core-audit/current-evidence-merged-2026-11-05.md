# 当前证据归并 — 2026-11-05

观察时刻 `2026-11-05T22:59:22+08:00`。静态哈希归并，不是运行验证、安全通过、修复或上线许可。未跑 tests、build、import、py_compile、产品 CLI、网络、浏览器或攻击验证。未改源码、旧报告、测试、gate、manifest。

## 程序计数

- 唯一 ID：63。来源：final-findings items 63、mapping 行 63、origin-id 对 64、continuation items 9。交叠 9，未相加。新增 0，遗漏 0。
- ID 指纹：{"changed-branch-sufficient": 7, "match": 47, "continuation-current-match": 9}。
- 文件指纹：{"match": 78, "no-baseline": 1, "blocked": 1, "changed": 4, "partial-match": 3}。
- 沿用静态标签：{"fixed": 1, "active": 46, "false_positive": 13, "unverified": 3}。verified_repair=0，runtime_verified=0，security_pass=0，unknown=0。
- 冲突：状态冲突 0，mapping/item 不一致 0，哈希来源差异 11（均已由当前字节等于续跑哈希解决，未决 0），unresolved ID 0。
- `fixed` 只是 exec-effort 的旧静态标签，不是已修。

## 父后续最小清单

- 不得把任何 ID 写成运行验证、安全通过或已修。verified_repair=0。exec-effort 的 carried 标签 fixed 只是旧静态分类沿用。
- 续跑 9 个 ID 不另计。当前字节与续跑哈希一致的 active 仍只审查、不授权修复: bg-busy, bg-human, bg-lease, bg-pid, bg-receipt, bg-stop, bg-swallow, bg-window。
- bg-plan 保持 false_positive，不升格。
- 快照哈希变化但最小分支只够沿用原标签、不是新漏洞也不是修复: exec-effort, exec-stop-window, appr-auto, inp-auth-bool, inp-empty-key, bg-approval-key, bg-sidecar。
- stale/recheck-required: 无。
- 支持路径 blocked 未读: nat-close。
- 支持文件无旧哈希、未用于改结论: prov-name。
- 唯一 ID 仍为 63。禁止与 continuation 9、mapping 64 对、或旧 active/items 相加。

## 路径

- `/root/hermes-workspace/clawd/docs/clawd/core-audit/current-evidence-merged-2026-11-05.json`
- `/root/hermes-workspace/clawd/docs/clawd/core-audit/current-evidence-merged-2026-11-05.md`

逐 ID 紧凑记录只在 JSON。历史行号不是独立证据；当前定位绑定写入时 sha256。
