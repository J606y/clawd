# browser-harness daemon 依赖边界

未改产品、未改锁、未安装、未导入、未启动 daemon、未做 CDP 探针。未读安装包、`/root/.hermes`、`/usr/local/lib/hermes-agent`。证据在 `artifacts/browser-daemon-source/`。

## 结论

实际依赖是 PyPI `browser-harness==0.1.13`，不是本仓库模块。同版本 `daemon.py` / `_ipc.py` 可从锁中的 sdist 或 Git tag `v0.1.13` 合法取得，二者逐字节相同。0.1.13 的 `handle` 不记录任务页、不校验任务绑定。客户端登记簿和 preamble 到不了 daemon。要补齐必须 fork 或 vendor 打过补丁的 `daemon.py`，并让绑定能穿过现有 `cdp()`。本路不自行 vendor，也不改锁。锁 pin 归分发 metadata 路。

## 来源

| 环节 | 值 |
|---|---|
| 声明 | `pyproject.toml:141` `browser-harness==0.1.13` |
| 锁 | `uv.lock` registry `https://pypi.org/simple`，version `0.1.13` |
| sdist | https://files.pythonhosted.org/packages/7f/fe/59ab493e7cf76731a7f5aabae3821eea6ea82a628c14c30e7aed48adc362/browser_harness-0.1.13.tar.gz |
| sdist sha256 | `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3`（已下载复核） |
| 许可证 | MIT，`Copyright (c) 2026 Browser Use` |
| 仓库 | https://github.com/browser-use/browser-harness |
| tag | `v0.1.13` → `c24e5072ee66f8499bacd663f4f4bcb089bc4492` |
| 最新 PyPI | 仍是 0.1.13。`main` 的 `daemon.py`、`_ipc.py`、`helpers.py` 与该 tag / sdist 逐字节相同 |

抽出的同版本文件：`artifacts/browser-daemon-source/daemon.py`、`_ipc.py`、`LICENSE`、`PKG-INFO`。哈希见同目录 `PROVENANCE.md`。

直接事件库是锁中的 `cdp-use==1.4.5`（MIT，https://github.com/browser-use/cdp-use）。sdist sha256 `0da3a32df46336a03ff5a22bc6bc442cd7d2f2d50a118fd4856f29d37f6d26a0`。只保留事件摘录，不纳入 clawd。

## 现有 IPC 形状

`_ipc.request` 是一行 JSON。Windows 才注入 `token`。POSIX socket 由 `umask 0o077` 建成 0600，请求里没有 token。`handle` 实际只读 `token`、`meta`、`method`、`params`、`session_id`，`set_session` 另读 `target_id`。不认识的字段被忽略，不会打断 0.1.13 上已有截图、输入、关闭。因此往旧 daemon 加 owner 字段既不生效，也不构成绑定。

`handle` 对普通 CDP 的返回是 `{"result": <send_raw 返回值>}`。`Target.*` 强制丢掉 `session_id`。

### createTarget

IPC 的 `Target.createTarget` 没有专门分支，成功后 daemon 不记 target。`cdp-use` 的 `CreateTargetReturns` 只有 `targetId`。所以现有成功形是：

```text
{"result": {"targetId": "<id>"}}
```

`attach_first_page` 里自己调用的 `createTarget` 只在具名、非云 daemon 的恢复路径写入 `dedicated_target_id`。那不是任务页登记，也不是 IPC 调用。

### pending_dialog

```text
{"dialog": <Page.javascriptDialogOpening 的 params 或 None>}
```

`_record_event` 把 `self.dialog = params`，丢掉回调里单独传来的 `session_id`。`cdp-use` 1.4.5 的 `JavascriptDialogOpeningEvent` 含 `url`、`frameId`、`message`、`type`、`hasBrowserHandler`，可选 `defaultPrompt`，不含 `targetId` / `sessionId`。因此：

- `frameId` 可能已在 `params` 里，daemon 不提升、不保证。未对活浏览器探针，不能证明当前 Chrome 一定发送。
- `sessionId` 在 tap 参数里有，响应里没有。
- `targetId` 事件里没有。daemon 也没有把 IPC `attachToTarget` 的 `sessionId → targetId` 记下来。只有 `self.session` / `self.target_id` 和 `set_session` 会更新当前页。

`Page.enable` 只在 `attach_first_page` 和 `meta=set_session` 调用。clawd 的 `harness_call` 只发 `Target.attachToTarget`，不发 `set_session`，也不 enable Page。这种会话上，dialog 事件可能根本不进 daemon。

