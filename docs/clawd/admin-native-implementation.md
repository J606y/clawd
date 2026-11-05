# 管理路径原生化

父代理在 `clawd_server.native.install` 里先把 `mod.config_set` 设成 stdout 包装，再按 browser、runtime、admin 的顺序安装。browser 与 runtime 各有一次安装标记，重复调用 `native.install` 不会再装它们。`admin_native.install` 随后把 `mod.config_set` 换成 `admin_config.config_set`，并标记 `_clawd_config_set_owner = "admin"`。`native.install` 在返回前检查这个标记；若包装仍在，启动失败，而不是静默留下会打印的写入。

未运行测试，未构建。

## 注册链

- 导出仍是 `clawd_server.admin_native.install(mod)`，由 `native.install` 在包装赋值之后调用。
- 新路由不改侧车源码，也不新开监听。`admin_routes.install` 只在侧车模块已有 `Handler._dispatch` 和 `Handler._route` 时，包装后者一次。
- HTTP 仍走 `do_POST` → `_dispatch`。面板密钥检查在 `_dispatch` 内、调用 `_route` 之前。Origin 与会话 Cookie 仍由面板代理的 `_authed` 检查，本模块不复制、不绕过。
- `POST /profiles/delete-preview` 只把 `name` 交给 `preview_delete_profile`。
- `POST /profiles/delete-confirm` 把 `name, confirm, paths, token, fingerprint` 交给 `confirm_delete_profile`。
- 既有 `POST /profiles/delete` 仍只调用 `delete_profile(name)`。多出来的确认字段不会被这个路由读走，函数本身也不再转给确认删除。
- 侧车模块没有 `Handler` 时不挂路由，也不假装已挂上。真实 `bind` 加载的侧车模块有 `Handler`，`native.install` 要求 `_clawd_admin_routes_attached` 为真，否则拒绝完成安装。

## 确认契约

预览不删除。可确认时才签发进程内一次性令牌，120 秒过期，同一名称的新预览作废旧令牌。确认必须同时满足：

- `confirm` 等于名称。
- `paths` 与当次预览顺序和内容完全一致，不只是排序后相等。
- `token` 是当次预览签发、未使用、未过期，且属于当前进程 home。
- `fingerprint` 等于预览时的内容指纹。指纹覆盖计划路径、文件身份，以及目录树里每个普通文件的整文件哈希；符号链接、未知目录项、生产 Hermes 目录、本进程 home、仍在运行的网关，都不签发可用令牌。树过大或无法无跟随读取时拒绝，不发弱指纹。
- 确认时重新计算计划与指纹。路径或内容变了，令牌作废，不删除。

只带名称的旧请求继续 `409 profile_delete_confirmation_required`。错误里的路径列表不是令牌。确认删除不调用会改写进程环境的 `hermes_cli.profiles.delete_profile`，不停网关，不删 systemd/launchd 单元。

## 前端反馈

设置页和插件页仍用原来的布局与样式。成功文案改读服务端事实：

- 配置写入返回 `saved`、`applied: false`、`restartRequired` 与 `output`。表单不再写死「已保存。新对话生效」。环境项显示待重启；其他项显示本进程未热加载。
- 插件切换读 `restartRequired`、`loadedNow: false`、`effectiveNow: false`，不说「可能已经生效」。
- 「设为当前」读 `applied: false` 与 `processSwitched: false`。没有这两项就不说当前配置档已切换。
- 删除先请求预览并列出路径。用户再点「确认删除」才发送 confirm，并带上当次 `paths`、`token`、`fingerprint`。不调用旧的只传 name 删除。

## 明确未完成

| 项 | 原因 |
| --- | --- |
| 进程级 profile 切换 | 多线程不能改全局 env。`use_profile` 只写 sticky |
| 插件热加载 | 只写名单。重启网关后新进程才按名单生效 |
| 投递记录 | 仍是 `501 webhook_deliveries_unavailable`，不返回空列表 |
| update / restart | 宿主禁用，未改 |
| 测试与构建 | 已补 `tests/clawd_server/test_admin_routes.py`，本次未运行 |

`clawd_server/admin_*.py` 不含 `subprocess`、`os.environ[`、`HERMES_BIN`、`redirect_stdout`。
