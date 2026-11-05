# clawd 集中验收就绪 — 2026-11-05

静态接线核对。未运行测试、构建、import、py_compile、CLI、浏览器或生产读取。不是通过，不是上线许可。未改源码、门禁、`implementation-completion-manifest.json`。未对在写文件取最终指纹。

旧快照不得再当当前结论：`resumption-integration-gaps` / `resumption-acceptance` 的“路由未注册、六份 detail 未到、package 只 import `build_wheel`、NOTICE 不存在”已过时。`session-handoff`、`handoff.md`、`verification.md`、`source-map.md`、`integration-review.md` 是历史分工或改造前基线，不是本表。

等终态、本表不读中间字节：`scripts/clawd_acceptance/execute.py`、`stages.py`、指纹与相关测试；`tools/vendor/browser_harness_0_1_13/NOTICE` 与 `tests/tools/test_browser_use_cli.py`。这两处落地前不要开 `run`，也不要把旧缺口再派一遍。

## 状态

| 类 | 含义 | 现在 |
| --- | --- | --- |
| 实现齐待运行 | 注册或调用点已在当前源码见到 | 下面接线表。零退出码 |
| 具体未接 | 批准能力没有真实消费者，或必须保持拒绝 | 宿主缺口；不是 typed 钩子未用 |
| 外部依赖 | 缺了就是 blocked/2，不模拟、不读生产 | 模型、MCP、Chromium、安装前缀、cutover journal、新档案 |
| 仅审查 | 未授权修 | 下面 9 个 ID。不修其余 `bg-*`，不重审 63 条 |

`implemented`、prepare 进程 0、模块存在、run 被接受，都不是 pass。

## 已见到的注册与调用

活入口 `python -m clawd_server`。`server.py` 先 `load_sidecar` 再 `bind`。`bind.py:149` 调用 `native.install`，并替换 `_continue_watcher` / `_continue_tick` / `main`。`native.install` 顺序：browser、runtime 各至多一次，然后 `admin_native.install`；后者 `admin_routes.install`。admin 未留下自己的 `config_set` 或未挂上删除路由会抛错。`api_server.connect` 先挂 `_http_route_table`（含 `clawd_runtime_routes.http_routes`），再 `register_project_routes`，再 `register_effort_routes`，最后才是 `/p/{profile}/{tail:.*}`。

| 域 | 调用点 | 待运行，不是缺口 |
| --- | --- | --- |
| 项目 | `register_project_routes`；`GET/POST /api/projects/{id}/memory` | 项目首发、分叉、迁移恢复、跨库窗口 |
| 继承 | `session_worker_child.py` 调用 `copy_parent_project_before_release`；`hermes_state_sessions.py`、`hermes_state_compression.py` 调用 `inherit_session_project` | 未把 `runtime_session_child.py` 写成已证明或未接：它不在 inheritance 导入命中里 |
| 记忆改绑 | `turn_facade.py` 在租约准入后、`conversation_loop` 前，仅 facade owner 调用 `_idle_project_memory_retarget` | 无项目记忆页是既定约束，不新建 |
| effort | 路由已挂；`session_api_turn.commit_pending_if_any(..., admission_held=True)`；`refresh_continuation_spec` 在冻结前提交；`FROZEN_LAUNCH` 已设 | 续跑用例与核心发现 |
| 续跑门槛 | `decision.py` 成功次数只观测，不返回 `streak_limit`。`MAX_ATTEMPTS` 仍是失败预算。面板 ts/tsx 无 `AUTO_CONTINUE_LIMIT` /「已连续自动继续」 | 旧 streak 硬停断言须已随用例改完；未跑 |
| runtime | `runtime_native.install` 替换 `get_session_run` / `get_run_wait`；事实缺失是 unknown，不是 0 | 未安装 native、绕过 `bind` 的旧函数仍在侧车文件。产品入口不走它。不要删侧车假装退役 |
| todo | `GET /api/runtime/subagents` → `list_subagents`；chat-page 仅在 `profileKey !== "unknown"` 时投影，不写 `default`，不把 `returned` 改成 `completed` | 活动页刷新与人数。`active_count` 仍是 completion units |
| 事件 | `runs.ts` 的 `foreignIdentity` 有调用；错配最多 3 次、250ms。面板 SSE 代理设 `Cache-Control: no-cache`、`X-Accel-Buffering: no`，用 `iter_any` | 网关侧同名 identity 检查本次未定位。不据此新派，也不把旧报告写成已核 |
| 设置 | `admin_routes.py` 已有技能、MCP 新增、自定义端点、`/settings/advanced`、工具集、记忆提供方、连接、插件配置、日志。`install` 已接上 | 见下节。无安全接口的项保持拒绝 |
| 定时任务 | 不新路由。`jobs-tab.tsx` 提交已有 `deliver` / `skills` / `repeat` | 保存回读与生效时机 |
| 浏览器 | `browser_native.install` 在 `native.install` 里 | 迁移未完成。NOTICE 与 `test_browser_use_cli` 等终态。`frameId` 是否由活浏览器发出未探针。`windowId` / IO 无映射。45 秒租约与 300 秒硬顶不改 |
| 包 | 验收入口已存在。执行器与 stage 等终态 | 没有新 wheel/sdist 就是 blocked/2。两个 `CLAWD_DISTRIBUTION_*` 都空时不得开包绿灯 |

