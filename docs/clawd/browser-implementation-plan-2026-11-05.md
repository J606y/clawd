# 浏览器 daemon 任务授权与云 dialog 身份链 — 实现方案

检查点：2026-11-05T05:45:08+08:00。本文是可执行文件方案，不是实现、不是验收、不是上线许可。

**本轮未实现。** 未改产品、未改锁、未 vendor、未写用例、未跑测试、未 build、未 import `browser_harness`、未启动 daemon、未做 CDP/网络/浏览器探针、未 commit/push/deploy。未读已安装第三方包、`/usr/local/lib/hermes-agent`、原面板、生产配置或秘密。未删文件。

父代理冻结本文件的 owner 与接口后，才给实现授权。这不是要求用户重批已有画面、输入、关闭或登记簿功能。客户端登记、preamble `_owned`、`owner_task_id`、Windows 环回 token、POSIX 0600 socket 都不是 daemon 任务授权。

## 结论

可以 vendor，但不能只把 `daemon.py` / `helpers.py` 盖到已安装包上。

`python -m browser_harness.daemon` 和 `python -m browser_harness.run` 按包名加载。PYTHONPATH 上第一个 `browser_harness` 包会挡住 site-packages 里的同名包，不会只替换两个文件。`daemon.py` 相对导入 `_ipc`、`auth`、`paths`；`helpers.py` 相对导入 `_ipc`、`paths`、`recorder`；CLI 入口再导入 `admin`、`auth`、`telemetry`。缺任一文件，现有 `--version` 和 daemon 启动都会断。

正确做法：从已锁定 sdist 整份复制 `src/browser_harness/`，只改 `daemon.py` 和 `helpers.py`，`_ipc.py` 保持逐字节。启动命令仍是 `sys.executable -m browser_harness.daemon`。传输仍是一行 JSON，不开第二 socket、第二 websocket、新 HTTP 或 VNC。MIT 版权与许可全文随副本走，不改成 clawd/Nous 版权。

未打补丁的 0.1.13 会忽略 `task_grant` 并照常执行 CDP。因此实现后若 capability 探针失败，任务页必须拒绝创建，不能把客户端登记当成授权，也不能改协议绕过。

## 冻结接口

实现前按本节对齐。字段名不要另起。

### 传输

沿用 `browser_harness._ipc.request`：一行 JSON，POSIX 为 `BH_RUNTIME_DIR` 下的 stem socket，Windows 为 loopback 加 port 文件 `token`。`_ipc.py` 不改。Windows `token` 仍由 `_ipc.request` 注入，与任务授权无关。

请求只新增可选字段 `task_grant`。旧 daemon 不读它，所以单靠加字段没有授权效果。新 daemon 不得把该字段设成非任务页的必填项。

既有请求字段保持：`token`、`meta`、`method`、`params`、`session_id`、`target_id`（仅 `set_session`）。

### daemon 签发的任务授权

只在 `Daemon.handle` 里、本进程 IPC `Target.createTarget` 成功之后签发。`attach_first_page` 内部的 `createTarget`（具名 daemon 专用页、无页时的 about:blank）不是任务页，不签发，也不写入任务表。

- 值：`secrets.token_urlsafe(32)`，只留在 daemon 进程内存。不写 socket、port 文件、pid 文件、日志、sqlite 的公开视图。
- 不接受客户端带来的 owner、`task_id` 或 `task_grant` 作为绑定来源。比较用 `hmac.compare_digest`。
- 成功响应保持 `result.targetId`，在同一对象外加兄弟字段 `task_grant`。不要把授权塞进 CDP `result`，避免被当成 CDP 参数转发。
- 同时记下本进程 `attachToTarget` 返回的 `sessionId → targetId`，以及 `set_session` 的同一映射。映射不到就不编 `targetId`。
- 这条路径不得改写 `self.session` / `self.target_id`。那是未带 session 的 `/browser connect` 默认页。任务页内部 attach 只进任务表。

任务页上的 `Page.*`、`Runtime.*`、`Input.*`、`Target.closeTarget`、`Target.attachToTarget`，以及把默认会话切到任务页的 `meta=set_session`，缺少匹配的 `task_grant` 就拒绝，不调用 CDP：

- 缺字段：`{"error": "task_grant_required"}`
- 不匹配：`{"error": "task_grant_mismatch"}`

