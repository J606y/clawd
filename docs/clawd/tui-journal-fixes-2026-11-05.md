# TUI journal 写失败与构建目的地 — 2026-11-05

本文件只记录静态修复。不是通过，不是上线许可。未跑 tui-check、tui-build、import 或 CLI。未提交、未推送、未部署。未删 `artifacts/final-acceptance/logs/`。未读生产、原面板或官方安装。未改依赖版本。

## 根因

三条失败都在 `artifacts/final-acceptance/logs/tui-check.log`，文件 `ui-tui/src/__tests__/slashAttachmentRenderer.test.tsx`，1908 通过 / 3 失败。断言没有改成允许丢数据。

| 断言 | 日志 | 实际收到 |
| --- | --- | --- |
| 233 `prompt.submit` 应为 false | caption + `chmod 0o500` | 已调用 `prompt.submit` |
| 252 输入应仍为 `/queue later please` | 同上 | 输入已是 `''` |
| 269 `dequeue()` 应为 undefined | enqueue 后 `chmod 0o500` | 返回 `inFlight: true` 的队头 |

调用顺序在失败前已经是：journal 写入返回之后才清草稿、消费 token、把队头标成 in-flight。

- caption：`useSubmission.ts` 的 `journaled()` 包住 `send()`。`send()` 先 `stage()` → `claim()` → `savePendingInput`，抛错则不到 `prompt.submit`，也不 `clearIn()`。
- `/queue`：同一 `journaled()` 包住 `enqueue()`。`useQueue.ts` 先 `savePendingInput`，成功后才 `push`。抛错则 composer 不清，队列不加行。
- attempt：`claim()` 先写 `{...item, inFlight: true}` 的副本，成功后才改内存 `inFlight` 并挂 `settle`。`dequeue()` 捕获写入错误后返回 undefined，不把行留在 in-flight。

这三条在验收里走了成功路径，因为写入没有失败。验收进程是 uid 0。内核对 uid 0 忽略目录 mode。本机探针：目录 `0o500` 时 `writeFileSync` 与 `access W_OK` 都成功。`mkdirSync(recursive)` 也不会把已有 `0o500` 改回 `0o700`。所以 `chmod 0o500` 没有制造 “journal 写不进去”，caller 正确地清了草稿并派发。

非 root 的 `EACCES`、`ENOSPC` 仍由 `writeFileSync` / `renameSync` / `fsyncSync` 抛出，没有在 `savePendingInput` 里吞掉。

## 修复顺序

1. 保持 caller 顺序，不改 `useSubmission.ts` / `useQueue.ts` / `useComposerState.ts` 的成功路径、文案和布局。`/quit` 与已通过的 slash 用例不重设计。
2. 在唯一持久化点 `savePendingInput` 里，于创建临时文件之前失败关闭：uid 0 且 journal 目录不是目录、或属主写位（`0o200`）缺失，则抛 `EACCES`。不写临时文件，不 `rename`。caller 既有 catch 因此会留下 caption、图片 token 和可再次 `dequeue` 的队头，状态为 `input not saved`。
3. 权限恢复或存储恢复后，同一次用户重试才清草稿、消费 token、claim 入 flight 并派发一次。失败的那次没有 `prompt.submit`，也没有把行标成 in-flight，所以不会重复派发，也不会丢掉已暂存的附件 token。
4. 没有把 “缺 `submissionId` / `sid`” 的既有 no-op 改成抛错。那不是这次三条断言的路径，也不是磁盘错误。
5. 构建目的地不改守卫。见下节。

## 构建输出契约

`artifacts/final-acceptance/logs/tui-build.log`：

```text
Error: Output must be outside source or in a supported generated destination: /root/hermes-workspace/clawd/artifacts/final-acceptance/tmp/hermes-tui-1rTTyU/tui
    at productOutput (scripts/build/frontend-common.mjs:78:48)
    at buildTui (scripts/build/tui.mjs:27:40)
    at buildDeveloperTui (scripts/build/tui.mjs:87:26)
```

`ui-tui` 的 `npm run build` 是无参数 `node ../scripts/build/tui.mjs`。`buildDeveloperTui` 用 `os.tmpdir()` 做中间产物，再发布到已支持的 `ui-tui/dist`。验收把 `TMPDIR` 设在源码树内的 `artifacts/final-acceptance/tmp`，中间目录因此被守卫拒绝。

实现已经支持合理目的地，没有缺陷要改：

- 源码树之外的目录。
- `.build/<子路径>`，以及 `apps/desktop/build/products/<子路径>`。`.build` 与 `apps/desktop/build/products` 自身不行。
- 精确开发产物：`ui-tui/dist`、`hermes_cli/web_dist`、`apps/desktop/dist`、`apps/desktop/dist-webapp`、`apps/desktop/build/native-deps`，以及 `native-deps-(darwin|linux|win32)-(x64|arm64)`。

未改 `scripts/build/frontend-common.mjs`、`scripts/build/tui.mjs`，也未把 `artifacts/` 加进生成目录。放宽守卫会让源码树内的任意 `TMPDIR` 变成可覆盖输出。调用方应把 `TMPDIR` 指到源码根之外；无参数 npm build 不接收 `--out`。这是 acceptance owner 的调用侧修复，不是 TUI 源码修复。

## 精确文件

改了：

- `ui-tui/src/lib/pendingInputs.ts`
- `ui-tui/src/__tests__/slashAttachmentRenderer.test.tsx`
- `docs/clawd/tui-journal-fixes-2026-11-05.md`（本文件）

读过、未改：`ui-tui/src/app/useSubmission.ts`、`ui-tui/src/hooks/useQueue.ts`、`ui-tui/src/app/useComposerState.ts`、`ui-tui/src/app/submissionCore.ts`、`scripts/build/frontend-common.mjs`、`scripts/build/tui.mjs`。未改 acceptance 脚本和其他 UI。

## 回归（已写入，未运行）

原三条断言保留：`chmod 0o500` 后不派发、不清 caption/token、不清 `/queue` composer、失败的 attempt 不把队头留在 in-flight；`chmod 0o700` 后同文案重试一次。

另加三条，注入 `disk full` 而不是 mode，避免只特殊处理 `EACCES` 并吞掉其他存储错误：

- caption + 图片：失败不 `prompt.submit`，token 还在；恢复后恰好一次提交，附件长度为 1，token 才清空。
- `/queue`：失败时输入和空队列保留；恢复后队列只有一行 `later please`。
- `dequeue`：失败返回 undefined，同一行 `inFlight` 仍为假；恢复后只 claim 这一行，并带 `settle`。

正常路径仍由已通过用例覆盖，包括 journal 成功后的 `/queue` 图片一次写入，以及真实 `/quit`。本次没有跑这些用例。