直接跑侧车脚本仍会碰到旧 `get_session_run` / `main`。那不是 `bind` 后的产品路径。`build/lib` 是旧副本，不删、不当活入口。

## 设置：钩子未用不是缺路由

这些页面打的是已注册路径，不为了改走 typed 函数而重构：

| 页面实际消费者 | 路径 | 未用、不补工 |
| --- | --- | --- |
| `skills-tab` → `useSetSkillEnabled` | `POST /skills/enabled` | 无 |
| `mcp-tab` 调用 `createMcp` | `POST /mcp/servers` | `useCreateMcp` |
| `models-tab` 自己的 `createCustomEndpoint` POST 同一路径 | `POST /models/custom-endpoints` | `admin-api` 同名函数与钩子。页面不读 `ProviderInfo.hasKey` |
| `config-tab` 调用 `getAdvanced` / `setAdvanced` | `GET/POST /settings/advanced` | `useAdvanced` / `useSetAdvanced` |
| `plugins-tab` 直接 `getPluginConfig` | `GET/PUT /plugins/config` | `usePluginConfig` |
| `memory-section` / `tools-section` / `connections-section` 本地 `request(...)` | `/memory/providers`、`/tools/toolsets`、`/connections/platforms` 及对应写入 | typed 函数和钩子 |
| `jobs-tab` 提交三字段；`logs-tab` 走 `sidecar.logs` | 既有 jobs / `GET /logs` | 无新路由 |

`apps/panel/src/lib/sidecar.ts` 不在面板 tsconfig 编译根。文件夹路由仍走面板路径策略，冻结，不另造核心文件浏览器。

必须保持缺口，禁止假开关、空列表、`applied:true`、`processSwitched:true`、`loadedNow:true`：宿主更新与 `clawd update`、宿主重启、进程级 profile 切换、插件热加载与安装器、记忆 setup、电脑控制授权、MCP 目录安装、消息配对、环境明文、webhook 投递日志 501、systemd journal 501。更新保持 403。

## Honcho 残留

不是全站完成。CLI 未跑，不能把“未注册”或“已支持”写成运行时结论。

已加未知声明、示例仍是 `hermes honcho` 的两页：

- `optional-skills/autonomous-ai-agents/honcho/SKILL.md`（5 处「未验证边界」）
- `website/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-honcho.md`

仍把 `clawd honcho` 写成命令或激活后会注册的能力，未改：