未登记的原 `/browser connect` 页保持可用，不认领、不扫 `Target.getTargets` 来证明归属。`Target.getTargets` 继续允许，避免打断官方 `list_tabs`；产品侧 preamble 已拒绝任务代码扫页，daemon 不靠扫页建立授权。

`Browser.close` 与 `Target.disposeBrowserContext` 在 `handle` 里直接 `{"error": "browser_close_refused"}`，不调用 CDP，不列 target。`Page.close` 同样拒绝：它关掉的是当前 session 的页，任务身份不能证明独占整浏览器。云浏览器停止仍只走已有 `meta=shutdown` → `stop_remote`。

带 `task_grant` 的请求如果遇到 `Session with given id not found`，不得走 `attach_first_page`。那条恢复会附到用户当前页。直接返回原错误。未带授权的 `/browser connect` 请求保留现有恢复。

### 云 dialog 身份

`pending_dialog` 响应形状不变：`{"dialog": <对象或 None>}`。只给对象加字段，保证 `helpers.page_info` 仍能读 `type` / `message`。

`_record_event` 在 `Page.javascriptDialogOpening` 时：

- 保留 params 里已有的 `url`、`frameId`、`message`、`type`、`hasBrowserHandler`、`defaultPrompt`。
- `sessionId` 只用 tap 回调里已有的 `session_id`。没有就不编。
- `targetId` 只用本进程 session 映射。没有就不编。
- 自造 `dialogId`（进程内随机），便于调用方点名。这是附加字段，不是新协议。
- 这三项不得只放进 `drain_events`。该缓冲 `maxlen=500`，读完即 `clear()`，且事件本身没有 `targetId`。

`Page.javascriptDialogClosed`：仅当关闭事件的 session 与当前 dialog 的 session 相同，或两边都没有 session 时，才清空 `self.dialog`。不要用别的页的关闭事件清掉任务 dialog。

应答只走既有 IPC `Page.handleJavaScriptDialog`，带 dialog 上的 `sessionId` 和该 target 的 `task_grant`。必须同时具备 daemon 给出的 `targetId`、`sessionId`、`frameId`，且登记簿里这个任务存有同一把 daemon 签发的 `task_grant`。缺任何一项：`dialog_unscoped` 或 `dialog_unowned`，失败关闭。不开 supervisor、不开第二条 websocket、不回落当前页、不关页。

daemon 没有 supervisor 那份 frame 父链。不要为补链再连 CDP。`frameId` 只使用事件 params 里已有的值。

harness 任务页在 daemon 首次记下 session 时启用 `Page.enable`，否则 dialog 事件可能根本不进 daemon。不要在每次 `harness_call` 的 attach 上同步打开 Page/DOM/Runtime/Network 四个域；`set_session` 已有 5 秒 IPC 预算，重复四路 enable 会拖垮截图。官方 `switch_tab` 仍会自己 `set_session`。

### helpers 必须带走授权

`helpers.cdp` 现在 `return _send(...).get("result", {})`（sdist `helpers.py` 72–77 行）。授权若只在 `result` 外面，preamble 和后续 `Page.*` 都看不见，新 daemon 会拒绝官方 helper 自己创建的任务页。

同一份 vendored `helpers.py`：

- `cdp()` 返回值仍是 `result`，`.get("targetId")` 不变。
- `_send` 若响应含 `task_grant` 且 `result.targetId` 存在，写入进程内表。`attachToTarget` 的 `sessionId` 也记到这张表。
- 之后发往该 target/session 的 `Page.*`、`Runtime.*`、`Input.*`、`closeTarget`、`attachToTarget`、`set_session` 自动带上这把授权。没有记录就不编。
- 新增 `task_grant_for(target_id) -> str | None`，只给 preamble 读。不新增 CDP 方法。
- 不改 `new_tab` 复用空白当前页的行为。任务路径的 preamble 已经绕过它，强制 `Target.createTarget`。改 `new_tab` 会误伤 `/browser connect`。

### 客户端登记

登记簿增加列 `task_grant TEXT NOT NULL DEFAULT ''`，沿用现有 `ALTER TABLE` 方式。空字符串表示 daemon 未签发。

- `register_created(..., task_grant: str = "")`。harness 传输没有非空 `task_grant` 就拒绝登记，不把行写成已授权。
- `session_view`、`task_connection`、面板 live JSON 不返回 `task_grant`。另给进程内读取函数，仅供 IPC 附带。不写日志，不放进 `cleanupError`。
- `owns` / 客户端 owner 仍不能代替 daemon 校验。

