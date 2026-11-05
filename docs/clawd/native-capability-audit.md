# clawd 原生能力静态盘点

状态：只读基线盘点，不是整合验收。上游核心 `c314285f077e91937c995d419b9fa40af9eb4981`；面板 `hk` `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29`。未读其他 worktree 的未合并 diff，不把三路自报当最终实现。用户确认面板侧仍有 CLI 绕接和 `state.db` 直读，与下面基线一致。父代理必须在合并后复验代码和测试。

原生指调用已有核心函数或在其真实扩展点补能力。把 `hermes` 子进程改名为 `clawd`，或把侧车 SQLite 搬进 `clawd_server`，都不是原生。不得削掉文末安全边界。

`patches/hermes-agent-8edc5ea2-recovery.patch` 针对 `8edc5ea2`，不能照贴到本基线。观察者键已经存在；来源戳仍然没有。

## 现有分工覆盖

| 路 | 源图归属 | 本盘点结论 |
| --- | --- | --- |
| 核心裁剪 `clawd-core-work` | 官方 Web/Desktop、入口、`CLAWD_HOME` | 不覆盖下面任何补偿 |
| TUI `clawd-tui-work` | 可见品牌 | 不覆盖 |
| 绑定 | `clawd_server` 兼容路由 | 用户确认 CLI 绕接和 `state.db` 直读仍在。`docs/clawd/api-contract.md` 仍把配置/技能交给 `CLAWD_HOME/bin/clawd` → `hermes_cli.main`。这是换名的子进程，不是进程内 API |
| 续跑 `clawd-resume-work` | `gateway/wake.py`、`api_server.py`、`api_server_runs.py`、新 `gateway/clawd_continuation.py` | 只衔接，不重设计。该路改 `api_server_runs.py` 时必须带上第 2 条观察者注册，否则会互相覆盖 |

## 尚需新增实施

第 1–5、7–9 条没有被上述分工实现。第 6 条绑定层若只保留 CLI 转发，仍算未做。第 10–12 条是保留边界或父代理专路，不要塞进绑定层的侧车拷贝。

---

## P0 正确性 / 安全

### 1. 消息来源：写入被标成内部通知，读取再旁路 SQLite

补偿：`get_message_origins`（`hermes-panel/sidecar/hermes_panel_sidecar.py:2105-2134`）只读连接 `state.db`，用 `messages.platform_message_id = session_admissions.admission_id` 且 `principal_id='api'`、`intent IN ('queue','steer')`、`json_type(payload_json,'$.api_turn_v1')='object'` 才返回 `api_queue` / `api_steer`。

消费者：`src/react/lib/api.ts:288-307`。先删掉响应里的 `input_origin`，只对 `display_kind=internal_notification` 的用户行补标。失败保留已加载历史。

根因在基线核心，不在面板：

- `gateway/session_ingress.py:346-347` 在执行信任戳 `event.internal = True` 之前没有保存“这是普通 API 人话”。
- `gateway/response_filters.py:174-189` `display_kind_for_event`：`internal` 且不是 producer card 就返回 `internal_notification`。
- `APIServerAdapter._message_response`（`gateway/platforms/api_server.py:2475-2481`）的安全字段没有 `platform_message_id`，也没有来源。

已有扩展点，不要新造第二张来源表：在 `execute_admission` 把 `internal=True` 之前写下私有戳（不读客户端 metadata）；`display_kind_for_event` 在 hidden / producer card / heartbeat 之后才放行普通 API 人话；`_message_response` 只增加服务端算出的 `input_origin`。`gateway/session_display.py` 的 `admission_display` 仍优先，不能被这个戳覆盖。

归属：`gateway/session_ingress.py`、`gateway/response_filters.py`、`api_server.py`。绑定层删除 `/sessions/message-origins`。

退役：消息列表在同一次会话查询里带上来源；侧车端点与直连 `session_admissions` 删除。验收：queue/steer 显示为人话且不可当编辑授权；真正的内部通知、hidden、heartbeat、producer card 仍保持原 kind。来源查询失败不得清空历史。

### 2. 准入回合的实时旁白 / 思考没有注册观察者

`observe_api_turn` 已接受 `_OBSERVER_KEYS`（`gateway/session_api_turn.py:19-20`），含 `interim_assistant_callback`、`reasoning_callback`。`wire_api_observers`（同文件 `476-513`）会把 agent 回调发布给已注册观察者。