### drain_events

```text
{"events": [{"method", "params", "session_id"}, ...]}
```

读完即 `clear()`。缓冲 `maxlen=500`，更早的会被丢掉。事件没有 `targetId`。清空缓冲不会清 `self.dialog`。官方 `helpers.drain_events` 会消费事件副本。dialog 身份不能只放在这个缓冲里。

## 为何客户端自报不够

能连上 socket 的同一用户进程可以直接 `method/params/session_id`，绕过：

- `browser_task_registry.owns` / `register_created`：只写 sqlite，daemon 不读。
- `browser_use_cli.py` preamble 的 `_owned`：只包住本进程里的 `cdp()`。
- `browser_session.owner_task_id`：会话缓存键，不进入 IPC。
- `harness_ping` 的 `pid` + `browser_kind`：只证明进程还是那个 daemon。

Windows `token` 是端口文件里的环回口令，不是任务绑定。读得到端口文件就能用。

## 最小补丁（未落地）

仍走现有一行 JSON，不开第二 socket / websocket，不加 daemon 不认识就会拒绝的必填字段到非任务页。

1. `daemon.py` `handle`：仅当本进程 IPC `Target.createTarget` 成功时，自己生成绑定并只保存在进程内。返回仍带原来的 `result.targetId`，另加 daemon 签发的绑定。不接受客户端带来的 owner 当作绑定。
2. 同时记下本进程 `attachToTarget` / `set_session` 的 `sessionId → targetId`。任务页上的 `Page.*`、`Runtime.*`、`Input.*`、该页 `Target.closeTarget`，缺这份绑定就拒绝。未登记的原 `/browser connect` 页保持可用。
3. `Browser.close` 与 `Target.disposeBrowserContext` 在 `handle` 里拒绝。daemon 不能靠列 target 证明独占整浏览器；云浏览器停止已走 `meta=shutdown` → `stop_remote`，不依赖这两条 CDP。
4. `_record_event` 把已有 `session_id` 和能从本进程映射得到的 `targetId` 写进 `self.dialog`，并保留 params 里的 `frameId`。缺映射就不要编。不要把这三项只放进 `drain_events`。`pending_dialog` 继续返回 `dialog` 对象，只加字段，以免 `helpers.page_info` 读不到 `type` / `message`。
5. 绑定若放在 `result` 外面，现有 `helpers.cdp()` 只返回 `result`，preamble 看不见。所以同一包里的 `helpers.py` `cdp` / `_send` 必须带上绑定；否则官方 helper 在自己创建的任务页上会被新 daemon 拒绝。`_ipc.py` 不用改协议。

## 文件归属

必须进入 clawd 运行路径的，是 fork 或 vendor，不是再包一层客户端检查：

| 文件 | 归属 |
|---|---|
| `src/browser_harness/daemon.py` | 必须打补丁。fork `v0.1.13` 或 vendor 这份抽出的文件 |
| `src/browser_harness/helpers.py` | 同一包必须改，否则 `cdp()` 丢掉绑定 |
| `src/browser_harness/_ipc.py` | 不改 |
| `cdp-use` | 不 fork。事件签名已够 |
| `tools/browser_use_cli.py` | 实现时把 daemon 返回的绑定写入回执。现在不改 |
| `tools/browser_task_cdp.py` | 实现时在已有 `harness_call` / close 上附带该绑定 |
| `tools/browser_task_registry.py` | 实现时只保存 daemon 签发值，不把客户端 owner 当绑定 |
| `tools/browser_session.py` | 不参与这条链 |
| `pyproject.toml` / `uv.lock` | 分发 metadata 路。本路不改 |

只 vendor `daemon.py`、却让 agent 继续导入未改的 `helpers.py`，任务页会被拒绝，官方 helper 也会断。所以是 fork 整包或同时 vendor 这两个模块，并用启动路径指向它们。不能改已安装副本。

## 剩余边界与阻塞

- 运行中的 daemon 仍是未打补丁的 0.1.13，直到 metadata 路改 pin 或启动路径改指向 fork/vendor。本路没有该权限。
- 未读、未改安装包和官方目录。
- `frameId` 只确认到 `cdp-use` 类型和 daemon 原样保存 params。没有活浏览器证据。
- 未把 `helpers.py` 全文写入 artifacts，只核对了哈希和 `cdp` / `pending_dialog` / `new_tab` 调用形。
- 云 dialog 与 daemon 内强制授权仍未完成。客户端拒绝不能算完成。