回执在现有三行（kind、pid、targetId）后加第四行 `task_grant`。文件保持短命，读后清空。缺第四行不得登记。创建已成功但没有授权时，只对这个 `targetId` 做 `Target.closeTarget`；旧 daemon 会忽略授权字段所以关得掉。关失败就报告未登记，不 `Browser.close`，不关其他页。

### 断线、清理、降级

| 情况 | 行为 |
|---|---|
| socket 不在、ping 失败 | 现有 `daemon_unavailable`。不新开 websocket，不改走可见页或 VNC |
| pid 不符 | 现有 `browser_replaced`。不改连别处 |
| capability 探针失败 | `daemon_grant_unsupported`。不创建任务页。不杀不属于本进程的 daemon。本进程拥有的才走已有 `--reload` |
| `task_grant_required` / `mismatch` | 原样失败。不去掉授权重试，不扫页 |
| 创建响应没有 `task_grant` | 不登记。只关闭刚返回的那个 targetId |
| 关闭任务页被拒绝 | 报告 `close rejected`。不升级成 `Browser.close` / `disposeBrowserContext` |
| CDP 断开 | 现有错误字符串。带授权的请求不重附到第一页 |
| dialog 缺 target、session 或 frame | 失败关闭，不应答 |
| daemon 进程退出 | `serve` 的 finally 只关闭本进程 `handle` 签发过的 targetId，锁顺序与现有 dedicated tab 相同；恢复没排空就跳过，与现有专用页一致。不列全部 target。不关用户页 |
| 云计费浏览器 | 仍只 `meta=shutdown` → `stop_remote` |

面板画面/输入继续走 `harness_call`。授权由 `harness_call` / `close_harness_target` 从登记簿内部附上，所以 `clawd_server/browser_native.py` 不必为授权改逻辑。现有 `CdpError` 仍映射为画面不可用或输入失败。这是降级，不是授权完成的替代证明。

### 明确不动

- `apps/panel/sidecar/hermes_panel_sidecar.py` 的 `LIVE_LEASE_S = 45`。
- `clawd_server/browser_native.py` 的 `_HARD_LEASE_S = 300.0`，以及 `min(LIVE_LEASE_S, hardUntil)`。
- `tools/browser_use_cli.py` 的 `_DEFAULT_TIMEOUT_S = 300`。那是工具超时，不是人工租约。
- 启动 argv 仍含 `-m browser_harness.daemon`。`_own_harness_daemon` 用它识别进程，不要改成别的模块名。
- 不改协议标识、不回落 VNC、不认领未知页。
- 不改 `uv.lock` 的 `browser-harness==0.1.13`。依赖仍安装，用来提供 `cdp-use` 等，并让 `find_spec("browser_harness")` 继续为真。运行中的 daemon 和 helper 必须解析到 vendor 树，而不是 site-packages 里未打补丁的副本。

## 文件 owner

一人一文件。公共启动环境只有 CLI owner。vendor 树是新文件，不抢已有产品文件。

| owner | 可改文件 | 做什么 | 不做什么 |
|---|---|---|---|
| vendor | 新建 `tools/vendor/browser_harness_0_1_13/` | 从 artifact sdist 复制整包 `src/browser_harness/`、`LICENSE`；只改 `daemon.py`、`helpers.py`；写 `NOTICE` | 不改 artifact 原件，不改已安装包，不改 `_ipc.py`，不 vendor `cdp-use` |
| CLI | `tools/browser_use_cli.py` | `_base_subprocess_env` 把 vendor `src` 放在 site-packages 之前；探针失败则不创建任务页；preamble 写第四行授权；登记时传入 `task_grant` | 不改 argv 模块名，不改 45/300 租约，不改 `_ipc` |
| IPC 客户端 | `tools/browser_task_cdp.py` | `harness_call` / `close_harness_target` 附带登记簿里的授权；创建函数返回 `(target_id, task_grant)`，缺授权即失败；`harness_pending_dialog` | 不把授权放进面板 JSON |
| 登记簿 | `tools/browser_task_registry.py` | 列与内部读取；`register_created` 增加 `task_grant`；公开视图不含明文 | 不把客户端 owner 当授权 |
| dialog | `tools/browser_dialog_tool.py` | harness 任务改走 daemon `pending_dialog`；三项身份加授权都在才应答；无身份的非任务页仍走 supervisor | 不关页，不新开 websocket |
| CDP 工具 | `tools/browser_cdp_tool.py` | harness `Target.createTarget` 改用返回授权的创建函数再登记；关闭类方法维持拒绝 | 不扫页，不认领未知页 |
| 分发 | `pyproject.toml` 的 `package-data.tools`、`MANIFEST.in` | 让 LICENSE、NOTICE、`SKILL.md`、`video-template.html` 进入 sdist/wheel。Python 模块靠现有 `tools.*` | 不改 `uv.lock`，不改 pin，不改 Nix 排除表 |
| 用例 | 与上表同一 owner 的新测试文件 | 实现波次只写、不跑 | 本轮不创建 |