缺口：准入路径 `api_server_runs.py:1126-1129` 只注册了 text 和 tool。非准入路径 `1138` 才把 `_interim_cb`（`1074-1082`，事件 `message.interim`）交给 agent。`reasoning.available` 的载荷映射在 `145`，准入路径没有对应回调。

这不是续跑。续跑路正在改同一文件，父代理合并时必须把这两个 kwargs 接上已有 `_interim_cb` 和 `tool_cb("reasoning.available", ...)`，不要再打 `8edc5ea2` 补丁，也不要在面板重放思考。

退役：准入 run 的 SSE 出现 `message.interim` 与 `reasoning.available`；迟到回调仍被 `check_approval_generation` 丢掉。面板不再为了补思考去读库。

### 3. 租约 / run 活性是第二套状态机，取消还直删租约

补偿与消费者：

- `get_active_sessions` `3262-3308`，`src/react/lib/sidecar.ts:382`。按 `session_turn_leases.conversation_id = sessions.id` 连接，再用自写 `/proc` 判断死活。
- `get_session_run` `3362-3437`，消费者 `sidecar.ts:396-399`。直读 `runs_idempotency.db`。持有者已死时只在返回值里把 status 改成 `interrupted`（`3176-3177`），不写回。这会和 `GET /v1/runs/{id}` 不一致。
- `stop_session_execution` `3491-3540`，消费者 `sidecar.ts:403`，确认词 `stop-cli`。`pidfd` 杀进程后 `DELETE FROM session_turn_leases WHERE conversation_id=?`（`3536`）。

核心已有、侧车没有用：

- 租约键是压缩谱系根，不是原始 session id：`SessionDB._session_turn_lease_key_on_conn`（`hermes_state_compression.py:685-709`）。侧车按原始 id 读写，压缩子会话会看错行或删错行。
- 死进程回收：`_compression_lock_holder_process_is_dead`（`hermes_state.py:142-169`）加 `try_acquire_session_turn_lease`（`compression.py:719-741`）。未知 holder、本进程、外部 pidns 保持占用直到 TTL。侧车 `_session_lease_identity`（`3181-3186`）对未知格式返回 None，再把未知当 busy，这一点方向对，但实现是另一套 `/proc` 解析。
- 释放：`release_session_turn_lease(session_id, holder)`（`833-842`），内部会走到谱系根。
- API 取消：`stop_run`（`gateway/platforms/api_server_authority_runs.py:138-167`）走 `authority.cancel_queued` / `authority.interrupt`，要求 generation。公开路由是 `POST /v1/runs/{run_id}/stop`（`api_server.py:71`）。
- 幂等记录：`RunIdempotencyStore.lookup`（`api_server_run_idempotency.py:157`）要 scope+key+fingerprint；`status_for_run`（`189`）要 run_id。没有按 session 发现 run 的方法。侧车自己扫 `status_json`。

要新增的核心能力：一个按谱系根读租约、复用 `_compression_lock_holder_process_is_dead` 的只读投影；一个按 session 发现当前 run、不改写存储 status 的读方法。面板只调这两个读，加上已有 `POST /v1/runs/{id}/stop`。

CLI 的 `pidfd` 停止是宿主边界（`3442-3444`：绝不杀共享网关，还检查 `hermes_cli.main` 与 cgroup）。隔离 CLI 若仍存在，杀进程留在宿主，但删租约必须改成 `release_session_turn_lease`。面板若只跑 API run，这条杀进程路径退役，不要移植。

退役：侧车不再打开 `state.db` / `runs_idempotency.db` 判断 busy。验收：压缩子会话的 busy 与根租约一致；死进程不占用；未知 holder 不判空闲；重复 stop 不二次执行；API stop 仍校验 generation。

### 4. 编辑分支直写 `state.db`，不能改接到 `/fork`

`create_edit_branch`（`1207-1279` 起）在 `mode=rw` 里复制选中用户行之前的原始历史，不结束源会话，子 id 为 `api_edit_` + request UUID。消费者：`sidecar.ts:404`、`queries.ts:687`、`chat-page.tsx:686`。

错误对接：`POST /api/sessions/{id}/fork`（`api_server.py:2766-2793`）会 `end_session(..., "branched")` 并复制全部消息。语义相反。

可复用但不是现成接口：`branch_in_transaction`（`hermes_state_mutation_branch.py:52-104`）不结束源会话，按 `through_message_id` 复制，并 `require_idle`。它要求 `state_meta` 里的 local policy，缺了抛 `runtime_coordination_required`；子会话是 `Platform.LOCAL`，不是 `api_server`。`SessionDB.rewind_user_turn`（`hermes_state_rewind.py:59`）是原会话软删，不是开分支。

