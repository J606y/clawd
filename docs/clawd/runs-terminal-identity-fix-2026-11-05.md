# 终态身份修复 2026-11-05

静态修改。未跑测试、构建、import、CLI、浏览器。未 commit/push/deploy。未 delegate。不是通过，也不是上线许可。

JSON `docs/clawd/post-failure-sensitive-integration-2026-11-05.json` 的 `settle-identity` 没有源码哈希。开改前两份 `runs.ts` 全文一致，sha256 `20a3ca79bd1550046e18d5668e851569fedb31085f31cce88588ad75210f6b6d`。改后仍全文一致：

`74648e133e0a32e5db9627b9e981f8d6fe234162f3734e75374386c1f1027a63`

## 产品状态 schema（只读，不用旧行号）

`GET /v1/runs/{run_id}` 是 `_handle_get_run`（`api_server_runs.py` 1301）返回 `_durable_run_status`。权威投影 `run_projection`（`api_server_authority_runs.py` 95）带 `run_id` 和 `session_id`。内存记录由 `_set_run_status`（351）写入 `run_id`；创建（907）写入 `session_id`。收紧终态不靠缺字段。

例外，不是终态结算：活动回退 `_set_run_status(run_id, "running")` 可以没有 `session_id`。进行中的 stop ack（`api_server_authority_runs.py` 164，以及进程内 `{run_id, status:"stopping"}`）也没有 `session_id`。这两条不按缺身份终态拒绝。事件流 `_run_event` 有 `run_id`、通常无 `session_id`，仍走缺字段兼容。

## 改了什么

两份 `runs.ts` 同步。`foreignIdentity`（113）仍只拒绝出现且不一致的 id，不丢缺字段的 `message.delta`。新增 `trustedTerminalIdentity`（130）：两枚 id 都是非空字符串，且与当前 run 精确相等。`finishFromStatus`（761）只接受该谓词加上 `completed` / `failed` / `cancelled` / `interrupted`。

接线的入口：

- follow 状态轮询（617–622）：无身份、少 id、错 run、错 session、旧 run 不结算，写 `无法确认这次运行的终态（返回身份与当前运行不一致或缺身份）`，`followDropped`，返回。不改成 completed。
- follow 404（643）：仍只在 sidecar 双 id 匹配时结算；否则 failed「无法确认这次运行的结果」，不是 completed。
- `settleLostExecutor`（1289）：200 终态体必须双 id。缺身份不 completed；sidecar 仍只接受同一次运行且 `busy===false`。
- 409 审批（1184）和澄清（1232）：不可信终态不 `finish`，不把 error 留成空成功。随后的 404 仍是 failed「运行记录已不存在」。
- `stop`（1451）：可信终态且 `busy===false` 才按响应体结算。无身份终态体不按该字符串结算；`busy===false` 时只把同一份 sidecar 状态交给 `reconcile`。仍未确认则 `stopping=false` 并写上述错误，不循环假 busy。`busy===true` 仍保持 stopping，不结束。无 `runId` 的待核验停止、500 抛错、409 `run_not_active` 原契约保留。`{run_id, status:"stopping"}` 不当成终态失败。
- `reconcile`（1312）：双 id 与当前 session 不精确匹配则返回，不写错误。健康 follow 会在非终态后调用它，旧 run 不能把进行中的回合涂成错误。可观察性是状态仍为 live，且不发 `RUN_FINISHED`。

`resolveAdmission`（902）已先核对双 id，再把同一状态体交给 `finishFromStatus`。

## 消费者

Vite `@` 指向 `apps/panel/src/react`。界面走 `apps/panel/src/react/lib/runs.ts`：

- `src/react/pages/chat/chat-page.tsx`：`runs` / `RUN_CHANGED` / `RUN_FINISHED`。`followDropped` effect 仍调用 `resubscribe`。
- `src/react/pages/chat/widget-card.tsx`、`src/react/docks/live-dock.tsx`、`src/react/pages/activity/subagents.tsx` 使用同一 `runs`。
- `live-tail.tsx` 只引用 `LiveRun` 类型。

`apps/panel/src/lib/runs.ts` 是测试用的同一份。未改布局、动画或文案组件。拒绝用已有 `error` 和 `followDropped`。

## 回归，未运行

- `apps/panel/tests/runs-terminal-identity.test.mjs`：两份 `runs.ts`。follow、404 丢失执行器、409 审批、409 澄清、`busy===false` stop、`reconcile` 各覆盖无 id / 少 id / 空 run / 错 run / 错 session / 旧 run / 双 id，以及 completed、cancelled、failed、interrupted。另有缺 id 正文仍入流、SSE `run.completed` 可无 `session_id`、未知 404、同 run 的 404 sidecar、审批 409 后 404、`busy===true` 不结束且只打一次 stop、非终态 stop ack、无身份 stop 体不能 completed、未核验停止、500、409 `run_not_active` 旧 run。
- `apps/panel/tests/approval-contract.test.mjs`：双 id 的 409 completed 仍成功；原先未使用的 `{status:"completed"}` 现在断言保持 running 并带未确认错误；404 仍 failed。未放宽。

## 其他 fixture，本路未改

这些缺终态身份。owner 要补当前 `run_id` 和 `session_id`，不能靠空成功。

- `apps/panel/tests/phase3-fixes.test.mjs` 97 行 `{status:"completed"}`。follow 不再据此 completed。现有断言会失败。
- `apps/panel/tests/degraded-replay.test.mjs` 54、76 行同样无身份。现有断言不看 status，静态上 follow 会返回且不 completed。
- `apps/panel/tests/react-admission-recovery-fixture.tsx` 76–78 行：stop `{status:"cancelled"}`，GET `{status:"running"}`，sidecar `run: null`。停止不会伪 cancelled。另一 owner。
- `apps/panel/tests/audit-start-stop.test.mjs` 67 行、`f01-contract-recovery.test.mjs` 68 行：stop 体无身份。sidecar mock 有当前双 id 且 stop 后 `busy===false`，所以仍可能经 `reconcile` 结算，不是响应体授权。应给 stop 体补双 id。
- `apps/panel/tests/runs-terminal.test.mjs` 208、213、236、261、269、270 行有无身份终态体。活跃断言不依赖它们结算。清理阶段若仍 live，不会再伪 completed。
- `execution-input-recovery-browser.mjs` / `assistant-image-preview-browser.mjs` 的 stop 体无 run 身份。未改。`artifacts-browser.mjs` 不在本路。

未跑上述任何文件。
