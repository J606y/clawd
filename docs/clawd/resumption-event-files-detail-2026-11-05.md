# 事件恢复、附件 fd、IPv6/home 接线复核 — 2026-11-05

复核时间：2026-11-05T05:51:46+0800。本文只记录对工作树源码的静态阅读和标准库 SHA-256。不是通过声明、验收报告或上线许可。

未运行：测试、构建、`import` 产品模块、产品 CLI、HTTP/SSE 探针、浏览器、网络。未改 `server._proxy`、sidecar、chat 或测试。未删除文件。未读生产秘密。未访问 `/usr/local/lib/hermes-agent`、原面板 `/root/hermes-workspace/hermes-panel` 或线上目录。`build/lib/` 只做了与当前 `clawd_server/server.py` 的字节对比，不当入口。

旧准备文档里的“按当前代码预期会失败”写于修复落地之前。不能把那些句子当成这次的红结果，也不能把源码里已有的早退写成测试已通过。

## 仓库身份

| 树 | 分支 | HEAD | 含义 |
| --- | --- | --- | --- |
| `/root/hermes-workspace/clawd` | `clawd` | `c314285f077e91937c995d419b9fa40af9eb4981` | 基线。下面的文件哈希是工作树字节，不是该提交的 blob |
| `/root/hermes-workspace/clawd/apps/panel` | `hk` | `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29` | 嵌套面板。不要当成原面板主检出 |

行号是 Python `splitlines()` 的 1 起算逻辑行，与阅读器一致。这些文件是 LF。片段哈希是该行段 `splitlines(keepends=True)` 的 SHA-256；表内写前 16 位，完整值在文末。文件哈希是原始字节 SHA-256。

## 1. 运行身份错配：状态体立即退出

真实入口不是网关自己发明的 `session_id` 查询。浏览器只按 `run_id` 订阅和轮询。

```text
shell.tsx:128 useResumeRuns()
  -> stream.ts:87 resumeAll()          全应用一次
  -> runs.ts:877
  -> runs.ts:502 consume() 内 follow()
chat 可见会话 effect chat-page.tsx:601-622
  -> runs.ts:645 resubscribe()
  -> runs.ts:708 再次 follow()
getSse / api.runStatus
  -> api.ts API_BASE = panel-api/
  -> server.py:158 _proxy_api
  -> server.py:243 _authed -> origin_allowed
  -> server.py:289 _proxy
  -> gateway GET /v1/runs/{run_id} 与 /events
```

Vite 别名 `"@"` 指向 `apps/panel/src/react`（`vite.config.ts:62`）。事件入口是 react 副本。`src/lib/runs.ts`、`sse.ts`、`artifacts.ts` 与 react 副本字节相同。`src/lib/api.ts` 不同，差异在消息来源字段，不是 `runStatus`；它不是这条别名的入口。

身份函数：`runs.ts:113-121` `foreignIdentity`。载荷里出现的非空 `run_id` / `session_id` 必须等于当前订阅。缺字段不是错配。不读 profile，也不给事件路由补 `session_id`。

状态轮询错配：`follow` `runs.ts:584-593`。`api.runStatus(run.runId)` 之后，若 `foreignIdentity(run, st)` 为真，调用 `dropFollow` 并 `return`。该分支不读 `status`、`error` 或正文，不调用 `finishFromStatus`，不写 `completed`，不改 `runId`，不置 `streamGone`，不清 `thinking`。

`consume` 的 `finally`（`runs.ts:522-528`）仍会调用 `finish`。`finish`（`371-372`）在 `isLive(run.status)` 时直接返回。`isLive`（`239`）只含 `starting` 和 `running`。错配退出时状态仍是二者之一，所以不会发 `RUN_FINISHED`，不会清存储，不会改思考。

页面显示：`chat-page.tsx:624-636` 只在 `streamGone` 或 `replayTruncated` 时把视图里的 `thinking` 置空。错配路径不设这两个标志。

事件帧是另一条规则，不是“立即退出”。`follow` `558-559` 对非错配帧才 `acceptFollow`；`applyRunEvent` `753-754` 对错配帧直接返回，不改写正文、思考或终态，但订阅循环继续。立即退出只发生在状态体错配。

