# 队列 journal 替换顺序 2026-11-05

静态修改。未跑测试、构建、import、CLI。未改 `pendingInputs.ts`、gate、manifest。未 commit/push/deploy。未把 `slashAttachmentRenderer.test.tsx` 里 3 条整函数 `disk full` mock 当成 `ENOSPC`/`EACCES` 已验。本报告不是通过，也不是上线许可。

## 时序

前：`takeQ` 在文本变化、`submissionId` 被剥掉后，先 `removePendingInput(previous)`，再 `claim`。`savePendingInput` 的 root 写位预检或 `ENOSPC` 一抛，旧 journal 已删，新文件未落盘。

后：换 id 时先在副本上算出后继，不把活行 splice 出队列，也不标 `inFlight`。`claim`（新 journal，`attempted: true`）成功后才 `retireReplacedJournal`。`claim` 的 `catch` 没有 `finally` 删旧文件；`finally` 只清重入集合。同 id 覆盖仍是先放回再 `claim`，写失败时原文件还在（temp+rename）。

## 失败 / 重启 / 重复

- 新写失败（`writeFileSync`/`renameSync` 的 `ENOSPC`，或目录无属主写位的 `EACCES`）：异常原样抛出。`useSubmission` 的 `journaled` 因此不 `clearIn`。旧行、附件、队列（非 inFlight）、composer token 都还在。重试再 `claim` 一次；成功后旧 id 删除，在飞项上再 `takeQ`/`dequeue()` 不会第二次派发。
- 删旧失败且文件已不在，或已是 `attempted`：不删新文件，状态用既有 `journalCleanupFailed`，仍返回这一次 claim。
- 删旧失败且旧文件仍会自动派发：`savePendingInput({ failed: true })` 把旧记录写成 `attempted`。重启 `loadPendingInputs` 得到 `failed: true`，现有 `dequeue()` 不自动取。两条都留，不另做调度器。显式 `dequeue(true)` 仍只取头一条，这是既有失败重试，不是重启双投。
- 墓碑也失败：回滚未派发的新 journal 并抛错，不删旧文件。回滚再失败则两条错误都进抛出的 message，仍不删旧、不返回 claim。此窗口现有 API 不能原子收口，未加 `pendingInputs` 事务接口。
- `claim` 已写、进程在返回前崩溃：调用方还没拿到 item，编辑未发出。重启只自动派发未 `attempted` 的旧行；新行是 failed，不自动再发。返回前已删除或墓碑，调用方发出后重启不会再自动派发旧行。
- 同一 `submissionId` 重入：集合挡住第二次 `claim` 和 `dequeue`，不删旧 journal。

## 用例（未运行）

`ui-tui/src/__tests__/queueJournalReplacement.test.tsx`

- `writeFileSync` 注入 `ENOSPC`：不 unlink，旧附件还在；撤门后重试只成功 claim 一次。
- `renameSync` 注入 `ENOSPC`：不出现第二份 uuid journal。
- `rmSync` 对旧 id 抛 `EACCES`：两条 `attempted`，重挂后 `dequeue()` 为空，`dequeue(true)` 只取一条。
- 真实 `savePendingInput` 途中重入 `takeQ`/`dequeue`。
- `chmod 0o500` 走真实 `takeQ`，断言 `EACCES`，再恢复只留新 id。
- `useSubmission` 编辑入口同样 `chmod 0o500`：无 `prompt.submit`，composer 与 image token、旧附件还在；恢复后 `prompt.submit` 一次且带附件。

`slashAttachmentRenderer.test.tsx` 只加了注释，标明那 3 条 mock 打不到 fs。