- `website/docs/user-guide/features/honcho.md`
- `website/docs/user-guide/features/memory-providers.md`
- `website/docs/reference/cli-commands.md`
- `website/docs/guides/migrate-from-openclaw.md`
- `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/honcho.md`
- 同树 `user-guide/features/memory-providers.md`
- 同树 `reference/cli-commands.md`
- 同树 `guides/migrate-from-openclaw.md`
- 同树 `user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-honcho.md`（仍是 `clawd honcho` 命令表，没有英文页那条未知声明）

`clawd plugins install`、`clawd memory setup|status|off` 不证明 `honcho` 子命令存在。

## 许可：三层分开

依据 `license-scope-independent-2026-11-05.md`。提取器未跑。不能说四包都要额外书面授权，也不能套 MIT。

| 包 | metadata | 提取器 | 作用域 | 不能推出 |
| --- | --- | --- | --- | --- |
| `hermes-tui`、`@hermes/shared` | 无 license 字段 | sibling 规则会标 unknown | 在带根 LICENSE 的同一提交树，文本无目录排除 | 不是已裁定 MIT，也不是必须新书面授权 |
| `@hermes/ink` | 同上 | 同上 | 树内 fork；上游 `license` 副本不在树里 | 根 MIT 不能代替上游通知；锁里的 MIT 字符串不是裁定 |
| `@clawd/panel` | 同上 | 同上 | 不在父提交树。父 MIT 不因目录嵌套附着 | 未知。书面授权是关闭未知的一条充分路径，不是已证明的法律义务 |

根 `LICENSE` 无 except。提取器只认同目录 notice，所以四包 unknown 是规则结果，不是排除条款。开包前不要写 `license: MIT` 或把根 LICENSE 拷进子目录。

## 审查：只重核这些 ID

`core-audit/final-findings-2026-11-05.json` 63 条不重审、不相加、不修。建议代码未应用。引用哈希早于续跑冻结收口，行号作废。

因引用文件在该归并之后被改，集中验收前只重核当前字节，不授权修复：

| ID | 归并时引用 | 为何重核 |
| --- | --- | --- |
| `bg-receipt` `bg-lease` `bg-human` `bg-busy` `bg-window` `bg-swallow` | `clawd_continuation.py` 和/或 `admit.py` | 冻结收口改过这两文件。回执仍在 admit 返回后 |
| `bg-pid` `bg-stop` | `admit.py` | 同上 |
| `bg-plan` | `decision.py` 与未改的 `gates.py` | `decision.py` 只加了注释。归并已作误报。重核行号，不升格、不修 |

不重核：`bg-claim-true`、`bg-sidecar`、`bg-approval-key`（引用文件不在这次续跑改动里）。`exec-effort` 归并时已按当时 `session_api_turn` 判 fixed。不扩大成全部 `bg-*`。

## 现有门禁，不另造

| 用途 | 路径 |
| --- | --- |
| 唯一入口 | `/root/hermes-workspace/clawd/scripts/clawd_acceptance/accept.py` |
| 契约门 | `scripts/clawd_acceptance/gate.py`；只认 schema 1 与 `implemented/unverified/pending/blocked` |
| 共享清单 | `docs/clawd/implementation-completion-manifest.json`。现在 `schema_version=1`、`implementation_complete=false`、`observed_at=2026-11-05T03:07:52+0800`。过期。本报告不改。两处终态落地后由集成人改，改完才可能 `run_allowed` |
| 阶段与退出码说明 | `docs/clawd/final-acceptance-matrix.md` |
| 隔离与 argv | `scripts/clawd_acceptance/isolation.py`、`constants.py` |
| 报告 schema | `scripts/clawd_acceptance/report.schema.json` |
| 解释器 | `/root/hermes-workspace/clawd/.venv/bin/python` |
| Node | `/root/hermes-workspace/clawd/.dev-home/tools/node-26.12.1-linux-x64/bin` |
| 验收根 | `artifacts/final-acceptance`。数据在 `account-home/.clawd` |

无参数退出 2。`prepare` 进程 0 只表示写了 `artifacts/final-acceptance/prepare-plan.json`，验收仍是 2。缺 `--run` 或缺 `--implementation-complete` 退出 2，不启动阶段。必需检查非空且全 pass 才 0；任一失败 1；无失败但有 blocked、unknown、not_run、interrupted、空结果或必需检查为空，则 2。失败优先于 blocked。

