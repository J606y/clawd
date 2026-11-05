# 浏览器 vendored daemon 实现记录

检查点：2026-11-05。本文是 vendor owner 的实现记录，不是验收，不是上线许可，不是浏览器迁移完成。

**本轮未运行。** 未跑测试，未 build，未 import `browser_harness`，未 py_compile，未启动 CLI，未做 CDP、网络或浏览器探针。未 commit、push、deploy。未改 artifact、已安装包、`uv.lock`、`pyproject.toml`、`MANIFEST.in`。未读生产配置或秘密，未碰 `/usr/local/lib/hermes-agent` 与原面板目录。未删已有文件。

字节对照只用 `sha256sum` 和本地文件比较，没有把 vendor 树放进 `sys.path`。

## 做了什么

从锁定 sdist 整份复制 `src/browser_harness/` 和 `LICENSE` 到 `tools/vendor/browser_harness_0_1_13/`。只改其中 `daemon.py`、`helpers.py`。`_ipc.py` 与其余源码保持 sdist 字节。另写 `NOTICE`、`tests/tools/test_vendored_daemon_grants.py` 和本记录。

传输仍是一行 JSON。没有第二 socket、第二 websocket、新 HTTP 或 VNC。可选字段名是 `task_grant`。非任务页不把该字段当成必填。

## 来源

| 物件 | sha256 | 本轮 |
|---|---|---|
| sdist `artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz` | `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3` | 已复核，与冻结方案一致 |
| vendor `LICENSE` | `5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631` | 与 sdist 相同。版权行仍是 `Copyright (c) 2026 Browser Use` |
| vendor `_ipc.py` | `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3` | 与 sdist 相同，未改 |
| 未打补丁 `daemon.py` | `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97` | 本副本已不是这个值 |
| 补丁后 `daemon.py` | `fd5e6dd82870d98235a0038243d60a295d375620c9f52759c49c4f8430008d5a` | 只此文件与 helpers 偏离 sdist |
| 未打补丁 `helpers.py` | `5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3` | 本副本已不是这个值 |
| 补丁后 `helpers.py` | `171744c2ff9c86321d1487c80eb975b433ee4aca9a02b26857fe11be40bd0a4a` | 返回值仍是 `result` |

`src/browser_harness/` 其余 14 个成员与 sdist 逐字节相同：`__init__.py`、`admin.py`、`auth.py`、`macos.py`、`mcp_cli.py`、`paths.py`、`recorder.py`、`run.py`、`telemetry.py`、`video.py`、`video_render.py`、`SKILL.md`、`video-template.html`，以及上面的 `_ipc.py`。

上游 tag `v0.1.13`、commit `c24e5072ee66f8499bacd663f4f4bcb089bc4492` 是先前来源记录。本轮未联网复核。

`NOTICE` 写明 Browser Use、MIT、sdist sha256、改了哪两个文件。clawd 补丁不移除上游版权。仓库根 Nous LICENSE 没有覆盖这份第三方许可。未 vendor `cdp-use`。

## daemon 验权覆盖

签发只发生在 `Daemon.handle` 里、本进程 IPC `Target.createTarget` 成功之后。值是 `secrets.token_urlsafe(32)`，只留在进程内存。不写 socket、port、pid、日志或响应以外的公开字段。不接受客户端 `owner`、`task_id` 或自带 `task_grant` 作为绑定来源。比较用 `hmac.compare_digest`。

成功响应保持 `result.targetId`，在同一对象外加兄弟字段 `task_grant`。授权不放进 CDP `result`。这条路径不改 `self.session` / `self.target_id`。`attach_first_page` 内部的 `createTarget` 不签发，不进任务表。

`meta=capabilities` 在 token 检查之后、任何 CDP 之前返回：

```text
{"task_grants": true, "dialog_identity": true, "task_grant_version": 1}
```

不接 CDP，不返回 token，不返回任何 `task_grant`。

任务调用没有匹配 grant 时不发 CDP。未知、未登记的用户页保持原路径，不扫 `Target.getTargets` 来认领。

### 类

| 类 | 判定 | 没有匹配 grant |
|---|---|---|
| 会话路由 | 凡不是 `Target.*` 的方法，都发到显式 `session_id` 或默认会话。包括 Page、Runtime、Input，以及 DOM、DOMDebugger、DOMSnapshot、DOMStorage、CSS、Network、Fetch、Emulation、Debugger、Overlay、Console、Log、Performance、Accessibility、Audits、Animation、Storage、IO、HeapProfiler、IndexedDB、ServiceWorker、WebAudio、WebAuthn，和表外未知域 | `task_grant_required` 或缺匹配时 `task_grant_mismatch`，不调用 `send_raw` |
| target/session 参数 | 不限前缀。`params` 的 `targetId` / `target_id` / `sessionId` / `session_id` 若指向已签发任务 target，一律要授权。已点名的包括 `Target.closeTarget`、`attachToTarget`、`activateTarget`、`detachFromTarget`、`exposeDevToolsProtocol`、`autoAttachRelated`、`getTargetInfo`、`sendMessageToTarget`，以及 `Browser.getWindowForTarget`。参数扫描不限于这张名单 | 同上，不发 CDP |
| IPC session 旁路 | `Target.*` 不转发 session，但请求里若带已映射的任务 `session_id`，仍算任务调用 | 不发 CDP |
| 无条件拒绝 | `Browser.close`、`Target.disposeBrowserContext`、`Page.close`。有 grant 也拒绝。不列 target，不发 CDP | `browser_close_refused` |
| 默认会话切换 | `meta=set_session` 的 `target_id` 或 `session_id` 指向任务页时，先校验再改 `self.session`。失败不 enable 域 | 原错误，默认用户页不变 |
| 已附上的任务页读回 | `current_tab` / `connection_status` 的 `self.target_id` 若是任务页 | 不发 `Target.getTargetInfo` |
| 兼容 | `Target.getTargets`、不指向任务页的 `Target.createTarget`、未知用户页上的 DOM/Network 等、未带 grant 的 `/browser connect` 断线恢复 | 照旧发 CDP |

