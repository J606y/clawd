# clawd 核心输入安全审查

审查范围是网关输入解码、通用路由鉴权、工具 shell/文件/网络输入到危险 sink，以及配置 schema/默认值源码。只读，未改产品或测试，未运行测试、构建、导入或网络探针。秘密不出现在本文；疑似值一律不记录。

## 摘要

最高已确认风险是媒体外发默认不严格。`hermes_cli/config_defaults.py:2212` 的 `gateway.strict` 为 false，`gateway/platforms/base.py:1082-1085` 因此允许回复里的 `MEDIA:` 绝对路径在拒绝名单之外被读取并外发。这是不安全默认，不是文件工具的路径沙箱漏洞。

最高未确认风险是鉴权结果用真值判断。`gateway/platforms/api_server.py:801`、`clawd_runtime_routes.py:150`、`clawd_project_routes.py:158` 对 401 响应使用 `if auth_err`。effort 路由已改为 `is not None`，并写明 `web.Response` 可能为假。本次禁止导入，不能确认 aiohttp 的 4xx 响应是否为假。若为假，已装饰路由会在鉴权失败后继续执行。这些文件正在改，只能标需验证和需稳定复核。

另一中等风险是面板来源端口比较。`clawd_server/auth.py:239-242` 把 http/80 与 https/443 记成省略端口，`275-280` 又让省略端口匹配任意显式端口。同主机默认端口页面可以对非默认端口面板发带 cookie 的 POST，`clawd_server/server.py:272-276` 会注入 API Bearer。面板强制 loopback，利用面限于本机。该文件是新文件，需稳定复核。

未发现可达的未授权命令注入。shell 工具执行命令是授权边界。webhook 脚本用参数数组，且路径限制在 scripts 目录。未发现源码中的可用硬编码密钥。空 API key 的请求路径会放行，但正常 `connect()` 拒绝启动，标需验证。

## 范围与方法

只读可达核心链：HTTP/webhook 解码、Bearer/cookie/HMAC/本地会话身份、shell/文件/网络 sink、`DEFAULT_CONFIG`。

未覆盖，也不把搜索命中当漏洞：审批、provider、SQLite（其他审查路）；附件、浏览器、管理（交接称仍在改）；平台插件全量；`node_modules`、`.venv`、生产凭据、用户文件、`/root/.hermes`、`/usr/local/lib/hermes-agent`。

正在修改或未跟踪的文件只记录当前 SHA-256 和短片段位置。行号以本次读取为准，后续改动后必须重读，不能凭本次快照结案。

## 输入结构

```text
入口                         校验                         身份                         sink
API /v1 /api/*               JSON 对象、长度、CORS        Bearer API_SERVER_KEY        会话回合、cron、文件/终端工具
/api/platforms/*/events      适配器 verifier，异常拒绝    平台自身凭证                 平台事件派发
/api/cron/fire               JWT verifier，异常拒绝      非 API_SERVER_KEY            cron 触发
POST /webhooks/{route}       HMAC，缺 secret 拒绝        路由 secret                  提示词、stdin 脚本、gh 参数数组
/p/{profile}/{tail}          未知 profile 404            前缀选定的适配器             该适配器自己的处理器
面板 /panel-api /panel-sidecar  cookie + Origin         hp_auth                      注入 Bearer / sidecar key 后反代
本地 !cmd / shell.exec       本地 source + 危险命令拒绝  本地会话策略                 shell=True，授权执行
模型 terminal/file/web       工具参数                     已进入回合的模型             bash -c / 文件 / URL
```

## 问题

### 1. 媒体外发默认关闭严格模式

- 位置：`hermes_cli/config_defaults.py:2212`；`gateway/media_policy.py:42-46`；`gateway/platforms/base.py:1055-1085`、`891-904`
- 严重程度：中
- 置信度：确定
- 描述：`gateway.strict` 默认 false。非严格模式下，`validate_media_delivery_path` 对已存在的普通文件只套拒绝名单（`/etc`、`/proc`、`~/.ssh`、Hermes 根上的 `.env`/`auth.json` 等）。拒绝名单之外的绝对路径会被网关读取并作为媒体发送。以 root 运行时，`/root` 与 `$HOME` 相同，`_path_under_denied_prefix` 会跳过这条前缀，`/root` 下不在更具体拒绝项里的文件也可外发。
- 触发：入站内容诱导模型在回复中写出 `MEDIA:/绝对路径`。不需要再调用文件或终端工具。公开消息平台上，这是提示注入到文件读取的捷径。API 客户端本身已持密钥，影响较小。
- 这不是路径穿越漏洞。文件工具本来就能读工作区外文件；这里报的是默认策略把“回复中的路径”变成外发。
- 修复：对非 loopback 网关默认严格，或把 schema 默认改为 true，由本地部署显式关闭。