新增核心能力：在 `hermes_state_mutation_branch.py` 旁加一条 API 编辑分支。必须走现有事务守卫（idle、压缩锁、`display_order`），核对 content+timestamp，不结束源会话，不按面板 SQL 插入。幂等键用现有 request id，不要面板私有 `_panel_edit_branch` JSON。

退役：`/sessions/edit-branch` 不再写 `state.db`。验收：源会话仍在；边界变化返回 409；压缩进行中拒绝；重复 request id 返回同一子会话。

### 5. 浏览器所有权是插件影子库，还改写工具代码

插件 `plugins/browser-window-guard/__init__.py`：`pre_llm_call` / `pre_tool_call` / `on_session_end`（`513-516`）。`on_pre_tool_call`（`327-391`）在缺少 `session_id`/`turn_id` 时阻断 `browser_exec`，把未纳入守卫的 `mcp__chrome_devtools`、playwright、其他 `browser_*` 阻断，并改写 `code`/`session`/`timeout_s`。`finish_session`（`396`）写明：核心 `task_id` 是执行器范围，不是插件自己的 `compute_task_id(session, turn)`。

影子状态：`sidecar/live_task_store.py` 与插件目录里的同名副本，默认库 `/root/hermes-workspace/hermes-panel-state/live-tasks.sqlite3`。侧车只读打开后再发 CDP。消费者：`src/react/lib/task-live.ts` 的 `/live/task`、`/live/task/control`、`/live/task/input`。

核心已有、不够用：

- `tools/browser_session.py:138` `owner_task_id`，`151` `session_for_task`，`cleanup_browser`（`browser_session_lifecycle.py:160`）。
- `tools/browser_use_cli.py:336` `_backend_cache_key(task_id)`。会话缓存在进程内，不持久化该任务创建的 CDP target。
- `human_holds_shared_browser` / `run_fenced`（`browser_session.py:87-101`）管的是 Bot Desktop 整屏租约，不是面板按任务 target 的 45 秒人工控制。
- hook 身份已传入：`hermes_cli/plugins.py:1908-1925` 的 `session_id`、`turn_id`、`task_id`。

不要把影子 SQLite 换个模块名放进核心。扩展点是浏览器工具自己：用它已有的 task id 登记自己创建的 target，提供只读画面和带租约的输入；关页只关该登记。面板不再改写 `browser_exec` 源码，不再维护第二份 Store。

必须留下的失败方式：没有身份就拒绝；浏览器 id 对不上就不关页；不回退到当前可见页或桌面 VNC；不把未登记弹窗猜成任务窗口；清理失败写 `cleanupError`，不报成功。

退役：两份 `live_task_store.py` 和 `PANEL_LIVE_TASK_DB` 不再被运行路径打开。验收：任务页不是前台时画面仍是该 target；人工控制过期后工具继续；结束回合不关用户自己开的页。

---

## P1 可直接原生化

### 6. 配置 / 技能 / MCP / 插件仍是 CLI 子进程

`_hermes`（`sidecar.py:461-473`）执行 `HERMES_BIN`。`config_set`（`497-515`）靠解析 stdout 里的 `⚠` 回滚未知键。技能开关把 `skills.disabled` 经同一 CLI 写入（`874-892`）。模型、MCP、插件、profile 的管理路由在 `_route_admin`（`5270-5349`）。

进程内已有，不要再包一层命令行：

- `hermes_cli/config.py:1978` `load_config`，`2366` `save_config`，`3310` `set_config_value`。未知键拒绝应调用这个函数的返回/异常，不要刮 ANSI。
- 技能目录：`tools/skills_tool.py:277` `find_all_skills`。启用位就是 `skills.disabled`，走 `set_config_value`，不是新表。
- MCP：`hermes_cli/mcp_config.py:210` `_get_mcp_servers`，`233` `_save_mcp_server`，`269` `_remove_mcp_server`。`cmd_mcp_*` 只是打印层。
- 插件：`PluginManager.list_plugins`（`hermes_cli/plugins.py:1461`）。启用仍走配置，不另建影子启用表。

绑定层应在请求线程里调用这些函数，并绑定目标 profile（`gateway/AGENTS.md` 的 profile scope）。`api-contract.md` 里的 `clawd` CLI 转发在这条退役后删除。