不改：`tools/browser_session.py`、`clawd_server/browser_native.py`、`clawd_server/native.py`、sidecar、`apps/panel/src/react/lib/task-live.ts`、`build/lib/**`、`artifacts/browser-daemon-source/**`、`nix/lib.nix`。Nix `pythonSrc` 已包含 `tools/`，vendor 放这里不必改过滤。不要放到 `docs/` 或 `artifacts/`，那两处不是运行包。

`tools/vendor/.../src` 只放进子进程 PYTHONPATH，不放进父进程 `sys.path`。父进程继续用已安装且未改协议的 `_ipc.request`。vendor 树里的 `browser_harness` 不要被父进程当成 `tools.vendor...browser_harness` 去跑 daemon。

实现波次的用例文件，随 owner 一起批，避免只批包装层：

- `tests/tools/test_browser_daemon_task_grant.py`（IPC 客户端 + 登记簿）
- `tests/tools/test_browser_dialog_harness_identity.py`（dialog）
- `tests/tools/test_browser_use_cli_vendor_path.py`（CLI；已有 `tests/tools/test_browser_use_harness.py` 断言子进程 PYTHONPATH 能跑 `--version`，vendor 前缀不能弄断这条，也不能让它连浏览器）

本轮不创建这些文件。统一验收前不跑。

## 供应链与分发契约

来源只认本仓库已有 sdist，不从网络重下，不读 site-packages。

| 物件 | 值 | 本轮 |
|---|---|---|
| 声明 | `pyproject.toml:141` `browser-harness==0.1.13` | 只读，不改 |
| sdist | `artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz` | 本轮 sha256 复核为 `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3`，与锁和 `PROVENANCE.md` 一致 |
| 许可 | MIT，`Copyright (c) 2026 Browser Use` | `LICENSE` 全文已在 artifact，sha256 `5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631` |
| 上游 | https://github.com/browser-use/browser-harness tag `v0.1.13` | commit `c24e5072ee66f8499bacd663f4f4bcb089bc4492` 是先前来源记录，本轮未联网复核 |
| `cdp-use` | `1.4.5`，MIT，不 vendor | 事件摘录已够 |

vendor 树必须包含 sdist 内这些成员，除注明外保持 sdist 字节：

`__init__.py`、`_ipc.py`、`admin.py`、`auth.py`、`macos.py`、`mcp_cli.py`、`paths.py`、`recorder.py`、`run.py`、`telemetry.py`、`video.py`、`video_render.py`、`SKILL.md`、`video-template.html`、以及打补丁的 `daemon.py`、`helpers.py`。

`NOTICE` 写明：上游 Browser Use、MIT、sdist sha256、tag、改了哪两个文件、clawd 补丁不移除上游版权。根 `LICENSE` 仍是 Nous，不得用它覆盖这份第三方许可，也不得把 Browser Use 许可套到面板或 TUI。

启动前核对：vendor `LICENSE` 版权行仍在；`_ipc.py` sha256 仍是 `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3`；`daemon.py` 不是未打补丁的 `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97`。失败则不回落 site-packages。

分发 owner 只补收录：

- `pyproject.toml` 的 `tools` package-data 加上 vendor 的非 Python 文件。`tools.*` 已能收带 `__init__.py` 的模块。
- `MANIFEST.in` 增加 `recursive-include tools/vendor/browser_harness_0_1_13 *`。仓库已有 `MANIFEST.in`，不能假设新树会自动进 sdist。
- 不改 pin，不改 `uv.lock`。以后若上游出现高于 0.1.13 的版本，先对锁定 sdist 重做字节对照，再换 vendor；不能只改 pin。

验收日才开 wheel/sdist 核对 vendor、LICENSE、NOTICE 和 hash。源码树可导入不算包内已包含。本轮不做。

## 补丁落点

行号是本轮只读位置，实现时以当时文件为准。

`artifacts/.../daemon.py`：

