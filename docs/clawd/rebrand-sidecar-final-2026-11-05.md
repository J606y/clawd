# 侧车第一方展示收口 — 2026-11-05

静态核对。未运行测试、构建、`py_compile`、导入、CLI、网络。未 commit / push / deploy。未删任何文件。未读生产凭据。未改原面板、线上服务或 `/usr/local/lib/hermes-agent`。未改 `apps/panel/src/react/lib/sidecar.ts` 及其他公共 lib、gateway 续跑、UI 源码。编辑器随补丁做了语法检查，不是产品探针，也不构成通过。

真实文件：`/root/hermes-workspace/clawd/apps/panel/sidecar/hermes_panel_sidecar.py`。`apps/panel/sidecar.py` 不存在。

## 已改（只改第一方展示字符串）

| 行 | 现文案 | 未动 |
| --- | --- | --- |
| 63 | 标签 `clawd 网关` | id 仍是 `hermes-gateway` |
| 473 | 超时前缀 `clawd {' '.join(args[:2])} 超时` | 仍调用 `[HERMES_BIN, *args]`，函数仍是 `_hermes` |
| 515 | `clawd 不认识 {key}，且自动撤回失败…` | 撤回仍走 `_hermes("config", …)` |
| 516 | `clawd 不认识配置项 {key}，未写入` | 同上 |
| 823 | `可能是 clawd 自己改的` | 冲突判断未改 |
| 2833 | `clawd 返回的配置不是 JSON` | 仍是 `config get --json` |
| 3279 | `clawd 返回的插件列表不是 JSON` | 仍是 `plugins list --json` |
| 5047 | `clawd 环境不可用或超时` | 进程仍是 `HERMES_PY` / `HERMES_SRC` |
| 5051 | `clawd 版本可能变了` | 同上 |
| 5242 | `读不到 clawd 安装目录的版本信息` | 仍读 `HERMES_INSTALL` 的 git HEAD |

473 的前缀只是错误展示。调用仍是 config / mcp / plugins / profile / webhook，不是 update。这不表示已经执行了 `clawd` 命令。

## 保留及原因

- 服务 id：`hermes-gateway`、`hermes-live-browser`、`hermes-panel-sidecar`，以及 `RESTARTABLE`。重启和 `systemctl` 用的是真实单元名。
- `HERMES_BIN`（56）、`_hermes()`（464–471）、`HERMES_HOME`、`HERMES_PY`、`HERMES_SRC`、`HERMES_INSTALL`。
- 5351–5354：实际命令仍是 `[HERMES_BIN, "update", "--plan"]`，超时文案仍是 `hermes update --plan 超时`。`clawd update` 不可用，不能改这条错误归属，也不声称更新可用。
- 5308 `UPDATE_SH`、5454 的 `hermes-update` argv0、5464 日志，都是真实 `hermes update` 启动事实。
- 5213 `Hermes Agent (v…)` 是解析 `HERMES_BIN --version` 的真实输出。改正则会让版本提取失败。不匹配时仍原样回传前 80 字，不改写成 clawd。
- 5850 `journalctl -u hermes-panel-sidecar` 是真实单元名，不是产品标签。
- 注释、文档字符串、日志名、`hermes.run`、`User-Agent`、路径和兼容前缀未按字符串替换。
- `absPath` 仍在 1434、1516、5755、5971。布局、间距、动画未改。既有脏改动未删。

绑定后的活路径会盖掉部分本文件常量，本路未改 `clawd_server/bind.py`：

- `bind.py:97-102` 把 `SERVICES` / `_service_states` 换成 `clawd-panel` / `clawd 面板`。63 行标签不是绑定后状态页的返回值。
- `bind.py:108-123` 拒绝 `start_update`，并把 `get_update_plan` / `get_update_info` 换成不访问系统安装目录的响应。5242 和 5354 不在这条活路径上。本文件仍保留真实命令，避免未绑定调用被改成不可运行的 `clawd update`。

## 五次成功续跑：显示未移除

本文件没有「已连续自动继续 5 次」的用户文案。可见提示不在侧车，本路未改、也不伪称已取消。

精确调用（面板，不是本文件）：

- `apps/panel/src/react/pages/chat/chat-page.tsx:18` 从 `@/lib/transcript` 引入 `AUTO_CONTINUE_LIMIT, continuationState`。`tsconfig.json` 与 `tsconfig.react.json` 的 `@/*` 指向 `./src/react/*`。
- 同文件 677：`const limited = continuationState(messages).limited;`
- 同文件 1081：`limited && !shown` 时渲染「已连续自动继续 {AUTO_CONTINUE_LIMIT} 次…」。
- `apps/panel/src/react/lib/transcript.ts:134`：`AUTO_CONTINUE_LIMIT = 5`。
- 同文件 281–297：`continuationState` 返回 `limited: trigger !== null && streak >= AUTO_CONTINUE_LIMIT`。136–137 只数以 `[面板自动续跑]` 开头的用户行。
- 副本 `apps/panel/src/lib/transcript.ts:134`、281–297 有同样逻辑。聊天页的 `@/` 导入不到这份副本。

侧车旧门禁仍在，未改常数，未恢复调度：

- 4111 `CONTINUE_MAX_ATTEMPTS = 5` 是 HTTP 失败重试，不是成功次数。
- 4116 `AUTO_CONTINUE_LIMIT = 5`。
- 4157–4182 `continuation_trigger` 返回 `(trigger, streak >= AUTO_CONTINUE_LIMIT)`。
- 4527–4528 在 `limited` 时不入队；4569–4572 同样跳过。这两处是静默跳过，不是界面提示。
- 6175 `main()` 仍会起 `auto-continue` 线程。未删、未改。

活路径不进这条线程：`clawd_server/bind.py:135-145` 把 `_continue_watcher` / `_continue_tick` 换成抛错，并拒绝 `sidecar.main()`。`clawd_server/server.py:256` 固定 `legacyWatcher=disabled`。

核心只做了静态阅读，未改、未跑：`gateway/clawd_continuation_decision.py:18` 的 `MAX_STREAK = 5` 注释写明只观察；336 写明成功连续次数不是门禁；337 拦截的是 `attempts >= MAX_ATTEMPTS`（失败预算），`decide()` 不返回 `streak_limit`。`gateway/clawd_continuation_journal.py:25` 仍有 `STREAK_LIMIT_REASON`。这不能证明界面提示已消失。界面仍按上面的面板调用自行计算 `limited`。

## 未运行

未运行 `scripts/run_tests.sh`、面板测试、类型检查、构建、导入、`py_compile` 或 `clawd` / `hermes` 命令。以上均为源码回读。