同类绕读：`get_context`（`4177-4211`）刮 `agent.log` 的 `in=`。`session_model_usage`（`hermes_state_schema.py:78-97`）只有累计 token，没有最近一次调用。不要把日志解析迁进核心；在现有用量写入点加最后一次调用快照，再给面板一个只读字段。这是小扩展，不是新状态机。

明确不迁入核心：`restart_service`（`403`）、`hermes update`、journal、webhook 投递日志刮取。`api-contract.md` 已禁止重启 systemd。这些留在宿主运维，或缺依赖就返回明确错误。

退役：面板进程不再 spawn `hermes`/`clawd` 来读写配置。验收：密钥键仍 403；未知键不写入；回读与 `load_config` 一致；profile 不串。

### 7. 模型锁核心已有；effort 仍在浏览器本地

锁已经是核心接口，不要重做：

- 请求字段 `require_model_lock`：`api_server.py:1882`。无路由时 `409 model_lock_unavailable`（`1893-1904`），禁止静默退回全局。
- 持久化：`SessionDB.update_session_runtime_lock`（`hermes_state_sessions.py:803-821`），存在 `model_config.browser_model_lock`。名字是历史字段，面板已经在用，不要再开一个 lock 字段。
- 会话锁路由：`POST /api/sessions/{id}/model`（`api_server.py:81`）。消费者 `api.ts:375-379`。

补偿在客户端：`src/react/lib/effort.ts:37` 用 `localStorage` 的 `hp_effort_by_session`。`model-effort.ts:41-53` 把 Argon/Flash 拼成 `模型-档位`，并带 `model_options.reasoning_effort`。发送入口 `api.ts:357-372`。

核心档位接口：`gateway/session_policy.py:48` `pin_reasoning_effort`。它写 `agent.reasoning_effort` 并记录 session pin，胜过 per-model override。

原生化：档位跟随会话 policy / 已持久化的 `model_options`，刷新后不靠 localStorage。Argon/Flash 后缀规则放到该 provider 的路由适配，不放在 React。UI 滑块保留。`409` 拒绝静默回退必须保留。

退役：换浏览器后同一会话档位仍在；未确认 provider 的模型名不再被客户端拼接。

### 8. 历史旁白已有投影，API 没用，侧车还跨会话直读

`get_commentary`（`2137-2152`）按消息 id 读 `codex_message_items`，SQL 没有 `session_id`。知道 id 就能读到别的会话旁白。消费者：`src/react/lib/commentary.ts`、`sidecar.ts:439`。

同一投影已经存在：`agent/history_commentary.py:78` `commentary_items`，`157` `project_history_commentary`，产出 `display_commentary`，并做思考剥离和密钥打码。TUI 在 `tui_gateway/session_history.py:300` 使用；官方 dashboard 在 `hermes_cli/web_routers/sessions.py:686` 使用。`APIServerAdapter._handle_session_messages`（`api_server.py:2730-2759`）没有调用它，`_message_response` 也不含 `display_commentary`。

直接改 API 消息投影，删掉侧车批量接口。不要把 `codex_message_items` 原样下发。

退役条件：消息接口带 `display_commentary`；侧车 SQL 删除。验收：只返回当前会话；`commentary_withheld` 和关闭展示时为空；reasoning 正文不被改写。

### 9. 后台进程读检查点文件，注册表已有列表

`get_session_background`（`3108-3150`）读 `HERMES_HOME/processes.json`，再对 `/proc` 核对 `host_start_time`。注释写明这是 `tools/process_registry` 的崩溃检查点。

已有：`ProcessRegistry.list_sessions`（`tools/process_registry.py:2356`），检查点路径 `48-57` 已按 profile 解析。工具入口 `_list_processes`（`2658`）按 `task_id` / gateway `session_key` 过滤，不是侧车用的 `parent_session_id`。

扩展：给 `list_sessions` 增加按 `parent_session_id` 的只读过滤，活体判断复用 `_refresh_detached_session`，经网关/API 暴露。面板进程不要自己打开 `processes.json`。缺注册表时返回明确不可用，不返回空列表冒充没有后台进程。

---

## P2 保留边界，不要原生化成核心状态机

### 10. 会话文件夹不是会话事实；项目复用点只列到这里

面板文件夹是独立库 `HERMES_HOME/panel_folders.db`（`sidecar.py:114-136`），只有名字和 session 归属，没有目录。消费者：`sidecar.ts:386-387`。它不该并进 `sessions` 表，也不该成为第二套运行事实。

