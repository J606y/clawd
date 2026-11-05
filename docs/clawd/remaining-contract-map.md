# clawd 剩余契约接线清单

父代理接续注记：本文件是03:07的开发中快照，并非当前缺口事实。runtime/admin/browser安装钩子、资源定位、迁移模块和effort服务已在后续落地或接线；具体当前状态以handoff与各路最终源码核对为准。implementation-completion-manifest暂不作实现已全部完成的证据。附件问题已独立派deleg_ac7020e7补齐；其余原pending项需要冻结后去重，不按本快照重做。未执行测试。

观察时刻：`2026-11-05T03:07:52+0800`。只读静态核对，未跑测试、构建或请求。`implemented` 只表示当时代码里接线已在，不是测试通过。并行路文件在变；缺文件是未落地，不是回归。

已排除、本清单不再展开：`CLAWD_HOME` 拒绝生产 home（`clawd_server/home.py:resolve_home`）、配置/技能进程内写入（`clawd_server/native.py:install`）、续跑线程已替换且 `arm_adapter` 已接（`bind.py:133`、`server.py:_start_continuation`）、审批三字段原样转发且核心核对代次（`api.ts:398-400`、`server.py:_proxy`、`api_server_authority_runs.py:respond_run`）、重启/更新拒绝、loopback、项目路由当前已挂（见下，迁移路可能改）。

## 父代理接线点（现在不要抢改）

公共入口仍只有父代理可动：`clawd_server/native.py`、`bind.py`、`server.py`。runtime 占 `gateway/platforms/api_server.py`，返回前父代理不能改它。

### 共享 install hooks

当前唯一调用：`clawd_server/bind.py:bind` 第 142–145 行，只执行 `clawd_server.native.install(mod)`，然后 `file_roots_under_home`。

插入点：`install(mod)` 之后、`file_roots_under_home` 之前，按此顺序调用（模块此刻都不存在，现在 import 会让启动崩溃）：

1. `runtime_native.install(mod)` — 文件未出现。runtime `sa-0-fb421060`。
2. `admin_native.install(mod)` — 未出现。admin `sa-1-7c803b11`。
3. `browser_native.install(mod)` — 未出现。browser `sa-0-94563a10`。

这三处应替换侧车函数，不在 `server.py` 再挂一套同名路由。`PanelServer.start`（`server.py:98-103`）只有 `/clawd/ready`、`/panel-auth/renew`、两条通配反代和静态文件，没有扩展表。

### register routes

API 注册在 runtime 拥有的 `APIServerAdapter.connect`（`gateway/platforms/api_server.py:3980-3987`）：

- 先 `_http_route_table()`，并镜像 `/p/{profile}`。
- 已调用 `register_project_routes`（3983–3984）。父代理不要再注册一遍项目路由。
- 最后才是兜底 `* /p/{profile}/{tail:.*}`（3987）。aiohttp 先注册先匹配，兜底之后再 `add_route`，profile 镜像会被吞掉。

effort 路由必须由父代理在 runtime 交还 `api_server.py` 之后、3987 之前插入。runtime 已被通知不要自己注册 effort。此刻没有 `_extension_routes` 之类回调，所以 effort 模块即使写出来也无法挂上。状态：blocked，不是 effort 路能单独做完的。

建议父代理在交还后加一次、且只加一次：`connect()` 在 3987 之前消费 `adapter` 上的扩展路由列表。effort 路只提供注册函数，不改 `api_server.py`。

## 必须修（当前树里的洞）

