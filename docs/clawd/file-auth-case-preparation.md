# 文件与鉴权边界用例准备

本次没有跑测试、构建、HTTP 探针或网络。没有访问 `/root/.hermes`、官方安装或原面板数据。临时目录只在测试函数运行时由 pytest `tmp_path` 创建。身份是 grok-4.7，未改配置。下面的预期是用例源码里的断言，不是运行结果。ready 不是通过。

## 接口依赖

隔离与绑定，签名来自当前源码和 `tests/clawd_file_auth_cases/helpers.py` 的实际导入：

- `clawd_server.bind.prepare_env(home: Path, api_base: str) -> None`
- `clawd_server.bind.load_sidecar()`：导入 `sidecar.hermes_panel_sidecar`
- `clawd_server.bind.clawd_command(home: Path) -> str`
- `clawd_server.bind.bind(mod, home: Path, command: str) -> None`。重写 `FILE_ROOTS`，安装 `DENY_PREFIXES`，再调用 `file_roots_under_home`
- `clawd_server.native.file_roots_under_home(mod, home: Path) -> None`。任一 `FILE_ROOTS` 解析后不在 `home` 下，或指向 `/root/.hermes` / `/usr/local/lib/hermes-agent`，抛 `RuntimeError`

`bind` 写入的根，测试通过 `isolated_sidecar` 间接使用，不另猜：

- `uploads`：`home/uploads`，`writable=True`，`create=True`
- `workspace`：`home/workspace`，`writable=True`，`create=True`
- `cron`：`home/cron/output`，`writable=False`，`create=False`
- `images`：`home/images`，`writable=False`，`create=False`
- `DENY_PREFIXES`：`/etc`、`/root/.ssh`、`home/.env`、`/root/.hermes`、系统安装目录

设了 `CLAWD_HOME` 时，侧车路径函数走家目录 fd。未设时仍有旧词法分支。本目录夹具在 `bind` 前 `prepare_env`，所以这些用例打的是家目录分支：

- `sidecar.hermes_panel_sidecar._root(root_id: str) -> tuple[Path, dict]`
- `_safe_path(root_id: str, rel: str) -> tuple[Path, Path, dict]`
- `_open_root_fd(root_id: str) -> tuple[int, Path, dict]`。从 home fd `openat` 走到根；越出 home 的符号链接拒绝
- `_require_nofollow() -> int`。没有 `O_NOFOLLOW` 则 `ApiError(500, "nofollow_unavailable")`
- `_open_download(root_id: str, rel: str)` / `_open_download_home(...)`。家目录分支从根 fd 逐段 `O_NOFOLLOW` 打开，不在 `resolve` 之后按词法路径重开
- `list_dir(root_id: str, rel: str) -> dict`。家目录分支为 `_list_dir_home`
- `Handler._dispatch(self, method: str) -> None`。`unquote` 一次，再分到 download / upload / artifact
- `Handler._upload(self, query: dict[str, str]) -> dict`。家目录分支转 `_upload_home(handler, query)`
- `_upload_home`：目录 fd，`O_CREAT|O_EXCL|O_NOFOLLOW` 建临时文件。`writable` 为假则 `403 read_only`
- `Handler._artifact(self, absolute: str, preview: bool) -> None`。家目录分支先 `_match_home_artifact`，再从根 fd 下载
- `_segment_denied(name: str) -> bool` / `_is_secret_name(name: str) -> bool` / `_reject_raw_upload_name(raw: str) -> str`。剥离前拒绝点文件和密钥名
- `MAX_UPLOAD_BYTES = 50 * 1024 * 1024`

面板鉴权：

- `clawd_server.auth.origin_allowed(method: str, headers, peer: str | None) -> bool`。Host 按 authority 解析，不按第一个冒号切。当前源码文档字符串写明 `[::1]` / `[::1]:port` 应得到 hostname `::1`。这是源码注释，不是运行通过。
- `add_user(home, user, password) -> None` / `credential_hash(user, password) -> str` / `cookie_value(header) -> str` / `hash_allowed(presented, allowed) -> bool`
- `COOKIE = "hp_auth"`，`RENEW_MAX_AGE = 30 * 24 * 3600`
- `clawd_server.server.PanelServer.__init__(self, home: Path, static_dir: Path | None)`
- `PanelServer._authed(self, request) -> web.Response | None`。Origin 失败 403 `origin_rejected`；Cookie 失败 401 `unauthorized`
- `PanelServer._renew(self, request) -> web.Response`。成功 204，`httponly=True`，`samesite="Strict"`，`secure` 仅当 `request.scheme == "https"`
- `PanelServer._proxy_sidecar(self, request) -> web.StreamResponse`。先 `_authed`，再把上游状态原样写回
- `clawd_server.server._artifact_roots(home: Path) -> list[str]`。只解析 home 自身，不解析子目录