```python
# hermes_cli/config_defaults.py gateway 段
"strict": True,  # 公开网关默认拒绝名单外的 MEDIA: 路径；本地可显式 false
```

非严格分支至少不要在 root 的 `$HOME` 上整树放行，只放行缓存、允许目录和新鲜产物。

### 2. 面板 Origin 省略端口匹配任意 Host 端口

- 位置：`clawd_server/auth.py:239-242`、`275-280`、`350-364`；`clawd_server/server.py:28-33`、`237-243`、`272-276`
- 严重程度：中
- 置信度：可能
- 需稳定复核：两文件未跟踪。当前 SHA-256：`auth.py` `db2dc7f169dbe4e4da6e9c1b9f1b8858c9ca2ecee3577e515e1b2c2d4b9e96ca`，`server.py` `edc8f9bcbc64ca27ba3fb5e3b85f1eb6925c267cd75e65540c39939762051f30`
- 描述：http 80 与 https 443 被规范成“无端口”。`_ports_compatible` 在任一侧无端口时返回 true。因此 Origin `http://127.0.0.1` 与 Host `127.0.0.1:<非默认端口>` 被当成同一端点。注释写明测试要求 Host 端口 9 匹配省略端口的 Origin。`Sec-Fetch-Site: same-site` 在允许集合内，挡不住同主机跨端口。cookie 是 `SameSite=Strict`，跨站请求不会带上；同主机不同端口仍是 same-site。
- 触发：用户已登录面板；攻击者能让浏览器打开同主机默认端口（http/80 或 https/443）上的页面；面板监听非默认端口。通过后的 POST `/panel-api/...` 由服务端注入 API Bearer，后续能力与 API 密钥持有者相同。`loopback_host` 拒绝非 loopback 绑定，所以远程网站的 Origin 对不上，利用面限于本机默认端口上的页面。
- 修复：比较前把省略端口还原成 scheme 的默认端口；两边都无法还原时拒绝。不要为了测试让无端口匹配任意显式端口。

```python
def _explicit_port(port: int | None, scheme: str) -> int | None:
    if port is not None:
        return port
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None

def _ports_compatible(left: int | None, right: int | None, left_scheme: str = "", right_scheme: str = "") -> bool:
    lp, rp = _explicit_port(left, left_scheme), _explicit_port(right, right_scheme)
    return lp is not None and lp == rp
```

`_endpoint_match` 应把元组里的 scheme 传进去。

### 3. 鉴权失败响应的真值判断

- 位置：`gateway/platforms/api_server.py:796-804`、`769-774`、`3319-3321`；`gateway/platforms/api_server_runs.py:1271-1273`；`gateway/platforms/api_server_openai_routes.py:1509-1511`；`gateway/platforms/api_server_room_grants.py:114-116`；`gateway/platforms/clawd_runtime_routes.py:49-54` 与 `150` 行一类 `if denied :=`；`gateway/platforms/clawd_project_routes.py:73-81`、`158`
- 严重程度：高，仅当 4xx `web.Response` 为假；否则不是漏洞
- 置信度：需验证
- 需稳定复核：`api_server.py` 工作树有改动，SHA-256 `a6b4ef32a53c5b9110665542307f0ff99bb03dc3c756fe3a6ba03fd49087ba31`。当前 diff 不包含 `_check_auth` 或 `if auth_err` 那几行，但文件仍在改，行号可能移动。runtime/project 路由未跟踪。
- 描述：失败分支返回 `web.json_response(..., status=401)`，调用方用 `if auth_err` 决定是否返回。effort 路由 `clawd_effort_routes.py:37-55` 已写明 Response 可能为假，并使用 `is not None`。续跑状态 `gateway/clawd_continuation.py:407-409` 也使用 `is not None`。核心和项目/运行路由没有对齐。
- 触发：若 aiohttp 对 status>=400 的响应实现 `__bool__` 为假，则 Bearer 错误或缺失时装饰过的路由仍会执行。本次未导入 aiohttp，不能把这一点写成已确认绕过。
- 修复：所有调用点改成身份比较，不看真值。

```python
auth_err = self._check_auth(request)
if auth_err is not None:
    return auth_err
```

runtime 的 `_auth` 在检查器返回非 Response 时返回 None，等于放行。effort 对非 Response 拒绝。真实 `_check_auth` 只返回 None 或 Response，这条只在检查器被替换时可达，单独为低，同样需验证。