网关侧是所有权，不是这次的错配早退：

- `_handle_get_run` / `_handle_run_events` 走 `_request_owns_run`（`api_server_runs.py:1264-1277`）。不属于调用方 scope 时返回现有 404 `run_not_found`，不返回另一会话的状态体。
- 拥有权通过后，`run_projection`（`api_server_authority_runs.py:95-96`）带 `run_id` 和 `session_id`。
- 没有 admission 投影时回落 `_run_statuses`。`_set_run_status`（`351-357`）总会写入 `run_id`。创建时 `906-907` 写入 `session_id`。缺 `session_id` 的旧记录按前端契约仍可结算，不是新的拒绝条件。

所以：客户端能对“200 且身份字段冲突”的状态体立即退出；网关正常路径返回的是该 `run_id` 自己的记录。跨 profile 仍没有单独错误码。不要把 404 说成错配退出已在服务端实现。

## 2. 当前身份三次 250ms 重订

常量：`runs.ts:104-106`。`MISMATCH_RESUB_ATTEMPTS = 3`，`MISMATCH_RESUB_MS = 250`。暂停文案不把回合标成完成。

页面只在可见且槽位未隐藏时调用（`chat-page.tsx:601-622`）。条件是 `followDropped`、已有 `runId`、`isActive`，且状态为 `starting` 或 `running`。`isActive`（`330-332`）与 `isLive` 同一对状态，并排除 `locallyReleased`。`queued`、`waiting_for_approval`、`stopping` 不在 `follow` 循环里，页面也不会为它们重订。这是同一道门，不是页面漏接了仍在 `follow` 里的状态。

`resubscribe`（`645-663`）绑定当前 `sessionId`、`runId`、`startedAt`，以及该 run 的 `idempotencyKey`（`695-708` 传入）。页面切走、槽位隐藏、中止信号、终态、run 被替换或幂等键变化则停止。已有的 follow 使用该 run 自己的 `AbortSignal`（`ctrls`），不因 effect 重跑拆掉。

`boundedResubscribe`（`703-717`）每次先等 250ms，再 `follow`。次数在 follow 之前加一，最多 3。三次后仍错配且仍在跑，只写 `MISMATCH_NOTICE`（`720-724`），已有 `error` 不覆盖，思考和正文不删。刷新仍走原来的 `resumeAll`，不另造 `session_id` 查询。

重订 follow 若收到匹配终态，会补一次 `finish`（`709-711`），因为原来的 `consume` 已经退出。错配体不会走到这里。

这段页面 effect 没有被 Node 用例 import。`tests/clawd_event_native` 直接调用 `runs.resubscribe`。页面门禁只做了静态接线核对。

## 3. SSE：iter_any / no-cache / no-buffer

两条头都在，代理读取方式只在面板转发层。

网关产生：

- `api_server.py:1337-1341` `_sse_headers`：`Content-Type: text/event-stream`，`Cache-Control: no-cache`，`X-Accel-Buffering: no`。
- `api_server_runs.py:1308-1335` `_handle_run_events` 用这个头构造 `StreamResponse`。游标仍是 `Last-Event-ID` 优先于 `last_seq`。没有 `session_id` 参数。

面板转发：