本目录没有 Hermes profile 文件隔离用例。`profile` 不出现在这些测试的断言里。不能把续跑目录的跨 profile 用例算进这里。

## 触发与 nodeid

未运行，未做 `pytest --collect-only`。nodeid 是文件加函数名。参数来自装饰器，不是收集结果。

符号链接、根 fd、`O_NOFOLLOW`：

| nodeid | 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_symlink_and_race.py::test_bind_refuses_symlinked_upload_root` | 绑定前 `uploads` 指向家外目录，调用 `bind` | `RuntimeError` 匹配 `outside CLAWD_HOME`；家外字节不变 |
| `...::test_preexisting_symlink_cannot_escape_or_open` | 根内链接指向家外文件 | `_safe_path` 403；`_open_download` 不是 401；`list_dir` 不列出该名 |
| `...::test_in_root_symlink_is_not_opened` | 根内普通文件的别名链接 | `_open_download` 403 |
| `...::test_symlink_loop_fails_closed` | 链接指向自己 | `_open_download` 403 |
| `...::test_file_swap_after_validation_does_not_return_outside_bytes` | `_safe_path` 返回后把文件换成家外链接，再 `_open_download` | 403；不读出家外字节 |
| `...::test_directory_swap_after_validation_does_not_return_outside_bytes` | 校验后父目录换成家外链接 | 403；不读出家外字节 |
| `...::test_upload_directory_swap_does_not_write_outside` | `_upload` 前父目录换成家外链接 | 不把 payload 写到家外；不留 `.upload-*` |
| `...::test_root_swap_after_bind_does_not_list_or_write_outside` | 绑定后把 `uploads` 换成家外链接，再 `list_dir` / `upload` / `_open_download` | 不能列出、不能写入、不能打开家外；401 算失败 |

这些断言打的是行为。它们调用 `_open_download`、`_upload`、`list_dir`，家目录分支内部用 `_open_root_fd` 和 `O_NOFOLLOW`。没有单独的用例去读 `/proc/self/fd` 或断言 flag 常量。fd 打不开或平台没有 `O_NOFOLLOW` 时，源码失败关闭；那不是本目录已验证的平台矩阵。

编码，入口是 `Handler._dispatch` 的一次 `unquote`：

| nodeid | 参数 / 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_encoded_paths.py::test_download_single_decode_cannot_escape` | `path=%2e%2e%2fsecret`、`%2e%2e/%2e%2e/secret`、`%2e%2e%5csecret`、`%00secret`、`folder%2f%2e%2e%2fsecret` | 400 或 403，不是 401；响应体不含家外字节 |
| `...::test_double_encoding_is_not_decoded_into_parent` | `path=%252e%252e%252fsecret`，根内有同名字面文件 | 不是 401；若 200，正文只能是字面文件 |
| `...::test_fullwidth_and_overlong_dots_do_not_escape` | 全角点与 overlong UTF-8 点 | 不是 200，不是 401 |
| `...::test_upload_dir_encoding_cannot_write_outside` | `dir=%2e%2e%2f%2e%2e%2foutside-upload` | 400 或 403；家外没有 payload |
| `...::test_list_encoding_cannot_escape` | `path=%2e%2e%2f%2e%2e` | 400 或 403 |
| `...::test_artifact_encoded_parent_is_rejected` | artifact path 含 `%2e%2e` | 400 或 403 |
| `...::test_encoded_dotfile_name_is_rejected` | 上传名 `%2eenv` | 400 或 403；home 内没有 payload |

密钥名、点文件、拒绝前缀：