### 4. 默认 profile 无密钥时请求路径放行

- 位置：`gateway/platforms/api_server.py:1415-1428`；对照 `3942-3999`
- 严重程度：低
- 置信度：需验证
- 需稳定复核：同一脏文件，见问题 3 的哈希。引用行不在当前 diff 块内。
- 描述：`_check_auth` 在期望密钥为空且不是具名 profile 时返回 None。具名 profile 无密钥则 401。`connect()` 在密钥缺失、过短或强度无法核验时拒绝监听，面板 `_start_api` 走的是这条路径。
- 触发：有代码不经过 `_api_key_passes_startup_guard` 就把同一 handler 挂到监听器上。本次看到的生产启动路径都会经过该门禁，因此不能报成已确认的未授权入口。
- 修复：请求路径也失败关闭。测试要无密钥时注入测试密钥，不要留生产放行分支。

```python
if not expected_key:
    return self._auth_failed_response()
```

### 5. Webhook 仍接受无时间戳的 V1 HMAC

- 位置：`gateway/platforms/webhook.py:887-910`
- 严重程度：低
- 置信度：确定
- 描述：出现 `X-Webhook-Signature-V2` 时缺少时间戳会拒绝，不会退回 V1。只带 `X-Webhook-Signature` 时，校验的是 body 的 HMAC，注释写明可无限重放。每次路由只告警一次，仍然接受。
- 触发：路由使用遗留 V1 签名。捕获一次有效请求后可重放，直到幂等缓存或 delivery id 挡住。没有 delivery id 时，`_header_delivery_id` 会生成新 id，重放不会被当成重复。GitHub/GitLab/Svix 头不走这条。
- 修复：默认拒绝 V1，只在路由显式 `allow_legacy_v1: true` 时接受。

```python
generic_sig = headers.get("X-Webhook-Signature", "")
if generic_sig:
    if not route_config.get("allow_legacy_v1"):
        return False
    return _hmac_str_equal(generic_sig, _hex_hmac(secret, body))
```

### 6. 面板口令哈希无拉伸、盐固定

- 位置：`clawd_server/auth.py:22-25`、`85-88`
- 严重程度：低
- 置信度：确定
- 需稳定复核：未跟踪，哈希见问题 2。
- 描述：口令与固定前缀拼接后做 SHA-256。比较使用 `compare_digest`，空用户列表失败关闭，这些是对的。哈希本身可离线穷举。盐是源码常量，不是每用户随机盐，本文不记录其值。
- 触发：能读 `panel/auth.json` 的人离线猜测口令。面板是 loopback，读文件通常已经是本地能力。
- 修复：每用户随机盐，并用 scrypt/argon2。不要把盐写成源码常量。

```python
def credential_hash(user: str, password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(f"{user.strip().lower()}\n{password}".encode(), salt=salt, n=2**15, r=8, p=1)
    return digest.hex()
```

### 7. 懒安装默认开启

- 位置：`hermes_cli/config_defaults.py:1778-1782`
- 严重程度：低
- 置信度：确定
- 描述：`security.allow_lazy_installs` 默认 true。首次启用需要额外包的后端时会从 PyPI 安装。不是未授权入口，但是不安全默认。
- 触发：操作者或模型启用对应后端，且进程能访问 PyPI。
- 修复：默认 false，由安装文档要求显式打开。

```python
"allow_lazy_installs": False,
```

## 已核对、不构成漏洞

