# 契约夹具收口 — 2026-11-05

静态同步，不是运行结果，不是通过，不是上线许可。

报告绝对路径：`/root/hermes-workspace/clawd/docs/clawd/contract-fixtures-closeout-2026-11-05.md`

未运行 pytest、`scripts/run_tests.sh`、`node --test`、构建、`py_compile`、产品 import、tsc、产品 CLI、浏览器。未删文件，未读生产，未改现网，未触 `/root/hermes-workspace/hermes-panel` 或 `/usr/local/lib/hermes-agent`。未 commit、push、deploy。编辑工具跳过的 lint 不是验收。

允许路径均存在，没有改到不存在的文件：

- `/root/hermes-workspace/clawd/tests/clawd_server/test_distribution_package_manifest.py`
- `/root/hermes-workspace/clawd/apps/panel/tests/transcript-continue.test.mjs`
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/chat/live-tail.tsx`（全库只有这一份；没有 `src/pages` 副本）
- `/root/hermes-workspace/clawd/tests/test_tests_tree_layout.py`
- 新增 `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_current_contract_registration.py`

`runs.ts`、`MANIFEST.in`、`pyproject.toml`、vendor 测试、核心续跑、聊天页、打包脚本都只读。`LiveRun` 仍没有 `display_kind` 字段；实时尾栏用可选交叉类型读取，没有改运行状态类型。

## 旧断言 → 新契约

### 1. 资源清单

旧：`MANIFEST_LINES` 14 行，等于改前的 `MANIFEST.in`。没有 browser vendor 的 include、exclude、prune。整份相等，所以 vendor 规则一进清单，`test_declarations_keep_manifest_as_depth_authority` 就会红。

新：同一断言仍是整份相等，不是子集，也不是“包含即可”。`MANIFEST_LINES` 换成当前 23 行，与 `MANIFEST.in` 以及 `tests/clawd_server/test_browser_vendor_distribution.py` 的 23 行一致。新增的是精确规则，不是把面板深度收成 `**`：

- `recursive-include tools/vendor/browser_harness_0_1_13 *` 仍在 `global-exclude *.pyc` 之前。
- 排除 `PKG-INFO`、`README.md`、`pyproject.toml`、`setup.cfg`、`src/mcp_server.py`。
- `recursive-exclude` egg-info 与 `__pycache__`。
- `prune tools/vendor/browser_harness_0_1_13/src/browser_harness.egg-info`。

没有弱化大小、哈希、禁入比较。`compare_distribution_archive` 仍在 `len(data) != size or hashlib.sha256(data).hexdigest() != digest` 时记 `hash/size mismatch`，仍调用 `forbidden_reason`，缺档案仍 `FileNotFoundError`。`PACKAGE_DATA` 的面板三层 `dist/*/*/*` 没改，也没有把 vendor 收进 `clawd_server` package-data。`license-files` 仍只是仓库根 `LICENSE`。

这不是开包验收。没有新的 clawd wheel 或 sdist。

### 2. 续跑第 5 次

旧：`transcript-continue.test.mjs` import 已删除的 `AUTO_CONTINUE_LIMIT`，循环 5 次后断言 `continuationState(msgs).limited === true`，再断言人话把 streak 重置成 `limited === false`。那是成功次数硬停。

新：不再 import `AUTO_CONTINUE_LIMIT`，也不再断言 `limited === true`。5 次成功续跑后再来一条完成通知时，`limited` 仍为 `false`，`trigger` 仍是那条完成通知。人话之后 `trigger` 为空，文案是“人话占有下一轮，不是次数解锁”，不是把门槛清掉或调大。人话之后的新完成通知仍可成为 trigger。`limited` 恒假，不代表人话重置了次数锁。

保留，没有改成成功次数锁，也没有拿掉：

- 失败完成通知：标题仍是「后台子任务失败」，`failed === true`。失败行不把 `limited` 翻成成功门槛。
- 取消：测试仍要求 `chat-page.tsx` 含「已停止」。
- 执行失败：仍要求「执行失败」。
- 审批等待：仍要求 `waiting_for_approval`、`respondApproval`、`live-tail.tsx` 的 `<ApprovalCard` 与 `onApprove`。
- 成功次数横幅「已连续自动继续」仍被否定，没有写回页面。

另补：`display_kind=clawd_continuation` 和无 kind 的 `[clawd自动续跑]` 是「子任务已完成，自动继续」。其他 kind（这里用 `process_complete`）即使正文带新标记，也不是这条续跑通知。旧 `[面板自动续跑]` 用例还在。

核心 `cancelled` / `approval_pending` / `attempts_exhausted` 仍在 `gateway/clawd_continuation_decision.py`。本路没有改决策文件，也没有把失败预算写成成功次数。

### 3. 实时尾栏

旧：`live-tail.tsx` 第 79 行只认 `run.userText.startsWith(AUTO_CONTINUE_MARK)`，也就是 `[面板自动续跑]`。进行中的核心续跑正文以 `[clawd自动续跑]` 开头，会先画成用户气泡。

新：`isLiveContinuation` 只改这一行进通知还是进用户气泡的条件。

- `display_kind === "clawd_continuation"` 即续跑，不要求正文再匹配次数。
- 其他 `display_kind` 不是续跑。
- 无 kind 时认 `[clawd自动续跑]` 或 `[面板自动续跑]`。

`NoticeRow` 仍是「子任务已完成，自动继续」，`detail` 仍是 `run.userText`。用户气泡仍是 `className="turn user"`。`pace={pace}`、`AssistantBody`、`ApprovalCard`、出字与间距没改。`remote || userInHistory` 仍不画这条输入。

`LiveRun` 类型没有 `display_kind`。尾栏用 `LiveRun & { display_kind?: string | null }` 读取；字段不在时退回前缀。没有改 `runs.ts`，所以进行中的核心行目前仍靠正文里的新标记被认出来。kind 字段若以后由运行状态 owner 挂上，同一条件会认，不必再改布局。

### 4. 测试目录

旧：`tests/test_tests_tree_layout.py` 要求 `tests/<目录>` 镜像源码包，或写进 `_NON_MIRROR_DIRS`。当时没有 `clawd_*` 用例目录。`clawd_runtime_cases` 和其余非镜像 `clawd_*` 目录会被点名。显式文件路径仍能发现，但全量 `tests` 的布局断言会红。不能因此删目录，也不能说布局门禁已经接受它们。

新：只把写这份报告时已经存在、且不镜像源码包的 14 个目录逐名写入 `_CLAWD_CASE_DIR_REASONS`，再解包进 `_NON_MIRROR_DIRS`。每个名字有一句原因。名字不含 `*`、`?`、`[`。没有 `startswith("clawd_")`，没有 fnmatch，没有关掉 `assert not offenders`。以后新建的 `tests/clawd_*` 仍是 offender，直到有人补精确名字和原因。

这 14 个是：

- `clawd_acceptance_cases`：验收编排跨包夹具
- `clawd_auth_native`：身份与制品根
- `clawd_continuation_cases`：续跑决策，不是 gateway 镜像
- `clawd_effort_cases`：档位与续跑 pending
- `clawd_event_native`：事件订阅身份
- `clawd_event_recovery_cases`：运行事件恢复
- `clawd_file_auth_cases`：文件鉴权
- `clawd_memory_native`：项目记忆身份与冻结
- `clawd_memory_scope_cases`：记忆作用域
- `clawd_race_cases`：审批与续跑竞态
- `clawd_runtime_cases`：安装后 run-wait 与 child 计数
- `clawd_settings_cases`：设置公共契约
- `clawd_todo_delegation`：待办执行投影
- `clawd_todo_native`：待办列表接线

`clawd_server` 与 `clawd_projects` 有同名源码包，不进这张表。它们仍走镜像规则。

列入名单不等于跳过收集。`_SKIP_PARTS` 仍只是 `integration`、`e2e`、`docker`。`pyproject.toml` 的 `testpaths` 仍是 `["tests"]`，没有 `python_files` 覆盖。`scripts/run_tests_parallel.py` 仍 `rglob("test_*.py")`，显式文件路径仍原样纳入。面板 `package.json` 的 `test:node` 仍是 `tests/*.test.mjs`，`transcript-continue.test.mjs` 仍在这个 glob 下。登记文件自己也是 `test_*.py`，落在已映射的 `clawd_acceptance_cases` 里，发现不会把它丢掉。

这些用例都没有 `integration` / `live` 标记。登记测试在发现到新的未映射 `clawd_*` 目录时失败，而不是自动放行。

## 文件

| 路径 | sha256 | 做了什么 |
| --- | --- | --- |
| `/root/hermes-workspace/clawd/tests/clawd_server/test_distribution_package_manifest.py` | `10246d5a310c21f414ad69e10d6842a19729f844a96265add385037efee5fbeb` | `MANIFEST_LINES` 同步 23 行；大小/哈希/禁入比较未弱化 |
| `/root/hermes-workspace/clawd/apps/panel/tests/transcript-continue.test.mjs` | `6d5b8cc1af8d6034e138e87502bd36bdaf1286daee7eb0230387c4a216bc5c4b` | 第 5 次不再要求 `limited === true`；保留失败、审批、取消 |
| `/root/hermes-workspace/clawd/apps/panel/src/react/pages/chat/live-tail.tsx` | `1eb5c766906f815e72ee38fa2c7aee894bb3ac8938b92409c7ba8c254e6dbca4` | 识别 kind 与新旧标记；布局、动画、审批卡片未改 |
| `/root/hermes-workspace/clawd/tests/test_tests_tree_layout.py` | `8dccc80ade3dc0336ead5aac7ab9efb74f188dd35fc969c87bd22908168ed472` | 14 个精确目录及原因；整条镜像规则仍在 |
| `/root/hermes-workspace/clawd/tests/clawd_acceptance_cases/test_current_contract_registration.py` | `5082e40bd6c464888bcc13ce1dd133ab578e1ccd7dbc4b9e1b00d2fc16cf696f` | 新增。只读源码与目录，登记上述契约和发现路径 |

哈希是写完后的 `sha256sum`，不是测试通过。

## 明确未运行

没有运行本报告点名的任何测试，包括新的 `test_current_contract_registration.py`、`transcript-continue.test.mjs`、`continuation-no-cap-display.mjs`、布局测试、分发清单测试、vendor 分发测试。没有构建，没有 import 产品模块，没有 CLI。准备好的静态断言不是通过。最终验收不豁免。