| nodeid | 参数 / 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_secret_dotfiles.py::test_upload_rejects_secret_or_dot_name` | `id_rsa`、`auth.json`、`config.yaml`、`.env`、`.npmrc`、`api_key`、`token`、`password` | 保存即失败。400 或 403，不是 401。home 内没有 payload |
| `...::test_threshold_tokens_is_not_a_secret_name` | 名 `threshold_tokens` | `_is_secret_name` 为假；上传和下载字节保留 |
| `...::test_bound_download_and_listing_hide_secret_and_dot_names` | `.npmrc`、`.git/config`、`id_rsa`、`api_key`、`token`、`credentials/a.txt`、`tokens/a.txt` | 列表不出现首段；下载不返回 payload。401 算失败 |
| `...::test_artifact_recheck_denies_secret_and_dot_names` | 上表去掉 `tokens/a.txt`，加上 `client.p12` | `_artifact` 403，不是 401 |
| `...::test_home_env_symlink_is_denied_without_leaving_temp_home` | `uploads/alias` 指向 `home/.env` | `_safe_path` 403 `denied` |
| `...::test_bind_installs_production_deny_prefixes_without_opening_them` | 绑定后的 `DENY_PREFIXES` | 含 `/etc`、`/root/.ssh`、`/root/.hermes`、安装目录、`home/.env`。对 `/etc/passwd`、`/root/.ssh/id_rsa`、`/root/.hermes/.env`、安装文件调用 `_artifact` 为 403。不打开这些路径 |
| `...::test_ordinary_note_still_uploads` | `credit.txt` | 上传字节与 `_artifact` 回读一致 |

上传限制与只读根：

| nodeid | 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_upload_limits.py::test_over_limit_is_413_before_read_and_leaves_no_file` | `Content-Length = MAX_UPLOAD_BYTES + 1` | 413 `too_large`；body 读取次数为 0；无 `.upload-*` |
| `...::test_missing_length_is_411_not_401` | 无 `Content-Length` | 411 `length_required` |
| `...::test_interrupted_body_removes_partial_file` | 声明长度大于实际 body | 400 `incomplete`；无半截文件 |
| `...::test_read_only_root_is_403_not_401` | 上传到 `root=cron` | 403 `read_only` |
| `...::test_small_upload_still_commits` | 4 字节上传到 `uploads` | `size==4`；无 `.upload-*` |

`absPath` 与注入根：

| nodeid | 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_abspath_consumers.py::test_upload_abspath_matches_consumer_shape_and_recheck` | 上传 `报告.txt`，再 `list_dir` 与 `read_artifact` | `root/path/absPath/size` 对齐；`absPath` 以前缀命中 `_artifact_roots(home)`；回读字节相同 |
| `...::test_cron_and_images_abspath_use_injected_roots` | `cron/output/job.txt`、`images/pic.txt` | 绝对路径命中注入根；注入根不是 `/root/hermes-*` 或 `/root/.hermes` |
| `...::test_symlinked_home_abspath_stays_consumable` | `CLAWD_HOME` 最后一段是指向真实目录的符号链接 | 上传 `absPath` 命中 `_artifact_roots`；`_artifact` 回读得到字节；路径仍在真实目录下 |

下载钥、Origin、Cookie：

| nodeid | 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_download_auth.py::test_panel_download_without_cookie_is_401_and_does_not_call_upstream` | 无 Cookie 调 `_proxy_sidecar` | 401 `unauthorized`；上游 `request` 被调用即失败 |
| `...::test_panel_download_bad_origin_is_403_not_401` | 有 Cookie，Origin 是 `evil.example`，peer 非回环 | `_authed` 403 `origin_rejected` |
| `...::test_sidecar_download_requires_key_and_serves_in_root_bytes` | 侧车缺钥、错钥、正确钥；正确钥再下越根链接 | 缺钥/错钥 401 且无字节；正确钥 200 且 `cache-control=no-store`；越根不是 401 |
| `...::test_valid_key_does_not_bypass_read_only_or_deny` | 正确钥下载 `id_rsa`，并上传到 `images` | 都是 403 |
| `tests/clawd_file_auth_cases/test_origin_and_cookie.py::test_referer_must_match_host_when_origin_is_absent` | POST/PUT/PATCH/DELETE，无 Origin | Referer 与 Host 一致才允许 |
| `...::test_get_ignores_bad_referer_but_not_cross_site_fetch` | GET 坏 Referer；带 `Sec-Fetch-Site: cross-site` | 坏 Referer 的 GET 允许；cross-site 拒绝 |
| `...::test_allowed_fetch_site_still_requires_origin_match` | `same-origin` / `same-site` / `none` | 站点值合法仍要求 Origin 匹配 Host |
| `...::test_host_alias_and_null_origin` | localhost 与 127.0.0.1 互为别名；`Origin: null`；Host 与 Origin 冲突；无 Origin 且 peer 为 `::1` | 别名允许；`null` 和冲突拒绝；回环省略 Origin 的 POST 允许 |
| `...::test_bracketed_ipv6_host_matches_origin` | Host 为 `[::1]` 或 `[::1]:9`，Origin `http://[::1]` | `origin_allowed` 为 True |
| `...::test_http_renew_sets_httponly_samesite_without_secure` | scheme `http` 调 `_renew` | 204；HttpOnly、SameSite=Strict、Max-Age=2592000、Path=/；无 Secure |
| `...::test_https_renew_sets_secure` | scheme `https` | 有 Secure，仍有 HttpOnly 与 SameSite=Strict |
| `...::test_rejected_renew_does_not_set_cookie` | cross-site；错误 digest | 403 与 401 都不下发 Set-Cookie |