- API 启动门禁要求 `API_SERVER_KEY` 至少 16 字符，强度函数异常时拒绝启动，不把异常当放行。默认绑定 `127.0.0.1`（`api_server.py:154-165`）。CORS 默认空，不反射任意 Origin；`*` 只有配置后才出现。
- 平台事件 verifier 缺失或抛错时 503/401，不放行。抽查 Google Chat verifier：未配置 audience 或账号时返回 false。
- cron fire：verifier 异常或 claims 为 None 时 401。未深入 Chronos 令牌格式，那属于其他路。
- Webhook：空 secret、非字符串、空白 secret 在启动和请求时都拒绝。`INSECURE_NO_AUTH` 仅 loopback；默认 host 为空时 `_is_loopback_host` 为假，因此该秘密不能配在默认绑定上。脚本 `subprocess.run([interpreter, path], input=json)`，不是 shell 拼接。路径 `resolve()` 后必须位于 `HERMES_HOME/scripts`。`gh pr comment` 用参数数组，仓库名有全匹配正则，PR 号转正整数。
- 提示模板是正则替换，不是 `format`/`eval`。签名比较走 bytes `compare_digest`。
- `terminal_tool` 和 `gateway/session_shell.py:26-69` 的 `shell=True` 是授权执行。后者要求本地 source、本地后端，并拒绝危险命令；cwd 不存在时不回落到进程 cwd。`base_session_env.py:179-191` 把命令放进单引号 `eval`，这是 shell 工具的实现，不是未授权注入。
- 文件工具不是沙箱。相对路径解析到工作区外只告警，不拦截。`~user` 展开前有用户名字符白名单。搜索参数有引号转义。不把“能读绝对路径”报成穿越。
- webhook 过滤器的 `in_file` 可由操作者配置读绝对路径。写入订阅文件的人已经有本地写权限，不是未认证 HTTP 入口。
- `clawd_server/yaml_adapter.py` 使用 `YAML(typ="safe")`。配置加载未见本次范围内的 pickle/eval。
- 面板静态文件 `resolve()` 后检查是否仍在静态根下。`/clawd/ready` 无 cookie，只返回续跑状态字段，未见秘密。反代 `allow_redirects=False`，并丢掉客户端的 Authorization。
- URL 工具在抓取前调用 `is_safe_url`。元数据地址不因 `allow_private_urls` 放行。代理存在且 DNS 失败时预检放行，连接期守卫只覆盖 Hermes 自己的 httpx 客户端。提取后端是否一律使用该客户端，未逐个证明，不升格为 SSRF。
- 抽查源码中的 `api_key = "..."` 是占位符或说明文字，不是可用凭据。不记录原文。

## 当前修改文件

下列哈希是本次读取时的文件字节。短片段只给行号，不摘秘密。结论不能当成稳定版本的最终漏洞判定。

| 文件 | 状态 | SHA-256 | 本次引用 |
| --- | --- | --- | --- |
| gateway/platforms/api_server.py | 已修改，+58/-17；鉴权函数不在当前 diff 块 | a6b4ef32a53c5b9110665542307f0ff99bb03dc3c756fe3a6ba03fd49087ba31 | 1415-1428，796-804 |
| gateway/platforms/api_server_runs.py | 已修改 | 554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b | 1271-1273 |
| gateway/platforms/clawd_runtime_routes.py | 未跟踪 | c922ca4b2e99d3be39d6ae6e3b839265d1c257d55e502cc501cd5183817d27bd | 49-54，150 |
| gateway/platforms/clawd_project_routes.py | 未跟踪 | dfb354f3b45b44922b16dff29f2fcb2e3db34a123e24fafb2959ce637164856e | 73-81 |
| gateway/platforms/clawd_effort_routes.py | 未跟踪 | ddb968815d6c9a9f033eecdb9178cd6789f28d0cb71da63d53476597e8a9a3b8 | 37-55，对照项 |
| gateway/clawd_continuation.py | 未跟踪 | 300f059641a218a78bf8be30fe7a1e4e162f44053b3105efe0bfc78341bbe476 | 407-409 |
| clawd_server/auth.py | 未跟踪 | db2dc7f169dbe4e4da6e9c1b9f1b8858c9ca2ecee3577e515e1b2c2d4b9e96ca | 239-242，275-280 |
| clawd_server/server.py | 未跟踪 | edc8f9bcbc64ca27ba3fb5e3b85f1eb6925c267cd75e65540c39939762051f30 | 272-276 |

同批已修改但本次未逐行定性的还有 `api_server_run_idempotency.py`、`event.py`、`readiness.py`、`response_filters.py`、`session_api_turn.py`、`session_ingress.py`、`session_local.py`、`session_local_recovery.py`、`session_policy.py`、`wake.py`。不根据文件名或搜索命中报漏洞。

稳定文件哈希，供以后对照：`webhook.py` `31e8fcc00d4649ad760aaeba8fe0bfda47e97dc4587622d74b8e1674285907f8`，`base.py` `25bce7af7473b6eb1289dc6e83570062d04fe4338620832889a28d9f598f6229`，`config_defaults.py` `2c7f02045f582d03565dbb59972f09fc21fcac71a26adb5117d5754759ccb634`。

## 未覆盖

审批策略、provider 凭据、SQLite 查询由其他路负责。附件、浏览器、管理源码正在改，本文不复述旧快照。平台适配器除 Google Chat 事件校验外未逐个读 verifier。提取后端、远程沙箱和插件注册的路由没有逐条证明鉴权包装。未执行任何请求，所以上述触发条件都未动态证实。
