# clawd 自有全局共享浏览器运行时

状态：源码已写入，未安装依赖、未部署、未启动、未做运行验收。本文不表示实时画面已经可用。

## 入口

```text
python -m clawd_server.live_browser_runtime --home /root/.clawd
```

服务模板使用工程虚拟环境解释器，工作目录是工程根 `/root/hermes-workspace/clawd`。导入本模块不会启动浏览器，也不会监听端口。`--home` 经 `clawd_server.home.resolve_home` 解析；拒绝 `/root/.hermes` 与 `~/.hermes`，不回落生产 Hermes 数据。

控制器进程一直存活。空闲时只关掉本次拉起的 worker，健康接口仍可查询。

## 冻结端口与显示

| 用途 | 地址 | 是否唤醒 |
| --- | --- | --- |
| 控制器 `GET /health`、`GET /websockify` | `127.0.0.1:39232` | `/health` 不唤醒；`/websockify` 有界唤醒后接受 WebSocket |
| 同屏 Chromium CDP | `127.0.0.1:39233` | 侧栏直连，不唤醒 |
| 按需 CDP 代理 | `127.0.0.1:39234` | 连接即唤醒，HTTP `/json` 把 `39233` 重写为 `39234`，WebSocket 同一条 TCP 代理 |
| x11vnc RFB | `127.0.0.1:15900` | 仅由本进程的 WebSocket 桥接，不单独对公网 |
| 显示 | `:98`，`1366x768x24` | Xvfb 只听 Unix socket，禁用 TCP 与 inet6 |

健康响应至少包含：

```json
{"protocol":"clawd-live-v1","home":"/root/.clawd","running":false,"error":null}
```

`home` 是当前 `--home` 解析后的绝对路径。`running` 只表示本次 worker 的 Xvfb、Openbox、x11vnc、Chromium 与看门狗都还活着，不会为了回答健康检查去启动它们。`error` 是附加状态码，没有错误时为 `null`；不包含页面 URL、Cookie 或口令。

`/websockify` 只接受 loopback 上的 WebSocket。非升级请求返回 426，不唤醒。二进制帧原样桥接到 `127.0.0.1:15900`，不改写、不记录载荷。观众上限 4，单帧上限 8MiB，写缓冲超过 1MiB 时暂停读取。任一方向结束都在 `finally` 取消并 join 双向 pump，再关闭套接字。`CancelledError` 不按普通异常吞掉。

CDP 代理同样只绑 loopback。连接从接受时计入活动，包括读首包和冷启动，避免 10 秒空闲回收在唤醒期间动手。请求方向把 `39234` 换回 `39233`，响应方向相反；两端口文本等长，不破坏 `Content-Length`。分片落在端口文本中间时会留住尾部再拼。侧栏应直接查 `39233`，不要走 `39234`。

## 生命周期

唤醒有一把锁和 20 秒截止时间。并发观众等待同一次启动，超时返回 `wake_timeout`，不附着已经占用的端口或显示。

本次 worker 使用独立进程组和 `CLAWD_LIVE_OWNER` 令牌。停止、空闲回收和控制器重启只向能同时对上令牌、命令行标记和进程组的 PID 发信号。对不上就记 `owner_unproven` 并留下，不按端口杀进程，不删未知的 X 锁或 Chromium `Singleton*`。

活动数只算 `/websockify` 与 `39234` 代理连接。两者都为 0，且最后一次活动或断开已过 10 秒，才关闭本次 worker。健康检查不计活动，也不会把休眠推迟。直接连 `39233` 的侧栏查询既不唤醒，也不算活动；这是故意的，避免状态轮询把浏览器常驻。

Xvfb、Openbox 或 x11vnc 退出会记日志、清本次 owner，有观众时按退避重建。Chromium 退出（包括关掉最后一页）在仍有观众时按 1、2、4、8 秒退避重启，不删 profile。看门狗退出先原地重启，失败再重建。子进程标准输出和标准错误都丢弃，避免 Chromium 把页面 URL 写进日志。

显示 `:98`、CDP `39233` 或 RFB `15900` 在启动前已被占用，则失败，不连接。控制器端口 `39232` 或代理端口 `39234` 绑定失败时进程以非零退出，不把请求转给未知监听者。

## 画面与档案

Chromium 有头，强制 X11，关闭 GPU，窗口 `1366x768` 并 `--start-maximized`。不传页面 URL。Openbox 使用 `CLAWD_HOME/live-browser/openbox-rc.xml`，不写 `/root/.config/openbox`。wmctrl 每秒去掉 hidden/shaded、加双向最大化并激活，避免无任务栏时最小化后变成黑屏。x11vnc 使用 `-shared -localhost -forever -noxdamage`，不使用客户端缓存参数。

档案目录是 `/root/.clawd/live-browser-profile`，权限 `0700`。只在能证明 `SingletonLock` 指向本次已退出的 Chromium 时删除那三个锁名字。Preferences 只在能解析时改 `custom_chrome_frame`、`exit_type` 和 `exited_cleanly`；解析失败保留原文件。不删除 Cookies、历史或其他资料。Xauthority 放在 `/root/.clawd/live-browser/Xauthority`，权限 `0600`，不写进 profile。

## 本路不改的边界

- 不改 `server.py`、`bind.py`、`browser_native.py`、前端、auth、`config.yaml`。
- 不接任务登记簿、云端签名连接、用户当前页或未知浏览器。
- 不实现、不修改输入租约。WebSocket 是透明字节桥。公网入口必须由面板现有登录门保护后再转到 `127.0.0.1:39232`。
- 不提供 `/live/core/*` 静态文件，也不调用 `websockify` 程序。noVNC 树由父代理放置、路由侧挂载。
- 不安装软件包，不写 systemd 系统目录，不启动服务。

路由侧接上后需要：

1. 登录通过后，把 `/live/websockify` 的 WebSocket 透明转到 `ws://127.0.0.1:39232/websockify`。不能复用会剥掉 `Upgrade` 的 HTTP 代理。
2. 把 `PANEL_LIVE_CDP` 从 `127.0.0.1:9` 改为 `127.0.0.1:39233`。在此之前侧栏仍打到空端口，不能算画面已接通。
3. 工具若要像 Hermes 的 `9222` 那样按需唤醒，应指向 `http://127.0.0.1:39234`。本次父代理不改工具配置，因此现有 agent 浏览器工具不会自动使用这台浏览器。

## 父代理部署时

依赖预期在 `/usr/bin/Xvfb`、`/usr/bin/openbox`、`/usr/bin/x11vnc`、`/usr/bin/xauth`、`/usr/bin/wmctrl`、`/usr/bin/chromium`。缺任一二进制时唤醒失败，健康检查返回 `missing_binary`，不自行安装。

模板是 `scripts/clawd-live-browser.service`。`WorkingDirectory` 为工程根，`Environment` 为 `HOME=/root` 与 `CLAWD_HOME=/root/.clawd`，`Restart=on-failure`。`KillMode=control-group` 只作为本服务 cgroup 的兜底；代码本身仍只信号已证明的进程组。复制到 systemd 并 enable/start 不属于本路。

## 未验证

未执行模块，未绑定端口，未启动 Xvfb/Chromium/x11vnc，未安装上述依赖，未做 CDP、WebSocket、10 秒休眠、关页重启或黑屏检查。静态核对只覆盖源码契约，不能当作通过。