1. 准入回合没有 interim/reasoning 观察者。`api_server_runs.py:_execute_run` 1168–1171 的 `observe_api_turn` 只传 text/tool；`_interim_cb` 只在非准入分支 1180。负责：runtime。暂定 pending。`api_server_runs.py` 约 284 分钟前有改动，但这段仍缺。
2. 运行事实仍是侧车第二套库。`hermes_panel_sidecar.py` 仍实现并路由 `get_active_sessions`、`get_session_run`、`stop_session_execution`、`create_edit_branch`（5231，`mode=rw`）、`get_commentary`、`get_session_background`。消费者 `src/react/lib/sidecar.ts`。负责：runtime + `runtime_native.install`。pending。
3. usage/context/subagents 仍直读。`get_usage` 2338 读 `state.db`；`get_context` 4215 刮 `agent.log`；`get_subagents` 4418 读 `state.db`。消费者 `queries.ts:267/336/345`、`agent-dock.tsx:490`。API `_http_route_table` 无对应路由。负责：runtime。pending。
4. 管理路由仍是 CLI。`_route_admin` 5308；`get_cron_model` 2612；`use_profile` 2886 调 `_hermes("profile","use")`。`admin_native.py` 不存在。消费者 `queries.ts` 的 models/cronModel。负责：admin。pending。
5. 浏览器影子库和工具改写仍在。`/live/task` 5232；`apps/panel/plugins/browser-window-guard/__init__.py` 与 `sidecar/live_task_store.py`、插件内同名副本都在。插件文件约 1681 分钟未改。`browser_native.py` 不存在。消费者 `task-live.ts`、`live-dock.tsx`。负责：browser。pending。
6. 显式迁移脚本不在。`clawd_projects/migration.py` 不存在。项目列表路由已挂，不要当成没做。负责：migration `sa-2-7900d665`。pending。
7. effort 仍在浏览器。`effort.ts:37` `hp_effort_by_session`。无会话持久化路由。文件约 1681 分钟未改。负责：effort `sa-0-7dfefca9` 写服务；挂载 blocked 于父代理（见上）。
8. 上传 absPath 与消费者根不一致，且符号链接根未在每次打开时按 CLAWD_HOME 重核。
   - `bind.py:80` 把 uploads 绑到 `CLAWD_HOME/uploads`。
   - `_upload` 5209 返回 `_root()` 解析后的 `absPath`。
   - `_artifact` 5107–5109 用未 resolve 的 `spec["path"]` 做 `relative_to`。home 本身是符号链接时，解析后的 absPath 对不上词法根，预览 403。
   - 下载 `_open_download` 968–975 有 `O_NOFOLLOW`，文件级符号链接是失败关闭，这点已在。
   - `file_roots_under_home` 只在 bind 时查一次。之后把 uploads 换成指向家外的符号链接，`_root` 会把解析结果当成允许根。
   - 消费者 `turns.tsx:18` 写死 `UPLOAD_ROOT=/root/hermes-uploads/`。`artifacts.ts:11` 仍保留 `/root/hermes-uploads/`、`/root/hermes-workspace/`、`/root/.hermes/images/`、`/root/.hermes/cron/output/`，另读 `__CLAWD_ARTIFACT_ROOTS__`（`server.py:50-51` 注入的是 CLAWD_HOME 根）。
   - 无人拥有：`apps/panel/src/react/pages/chat/turns.tsx`、`apps/panel/src/react/lib/artifacts.ts`、`apps/panel/src/lib/artifacts.ts`、侧车 `_root`/`_artifact`/`_upload`。父代理拥有 `bind.py` 的根，但不能把这个洞算进现有九路。

## 当前路负责、尚未落地

| 路 | 本快照 | 暂定 |
| --- | --- | --- |
| runtime `sa-0-fb421060` | 无 `runtime_native.py`；`api_server.py` 无 `input_origin`；准入观察者未接 | pending |
| admin `sa-1-7c803b11` | 无 `admin_native.py`；`_route_admin` 仍 CLI | pending |
| migration `sa-2-7900d665` | 无 `migration.py`；`register_project_routes` 已在 3983 | 脚本 pending；路由 unverified（路可能改） |
| browser `sa-0-94563a10` | 无 `browser_native.py`；插件与 live store 仍是旧副本 | pending |
| distribution `sa-1-6e165d26` | `pyproject.toml` 的 package-data 无面板静态资源/sidecar/browser-window-guard；无 `MANIFEST.in`。源码树 `apps/panel/dist/index.html` 存在，`server.py:306` 只从源码树供静态文件 | pending。已知 wheel 不含 `apps/panel`，不是新回归 |
| effort `sa-0-7dfefca9` | localStorage 仍在；禁止改既有 api_server/state | pending；挂载 blocked |
| cutover `sa-1-bd80db8f` | `scripts/clawd_cutover` 不存在。只许写该脚本和专属文档 | pending |
| acceptance `sa-2-772596c7` | `scripts/clawd_acceptance` 不存在。本清单不是 implementation-complete | pending，不阻塞产品接线 |

## 需确认，不要塞进现有路

- 面板 `use_profile` 文案已写明不切换运行中网关（`hermes_panel_sidecar.py:2893`）。进程内 API 适配器绑在启动时的 `CLAWD_HOME`，`server.py:_proxy_api` 不加 `/p/{profile}`。是否要让面板切档重绑活适配器，是新范围。当前单 home 不是缺陷。
- cron 面板走侧车 `cron-model`，不是 `/api/jobs`。`/api/cron/fire` 与 `/v1/runs/{id}/events` 没有静态共享游标。事件顺序是否要并进 run SSE，需确认。无人拥有这个并轨。
- `get_run_wait` 4302 仍标 `source: log`。调度已在核心；展示投影没人认领。不阻塞调度所有权。
- `cmd_dashboard`（`hermes_cli/main.py:2664`）在 `CLAWD_CLI=1` 或缺少 `web/package.json` 时拒绝。`web/package.json` 与 `apps/desktop` 当前不存在，所以拒绝有效。函数体后半仍是旧 dashboard。删残留需确认，不属于 cutover 的 `scripts/clawd_cutover` 范围。
- 侧车 500 文案仍写 `journalctl -u hermes-panel-sidecar`（5095）。无人拥有，不阻塞。

## 验收门禁

`implementation_complete` 现为 false。blocking 项全部变成静态 `implemented` 且父代理另行确认之后，验收路才能当实现收齐。本文件不代表测试通过，冻结后还要再核对一次，因为九路仍在写。