用户已定：普通聊天保持普通模式；文件夹升级为项目；每个项目必须绑定目录；项目记忆叠加全局记忆，按 Claude Code 方式。项目路由由父代理设计，本报告不展开。

既有复用点是 `hermes_cli/projects_db.py`，不是 `panel_folders.db`：

- `projects_db_path`（`23`）、`connect`（`114`）
- `Project.primary_path` 与 `ProjectFolder`（`149-171`）
- `create_project`（`220`，`folders` 规范成绝对路径，`primary_path` 写入并标记 primary；同路径已有项目则拒绝）
- `add_folder`（`305`）、`set_primary`（`353`）、`find_by_primary_path`（`207`）、`project_for_path`（`462`）

该文件的公开函数没有项目记忆 API。记忆叠加不要假装这里已经有。迁移时不要把 `panel_folders.db` 的行直接插进 `projects` 而不绑定目录。

### 11. 文件产物是宿主边界

`FILE_ROOTS`（`sidecar.py:87-92`）固定 `/root/hermes-uploads`、`/root/hermes-workspace`、`HERMES_HOME/cron/output`、`HERMES_HOME/images`。拒绝 `.env`、`config.yaml`、数据库、密钥名，以及 `/etc`、`/root/.ssh`（`94-97`）。消费者：`src/react/lib/artifacts.ts:11-37`，下载走 `/panel-sidecar/files/artifact`。

保留这层独立复核。迁到 `CLAWD_HOME` 下的根，拒绝 `/root/.hermes` 和生产安装目录。不要把浏览/下载并进 agent 文件工具，也不要让模型看到的路径直接成为下载授权。`absPath` 字段可兼容，但必须经同一拒绝表重解析，含符号链接。

### 12. 自动续跑只衔接，不重设计

面板实现：`continuation_trigger`（`3604-3629`）、`_continue_watcher`（`4125`）、`main` 启动线程（`5375`）。完成通知 `display_kind=async_delegation_complete` 必须晚于最后一条助手消息和最后一条人话；连续上限 5；去重库 `panel_auto_continue.db`（`3792`）；向 `127.0.0.1:8642` 发 HTTP（`3639`、`3554-3555`）。人话判定排除 `[面板自动续跑]` 前缀和压缩脚手架（`3599-3601`）。

核心已有、专路应接：

- `gateway/wake.py:127` `deliver_wake`；无 push 的 API 适配器走 self-post。文件头写明：async-delegation 完成不能当成新的 `role=user` 去跨过待确认门，应走 `persist_delegation_delivery`（`188`）。
- `APIServerAdapter.run_internal_session_turn`（`api_server.py:2511`）是进程内唤醒，不经过 HTTP，也不用面板 API key。
- 待审批 / 未知执行：`stop_run` 对 `unknown` 抛 `unknown_execution`（`authority_runs.py:165-166`）。续跑必须同样停。

专路模块名以 `source-map.md` 为准：`gateway/clawd_continuation.py`。面板线程和 `panel_auto_continue.db` 在该模块有持久去重、重启恢复、并证明取消/插话/待审批不唤醒之后才能退役。两套调度不能并存。本盘点不设计调度器。

展示契约 `/panel-sidecar/run-wait`（`4264`）可暂时留作只读投影，数据改从续跑模块读，不要面板再扫消息表决定是否唤醒。

---

## 不得削掉的边界

- 来源标记不是真人身份，也不授予编辑。hidden、producer card、heartbeat、协议包装保持原样。
- 未知租约格式和外部 pidns 保持 busy，直到 TTL。不杀 `hermes-gateway.service`，不杀本进程。
- 审批体仍只接受 `request_id`、`execution_generation`、`choice`（`authority_runs.py:122-125`）。缺代次不猜。
- `require_model_lock` 无法路由时 409，不退回全局模型。
- 浏览器：无身份即拒绝；不回退可见页；不猜测关页。
- 文件根拒绝密钥、数据库、`/etc`、`.ssh`、生产 home。配置密钥键保持不可写。
- 新读路径绑定 profile，不读启动进程的 `HERMES_HOME`。路径走 `CLAWD_HOME`，不回落 `~/.hermes`。
- 依赖缺失返回明确错误或 `running: false`，不用空列表或假事件冒充成功。

## 验收说明

本文件只证明基线里补偿和核心符号的对应关系。最终是否迁完，要父代理对合并树核对：侧车运行路径不再 `subprocess` 调 CLI、不再以 `mode=rw` 写 `state.db`、不再为来源/旁白/租约/run 打开第二套 SQLite。未跑整合测试，不得标为通过。
