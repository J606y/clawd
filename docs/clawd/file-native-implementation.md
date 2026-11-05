# 附件根与符号链接约束

未跑测试、未构建、未发 HTTP、未读 `/root/.hermes` 或官方安装。身份是 grok-4.7，与当前运行时一致，未改配置。`tests/clawd_file_auth_cases` 与 `file-auth-case-preparation.md` 只作静态核对，不把其中的预期失败当成已复现。

认证路拥有 `clawd_server/auth.py` 与 `server.py` 的 origin/认证/`_artifact_roots` 注入。本路不改那些文件。注入 schema 仍是 `window.__CLAWD_ARTIFACT_ROOTS__` 字符串数组。当前 `_artifact_roots` 只解析 home 自身，子目录不解析，拼写仍以 `/` 结尾。本路按它的活返回值对齐 `absPath`，不抢改注入。

## 静态核对

- `bind.py` 把 uploads 绑到 `CLAWD_HOME/uploads`。侧车模块默认根仍是 `/root/hermes-uploads`、`/root/hermes-workspace`、`HERMES_HOME/cron/output`、`HERMES_HOME/images`。未设 `CLAWD_HOME` 时保持这条旧适配器路径，避免改生产行为。
- 设了 `CLAWD_HOME` 后，`_root` / `_artifact` / `_upload` 不再把上述生产根当兜底。配置根必须是当前 `CLAWD_HOME` 的子目录，否则 403，不打开。
- 旧 `_root` 的 `resolve()` 会把绑定后换成家外符号链接的 uploads 当成新根。现在每次打开/上传用 home fd + `openat`/`O_NOFOLLOW` 重验。根目录本身是越家符号链接则拒绝。home 最后一段是合法符号链接时只跟随一次，并用 `/proc/self/fd` 证明打开的是当前 home，不把合法 home 链接机械拒绝。
- 旧 `_artifact` 用未解析 `spec["path"]` 做 `relative_to`。home 是符号链接时，解析后的 `absPath` 对不上词法根。现在解析后的路径必须落在当前 home 的解析根内；真正打开仍从根 fd 走相对段，不再 resolve 之后按不可信词法路径重开。
- `turns.tsx` 原先写死 `/root/hermes-uploads/`。现改为消费 `__CLAWD_ARTIFACT_ROOTS__` 里以 `/uploads/` 结尾的前缀。未注入时才保留旧根，供旧适配器和现有解析测试。注入存在但不是数组，或没有可用项，则不回落生产根，附件引用失败关闭，不假装成功。
- 用例准备文档第 1 条 `origin_allowed` 的 IPv6 Host 属于认证路，本路未改。第 2–6 条的函数已按契约改，但没有运行那些用例。

## 路径约束

- 上传写入用目标目录 fd：`O_CREAT|O_EXCL|O_NOFOLLOW` 建临时文件，`linkat` 提交，失败删除 `.upload-*`。不在 `_safe_path` 之后用 `mkstemp(dir=词法路径)`。
- 下载从根 fd 逐段 `O_NOFOLLOW`。文件或父目录在校验后被换成符号链接时，这次打开失败，不读家外字节。
- `/proc/self/fd` 读不到绝对路径、没有 `O_NOFOLLOW`、或读不到活的 `_artifact_roots`，返回错误，不返回 `ok`。这是附件路既有失败关闭，不是这次新增的功能回退。
- 点文件、`DENY_NAME_RE`、`ARTIFACT_SECRET_RE`、`_is_secret_name` 共用 `_segment_denied`。`threshold_tokens` 仍可上传。`id_rsa`、`config.yaml`、`.env`、`api_key`、`token`、`credentials`、`tokens`、`.npmrc` 在 `CLAWD_HOME` 下拒绝列出、下载和 artifact。剥离文件名之前就拒绝点文件，所以 `.env` 不会被 `strip(" .")` 收成 `env`。
- 未设 `CLAWD_HOME` 时，`_root`、`_open_download`、`_upload`、`_artifact`、`list_dir`、`delete_file` 和生图的旧实现仍在，不套这套家目录重验。真正 clawd 入口不会走到这里，见文末。

## absPath

- 字段保留。新上传的 `absPath` 用活的 `_artifact_roots(CLAWD_HOME)` 前缀加上相对路径，并 `stat` 核对与已打开 fd 是同一 inode。当前注入是词法 home 时，返回词法绝对路径；认证路若把注入规范化成解析路径，同一函数会返回解析路径。对不上就删掉刚提交的文件并失败，不返回一个消费者打不开的成功。
- `_artifact` 同时接受词法 `spec["path"]` 下的绝对路径，和解析后仍严格属于当前 home 根的绝对路径。既有记录若已是 `resolve()` 结果，home 不是符号链接时与注入前缀相同；home 是符号链接且注入仍是词法前缀时，服务端能打开解析路径，但前端前缀比对要等注入规范化后才对得上。
- 正确的绝对路径不当作泄露删掉。助手正文里落在允许根下的路径仍做成附件卡片。`MEDIA:`、Markdown 链接、既有 `/root/` 裸路径扫描都保留。注入根不在 `/root/` 下时，额外按注入前缀扫裸路径。密钥名和点文件仍拒绝。

## 消费者