状态码：

| nodeid | 触发 | 断言 |
| --- | --- | --- |
| `tests/clawd_file_auth_cases/test_status_codes.py::test_sidecar_denial_status_is_not_401` | 下载 `id_rsa`；POST 重启服务 | 403；重启码 `restart_disabled` |
| `...::test_name_exhausted_is_409_not_401` | 同一目录已有 `same.txt` 与 `same (1).txt` 到 `same (999).txt` | `_upload` 与 `_dispatch` 都是 409 `name_exhausted` |
| `...::test_proxy_preserves_sidecar_403_and_409` | 将来执行时监听 `127.0.0.1:0`，经 `_proxy_sidecar` 转发重启与重名上传 | 403 与 409 原样保留，不改成 401。准备阶段没有启动它。它不调用 `PanelServer.start()` |

## root / fd / nofollow / profile

按已有测试能说的边界，只这些：

- root：绑定前符号链接根被 `file_roots_under_home` 拒绝。绑定后把根换成家外链接，列表、上传、下载不得出去。预先存在的越根链接、根内链接、链接环都拒绝打开。生产前缀只出现在拒绝表和 `_artifact` 拒绝断言里，测试不打开 `/etc`、`/root/.ssh`、`/root/.hermes`、安装目录。
- fd：用例没有自己 `os.open`。它们调用的家目录分支在源码里用 home/根/目录 fd。断言是“换链接后不读不写家外”，不是 fd 号或 `/proc` 路径的直接比对。
- nofollow：同样没有直接断言 flag。根内链接、校验后替换、上传目录替换若被跟随，对应用例会失败。源码在这些入口使用 `O_NOFOLLOW`；没跑之前不能写成该 flag 已挡住。
- profile：本目录没有 profile 名、profile home 或跨 profile 文件根用例。`writable=False` 的 `cron` / `images` 是根权限，不是 Hermes profile 权限。不要把 `tests/clawd_race_cases` 的 profile 隔离写到这里。

## 旧“预期失败”不能改写成通过，也不能当成这次红结果

旧文本第 1–6 条写的是当时源码上的预期失败。之后的静态实现说明称，有 `CLAWD_HOME` 时路径函数已改成根 fd、`O_NOFOLLOW`、剥离前拒绝和注入前缀对齐；`origin_allowed` 的文档字符串现在写明括号 IPv6 按 authority 解析。那是别路的静态核对，不是本目录的运行结果。

本批未运行这 45 个函数。不能把第 2–6 条写成已绿，也不能把旧“预期失败”继续写成当前红结果。用例断言保持不变：失败时修函数，不改断言放行，不加 skip/xfail。

明确仍未在此补、也未跑的缺口：

- 正好 50MB 的允许边界没有写 50MB 文件。拒绝侧是 `MAX_UPLOAD_BYTES + 1`。
- 面板 `client_max_size` 是 52MiB，侧车上限是 50MiB。`_proxy` 会先 `request.read()` 再转发。这不是越权。
- 核心审批/运行的 409 透传没有重测。那需要 `PanelServer.start()` 和 gateway。
- 若 `aiohttp` 不能把 `request.scheme` 设为 `https`，HTTPS `Secure` 用例会失败，不能改成 skip。
- `image_studio.py` 的 fd 写入和 sqlite connect 窗口不在本目录。
- home 的祖先目录是符号链接时，源码失败关闭。本目录只让 home 最后一段是符号链接。