- `Daemon.__init__`（424 行附近）增加任务表：`target → grant`、`session → target`、dialog 身份。不要用 `self.dialog = params` 覆盖掉 session。
- `_record_event`（626–631 行）按上一节写身份。
- `handle`（665 行）在 Windows token 检查之后、`send_raw`（769 行）之前做关闭拒绝和任务授权。`pending_dialog`（739 行）仍返回 `dialog` 键。
- `set_session`（709 行）切到任务页时要授权。不要让任务创建改掉 `self.session`。
- 带授权请求禁止进入 786–803 行的 `attach_first_page` 恢复。
- `serve` finally（850–860 行）的专用页清理旁，只加本进程签发过的 targetId。恢复未排空则跳过。

`helpers.py`（本轮从 sdist 读出，未写入 artifact）：`_send` / `cdp`（52–77 行）保存并自动附带授权；`page_info`（159–169 行）不改读取方式。

产品侧：

- `tools/browser_use_cli.py` `_base_subprocess_env`（599–618 行）现在把 PYTHONPATH 整个换成 site dir。改为 `vendor_src + os.pathsep + site_dir`。site dir 仍要留着，否则 `cdp_use` 找不到。`cloud_autospawn_preamble`（214 行附近）在 `createTarget` 后读 `task_grant_for`。`_register_autospawn_receipt`（461 行）读第四行。
- `tools/browser_task_cdp.py` `harness_call`（444–454 行）现在丢掉 `result` 以外的字段，必须在发出前附上授权，创建时单独返回兄弟字段。
- `tools/browser_dialog_tool.py` `_harness_dialog_block`（66–90 行）今天是硬拒绝。实现时改成身份齐全才应答，不齐仍拒绝。不要删掉“无身份只应答非任务页”的 supervisor 分支。
- `tools/browser_cdp_tool.py` 533–537 行的 `Browser.close` / `disposeBrowserContext` 拒绝保持。harness 创建分支（461 行）改为登记 daemon 返回的授权。

## 来源证据

本轮工具观察：

- sdist sha256 与 `PROVENANCE.md`、依赖边界文档一致。
- `helpers.py` sha256 `5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3`，与 `PROVENANCE.md` 一致。先前未把全文写入 artifact；本轮只在内存里看了调用形，没有另存副本。
- sdist `admin.py` 579–588 行启动命令是 `[sys.executable, "-m", "browser_harness.daemon"]`，环境继承调用方。这就是 PYTHONPATH 前缀能生效、且 argv 检查不能改的原因。
- `cdp-use` 1.4.5 摘录：`JavascriptDialogOpeningEvent` 有 `frameId`，无 `targetId` / `sessionId`。session 在事件回调的单独参数里。

先前文档，本轮未把它们当成新的运行事实：

- `docs/clawd/browser-daemon-dependency-boundary.md`
- `docs/clawd/browser-native-implementation.md`
- `docs/clawd/session-handoff-2026-11-05.md` 第 5 节

## 缺口

- 活浏览器是否发出 `frameId` 未探针。缺 `frameId` 时实现必须失败关闭。不能把“类型里有这个字段”写成 Chrome 已发送，也不能因此改应答条件。
- tag 与 `main` 的逐字节相同是先前记录。本轮只复核了本地 sdist sha256，没有联网。
- sdist 里除 `daemon.py`、`_ipc.py`、`helpers.py`、`LICENSE`、`PKG-INFO`、`package-pyproject.toml` 以外的文件，先前没有逐个对 git。实现时整包以本地 sdist 字节为准，不要改从 GitHub 另拉一份。
- 已在跑的未打补丁 daemon 会占着 socket。探针失败前，任务授权不能算接上。
- daemon 仍只存一个 `self.dialog`，不是 supervisor 那种队列。第二个 dialog 会覆盖第一个。本方案不新做队列，避免改协议。
- 依赖兼容、wheel 收录、真实云 dialog 都还没验。做完上述文件之前，不能说浏览器迁移完成。

## 实现授权之后的顺序

1. vendor owner 复制并打两个文件的补丁，写 NOTICE。
2. 登记簿 owner 加列和内部读取。
3. IPC 客户端 owner 接上附带与创建返回值。
4. CLI、dialog、CDP 工具三个 owner 按冻结字段接线。
5. 分发 owner 补 package-data 和 MANIFEST。
6. 各 owner 写用例，不跑。统一验收再跑。

任何一步都不要修改已安装的 `browser_harness`，不要读生产秘密，不要关用户浏览器。
