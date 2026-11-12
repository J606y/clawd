# clawd 前端验收修复（transcript 泛型 / 项目档案校验）

目标机：MOSS `list_servers` 核实 `beqCprBT` 为在线的 HUAWEI CLOUD SG SERVER。未提交、未部署、未跑测试/构建/探针。

## 根因

1. `projectSessionHistory` 等约束 `T extends { session_id?: unknown }`。`Message` 没有 `session_id`，弱类型约束使推导丢失，参数和返回都变成 `{ session_id?: unknown }[]`。chat-page.tsx 517/557/805/811 的 TS2345 由此而来，本次未改该文件。
2. `{ success: true }` 没有 `project_id`。`parseInstructionsWrite` / `parseFileWrite` 用 `!==` 先把它当成跨项目，测试要的是缺少内容/缺少档案。已声明且不同的 `project_id` 仍先报「不属于」。缺字段但已有正文/档案仍拒绝，文案是「缺少项目身份」，不放行。
3. 文件名字节：改前 `includes("\\")` 是单个反斜杠，正则 `/[\u0000-\u001f\u007f]/` 是单层 `\u`，不是双转义。日志里的双反斜杠是转义显示。已改为码点 `0x2f` / `0x5c` / `0x00-0x1f` / `0x7f`，避免以后字面量被双重转义后放过控制字符。`projectIdError` 未改。`resourcesErrorMessage` 的 `error.message` → 顶层 `message` → 字符串错误顺序未改。
4. `session-identity-bug.test.mjs` 首轮失败是 Node 26 直接 import `transcript.ts` 时解析 `./attachments.js` 失败（`ERR_MODULE_NOT_FOUND`），与泛型不是同一问题。测试改为 vite `ssrLoadModule`，原断言保留，并要求返回行是输入对象本身。

未改：`chat-page.tsx`、`src/lib/transcript.ts`、`runs.ts`、`runs-terminal-identity.test.mjs`、api.py、后端、浏览器测试。

## 哈希

修改前备份：`/root/hermes-workspace/clawd-frontend-fixes-backup-20261105T215524185075293Z`

| 文件 | 修改前 sha256 | 写后回读 sha256 |
| --- | --- | --- |
| apps/panel/src/react/lib/transcript.ts | dccfcec68dcd6f0304b45552a51be413c2bc7e58b22ad69caa7342a2b0aed4f1 | 1fc03c226344e2a8b0b2ecc56c5798d3b63ff56729038ebecf1c03b92008fba4 |
| apps/panel/src/react/lib/project-resources.ts | 39c89879905001d4713238438342b90d99df745de9d7cf19584d829d189dfe93 | 407716d0f91cb7f3f6bb1f7a3a2691116443cd886ca191cc56709b4577ffe74e |
| apps/panel/tests/project-resources.test.mjs | 229d555641136849e848e93f308b5e9f97b7db52f77e8b1b9279136cbe0fe5a8 | b1d61e85d636ddb9a039e77b61a9cc0710bb9de9c71f1d68d1ed984350d0f0af |
| apps/panel/tests/session-identity-bug.test.mjs | a609591e9e781ca9199cc4b760487bad29714978dc01632bd0d1b4394507d3a1 | 963190aa9f3788ea6d9f38b973fe9c1c8274f0eb9f40212e0d172e2cce5e9d3b |

写后哈希由独立 `sha256` 回读，与写入前本地暂存一致。

## 未测试

未运行 `npm run typecheck`、`npm run build`、`node --test`、浏览器测试或远程探针。只做了本地 `node --check`（两个 mjs 语法）和决策函数的本地副本核对。负向断言未删：空成功、跨项目、`../`、单斜杠、单反斜杠、控制字符仍拒绝。集中验收仍须由主代理执行。