`run_tests.sh` 用 `env -i`。转发 `HOME`，不转发 `CLAWD_HOME` / `HERMES_HOME` / `TMPDIR`。转发的 `HOME` 必须是 `account-home`，子进程数据才是 `$HOME/.clawd`。父进程 `CLAWD_HOME` 不是隔离证明。父进程 `HERMES_HOME` 设成该数据目录，避免包装器去读账号生产工具库。`--basetemp` 是参数，能穿过 `env -i`。禁止 `HERMES_HOME=/root/.hermes`，禁止系统 `python3`，禁止 `/root/panel-deploy.sh`，禁止 `/usr/local/lib/hermes-agent`。

重型串行：核心 `-j 1`，只发现一次 `tests`，不要把 `clawd_race_cases`、`clawd_file_auth_cases`、`clawd_memory_scope_cases`、`clawd_event_recovery_cases` 再传一遍。日志里看不到这些目录不能把核心记 pass。面板 Node `--test-concurrency=1`，墙钟至少 900 秒。35 或 240 秒杀掉是编排错误，记 blocked，不是产品失败。pytest 每文件 300 秒。TUI `npm run check` 已含内部步骤，只记一次。

## 将来一次启动

两处终态落地、集成人更新 schema 1 清单、且 blocking 项均为 `implemented` 之后。现在即使加上旗标，门禁也会拒绝。那是正确拒绝。

```sh
PY=/root/hermes-workspace/clawd/.venv/bin/python
REPO=/root/hermes-workspace/clawd
"$PY" "$REPO/scripts/clawd_acceptance/accept.py" prepare
"$PY" "$REPO/scripts/clawd_acceptance/accept.py" run \
  --run --implementation-complete
```

可选且缺了就 blocked，不编造：`--provider-config <非生产、含真实凭据、不是 /root/.hermes>`、`--install-prefix <隔离前缀>`、`--rollback-entry <隔离 journal>`。恢复中断才加 `--resume-interrupted`。不要另写第二套验收脚本。矩阵里的 `python3` 指同一入口，实际用上面的 venv。

编排器内部顺序以 `final-acceptance-matrix.md` 为准，单路：core → tui-check → tui-build → tui-pty → panel-typecheck → panel-build → panel-node → panel-python → panel-browser → package → license-inventory → verify-integration → real-model-mcp → continuation → rollback。

独立条件，不能互相冒充：

- 真实浏览器：`/usr/bin/chromium`，`apps/panel/scripts/test-gates.mjs --target=browser`。没有就不启动，不 mock。
- 真实 TUI：已构建 `HERMES_TUI_DIR`，PTY，`TERM=xterm-256color`，先写入 `/quit` 并看到回显再判超时。强制杀掉不是干净退出。`storage_unavailable` 不能被后来的 ready 抵消。与浏览器门无关。
- 真实模型：请求 `grok-4.7` 且完成真实回合。`test-not-llm`、日志广告名、空 MCP body 不能通过。缺 `runtime.model` 是 blocked，不是父代理不符。
- 脱源码：隔离目录里本次唯一 wheel 与 sdist，两个变量都指向它们，再按 `RESOURCE_CONTRACT.json` 开包。源码可导入不是开包通过。执行器终态未到，本表不写它的当前 argv。
- 许可提取：`scripts/clawd_inventory/generate.py --checkout <repo> --output <隔离 json>`。unknown 保持 unknown。
- 回滚：只对隔离 target 与隔离 journal。没有 journal 就不调用。

静态首页同时核构建产物与 `window.__CLAWD_ARTIFACT_ROOTS__` 注入（`clawd_server/server.py`、`scripts/verify_clawd.py`）。附件根注入会改变 HTML，不能和原始文件逐字节比，也不能只看 HTTP 200。

验收通过也不构成 commit、push、deploy 或生产迁移。
