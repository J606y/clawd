# 浏览器工具 grant 接线 — 实现记录

检查点：2026-11-05。本文是 CLI / CDP 工具 / dialog 三个独占文件的接线记录，不是验收，不是上线许可。

**未跑 pytest。未 build。未启动 daemon。未做 CDP / 网络 / 浏览器探针。未 commit / push / deploy。** 未读生产配置、秘密、已安装第三方包、`/usr/local/lib/hermes-agent` 或原面板。未删文件。未改 45 秒人工租约或 300 秒硬顶。

写完后只做了一次静态核对：`verify_vendor_tree()` 接受当前 vendor 树；`cloud_autospawn_preamble` 生成的源码 `ast.parse` 通过。这不是测试通过。

## 改动文件

| 文件 | 作用 |
|---|---|
| `/root/hermes-workspace/clawd/tools/browser_use_cli.py` | vendor `src` 置于 site dir 之前；启动校验失败不回落；建页前 capability 探针；回执第四行 `task_grant` |
| `/root/hermes-workspace/clawd/tools/browser_cdp_tool.py` | harness `Target.createTarget` 改走 `create_harness_target` 再登记；关闭类拒绝保持 |
| `/root/hermes-workspace/clawd/tools/browser_dialog_tool.py` | harness 任务只在 target / session / frame 与登记簿 grant 齐全时应答 |
| `/root/hermes-workspace/clawd/tests/tools/test_browser_use_cli_vendor_path.py` | 已写，未跑 |
| `/root/hermes-workspace/clawd/tests/tools/test_browser_dialog_harness_identity.py` | 已写，未跑 |

其他源码未改。`argv` 仍是 `-m browser_harness.daemon`（`_DAEMON_MODULE`）。`_DEFAULT_TIMEOUT_S` 仍是 300。`LIVE_LEASE_S = 45` 与 `_HARD_LEASE_S = 300.0` 未改。

## 调用链

1. `browser_exec` → `_base_subprocess_env` → `verify_vendor_tree()`。核对 LICENSE 版权行 `Copyright (c) 2026 Browser Use`、`_ipc.py` sha256 `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3`、`daemon.py` 不是未打补丁哈希 `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97`，并要求包内成员与 `def task_grant_for`。失败抛 `VendorPathError`，PYTHONPATH 不会只剩 site-packages。通过时 `PYTHONPATH = vendor/src + os.pathsep + site_dir`。父进程不把 vendor 放进 `sys.path`。
2. 任务页创建前 `_pin_owned_target` → `_require_task_grant_daemon`。对已在跑的 daemon 发既有 socket 的 `meta=capabilities`，只认顶层 `task_grants: true`、`dialog_identity: true`、`task_grant_version: 1`。失败码 `daemon_grant_unsupported`，不创建任务页。只有 `_own_harness_daemon` 为真才走已有 `--reload`；非自有 daemon 不杀。socket 不在则不杀，交给子进程用 vendor 树启动，preamble 再建页前再探一次。
3. `cloud_autospawn_preamble`：capability 通过后 `Target.createTarget`，再 `browser_harness.helpers.task_grant_for`。有 grant 则回执四行（kind、pid、targetId、task_grant）。无 grant 只写三行并 `Target.closeTarget` 关这一页，不 `Browser.close`。同一次调用里后续新页把 `targetId` + grant 追加进同一回执，不写 spool。
4. `_register_autospawn_receipt` 读后清空回执。缺第四行不登记，并用既有 `harness_exchange` 只关该 `targetId`。有 grant 则 `register_created(..., task_grant=grant)`。登记失败只关这一页，并带上这把 grant，避免 `close_harness_target` 因尚未入库而根本不发关闭。
5. `browser_cdp` 的 harness 分支：`_harness_grant_probe` → `ensure_harness_task_grants` → `create_harness_target` → `register_created(..., task_grant=)`。不再用 `harness_call` 建页（IPC 侧已拒绝那条路）。`Browser.close` / `Target.disposeBrowserContext` / `Page.close` 拒绝保持。其他页方法仍走 `harness_call(..., expected_pid=pid)`，不开第二条 websocket。
6. `browser_dialog`：非 harness 连接仍走原 supervisor。harness / `wss://` 不进 supervisor。locator 齐全才 `harness_pending_dialog`；缺 target、session 或 frame 为 `dialog_unscoped`。`active_binding` 不成立或 `harness_task_grant` 不匹配为 `dialog_unowned`。齐全才用既有 IPC `Page.handleJavaScriptDialog`，带 dialog 的 `sessionId` 和登记簿 grant。不应答则不关页、不回落当前页、不退 VNC。

当前 vendor 树静态结果：LICENSE、`_ipc.py` 哈希、已打补丁 `daemon.py`、`task_grant_for` 均符合。`NOTICE` 不在树里。

## 残留缺口

- **未跑。** 上述两个新测试、已有 `tests/tools/test_browser_use_harness.py` 的 `--version`、全量、构建、真实云 dialog 都没跑。不能把静态哈希核对说成端到端通过。
- `tests/tools/test_browser_use_harness.py` 断言子进程 `PYTHONPATH == _harness_site_dir()`。vendor 前缀会让这条失败。该文件不在本 owner 范围，未改。`--version` 是否仍能在不连浏览器的情况下通过，未跑。
- `tests/tools/test_browser_task_cloud.py` 的 preamble 断言 `"environ" not in text` 且 `"getTargets" not in text`。这两处在本次之前的 preamble 里已经存在（`BU_NAME` 读取，以及拒绝 `Target.getTargets` 的守卫）。未删守卫。该断言仍会失败，不是本轮新增扫描。
- vendor `NOTICE` 未落地。启动门禁按冻结的许可 / `_ipc` 哈希 / 未打补丁 daemon 哈希执行，不因缺 NOTICE 回落 site-packages。分发收录仍未完成，本轮不改 `pyproject.toml` / `MANIFEST.in`。
- 活浏览器是否发出 `frameId` 未探针。缺 `frameId` 时 dialog 失败关闭。不能写成 Chrome 已发送。
- daemon 仍只存一个 dialog。第二个会覆盖第一个。未新做队列。
- `_cdp_on_live_supervisor` 的 `create_blank_target` 仍不拿 daemon `task_grant`。没有把它改成拒绝，也没有为它新开 websocket。这条兼容未完成。
- 无任务身份的 `_OWN_TAB_PREAMBLE`，以及 loopback `open_and_register`，不走 daemon grant。未改。
- preamble 不再把额外 target id 写入 spool。若别处仍写入无 grant 的 spool，`adopt_spool` 会被登记簿拒绝；本函数不因此 `Browser.close`，只报告 additional spool 未登记。
- 创建成功但回执没有 grant 时，关闭请求不带 grant。旧 daemon 会忽略该字段；新 daemon 若已签发 grant 而客户端没拿到，关闭可能被拒绝。只报告未登记，不升级成 `Browser.close`。
- `harness_pending_dialog` 会剥掉 dialog 上的 `task_grant`。匹配用登记簿 `harness_task_grant`，不从面板 JSON 读 grant。
- 画面、输入、vault 仍走原有 daemon IPC。本轮没有再开第二条签名连接，也没有改那些文件。不能据此说浏览器迁移完成。
