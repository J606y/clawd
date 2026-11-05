# 敏感边界静态集成 2026-11-05

独立只读。未跑测试、构建、import、CLI、浏览器、网络或生产读取。未改源码、测试、gate、manifest。未 delegate。两份 `runs.ts` 全文一致。全部运行结果 **unverified**，本报告不是通过，也不是上线许可。未审 package 中间字节，未重开原 63 项背景风险，未重设计 UI。`execution-input-recovery-fixture.tsx` 的空 `{}` 是生命周期报告已排除项，本次不新派。

## 必须修

1. `apps/panel/src/lib/runs.ts` 与 `apps/panel/src/react/lib/runs.ts`（全文相同）。`foreignIdentity`（113–120 行）在缺 `run_id`/`session_id` 时返回 false。`finishFromStatus`（730–749 行）只看状态字符串。`settleLostExecutor`（1247 行）因此会把 `{status:"completed"}` 且无身份的 200 结算为 completed。同文件 409 审批/澄清（1140、1187 行）和 follow 状态轮询（592–599 行）相同。`stop`（1406–1415 行）在 sidecar `busy===false` 时，无身份的 `cancelled` 也会 finish。注释写明缺字段不算错配；这与「只有可信身份匹配的终态才能 settle」冲突。真实 `GET /v1/runs/{id}` 在 `api_server_runs.py` 357、907 行带 `run_id`，创建时带 `session_id`，收紧终态不会靠缺字段。404 不假 completed：`settleLostExecutor` 的 catch 不改状态；`reconcile`（1271 行）要求 `run_id` 与 `session_id`；follow 的 404 无匹配记录时是 failed「无法确认」（621–624 行），不是 completed。`approval-contract.test.mjs` 398–400 行给 conflict-completed 补了身份，384 行无身份体未使用，夹具绿不能证明拒绝了无身份终态。建议：终态结算另加正向匹配（两 id 都等于当前 run，且状态为终态）；不要把内容事件的缺字段规则用到 settle。错配/非活动事件：`applyRunEvent` 757 行不改字段，但 follow 560 行仍 `changed()`。内容未注入，重绘未验证，不单列。

2. `apps/panel/tests/artifacts-browser.mjs`。47 行：没有 run 时 `GET /v1/runs/{id}` 回 200 `{}`。62 行：未识别的 `/panel-` 路由回 200 `{data:[],running:0,items:{}}`，不是 404。空对象会被客户端当成 HTTP 成功，绕过「未知/故障不回空」。建议：缺 run 与未知路由都 404，带 `error.code`，禁止 `{}` 和空列表成功体。

3. `ui-tui/src/hooks/useQueue.ts` `takeQ` 475–481 行。编辑换了 `submissionId` 时先 `removePendingInput(previous)`，再 `claim`。`savePendingInput` 现在会在 root 且目录无属主写位时抛 `EACCES`（`pendingInputs.ts` 93–117 行），`ENOSPC` 本来也会抛。旧 journal 已删、新文件未写成，重启后这一行和附件 token 不在盘上。建议：先成功 `claim`，再删旧记录；抛错则保留旧文件。`enqueue`/`stage`/`dequeue` 是先写再改内存，并有 catch，这三条验收路径静态上不会先清输入。

4. `apps/panel/src/react/lib/admin-api.ts` `setPluginConfig` 560–561 行。GET 走 `request<unknown>` 再 `readPluginConfigResult`（556–557 行），缺 fields、非布尔、秘密行带 `value` 会抛。PUT 仍是 `request<PluginConfigResult>` 再 `withoutSecrets`。空体经 `{...undefined}` 变成 `{}`，不抛；秘密字段明文也不走拒绝。`settings-type-boundaries.mjs` 只断言 GET 接线，且不匹配 `request<PluginConfigResult>`，会绿。建议：PUT 同样 `.then(readPluginConfigResult)`。

## 静态已核，运行一律 unverified

- 文件：`_open_root_fd`（1086–1116）祖先用 `O_NOFOLLOW`；home 叶节点可跟链接，跟完再对 `/proc` 身份拒绝生产根。列表词法 `..` 由 `_normalize_inside`（954–963）在越根时 403 `outside_root`；路径里出现密钥名或 `.git`/`hermes-panel-state` 时，`_home_rel_denied` 在归一化前 403。下载里的 `..` 仍是 400（1286–1289）。符号链接目录只 `readlink`，不 follow-open（1463–1470）。列表 `absPath` 用配置路径并对 inode（1431–1444）；上传 `absPath` 对注入前缀和已打开 fd 的 inode（1137–1151）。不一致是 500，不是成功。未知路由 `_route` 末尾 404，不回 `{}`。`test_workspace_deny.py` 打真实 Handler，只改 `CLAWD_HOME`/`FILE_ROOTS`，不 mock 权限。symlink 置换用例 mock `_safe_path` 只为在真实检查后换目标，断言仍是 403 且不泄漏。未运行。
- journal：root 预检在产品 `savePendingInput`，不是 test 旗标，也不是只认 `0o500`。`0o700`/`0o755` 含 `0o200` 会通过。非 root 不预检，真实 `writeFileSync`/`renameSync`/`fsyncSync` 错误不吞。不先清 composer。ACL 与真实磁盘 **unverified**。三条 diskfull 用例 mock 整个 `savePendingInput`，打不到 `ENOSPC`，不能当存储错误已证。
- 类型：`assertSubagentPage` 要求数字 `total`，缺 total 抛错，不编 0；profile 不能证明时省略，不写 `default`。`config-tab.tsx` 97–100 行不把未确认 sticky 当成 default。未用 `any`/`ts-ignore` 掩盖这 6 处。未跑类型检查。
- `loadEarlier`（`chat-page.tsx` 914–934）在 await 前钉 `reloadGen` 和消息引用，代次或引用变了就丢弃；按钮和 `onNearTop`（993、1010、1012）调用的是这个函数。未跑。
- 浏览器夹具：`react-gate-fixture.tsx`、`react-chat-gate-fixture.tsx`、`react-admission-recovery-fixture.tsx`、`stream-perf-react-fixture.tsx`、folders/folder-offpage 的未知路由是 404，不是 `{}`。subagent 页不写 `profileKey: "default"`。`stream-perf-react-browser.mjs` 529 行预算仍是 frame p95 16.7、long task 0，未放宽。`native_browser_fixture.py` 调 `browser_native.install` 和 `register_created`，patch 只改环境，不 mock 权限。`subagent-clock-fixture.html` 72 行、`subagents-session-frame-fixture.html` 118 行在缺 total 时用 `data.length` 填 total，分页拒绝路径未被这些夹具打到；不是产品放宽。admission 夹具 76–78 行的 stop/run 状态仍无身份，会掩盖第 1 条。

## 父代理分派

只派上列 4 个 owner。不要改 gate/manifest，不要跑测试，不要把本报告写成通过。