`Target.setAutoAttach` 本身不点名 target，为兼容用户页没有整段拒绝。若事件 `Target.attachedToTarget` 的 target 已是本进程签发的任务页，才记下 `sessionId → targetId`。未知页不认领。

带 `task_grant` 的请求，或断掉的 session 已映射到任务页时，遇到 `Session with given id not found` 直接返回原错误，不进 `attach_first_page`。未带授权、且不是任务 session 的默认页，仍走原恢复。grant 失败发生在 `send_raw` 之前，不会落入恢复。

退出时，`serve` 的 finally 在原专用页清理旁、同一把锁顺序下，只 `Target.closeTarget` 本进程 `handle` 签发过的 targetId。恢复没排空则与专用页一样跳过。不列全部 target，不关用户页。云停止仍只走 `meta=shutdown` → `stop_remote`。

## dialog 身份

`pending_dialog` 仍是 `{"dialog": <对象或 None>}`。`Page.javascriptDialogOpening` 复制事件里已有的 `url`、`frameId`、`message`、`type`、`hasBrowserHandler`、`defaultPrompt`。`sessionId` 只用 tap 回调里的 `session_id`，没有就不编。`targetId` 只用本进程 session 映射，没有就不编。另造进程内 `dialogId`。这三项不只放在 `drain_events`。不连 CDP 补 frame 父链，也不编造 `frameId`。

`Page.javascriptDialogClosed` 只在关闭事件的 session 与当前 dialog 的 session 相同，或两边都没有 session 时清空。别的页的关闭不清任务 dialog。仍只有一个 `self.dialog`，第二个 opening 会覆盖第一个。没有新队列。

任务页第一次记下 session 时只 `Page.enable` 一次。不在每次 attach 上同步打开 Page/DOM/Runtime/Network 四域。`set_session` 原有的四域 enable 没改。enable 失败只记日志，不改默认页，不回落 `attach_first_page`。

## helpers

`cdp()` 仍 `return _send(...).get("result", {})`。`.get("targetId")` 约定不变。`_send` 若响应含兄弟字段 `task_grant` 且 `result.targetId` 存在，写入进程内表。`attachToTarget` 的 `sessionId` 记到同一进程表。之后发往该 target/session 的调用自动带上这把授权，包括 DOM/Network/Fetch，不只 Page/Runtime/Input；否则新 daemon 会拒绝官方 helper 自己的任务页。没有记录就不编。已有 `task_grant` 不覆盖。

`task_grant_for(target_id) -> str | None` 只读这张表，不发 IPC，不新增 CDP 方法。`new_tab` 复用空白当前页的代码未改。

## 未完成

- 运行路径还没接上。`tools/browser_use_cli.py` 仍会把子进程指到未打补丁的 site-packages。本树不在父进程 `sys.path`。已在跑的旧 daemon 会占着 socket。`meta=capabilities` 已实现，但没有客户端在失败时拒绝创建任务页。探针没接上之前，任务授权不能算接上。
- 登记簿、`harness_call` / `close_harness_target`、dialog 工具、CDP 工具、`pyproject.toml` package-data、`MANIFEST.in` 都不在本 owner 范围，未改。
- 用例只写在 `tests/tools/test_vendored_daemon_grants.py`。本轮未运行。统一验收前不算通过。
- 活浏览器是否发出 `frameId` 仍未探针。缺 `frameId` 时 daemon 不编造；应答侧必须失败关闭。那一步还没接到 dialog 工具。
- `windowId`、IO stream handle 没有映射。没有为补映射再连 CDP。只按 target/session 参数和会话路由拦截。
- tag 与 `main` 的逐字节相同是先前记录。本轮只复核了本地 sdist sha256。
- wheel/sdist 是否收录 vendor、LICENSE、NOTICE，验收日才开包核对。源码树存在不等于安装包已包含。
- 依赖 pin 仍是 `browser-harness==0.1.13`。这是故意不改。`cdp-use` 仍由已安装包提供。
- 不能把本文件说成浏览器迁移完成。

## 路径

实现记录：`/root/hermes-workspace/clawd/docs/clawd/browser-vendor-implementation-2026-11-05.md`

vendor 树：`/root/hermes-workspace/clawd/tools/vendor/browser_harness_0_1_13/`

用例：`/root/hermes-workspace/clawd/tests/tools/test_vendored_daemon_grants.py`
