# /live 路由契约

日期：2026-11-06。只接通源码。未运行、未探针、未构建、未部署、未改登录。不能把本文件当成实时画面已验收。

## 注册

`PanelServer.start` 在 `/{name:.*}` 静态兜底之前调用 `register_live_routes(app, self)`。固定路径：

- `*` `/live/websockify`
- `*` `/live` 与 `/live/`：认证后 404，不列目录
- `*` `/live/{name:.*}`：noVNC 静态文件

因此 `/live/core/rfb.js`、`/live/vendor/...`、`/live/vnc_lite.html` 不会落入面板 `dist`，也不会返回成功 HTML。

## 何时启用

只读 `CLAWD_HOME/panel/live-browser.json`。必须同时满足，否则保持隔离：

- 普通文件，不是符号链接，模式正好 `0600`，属主是当前进程 uid，不超过 4096 字节
- JSON 对象且键正好是 `protocol`、`home`、`controller`、`cdp`、`novnc_root`，无重复键
- `protocol` 为 `clawd-live-v1`
- `home` 解析后等于当前面板 home。这是跨 profile 门，不是任意路径
- `controller` 正好是 `http://127.0.0.1:39232`
- `cdp` 正好是 `127.0.0.1:39233`
- `novnc_root` 不是符号链接，解析后等于真实 `panel_root()/vendor/novnc`

缺文件、模式不对、home 不同或路径不是这块真实目录时，`panel_live_cdp()` 返回 `127.0.0.1:9`。路由不使用配置里的 URL 去连接，也不打开配置给出的其他目录。

`bind.prepare_env` 在导入 sidecar 前写入该 CDP。状态和中文输入走这个非唤醒端口。本路由不连接唤醒代理端口。进程启动后 sidecar 已读过环境变量；配置晚于启动写入时，必须重启面板后 CDP 才会从端口 9 改为 39233。路由每次请求重读配置，但还要求进程环境里的 `PANEL_LIVE_CDP` 已经是 `127.0.0.1:39233`，避免像素和状态指向不同屏幕。

## 静态

先 `server._authed`。`denied is not None` 原样返回。没有检查器、检查抛错或返回值不是 Response/None，都回 401。静态不再加一套登录。

GET 才读文件。其他方法 405。路径拒绝 `..`、绝对路径、反斜杠和符号链接；越界 403，缺文件或未知后缀 404，正文是 `text/plain`，不是 200 HTML。命中文件按后缀给 MIME，并带 `Cache-Control: no-cache, no-store`。JS 为 `text/javascript`。不列目录。静态不请求 `/health`，不连接 WebSocket，不唤醒。

## WebSocket

同样先 `_authed`。然后单独做严格同源：Origin 必须存在，scheme 与 Host 端口按默认端口归一后精确相等。不沿用 GET 可省略 Origin、省略端口即兼容的规则。nginx 对 loopback 对端设置的单个 `X-Forwarded-Proto` 用作对外 scheme；不信任 `X-Forwarded-Host`，也不改 cookie。`Sec-Fetch-Site` 若存在必须是 `same-origin`。

不是 WebSocket 升级则 426，不连接控制器。升级后才读配置。不合规则 503。然后 GET 固定 `http://127.0.0.1:39232/health`，不跟随重定向。只接受 `protocol=clawd-live-v1`、`home` 与 `str(当前解析 home)` 全等、`running` 为布尔。`running` 不作为是否连接的条件，健康检查本身不唤醒。健康失败 503。

通过后才连接固定 `ws://127.0.0.1:39232/websockify`。握手期限 45 秒；控制器若把接受推迟到唤醒完成，必须在这期限内接受。超时或连接失败 503，且发生在向浏览器完成升级之前。不转发 Cookie、Authorization、`X-Panel-Key`。不使用任务端点、云端地址或配置中的其他 URL。

双向转发二进制和文本帧，`max_msg_size` 为 16MiB，关闭压缩，发送等待形成背压，不另建无界队列。任一侧结束、取消或服务 `on_cleanup` 时，先取消并 join 两个 pump，再关闭 upstream 与会话。取消路径用 shield，关不掉则 abort transport。避免残留连接拖住休眠。升级完成后的故障改为关闭套接字，不再补发 503。

## 未运行

未导入运行中的面板，未打开健康检查或 WebSocket，未验证 cookie、RFB、中文输入或页面。父代理收齐 runtime 与本路由后再做静态整合；部署和验收不在本路。
