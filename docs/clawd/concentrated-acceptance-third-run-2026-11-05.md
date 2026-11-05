# clawd 集中验收第三轮 — 2026-11-05

marker `third-20261105T213200`。冻结时刻 `2026-11-05T21:32:00+0800`。不是通过，不是上线许可。未改产品源码、打包后端、资源契约、gate、stage 或测试断言。未 commit / push / deploy。未读 `/root/.hermes`，未调用 `/root/panel-deploy.sh`，未连用户 i9，未删旧数据或缓存。

本文件先记入口、预检和静态冻结。真实 prepare/run 结果在运行结束后追加。冻结时运行尚未开始。

## 入口

`scripts/clawd_acceptance/accept.py` 的 argparse 没有 `--root` / `--out`。根目录是常量 `ACCEPT_ROOT=artifacts/final-acceptance`。未发明参数。

固定解释器：`/root/hermes-workspace/clawd/.venv/bin/python`。

1. `prepare`
2. `run --run --implementation-complete --rerun`

`--rerun` 是入口已有旗标，不是绕门。冻结时 15 个 `logs/*.state.json` 都已是终态（pass 1 / fail 9 / blocked 5），pid 皆空。`resume.decide` 对 pass/fail/blocked 且无 `--rerun` 返回 `skip` 并交回旧结果。不用它，第三轮不会重新执行阶段。未传 `--provider-config`、`--install-prefix`、`--rollback-entry`。

父进程 `env -i`。`HOME=artifacts/final-acceptance/account-home`，`CLAWD_HOME` / `HERMES_HOME` 为其 `.clawd`，`TMPDIR=artifacts/final-acceptance/tmp`，`PATH` 只含工程 Node、固定 venv、`/usr/bin`、`/bin`。无生产凭据。子进程隔离仍由脚本自己的 `direct_env` / `run_tests_parent_env` 决定，不把父 export 当成子隔离证明。

## 预检

冻结时无 `accept.py` / `run_tests.sh` 活进程。RAM 总量 3.8Gi，可用约 2.0Gi，Swap 已用约 992Mi。CPU 2，load 0.47。磁盘 `/` 可用 1.9G，90%。上一轮约 2G 是历史值。空间够写下本次文档和日志备份（备份 1.9M），未清盘。全量 pytest / 构建可能再吃磁盘；不足时只列路径，不删。

旧日志与 `report.json` 已复制到：

`/root/hermes-workspace/clawd/artifacts/final-acceptance/preserved/third-20261105T213200/before-run`

原 `logs/*rerun-20261105T193153*`、`accept-prepare.stdout.log`、`accept-run.stdout.log` 未覆盖、未删除。旧 wheel `artifacts/final-acceptance/wheel/accept-build-320184-6978a4cb34f949158ec4cd590a044863/` 未复制、未打开、未删除。

## 静态接线（冻结前回读，未跑测试）

- 两份 `runs.ts` 仍逐字节相同，sha256 `74648e133e0a32e5db9627b9e981f8d6fe234162f3734e75374386c1f1027a63`。`trustedTerminalIdentity` 要求双 id 精确相等。`finishFromStatus` 只结算 completed/cancelled/failed/interrupted。正文事件仍走 `foreignIdentity`，缺字段不丢。未改产品源码。
- `useQueue.takeQ` 换 id 时先在副本上算出后继，`claim` 之后才 `retireReplacedJournal`。抛错路径没有 finally 删旧 journal。`journaled` 只在写成功后 `clearIn`。
- `setPluginConfig` 是 `request<unknown>(...).then(readPluginConfigResult)`。缺 fields 抛错，不编空数组，不回读 GET。
- `artifacts-browser.mjs` 未知路由 404 `route_not_found`，缺 run 404 `run_not_found`，创建只读 body `session_id`。
- `run_tests.sh` 与 `split_forwarded_argv` 都把 `--basetemp <path>` 收成等号形式，且不把 `tests` 当值。`core_argv` 仍传独立 token。
- TUI 构建计划目录是 `/root/hermes-workspace/clawd-acceptance-tui/<token>/{tmp,tui}`，拒绝与检出重叠、拒绝 `/tmp`。`run_tui_build` 追加 `--source` / `--out`。Nix 未构建，静态变更不等于 Nix 通过。
- `generate.py` 全文有 `--checkout` / `--output` / `--offline` / `--input`。`license_signature_ok` 读完整文件。解析不是法律结论。

## 夹具

只给本应成功的 status/stop 体补夹具里已有的 `run_id` / `session_id`。未 200 `{}` 兜底，未编 profile/default，未改负向断言。

改了：

- `apps/panel/tests/phase3-fixes.test.mjs`：第三次 completed 补 `run-c2` / `sess-c2`。
- `apps/panel/tests/degraded-replay.test.mjs`：B1/B2 的 completed 补各自 `run-b1`/`sess-b1`、`run-b2`/`sess-b2`。404 与 `{}` 未动。
- `apps/panel/tests/audit-start-stop.test.mjs`：stop/status 使用 POST body 里已有的 `session_id` 和固定 `audit-run-123`。
- `apps/panel/tests/f01-contract-recovery.test.mjs`：同样使用 POST 的 `session_id` 和 `f01-run-123`。第 245 行负向 mock `{status:'cancelled'}` 未补 id。
- `apps/panel/tests/execution-input-recovery-browser.mjs`：成功 cancelled stop 补常量 `RUN` / `SID`。hang 的 `stopping` 和 500 失败体未补。

未改：

- `runs-terminal.test.mjs`：成功体已有双 id。208/213/236/261/269/270 是过期结果、finally 清理或负向，补 id 会伪结算或破坏“替换 run 仍 running”。
- `assistant-image-preview-browser.mjs`：stop 体没有已有 `run_id`（只有 `sessionId: "sess"`，且 `run: null`）。测试不发 stop。未发明 run id。这是缺口，不是通过。

## 注册

集中 `panel-node` 不是显式列表。`execute.py` 与 `package.json` 的 `test:node` 都自动发现 `tests/*.test.mjs`。因此未改 `package.json`，未改 stage。

会被集中套件执行：

- `runs-terminal-identity.test.mjs`
- `artifacts-fixture-contracts.test.mjs`
- `ui-tui/src/__tests__/queueJournalReplacement.test.tsx`（`npm run check` → `vitest run`，配置只排除 dist/node_modules）

不会被集中 `panel-node` 执行，且未越权改发现规则：

- `settings-type-boundaries.mjs`
- `settings-wire-contracts.mjs`

`execution-input-recovery-browser.mjs` 不在 `test-gates.mjs` 浏览器显式列表里。集中 browser 阶段走该列表。未把该文件加进列表。

## manifest

只刷新已 implemented 条目里对得上的 sha256，以及顶层 `observed_at=2026-11-05T21:32:00+0800`。`implementation_complete` 仍为 true。status / scope / blocking 未改。

变了：

- `apps/panel/sidecar/hermes_panel_sidecar.py` → `11840a85bd8ce3a5be3a9abd4797b74cea1050895b19e80a63e95bd1fde8ff82`
- `apps/panel/src/react/lib/sidecar.ts` → `5b4984fbe252d8f6cbd69b79502380b4a9a5d21d30f87a3911bba98f894ceabd`

未刷新：`browser-vendor-grant-dialog` 的证据路径 `daemon.py` 在仓库根不存在。未用 vendor 副本冒充。其余 19 条 sha256 与当前字节一致。

## 运行

冻结后才启动。结果见文末追加。
