# 浏览器最终静态集成 — 2026-11-05

本文件是静态接线记录，不是验收，不是上线许可。

**未运行。** 未跑 pytest、未 build、未 import 产品模块、未启动 CLI、未做 CDP / 网络 / 浏览器探针。未 commit / push / deploy。未读生产配置、秘密、已安装包、`/usr/local/lib/hermes-agent` 或原面板。未删文件。未改 45 秒人工租约或 300 秒硬顶。未改 `_ipc.py`、其他 vendor 原件、许可、package 清单或面板。

## 修了什么

| 断裂 | 处理 |
|---|---|
| 关闭后 `Target.getTargetInfo` 不带 grant，daemon 在 CDP 前返回 `task_grant_required`，客户端把任何 error 当成已关闭 | 核对请求带同一把登记簿 grant。grant 错误是 `close rejected`，不是已关闭。CDP 的 not-found 仍表示关成了 |
| 会话路由的调用可以在 params 里点名任务页，grant 通过后仍发到默认用户页 | daemon 在 `send_raw` 前拒绝，错误 `task_grant_mismatch`，不进 `attach_first_page`。`Target.*` 仍按参数寻址 |
| 多页 spool 只能整批拒绝，或把一把 grant 抄到每一页 | 行格式 `targetId<TAB>grant`。每页用自己的 grant 登记。裸多页和不齐的行仍拒绝，不抄。云回执读后立刻清空 spool，grant 不留在磁盘 |
| `PYTHONPATH == site_dir` 与 preamble 子串 `getTargets` / `environ` 会误判 | 允许范围内的用例改为 vendor 首项且保留 site；扫页按 AST 调用核，守卫字面量不算扫描 |

daemon 现哈希 `781609813fa0f0d20dda3c5a634d3bc6a01cc7403d8d7b303874903b15434d45`。helpers 仍是 `171744c2ff9c86321d1487c80eb975b433ee4aca9a02b26857fe11be40bd0a4a`。`_ipc.py` 仍是 `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3`。没有新 socket、websocket、HTTP 或 VNC。非自有 daemon 仍不杀。

## 当前调用链

1. 能力：daemon `meta=capabilities` 返回顶层 `task_grants: true`、`dialog_identity: true`、`task_grant_version: 1`。`_ipc.request` 原样解析这一行，不包进 `result`。CLI preamble、`ensure_harness_task_grants`、CDP `_harness_grant_probe` 都只认顶层。嵌套、布尔版本或错误是 `daemon_grant_unsupported`，不建任务页。
2. 字符串 grant：只在 IPC `Target.createTarget` 成功后由 daemon `token_urlsafe(32)` 签发，作为 `result` 的兄弟字段。helpers `cdp()` 仍只返回 `result`，`task_grant_for` 读进程内表。登记簿和客户端只存 ASCII `[A-Za-z0-9_-]{16,128}`。43 字符的 urlsafe token 落在这个范围。公开视图、cleanup 文本、dialog 成功 JSON 不含明文。
3. 建页：harness / 云走 `create_harness_target` 或 preamble 的 `Target.createTarget`，缺外层 grant 只关这一页，不 `Browser.close`。回执四行登记第一页；后续页在回执里是成对的 id+grant，在 spool 里是 `id<TAB>grant`。`register_spooled_text` 按页登记，不把函数参数那一把抄到其他页。
4. 画面 / 输入：`browser_native` 仍是 `capture_harness_jpeg(*locator, target_id)` 和 `harness_call(*bound, target_id, method, params)`。客户端按 `int` pid 还原，grant 放在 `method` 同级，不放进 `params`，不开第二条 websocket。
5. 关闭：`close_harness_target` 带 grant 发 `Target.closeTarget`，再带同一把 grant 发 `Target.getTargetInfo`。grant 拒绝不当成已关闭，也不升级成 `Browser.close`。
6. dialog：harness / `wss://` 不进 supervisor。必须有 daemon 给出的 `targetId`、`sessionId`、`frameId`，并且登记簿里同一 target、同一 pid 有 grant。对得上才发既有 `Page.handleJavaScriptDialog`，带 daemon 的 `sessionId` 和登记簿 grant。缺一项是 `dialog_unscoped` 或 `dialog_unowned`，不应答、不关页、不回落当前页。daemon 不编 `frameId`。dialog 对象本身没有 grant 时，用登记簿那一把，不从面板 JSON 读。

## 无 daemon grant 的兼容

这不是把本地入口改成云 token。

- loopback `open_and_register` 和本地 supervisor / 直接 CDP 不是 harness daemon。登记不要求 daemon grant，也没有改成拒绝。
- 无任务身份的 `_OWN_TAB_PREAMBLE` 仍是 `Target.createTarget` 加 `switch_tab`。daemon 会对这次 IPC 建页签发 grant，helpers 自己带走。不进登记簿，不泛拒绝。
- 已存在的 supervisor 上 `create_blank_target` 仍不拿 daemon grant。云签名地址禁止再开第二条 websocket。有 daemon locator 的画面、输入、关闭走 harness grant；没有 locator 的 supervisor 调用保持原传输，不伪造 token。

## 剩余缺口

- **未运行。** 上述用例、`--version`、全量、构建、真实云 dialog 都没跑。静态接线不是端到端通过。
- 活浏览器是否发出 `frameId` 仍未探针。缺 `frameId` 时 dialog 失败关闭。不能写成 Chrome 已发送。daemon 仍只有一个 `self.dialog`。
- `tests/tools/test_browser_use_cli.py` 的 `test_subprocess_env_replaces_parent_python_import_paths` 仍断言 `PYTHONPATH == "/harness-site"`。该文件不在本轮可改名单。跑到时会失败。允许范围内的 `test_browser_use_harness.py` 已改为 vendor 首项且保留 site。
- `tools/vendor/browser_harness_0_1_13/NOTICE` 仍写旧 daemon 哈希 `fd5e6dd82870d98235a0038243d60a295d375620c9f52759c49c4f8430008d5a`。NOTICE 不在本轮可改名单，未改。启动门禁只拒绝未打补丁哈希，不读 NOTICE 里的新哈希。
- `windowId` 和 IO stream 仍没有映射。没有为补映射再连 CDP。
- 不能把本文件说成浏览器迁移完成。
