# 续跑界面收口 — 2026-11-05

静态实现记录，不是运行结果、验收或上线许可。未自行运行测试、构建、import、py_compile、tsc 或 CLI。未打开浏览器，未读生产，未改现网，未删文件，未 commit、push、deploy。编辑工具跳过的 lint 不是验收。未启动第二调度器。

报告绝对路径：`/root/hermes-workspace/clawd/docs/clawd/continuation-ui-closeout-2026-11-05.md`

核心次数门槛已由另一路去掉。本路只同步展示，不重绘，不改出字动画、布局和间距。effort / 核心续跑接线仍属原 owner。`sidecar.ts`、`queries.ts` 分页、核心续跑文件、侧车、`live-tail.tsx`、旧测试均未改。

## 消费链

核心已有语义，本路只读、不改：

1. `gateway/clawd_continuation_decision.py`：`DISPLAY_KIND = "clawd_continuation"`，`MARK = "[clawd自动续跑]"`，`LEGACY_MARK = "[面板自动续跑]"`。`is_continuation_row` 认 kind，或无 kind 且正文以前缀开头。其他 `display_kind` 不是续跑。
2. 成功连续次数只作观测。`decide()` 不再因 streak 返回 `streak_limit`。`MAX_ATTEMPTS = 5` 仍是失败预算，原因是 `attempts_exhausted`，不是成功门槛。
3. 仍会挡住续跑的安全原因包括 `cancelled`、`approval_pending`、`clarify_pending`、`plan_pending`、`attempts_exhausted`。这些不在消息列表里写成「请发一句话解锁」。
4. 准入后写入的用户行带 `display_kind=clawd_continuation` 和 `[clawd自动续跑]` 前缀。面板不调度这一行。

面板消费（两份 `transcript.ts` 字节相同；活导入是 `@` → `apps/panel/src/react`）：

1. 历史仍走原有消息加载。本路不新开轮询，不调用 `continuation_trigger`，不调用 `sidecar.main()`。
2. `isAutoContinue`：`display_kind === "clawd_continuation"` 即续跑；其他 kind 不是，除非仍是误标的旧入站且正文以前缀开头；无 kind 时认 `[clawd自动续跑]` 或 `[面板自动续跑]`。
3. 这些行不是人话，所以不能当作「发一句话解锁」。它们仍是既有回合起点，进行中裁剪沿原 `isTurnStart` 路径，不新造调度。
4. `noticeOf` 仍用原通知标题「子任务已完成，自动继续」。失败完成通知仍是「后台子任务失败」。
5. `continuationState` 不再数成功次数。`limited` 恒为 `false`。不读 `MAX_ATTEMPTS`，不把 5 调大。
6. `chat-page.tsx` 不再读取 `continuationState`，也不再显示「已连续自动继续 5 次……发一句话即可让 clawd 继续」。

## 变更

| 文件 | 做了什么 |
| --- | --- |
| `apps/panel/src/lib/transcript.ts` | 消费核心 kind 与两种前缀。去掉 `AUTO_CONTINUE_LIMIT` 和 streak 硬停。`limited` 恒为 false。 |
| `apps/panel/src/react/lib/transcript.ts` | 与上一份字节相同。 |
| `apps/panel/src/react/pages/chat/chat-page.tsx` | 去掉成功次数横幅和对应 import。首发、待办、profile 门、effort 提示、失败/取消/审批等待未动。 |
| `apps/panel/tests/continuation-no-cap-display.mjs` | 静态源码断言。未执行。 |

未改：`gateway/clawd_continuation*.py`、`wake.py`、`apps/panel/sidecar/hermes_panel_sidecar.py`、`apps/panel/src/react/lib/sidecar.ts`、`queries.ts`、`live-tail.tsx`、`build/lib`、官方面板目录。

## 保留的保护提示

这些提示还在，没有改成成功次数锁，也没有拿掉：

- 失败完成通知：`transcript.ts` 第 194 行「后台子任务失败」。
- 取消：`chat-page.tsx` 第 494 行 `setNotice("已停止")`，第 1081 行仍按「已停止」展示，不加错误色。
- 执行失败：第 496 行 `setNotice(run.error || "执行失败")`。
- 停止失败：第 803 行「停止失败：」。
- 等待停止：第 833 行「请先停止当前回复，等待停止完成并加载历史后再编辑」。
- 另一端执行：第 1058 行「任务在另一端执行，等待实时连接」。
- 审批等待：第 542、556 行仍认 `waiting_for_approval`；第 1053 行 `onApprove` 仍走 `respondApproval`。审批卡片在 `live-tail.tsx`，本路未改。
- 澄清等待：第 1154 行 `ClarifySheet` 仍在。
- effort 提示：第 1080 行 `effortNote` 仍在，归属另一 owner。

`MAX_ATTEMPTS` 没有被面板拿来当成功门槛，也没有新增「失败 5 次」横幅。消息列表里没有失败预算计数；不能靠数续跑行冒充 `attempts_exhausted`。

## 保留的首发与任务投影

- 第 13、754 行仍走 `prepareCreatedSession` / `getPendingNewChatProjectId`。
- 第 155–159 行仍投影 `todoId`。
- 第 661–663 行 `profileKey === "unknown"` 时仍不采用未确认执行列表。成功/profile 门未改。

出字动画、`pace`、`mount-row`、间距和任务坞高度都没改。只删了那一条成功次数通知。

## 未完，且不在本路文件名单

- `apps/panel/src/react/pages/chat/live-tail.tsx` 第 79 行仍只认 `[面板自动续跑]`。进行中的核心续跑正文以 `[clawd自动续跑]` 开头，实时尾栏会先显示成用户气泡；入库后由 `transcript.ts` 显示成续跑通知。本路未改该文件。
- 侧车 `AUTO_CONTINUE_LIMIT = 5` 仍在死分支。`bind` 已替换 watcher/main。未恢复第二调度器，未改这个常数。
- `apps/panel/tests/transcript-continue.test.mjs` 仍 import 已删除的 `AUTO_CONTINUE_LIMIT`，并断言第 5 次 `limited === true`。该文件不在本路可写名单，未改。以后跑到它会失败，这不是本路把门槛调大或藏起来。
- 核心 7 个审查项未修。本路不是审查收口。
- `docs/clawd/continuation-no-five-cap.md` 仍是改前核对，未改。

## 明确未运行

未运行 `apps/panel/tests/continuation-no-cap-display.mjs`，也未运行 `transcript-continue.test.mjs` 或其他测试、构建、import、tsc、产品 CLI、浏览器。准备好的静态断言不是通过。最终验收不豁免。
