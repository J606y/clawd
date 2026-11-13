# clawd 面板兼容契约

入口是 `clawd_server.__main__.main(argv)`。核心的 `clawd panel` 应调用它。本进程只绑定 loopback（默认 `127.0.0.1`，可用 `--port`，`0` 表示临时端口）。

## 目录

- 必须设置 `CLAWD_HOME`。`HERMES_HOME` 未设置时映射到同一目录；已设置则必须相同。
- 拒绝 `/root/.hermes` 与 `~/.hermes`。不读取生产 home、不调用 `/usr/local/bin/hermes`、不访问 `/usr/local/lib/hermes-agent`。
- 配置与技能命令走 `CLAWD_HOME/bin/clawd`，其实现是本仓库 `clawd_server.cli_entry`，再进入 `hermes_cli.main`。没有独立的 `clawd` 控制台脚本时，这是明确的内部命令，不是系统 hermes。

## 路由

| 前缀 | 行为 |
| --- | --- |
| `/panel-auth/renew` | POST。Cookie `hp_auth` 必须是已登记用户的 SHA-256(`hermes-panel:v2:` + 小写用户名 + `\n` + 密码)。成功 204，并下发 `HttpOnly; SameSite=Strict; Max-Age=2592000`。HTTP 不带 `Secure`，HTTPS 才带。 |
| `/panel-api/` | 去掉前缀后转发到进程内 `APIServerAdapter`，注入 `Authorization: Bearer`。不把浏览器 Cookie 转给核心。会话、运行、事件、任务以该适配器为准。 |
| `/panel-core/` | 同进程面板核心路由（`clawd_server.panel_core`），面板 cookie + Origin 鉴权。文件根在 `CLAWD_HOME` 下，不是 `/root/hermes-uploads` 或 `/root/hermes-workspace`。 |
| `/` | 已构建的 `apps/panel/dist`。未构建时 503 `panel_not_built`，接口仍可用。 |

不安全方法要求 `Origin`/`Referer` 与 `Host` 一致。两者都缺时，只接受 loopback 对端。跨站 `Sec-Fetch-Site` 拒绝。

## 冻结字段

- 审批 POST `/panel-api/v1/runs/{id}/approval` 原样转发 `request_id`、`execution_generation`、`choice`。缺身份或代次由核心拒绝，本层不补、不猜。
- 事件流 `GET /panel-api/v1/runs/{id}/events?last_seq=` 流式转发，不断开成缓冲完成体。
- 幂等键 `Idempotency-Key` 原样转发。
- 附件 `absPath` 仍由面板核心复核。拒绝 `..`、点文件、密钥名、`/etc`、`/root/.ssh`、`/root/.hermes`、生产安装目录。
- 401 只表示登录失效。业务拒绝保持 403/409，不能把面板踢回登录页。

## 自动续跑

面板 `_continue_watcher` 源码保留，供旧测试使用。clawd 启动时替换该函数并不启动名为 `auto-continue` 的线程，也不会向 `127.0.0.1:8642` 发续跑。状态展示契约（`/panel-core/run-wait`、前端续跑标记）保留。调度归属 `gateway/clawd_continuation.py`。该模块尚未导出时，就绪接口报告 `continuation=missing`，不伪装成已接管。

## 文件夹

`/panel-core/sessions/folders` 保持现有 schema：`folders` 与 `assignments`，操作仍是 create/rename/collapse/delete/move。不迁移、不删除已有文件夹数据，不引入项目或专属记忆模型。项目契约由父代理另发后再改。

## 已原生化（本进程直接调用核心，不经 hermes CLI，不自写 state.db 查询）

- 配置写入：`hermes_cli.config.set_config_value` / `unset_config_value`。展示仍读 `get_config_path()` 指向的 yaml，并用面板既有脱敏。
- 技能启停：`hermes_cli.skills_config.get_disabled_skills` / `save_disabled_skills`。技能全文仍按 `get_skills_dir()` 下的 SKILL.md 读写，保留备份与 mtime 冲突语义。
- 消息来源：`SessionDB.get_messages` + `hermes_state_runtime.get_session_admission`。没有新增核心查询接口。
- 文件：没有核心文件浏览 API。面板路径拒绝规则在进程内执行，根目录只在 `CLAWD_HOME` 下。

## 仍是旧补偿，不能算完成

这些路由仍进原 sidecar，部分会调用本仓库 `clawd` 包装去跑 `hermes_cli.main`，或只读 state.db / 日志。需要核心接口后才能迁：

| 面板路由 | 现补偿 | 可迁的核心接口（尚未改核心） |
| --- | --- | --- |
| `/sessions/run` `/sessions/execution` `/sessions/background` | 读 state.db / 进程表 | gateway run registry / session authority |
| `/sessions/commentary` `/activity` `/usage` `/subagents` `/context` | 只读 state.db 或 agent.log | SessionDB 展示投影；没有旁白专用读接口 |
| `/models` `/cron-model` `/mcp` `/plugins` `/profiles` `/webhooks` `/settings` | `hermes_cli.main` 子命令 | 对应 `hermes_cli` 模块的进程内函数；改它们会碰到核心文件，先不改 |
| `/live/*` | 本机 CDP | 无 clawd 浏览器生命周期 |
| `/update/*` `/services/*/restart` | 已禁用，返回拒绝 | 不迁到系统 systemd |
| 自动续跑线程 | 已禁用，源码保留 | `gateway/clawd_continuation.py`（未导出） |


- 不重启 systemd 单元，不跑 `hermes update`，不读生产 journal。
- 不自造第二套 run 状态机。
- 外部模型、MCP、实时浏览器缺失时返回上游错误或 `running: false`，验收标阻塞，不用假事件冒充端到端成功。