- `server.py:40-42` `_event_stream` 只认去掉参数后的 `text/event-stream`。
- `server.py:313-320`：是 SSE 时覆盖 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`，并用 `upstream.content.iter_any()`。非 SSE 仍是 `iter_chunked(65536)`，不加这两个头。
- `Content-Length` 在 `_HOP`（`server.py:21-24`）里，不转发。
- 客户端超时 `server.py:153`：`total=None`，`sock_connect=5`，`sock_read=None`。这是既有设置，本次未改 `_proxy`。

浏览器 `sse.ts:13-24` 用 `fetch` + `Accept: text/event-stream`，URL 基于 `panel-api/`。请求先过 `_authed`。IPv6 Host 对不上时，事件流在 `iter_any` 之前就是 403，不会进入转发。

`iter_any` 只说明面板进程不等满 64KiB 或上游 EOF 再交出已收到的字节。内核写缓冲、反向代理和浏览器没有探针。不能声称缓冲问题已消失。

## 4. IPv6 authority 与 home 解析的消费者

IPv6 修复在 `clawd_server/auth.py`，不是把内部 API/侧车改成双栈监听。

解析：`_netloc_ok` / `_authority_from_netloc`（`182-243`）按 authority 取主机名。`[::1]` 与 `[::1]:port` 得到 `::1`，不再 `Host.split(":")[0]`。userinfo、多值头、非法 authority 失败关闭。`origin_allowed`（`309-374`）使用这个结果。

消费者：

- `PanelServer.start` `server.py:129` 调用 `loopback_host`（`28-33`）。允许 `127.0.0.1`、`localhost`、`::1`，其它拒绝。面板对外监听可以是 `::1`。
- `_proxy_api` / `_proxy_sidecar` 都先 `_authed`（`242-243`），再转发。事件流和附件代理都走这道门。
- `_renew`（`264-265`）同样先 `origin_allowed`。
- 内部 API 仍绑 `127.0.0.1`（`_start_api` `180`）。侧车仍绑 `127.0.0.1`（`_start_sidecar` `223`）。`loopback_host` 接受 `::1` 不能证明内部上游已听 IPv6。

home 解析：

- `home.py:38-63` `resolve_home` 要求 `CLAWD_HOME`，`resolve()` 后写入 `CLAWD_HOME` 和 `HERMES_HOME`。拒绝生产 home 和分裂 home。不回落 `~/.hermes`。
- 面板入口 `server.py:367-369` `run_serve` 先 `resolve_home` 再 `serve`。CLI `cli_entry.py:58-61` 同样先解析。`__main__.py:94` 转入 `run_serve`。
- `PanelServer.start` 顺序：`prepare_env`（`142`，此时 home 已解析）→ `assert_core_home` → 启动 API → 再次 `prepare_env`（`146`）→ `load_sidecar`（`149`）→ `bind`（`150`）→ `_start_sidecar`（`152`）。请求在这之后才进代理。
- `bind.py:23-26` 在导入侧车前写 `CLAWD_HOME`。`bind.py:83-89` 把 `FILE_ROOTS` 改到该 home 下的 `uploads`、`workspace`、`cron/output`、`images`。
- `_artifact_roots`（`server.py:69-109`）只解析 home 本身，再词法拼接子目录，每项以 `/` 结尾。子目录不解析。生产或不可用 home 返回空列表，不回落 `/root/hermes-uploads`。
- 注入消费者：`server.py:346-353` 在 `index.html` 的 `</head>` 前写入 `window.__CLAWD_ARTIFACT_ROOTS__`。侧车 `_injected_prefix`（`1074-1089`）调用同一个 `_artifact_roots`。前端 `artifacts.ts:21-33` 读取注入；注入存在但不是可用数组时不回落生产根。

`build/lib/clawd_server/server.py` 的 SHA-256 是 `fb35fe80dfd26a21206e392219f231ad1002b33c66584919b9be89e2ced1a6ee`，与当前 `ea3534ab…` 不同。不要用它核对行号。本轮禁止构建，没有刷新它。

## 5. 附件 fd、O_NOFOLLOW、absPath

`CLAWD_HOME` 已设置时，列表、上传、下载、artifact 和生图不再用生产根兜底。未设置时旧词法分支仍在；真正 clawd 入口在导入前设置了该变量，请求不会走到旧分支。旧分支只留给未绑定的适配器进程。

路由（侧车 `Handler._dispatch` / `_route`）：

| 方法路径 | 行 | 转到 |
| --- | --- | --- |
| `GET /files/artifact` | `5825-5826` | `_artifact` |
| `GET /files/download` | `5828-5831` | `_download` → `_open_download` |
| `POST /files/upload` | `5833-5834` | `_upload` |
| `GET /files/roots` | `6015-6016` | `list_roots` |
| `GET /files` | `6017-6018` | `list_dir` |
| `POST /files/delete` | `6019-6024` | `delete_file` |
| `GET /image/history` | `6062-6063` | `image_history_view` |
| `POST /image/generate` | `6064-6065` | `image_generate_view` |
| `POST /image/delete` | `6066-6067` | `image_delete_view` |

面板外层是 `/panel-sidecar/{tail}`（`server.py:159`），先过 `_authed`。

`CLAWD_HOME` 分支：

- `list_dir` `1519-1521` → `_list_dir_home` `1380-1441`。根 fd 逐段 `O_NOFOLLOW`。名字来自该 fd 的 `/proc/self/fd`（`_list_names` `1345-1352`）。类型用 `lstat(dir_fd=)`。符号链接不跟随、不展示。枚举失败返回错误，不返回空 `entries` 冒充成功。
- 上传 `5914-5917` → `_upload_home` `5669-5755`。目标目录 fd 上 `O_CREAT|O_EXCL|O_NOFOLLOW` 建 `.upload-*`，`linkat` 提交。不在 `_safe_path` 之后用 `mkstemp(dir=词法路径)`。点文件和密钥名在 `strip` 之前拒绝（`_reject_raw_upload_name` `1162-1171`）。
- 下载 `_open_download` 在有 home 时走 `_open_download_home` `1232-1274`，从根 fd 逐段 `O_NOFOLLOW`。
- artifact `5852-5865` 在有 home 时走 `_match_home_artifact`，再 `_download`。真正打开仍是根 fd，不按解析后的词法路径重开。
- 没有 `O_NOFOLLOW`、读不到 `/proc/self/fd`、或注入前缀对不上，失败关闭，不返回 `ok`。

`absPath` 字段保留：

- 上传返回 `absPath`（`5755`），来自 `_abs_for_injection`（`1092-1106`）。前缀是活的 `_artifact_roots`，再 `stat` 核对与已打开 fd 同一 inode。对不上就删掉刚提交的文件并失败。
- 列表目录的 `absPath` 来自 `_dir_abs_for_injection`（`1357-1369`），`follow_symlinks=False`，并核对目录 inode。条目本身没有 `absPath`；文件页用目录 `absPath` 加名字。
- 生图成功路径走 `publish` → `_abs_for_injection`（`image_generate_view` `5621-5629`，`image_studio.save_image` `455-473`）。记录行仍输出 `absPath`（`image_studio.py:620`）。
- 前端：`turns.tsx:18-25` `uploadRef` 只接受注入数组里以 `/uploads/` 结尾的前缀。预览仍是 `/panel-sidecar/files/artifact?path=`。`artifacts.ts` `artifactReference` 不另写根。`composer.tsx` / `live-tail.tsx` 传递侧车返回的 `absPath`。本轮未改这些消费者。

home 最后一段可以被跟随一次（`_open_home_fd` `982-1011`）。祖先段是 `O_NOFOLLOW`，换成符号链接则失败关闭。`/proc` 身份只拒绝生产文本，没有和 `Path(CLAWD_HOME).resolve()` 或绑定时的 inode 比较。实现说明里“证明打开的是当前 home”比这段代码强。正常入口的 `resolve_home` 已经把字符串解析成真实路径；之后若有人替换 home 最后一段为家外符号链接，这次打开会跟随，后续根检查相对于跟随到的目录。这是同用户、同路径项的残留，不是远程选路。没有复现。

## 6. `panel_image_studio.db` 不是 `state.db`

不要把这段重开说成会话库。

- 路径：`_image_runtime` `5576`，`db_path = identity / "panel_image_studio.db"`。`identity` 是当时 home fd 的 `/proc/self/fd`。文件名固定。请求体不能选路径。
- 表：`image_studio.py:592-604` 的 `generations`。不是 `sessions` / `messages`。
- 会话编辑副本仍是另一条路由：`hermes_panel_sidecar.py:5993` `HERMES_HOME / "state.db"`。那不是生图库，也不在本域改动里。
- `_store`（`image_studio.py:563-606`，片段前 16 位 `42396f04e0b1f9d0`，与先前存储复核指纹相同）在 `parent_fd` 上对固定文件名 `O_NOFOLLOW` 打开，要求普通文件，并与 `os.stat(db_path, follow_symlinks=False)` 的 inode 一致，然后关掉 fd。
- 下一行 `sqlite3.connect(db_path)` 按路径再开。`BEGIN IMMEDIATE` 在 connect 之后，锁不住刚核对的 inode。标准库没有 `dir_fd` 连接参数。不改成跟随符号链接来消窗口。

同 home 控制前提，缺一则不是这条窗口：

1. clawd 入口已 `resolve_home`，并把解析后的路径写入 `CLAWD_HOME`；侧车按调用时的环境变量判断，不在导入时冻住生产根。
2. `bind` 已把 `FILE_ROOTS` 改到该 home 之下。生产根在有 `CLAWD_HOME` 时被拒绝。
3. 打开 home 时祖先段 `O_NOFOLLOW`；最后一段允许跟随一次，但 `/proc` 身份不能是生产路径。
4. 库路径来自这次 `/proc` 身份加固定名 `panel_image_studio.db`，不是请求参数，也不是 `state.db`。
5. `image_history_view` / `image_generate_view` / `image_delete_view` 在 connect 前重新打开 home fd，并作为 `parent_fd` 传入。新 home 与 `db_path` 的 inode 对不上时，`_store` 在 connect 前 403。
6. 绕过只存在于 inode 比对通过并 `os.close(fd)` 之后、`sqlite3.connect` 之前，且攻击者能替换同一个 home 里的这个固定文件。下一请求会重新打开 home fd。这不是跨 home，也不是每次请求都落到别的库。
7. 同用户本来就能写自己的 home。不要升格成远程高危，也不要并进 `state.db` 漏洞数。安全归并另有 owner。

`tests/` 下没有 `panel_image_studio` 命中。这个窗口没有对应用例。

## 7. 用例覆盖

这些文件存在。本轮没有运行，没有收集通过数。

事件：

| 文件 | 覆盖 | 未覆盖 |
| --- | --- | --- |
| `tests/clawd_event_recovery_cases/frontend-event-recovery.test.mjs` | vite 加载 react `runs.ts` 与 `sse.ts`。错配状态轮询不得 `completed`；错配 `run_id` 事件不得写入正文；断线不是完成；`last_seq`；会话切换 | 不加载 `chat-page.tsx`，不证明页面 effect |
| `tests/clawd_event_recovery_cases/test_frontend_event_recovery.py` | 上述 Node 文件的 pytest 入口，超时 180 秒 | 包装本身不是产品超时 |
| `tests/clawd_event_recovery_cases/test_run_events_handler.py` | 真实 `_handle_run_events`，记录型 `StreamResponse`，不 `listen` | 不构造 `PanelServer`，不能代替 `_proxy` 的 `iter_any` |
| `tests/clawd_event_recovery_cases/test_run_stream_cursor.py` | `_RunStream`、`terminal_run_status`、`SSE_CURSOR` | 不测面板转发 |
| `tests/clawd_event_native/frontend-subscription-identity.test.mjs` | 错配不清思考；同一 run/代次最多重订；错代次和中止不采用错配体；缺身份字段仍按旧行为结算 | 直接调 `resubscribe`，不挂 React effect |
| `tests/clawd_event_native/test_frontend_subscription_identity.py` | Node 包装 | 不替代 recovery 目录 |

静态对照，不是执行结果：recovery 契约要求错配状态体不得把订阅 run 写成 `completed`、不得改 `runId`、不得改已有正文。当前 `follow` 在 `finishFromStatus` 之前返回。native 契约要求错配事件保留思考，并且重订绑定同一 `runId`/`startedAt`、最多 3 次、先等 250ms。当前常量和 `boundedResubscribe` 是这个形状。是否真的在 10 秒内返回，没有跑。

附件与来源：

| 文件 | 覆盖 |
| --- | --- |
| `tests/clawd_auth_native/test_origin_and_roots.py` | 括号 IPv6、压缩与大小写、显式冲突端口、省略端口、userinfo、多值头、`_artifact_roots` 只跟解析后的 home |
| `tests/clawd_file_auth_cases/test_origin_and_cookie.py` | 同一 IPv6 Host 契约，外加 Referer、Cookie、Secure |
| `tests/clawd_file_auth_cases/test_symlink_and_race.py` | 绑定后根替换、上传目录替换、校验后文件/目录替换 |
| `tests/clawd_file_auth_cases/test_abspath_consumers.py` | 上传/列表 `absPath` 与 `_artifact_roots`、`_artifact` 回读；符号链接形态的 `CLAWD_HOME` |
| `tests/clawd_file_auth_cases/test_secret_dotfiles.py` | 点文件和密钥名在剥离前拒绝 |
| `tests/clawd_file_auth_cases/test_encoded_paths.py` | 一次解码、双重编码、上传 `dir` |
| `tests/clawd_file_auth_cases/test_upload_limits.py` | 413 在读 body 前、411、中断删 `.upload-*` |
| `tests/clawd_file_auth_cases/test_download_auth.py` | 无 Cookie 401、坏 Origin 403、侧车钥与越根 |
| `tests/clawd_file_auth_cases/test_status_codes.py` | 侧车 403/409 原样转发 |

`file-auth-case-preparation.md` 第 1–6 条描述的是改前缺陷。当前源码里，有 `CLAWD_HOME` 时对应函数已改成 authority 解析、根 fd、`O_NOFOLLOW`、剥离前拒绝和注入前缀对齐。那份准备文档的“预期失败”不是本次红结果。用例没有跑，不能写成已绿。

明确没有用例的实现：

- `image_studio.py` 的 fd 写入和 `panel_image_studio.db` connect 窗口。
- `chat-page.tsx:601-622` 的可见会话 effect。
- 面板 `_proxy` 的 `iter_any` 与下游是否仍缓冲。handler 用例不能代替。
- home 最后一段在 `resolve_home` 之后被换成符号链接。原用例只要求最后一段可以是合法链接，不要求钉住绑定时的 inode。

## 8. 集中验收命令

未执行。准备成功不是通过。空结果、未跑、阻塞不能写成 0。

总入口以 `docs/clawd/final-acceptance-matrix.md` 为准。无参数退出 2。`run` 必须同时有 `--run` 和 `--implementation-complete`，且完成清单里全部 `blocking` 项为 `implemented`，否则不启动阶段。`implemented` 不是测试通过。

```text
python3 scripts/clawd_acceptance/accept.py
python3 scripts/clawd_acceptance/accept.py prepare
python3 scripts/clawd_acceptance/accept.py run --run --implementation-complete
python3 scripts/clawd_acceptance/accept.py report
```

核心只发现一次 `tests`，不要把子目录再传一遍，否则会重复执行：

```text
scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> tests
```

这次发现必须能在日志里看到，缺一则不能把核心记为通过：

- `tests/clawd_event_recovery_cases`
- `tests/clawd_event_native`
- `tests/clawd_auth_native`
- `tests/clawd_file_auth_cases`

`run_tests.sh` 的 `env -i` 转发 `HOME`，不转发 `CLAWD_HOME`。验收应把 `HOME` 设到隔离账号目录，数据在其下 `.clawd`。不要借生产 home，不读生产凭据。

若集成人要先看本域切片，而不是代替上面的一次发现，命令是：

```text
scripts/run_tests.sh -j 1 --file-timeout 180 tests/clawd_event_recovery_cases tests/clawd_event_native tests/clawd_auth_native tests/clawd_file_auth_cases
```

Node 文件由同目录 pytest 包装调用。需要已有 `node` 与 `apps/panel/node_modules/vite`。不执行 npm install。包装超时 180 秒只防误监听，不是产品超时。

退出码沿用验收编排：全部必需检查通过才 0；断言失败 1；阻塞、未知、未跑或空结果 2。本文件不产生这些退出码。

静态首页验收不能把注入后的 `index.html` 与原始文件逐字节比较。`__CLAWD_ARTIFACT_ROOTS__` 会合法改变 HTML。附件验收要核对注入前缀和 `absPath` 是否同一套拼写，而不是只看 HTTP 200。

## 9. 尚缺，按文件

不是本轮要改的产品缺口，列出以免收口时误勾：

| 文件 | 现状 |
| --- | --- |
| `gateway/platforms/api_server_runs.py` `_handle_run_events` | 没有 `session_id` 查询。按契约不新造。无主 run 仍是 404。跨 profile 不由本路猜测 |
| `apps/panel/sidecar/image_studio.py` `_store` `563-606` | connect 前有 inode 核对，connect 仍按路径重开。没有 `dir_fd` 接口可消。不是 `state.db`，也没有用例 |
| `apps/panel/sidecar/hermes_panel_sidecar.py` `_open_home_fd` `982-1011` | 最后一段跟随一次后，只拒绝生产文本，不比对解析后的 `CLAWD_HOME` inode。与“证明是当前 home”的说明不完全一致 |
| `apps/panel/src/react/pages/chat/chat-page.tsx` `601-622` | 重订调用在。用例没有加载这个 effect |
| `clawd_server/server.py` `_proxy` `313-320` | `iter_any` 与两个头在。下游缓冲未测。不要改这个函数来“补测” |
| `build/lib/clawd_server/server.py` | 旧副本，哈希不同。禁止用构建刷新来制造一致 |
| 未设置 `CLAWD_HOME` 的侧车旧分支 | `list_dir` / `_upload` / 生图仍有词法实现。clawd 的 `start` 在导入前设置了变量。不要删旧分支来假装未绑定适配器已迁移 |

已在工作树、待统一验收、本轮不重复改：错配状态体早退、3 次 250ms 重订、SSE `iter_any` 与 no-cache/no-buffer 头、IPv6 authority 解析及其 `_authed`/`_renew` 消费者、`resolve_home` 与 `_artifact_roots` 消费者、有 `CLAWD_HOME` 时的列表/上传/生图根 fd 与 `O_NOFOLLOW`、`absPath` 字段。

## 10. 当前哈希

片段前 16 位用于和旧指纹对照。完整 SHA-256 在同一行后部。

| 对象 | 行 | 片段 SHA-256 |
| --- | --- | --- |
| `foreignIdentity` 与重订常量 | `runs.ts:104-121` | `1de4e69cf1acda21` / `1de4e69cf1acda217bdcecbd508f8860b5c40fde95c9fecfc94cc3277978a466` |
| `isLive` | `runs.ts:239` | `ccf4ca9749f238be` / `ccf4ca9749f238be2fbf9563f6fd3c925c18135d6a517f8bfb86d40130350fc7` |
| `follow` 错配早退所在函数 | `runs.ts:534-639` | `61d5b715b3563d89` / `61d5b715b3563d8917568d7635991a2bc39d40649620100084fb7b0a794e1012` |
| `resubscribe` 至 `noteMismatchSilence` | `runs.ts:645-725` | `1649f86955f1ac1c` / `1649f86955f1ac1c939f7de565cc2926dbf47bb45265d7518fe59edd5a70f604` |
| 页面重订 effect | `chat-page.tsx:601-622` | `d52c8062b325ff99` / `d52c8062b325ff99b9aaa29d0bbaae7324e6ae9ccb99668faee82afec62f5784` |
| `_artifact_roots` | `server.py:69-109` | `1af6ce6c456bd77b` / `1af6ce6c456bd77bc74675e4edbefbe763fea386df60f987fe3748367665f5be` |
| `_proxy` | `server.py:289-326` | `6058a4a98ffae1b7` / `6058a4a98ffae1b7a932034fd4e8f99d02bdd7ae109bdef34b35e01364ea1a8a` |
| `origin_allowed` | `auth.py:309-374` | `58b22a2e4543a229` / `58b22a2e4543a2299362431843c51e500ca001834e58701b5886fddc042a2216` |
| IPv6 authority 解析 | `auth.py:182-243` | `de3cc2f7ae3dd009` / `de3cc2f7ae3dd009cb6ca7df6574c72f020606a2cad8d8b7ceb06adafe208bb8` |
| `resolve_home` | `home.py:38-63` | `d32545f6575f4532` / `d32545f6575f45322b4a11dd0f37561534711e8623e0a0e48c1577ffc687ae0c` |
| `_sse_headers` | `api_server.py:1337-1345` | `292d034458f3f3ef` / `292d034458f3f3efbb058c0800f0bf70606d82a70e6deb3fddf3fdbc3105adf0` |
| `_handle_run_events` 头与游标 | `api_server_runs.py:1308-1335` | `e1dba4abe88c7a69` / `e1dba4abe88c7a692ef78f4dd079d5d42ebf8cc85b4708b6cb486a3f7d248ac7` |
| `run_projection` 身份字段 | `api_server_authority_runs.py:68-97` | `fbcdc732ddfde43a` / `fbcdc732ddfde43a56f1d74394026055fee9ca61e0fa713a7fbe53ee6190b2f7` |
| `_open_home_fd` | `hermes_panel_sidecar.py:982-1014` | `fc813aa84dda9f83` / `fc813aa84dda9f839e32aebbf92aae20acac25bb51ed3cab309c6714b7c5a312` |
| `_open_root_fd` 与注入前缀 | `hermes_panel_sidecar.py:1041-1090` | `4d7fefeae9f3aaf8` / `4d7fefeae9f3aaf8eaa06e3ae582f6e7e9e34a8a30c6a6ae941ead6b1d9aa1d2` |
| `_list_dir_home` | `hermes_panel_sidecar.py:1380-1441` | `be74ccceb1475af3` / `be74ccceb1475af398acdbb12963c763c27cc27e7abfa79778d7e8ca72dd7993` |
| `_image_runtime` | `hermes_panel_sidecar.py:5552-5586` | `882da460f6c4b9e7` / `882da460f6c4b9e78e26906b5f866bc25f8ab9949cf37199bbfd48204ce7dfb4` |
| `_upload_home` | `hermes_panel_sidecar.py:5669-5755` | `e3a8be4523383306` / `e3a8be4523383306fe0bc303cf96ec5383276151bcc1bf20e71e36ddfe31d681` |
| `_store` | `image_studio.py:563-606` | `42396f04e0b1f9d0` / `42396f04e0b1f9d0b264f0a8c6291429bead887ce5a9766caa5ab892a599c3eb` |

文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `apps/panel/src/react/lib/runs.ts` | `7b4cb82eb2bbb42e3dec21687c3daabe359ab2e8ae81e4ee8b7c8f2c26e3a712` |
| `apps/panel/src/lib/runs.ts` | 同上 |
| `apps/panel/src/react/pages/chat/chat-page.tsx` | `a209be6367d8f1d679fabf59300b51e5c09dbfbd9d1bbcbf8c69ee3cf750d029` |
| `apps/panel/src/react/lib/sse.ts` | `3ad74211053b71801f4a1d07694976001320e63a986cf3f4f6c7c1e40b50dbf2` |
| `apps/panel/src/lib/sse.ts` | 同上 |
| `apps/panel/src/react/lib/artifacts.ts` | `b2cd97433289f06c81e4d9abb0ba1ad386fe77f926f65c43bc07efc0096dcbfd` |
| `apps/panel/src/lib/artifacts.ts` | 同上 |
| `apps/panel/src/react/lib/api.ts` | `8f5d636859312304f1f3efae64f5cbd136f0a6a308364592f7d4672fc816df00` |
| `clawd_server/server.py` | `ea3534ab92e054fe38c79f6533b8797c86dea07facdfb09d3badc5a060dd03ab` |
| `clawd_server/auth.py` | `db2dc7f169dbe4e4da6e9c1b9f1b8858c9ca2ecee3577e515e1b2c2d4b9e96ca` |
| `clawd_server/home.py` | `3bef1ec022dc2e3756539f1ec2d5c04713b0352e9f46959c772092e2dd3e14cd` |
| `clawd_server/bind.py` | `a0cfe9432fbd526d11850105d040d0d0cf1e85f9ceabada2aa57ff04f20d101b` |
| `gateway/platforms/api_server.py` | `b95284ed570ea9f3ed4f0218af12eff91891373a32a93f60f89658b43ba839de` |
| `gateway/platforms/api_server_runs.py` | `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b` |
| `gateway/platforms/api_server_authority_runs.py` | `41a510fb9354776ba57ac57d02c0fa33ae974b192b5ffb783dc97e67f9676520` |
| `apps/panel/sidecar/hermes_panel_sidecar.py` | `c8e2b0890e489e2f5c96183068f06a5d923ae67356f6fdfa2fba751288fd9bc8` |
| `apps/panel/sidecar/image_studio.py` | `8d658411e827ee2adee269d88760db8d3743c6ee6504c554c93d0d89389e439d` |
