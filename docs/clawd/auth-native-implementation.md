# 认证与来源校验实现说明

身份是 grok-4.7，与当前运行时一致。未改配置，未改公共 `docs/clawd/handoff.md`。

本文件只记录静态阅读和源码修改。没有运行测试、构建、探针或服务，没有读取生产 home 内容，没有 commit、push、deploy。下面凡是“预期”都不是已复现结果。

## 已确认的代码缺陷（改前静态阅读）

1. `clawd_server/auth.py` 旧 `origin_allowed` 用 `Host.split(":")[0]`。`[::1]` 得到 `[`，`[::1]:9` 也得到 `[`。`urlsplit("http://[::1]").hostname` 是 `::1`。两边对不上。这是源码结构，不是执行结果。
2. `clawd_server/server.py` 旧 `_artifact_roots` 用未解析的 `home / name`。侧车 `_upload` / `list_dir` 的 `absPath` 来自 `_root()` / `_safe_path()` 的 `resolve()`。`Handler._artifact` 仍用绑定当时的 `spec["path"]` 做 `relative_to`，不 resolve。`CLAWD_HOME` 本身是符号链接时，三套拼写不是同一字符串。
3. 生产启动若先走 `resolve_home()`，绑定和注入会先拿到已解析 home。用例 `isolated_sidecar(home=link)` 不走这条路，直接把符号链接交给 `bind` 和 `_artifact_roots`。这是调用链差异，未执行。

## 推测，不能称已复现

- 用例路写明 `test_bracketed_ipv6_host_matches_origin`、`test_symlinked_home_abspath_stays_consumable` 按当前代码预期会失败。本路没有运行它们，不能写成已红。
- 若测试机的临时目录父路径本身是符号链接，解析后的注入前缀可能和 `str(home / relative)` 这种未解析字符串不一致。未检查本机 `/tmp`，未跑 `test_cron_and_images_abspath_use_injected_roots`。

## 本次修改

- `clawd_server/auth.py`：`origin_allowed` 按 authority 解析 Host，不再按第一个冒号切开。括号 IPv6、大小写、显式冲突端口、userinfo、多值头、非法 authority 失败关闭。
- `clawd_server/server.py`：`_artifact_roots` 只解析 home 本身，再词法拼接 `uploads`、`workspace`、`images`、`cron/output`。不解析子目录，避免绑定后根被换成外部符号链接时把注入允许表扩大。生产 home 或无法解析时返回空列表，不回落 `/root/hermes-uploads` 等生产根。
- 未改 `_authed` / `_renew` / `_proxy`。401 仍只表示登录失效，来源拒绝仍是 403 `origin_rejected`，上游 403/409 仍原样转发。
- renew 仍是 `HttpOnly`、`SameSite=Strict`、`Path=/`、`Max-Age=2592000`；只有 `request.scheme == "https"` 才带 `Secure`。
- 注入仍是 `window.__CLAWD_ARTIFACT_ROOTS__=<JSON 字符串数组>`，每项绝对路径且以 `/` 结尾。未删 `absPath`，未改字段格式。

## 保留的 loopback 契约

- 不安全方法缺少 Origin 和 Referer 时，只接受对端 `127.0.0.1` 或 `::1`（含这两种地址的规范写法，不含 `127.0.0.2`）。
- Host 属于 `{127.0.0.1, localhost, ::1}` 且 Origin/Referer 主机属于 `{127.0.0.1, localhost}` 时仍可别名匹配。`127.0.0.1` 对 `::1` 的 Origin 不因别名放行，必须主机相等。
- 一侧省略端口时不因此失败。这是既有用例契约，包括 `Host: localhost:9` + `Origin: http://127.0.0.1`，以及待修的 `[::1]:9` + `http://[::1]`。没有为了这些断言放宽 userinfo 或多值头。
- 两侧都有显式端口且规范化后不同，拒绝。`http` 的 `:80` 和 `https` 的 `:443` 先视为省略，再套用上面的省略规则。
- `Sec-Fetch-Site: cross-site` 即使 Origin 匹配也拒绝。`same-origin` 要求 Origin 存在且主机字符串相等，别名不够。
- Origin 与 Referer 同时存在时，两者都必须匹配 Host；主机冲突或 scheme 冲突则拒绝。GET 没有 Origin 时仍忽略坏 Referer。

## 测试 harness，不是已复现失败

- `test_https_renew_sets_secure` 把 `request.scheme` 设为 https，但 Origin 仍是 `http://127.0.0.1:9`。本路没有把请求 scheme 纳入 `origin_allowed`，否则会在写 Cookie 前 403，Secure 用例会被来源门禁挡住。不要为此去掉 Secure。若以后要核 scheme，应改 harness 的 Origin，不能改成 skip。
- 省略端口与显式非默认端口相容，是用例契约，不是 harness 写错。不要把 `test_host_alias_and_null_origin` 或 IPv6 用例改成拒绝。

## 未改、仍待附件路

- `apps/panel/sidecar/hermes_panel_sidecar.py` 的 `_artifact` 仍 `relative_to(spec["path"])`。本路不能改该文件。静态上看，符号链接 home 的已解析 `absPath` 仍可能 `outside_root`。这不是本路已复现的失败。
- 建议附件路在复核时对 `spec["path"]` 使用与 `_root()` 相同的 resolve 拼写，保留 `absPath` 字符串字段。不要把绑定后换成家外的符号链接当成新根。
- `turns.tsx` 的 `UPLOAD_ROOT` 和 `artifacts.ts` 里写死的生产前缀不在本路。注入格式不要改；前端把注入数组接在写死根后面，那是另一路的事。
- `build/lib/clawd_server/` 仍是旧副本。本阶段禁止构建，没有刷新它。

## 新增但未运行

- `tests/clawd_auth_native/test_origin_and_roots.py`
- 未改 `tests/clawd_file_auth_cases/`。

## 给调度的交接建议

不要把本说明写入公共 handoff，除非父代理自己整合。统一测试前不要把上述预期红写成已复现。附件路收口 `_artifact` 与 `absPath` 拼写后，再和本路的注入前缀一起测 `test_symlinked_home_abspath_stays_consumable`。来源用例可直接测 `origin_allowed`，不必为 IPv6 启动面板。