- `turns.tsx` `uploadRef`：聊天历史上的 `[附件已上传：absPath]` 由 `attachments.ts` 解析后交给这里。本路不改 `attachments.ts`。预览仍走 `/panel-sidecar/files/artifact?path=`，下载仍走 `downloadUrl("uploads", relative)`。文案和动画未改。
- `src/react/lib/artifacts.ts` 与 `src/lib/artifacts.ts` 同步。消费者还有 `agent-dock.tsx`、`image-page.tsx`、`AssistantBody`。它们只调用 `artifactReference`，不另写根。生图预览同样走这条，所以生成结果的 `absPath` 必须对齐活的注入前缀，本路不改这两份文件。
- `composer.tsx` / `live-tail.tsx` 继续传递 sidecar 返回的 `absPath`，本路未改那些文件。

## 列表与生图

`/files`、`/files/roots`、`/files/delete`、`/image/models|history|generate|delete` 都在侧车 `_route` 里，clawd 入口经 `/panel-sidecar/` 代理后用户可达。设了 `CLAWD_HOME` 后不再用 `FILE_ROOTS` 词法路径继续读，也不在 `_open_root_fd` 拒绝后回落生产根。

- `list_dir` 从根 fd 逐段 `O_NOFOLLOW` 打开目标目录，用该 fd 的 `/proc/self/fd` 枚举名字，再用 `lstat(dir_fd=)` 取类型和排序字段。不 `resolve()`、不 `iterdir()`。符号链接不跟随、不展示。枚举或身份证明失败返回错误，不返回空 `entries` 冒充成功。`absPath` 用活的注入前缀，并与目录 fd 的 inode 核对。
- `delete_file` 同样只对已打开目录 fd 做 `unlink`。符号链接和非常规文件拒绝，不按路径删除。
- `list_roots` 只返回通过根 fd 和注入前缀核对的根。生产根被拒绝且没有任何可用根时返回 403，不返回空列表，也不把 `/root/hermes-uploads` 写进响应。
- 生图配置和 `.env` 从 home fd `O_NOFOLLOW` 读普通文件。`.env` 若是符号链接则失败关闭，不跟随。输出写入重新打开的 uploads 根 fd：`image-studio/<day>/` 用 `mkdirat`/`openat`/`linkat`，不 `mkdir` 词法路径。成功的 `absPath` 走 `_abs_for_injection`，对不上就删掉刚提交的文件并失败。预览仍由既有 artifact 打开，不另开根。
- 删除生图文件只接受注入前缀或这次根 fd 的 `/proc` 身份前缀下的 `image-studio/<day>/img_*.ext`，再从根 fd `unlink`。不按记录里的绝对路径重开。
- 未设 `CLAWD_HOME` 时，生图仍调用 `load_runtime(HERMES_HOME, FILE_ROOTS["uploads"]["path"])`，列表仍走旧路径迭代。这是旧适配器分支。

## 未验证与依赖

- 未运行 `tests/clawd_file_auth_cases`、面板 Node 测试、生图测试、构建或上传探针。静态核对不能代替统一测试。
- `server.py:_artifact_roots` 由认证路拥有，本路未改。它解析 home 自身、不解析子目录。上传、列表和生图按它的活返回值对齐 `absPath`。若后缀不再以 `/uploads/`、`/cron/output/` 结尾，这些调用会明确失败，不会猜一个成功路径。
- home 的祖先目录若是符号链接，`O_NOFOLLOW` 会失败关闭。只保证 home 自己最后一段可以是符号链接。这不是原契约要求跟随祖先链接；原用例只要求 home 最后一段可以是符号链接。不能靠去掉 `O_NOFOLLOW` 来放行，因为随后的 `/proc` 身份检查挡不住打开过程中的祖先替换。clawd 入口的 `resolve_home()` 会先解析再写入 `CLAWD_HOME`，所以正常入口的字符串里没有祖先符号链接；解析之后祖先被换成符号链接，则失败关闭。
- `/proc/self/fd` 读不到，或平台没有 `O_NOFOLLOW`，列表、下载、上传和生图写入都失败关闭。这是既有身份证明，不是新功能回退。当前解释器有 `O_NOFOLLOW`。列表枚举依赖同一个 `/proc/self/fd`，失败不退回词法 `iterdir`。
- `sqlite3.connect` 没有 `dir_fd`。生图库在 connect 前用 home fd `O_NOFOLLOW` 打开并核对 inode，但 connect 仍按已证明的路径再开一次。这个窗口没有标准 fd 接口可消掉，不改用跟随符号链接来绕过。
- 未注入 `__CLAWD_ARTIFACT_ROOTS__` 的 clawd 页面会走旧根。那是服务端注入缺失，不是前端成功。注入由认证路拥有。
- 真正 clawd 入口不会误走未设 `CLAWD_HOME` 的旧分支。只读接点：`home.resolve_home` 要求并设置 `CLAWD_HOME`；`bind.prepare_env` 在 `load_sidecar()` 之前再次设置；`PanelServer.start` 顺序是 `prepare_env` → `load_sidecar` → `bind`（改写 `FILE_ROOTS` 并禁用 `sidecar.main`）→ 同进程 `_start_sidecar`。请求在这之后才进入代理。侧车按调用时的环境变量判断，不在导入时把生产根冻成兜底。`native.file_roots_under_home` 只检查绑定后的根仍在 home 内，本路未改。旧分支只留给未绑定的适配器进程。
