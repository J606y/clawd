# clawd 面板接口与状态缺陷审查（2026-11-06）

状态：只读静态审查。未运行测试、构建、部署或运行时探针。结论分 `confirmed-static`（调用链闭合，尚未运行）、`risk`（条件未证）、`duplicate-current-fix`。

## 身份与基线

- 动态 `list_servers`：`beqCprBT` = HUAWEI CLOUD SG SERVER，online。只读 `moss.run`，未 SSH，未输出凭据。
- 核心 HEAD `a3c447d2d2`，porcelain 22。面板嵌套 HEAD `c28f2f8bf6`，porcelain 123。未还原。
- 入口：React `apps/panel/index.html` → `/src/react/main.tsx`，别名 `@` → `src/react`。`apps/panel/src/lib/sidecar.ts` 只被旧测试加载，不当活缺陷。
- `clawd_server/server.py` 把 `/panel-api` 反代到本进程 `APIServerAdapter`，把 `/panel-sidecar` 反代到已 bind 的 sidecar。`admin_native.install` 替换设置/配置写函数；`browser_native.install` 替换 `live_task*`；`runtime_native.install` 替换会话运行时读。文件浏览仍走 sidecar 路径策略。`.bundled` 副本未当活路径。

## 缺陷（严重度序，最多 8）

### P1-01 confirmed-static 分叉不继承项目

- 路径：`gateway/platforms/api_server.py:2813-2852`；`hermes_state_sessions.py:443-446`；对照 `hermes_state_compression.py:372-380`；面板 `apps/panel/src/react/shell/session-list.tsx:488-498`。
- hash：`api_server.py` `e653b26b4bcb8e816cc673c62304727b511bbb24b980cfff8024d35beeab7a5c`（已提交，porcelain 未列）；`session-list.tsx` `49be054c56e7ba5ce967d7d3b1549352cc8877b66a296d46f0a76b6034a0344c`（面板已改）。
- 证据：`POST /api/sessions/{id}/fork` 调 `db.create_session(..., parent_session_id=source_id)`。`create_session` 只 `_insert_session_row`。项目快照只在 `_publish_child_session_row` 里调用 `inherit_session_project`。编辑副本走 `api_edit_branch` → `_publish_child_session_row`，会继承；分叉不走这条。面板 `forkOne` 成功后直接 `onSelect(copy)`，不调 `projectsApi.bind`。
- 影响：项目内对话分叉后，新会话没有 `project_sessions` 行。项目页按归属过滤，分叉不在项目里；却已被选中。项目记忆/工作目录不会套上，看起来像普通对话。
- 复现建议：项目内选一条对话，侧栏分叉。核对 `projects.db` 的 `project_sessions` 无新 id，而 `state.db` 有 `parent_session_id`。不要和「主会话被子会话替换且切不回」混为一谈。
- 测试：`tests/clawd_projects/test_project_inheritance.py` 只覆盖 `inherit_session_project` 函数，没有 API fork 用例。未运行。

### P1-02 confirmed-static 绑定成功后的清理只删会话、不解绑

- 路径：`apps/panel/src/react/lib/project-send.ts:55-72`；`apps/panel/src/react/pages/chat/chat-page.tsx:988-994`；`gateway/platforms/api_server.py:2725-2759`；`gateway/platforms/clawd_project_routes.py:362-375`；`apps/panel/src/react/lib/projects.ts:272-285`；`apps/panel/src/react/lib/project-model.ts:64-77`。
- hash：`project-send.ts` `7b812ad4314a519696b908f6214565b569ad8602b59b8a6deb8f9986a1ae4f05`（未跟踪）；`chat-page.tsx` `9a16432e91edbb356d06752dfc2d76c03b6116a1b7d63be05f7759a48f931df8`（已改）；`clawd_project_routes.py` `24deb163ae07c768a64d5c37e5c2716495eb0f52efc7622240fc32efd704c95f`（核心已改）；`projects.ts` `1700e75631bad2dbd92f930c3fd056af81681b7e4b8c45d64cc0bb1b2939db21`（未跟踪）。
- 证据：首发顺序是创建会话 → `prepareCreatedSession`（先 bind 再 lock）→ 才 `start`。bind 或 lock 失败时 `remove` 是 `api.deleteSession`。`DELETE /api/sessions/{id}` 删 state 行和网关路由，不写 `projects.db`。侧栏手动删除会先 `bind(session, null)`，这条清理路径没有。`GET /api/projects/{id}/sessions` 在 `get_session` 失败时仍返回 `{session_id}`。`projectSessionToSummary` 只要有 id 就生成标题为空、消息数为 0 的摘要。
- 影响：项目首发若绑定已成功、锁模型失败（或 bind 请求在服务端已写入后客户端超时再删除），项目列表留下指向已删会话的空对话。重试会再造一条真会话，幽灵行还在。点进去会 404。
- 复现建议：项目模式首发，让 `POST /api/sessions/{id}/model`（lock）失败或在 bind 返回前掐断客户端。确认 state 行已删、`project_sessions` 仍在，项目页出现无标题行。
- 测试：`apps/panel/tests/project-panel.test.mjs` 只断言 mock `remove` 被调用，不断言解绑。未运行。

### P1-03 confirmed-static 后台进程状态在面板进程恒 503，界面吞掉

- 路径：`clawd_server/server.py:175-196`；`gateway/platforms/api_server.py:1112`、`4048-4049`；`gateway/platforms/clawd_runtime_routes.py:168-173`、`276-287`；`apps/panel/src/react/lib/sidecar.ts:618-622`；`apps/panel/src/react/pages/chat/chat-page.tsx:1314-1317`。
- hash：`server.py` `6f02e7fdece74187ed121a4ac865c1f213919c9f605dd71d817c42f0d0e949ac`（核心已改）；`clawd_runtime_routes.py` `45ac61a9e568b3cc9cd0fb6a3d74c6886801b6b5117de6f84e43f13d231838ad`（porcelain 未列）；`sidecar.ts` `5b4984fbe252d8f6cbd69b79502380b4a9a5d21d30f87a3911bba98f894ceabd`（已改）；`chat-page.tsx` 同上。
- 证据：面板 `run_serve` 自建 `APIServerAdapter` 并 `connect()`。`gateway_runner` 默认 None，注释写明由 `gateway/run.py` 设置。`connect()` 只在已经非空时抄到 app，不从 `_gateway_runner_ref()` 回填。`GET /api/runtime/sessions/{id}/background` 在 `gateway_runner is None` 时抛 `ProcessRegistryUnavailable`，HTTP 503。客户端只在 `background.data.length > 0` 时渲染「后台还有 N 个进程」，错误不展示。sidecar 上的 `get_session_background` 不是这条活请求。
- 影响：面板对话里启动的后台进程不会出现在「后台还有 N 个进程」。失败被当成没有进程。
- 复现建议：面板会话里启动长进程，看该 GET 是否 503 `process_registry_unavailable`，页面无横幅也无错误。未对现网进程做探针。

### P1-04 confirmed-static 设置表单与 YAML 回读不互相失效

- 路径：`apps/panel/src/react/pages/settings/config-tab.tsx:159-167`、`194-204`、`310-319`；`apps/panel/src/react/lib/queries.ts:248-253`。
- hash：`config-tab.tsx` `aed1240b4ae44aab91725036c1f89a4a9e290536bf3d309d0f8c4865cc13aa58`（已改）；`queries.ts` `49d7b18ea513a5c8e4c0eb4b2f881c3b4a5aaa7bc52f00a4844fb227f80a7098`（已改）。
- 证据：同一组件同时订阅 `useSettings()` 与 `useConfigYaml()`。表单 `setSetting` 成功后只 `invalidateQueries(settings)`，并先用客户端提交值打补丁。YAML 保存只 `invalidateQueries(config)`。两个查询都保持挂载，未失效的那个不会因为切页签而重新挂载。服务端 `set_setting` / `config_set` 写的是同一份 profile 配置，并做了写后回读；界面另一页签不读那次回读。
- 影响：表单改完，YAML「当前配置」仍是旧文本；YAML 改完，表单值和「已修改」徽章仍是旧的。用户按旧画面再存一次会覆盖刚写入的值。服务端 `applied` 仍为 false，这点 `savedNotice` 有表达，不是本条。
- 复现建议：表单改一个非密钥字符串，不刷新窗口，打开 YAML 页签看预览。再在 YAML 用旧文本写回同一 key。

### P2-01 confirmed-static `model_context_length` 的「已修改」不跟真实键

- 路径：`clawd_server/admin_settings.py:19-20`、`83-86`、`156-169`、`198-209`；`apps/panel/src/react/pages/settings/config-tab.tsx:475`、`220`。
- hash：`admin_settings.py` `8e37d08a6e2de77bf51c24ac174cb3902aa02f3e41ba27100c8ffeed5192710a`（porcelain 未列）。
- 证据：`_KEY_MAP` 把表单键 `model_context_length` 写成 `model.context_length`。回读 `userSet` 用 `_dig(raw, 表单键)`，不走映射。原始 YAML 只有 `model.context_length` 时，规范化值能显示（`_normalize_config_for_web` 会填虚拟字段），但 `userSet` 为 false。保存成功后的 invalidate 会把乐观的 `userSet: true` 刷掉。
- 影响：上下文长度保存后「已修改」消失，「只看已修改」把它滤掉，像没写入。YAML 里实际已有 `model.context_length`。
- 复现建议：表单改该字段，等设置查询刷新，看徽章和筛选。对照 config.yaml 的 `model.context_length`。

### P2-02 confirmed-static 文件页上传不带当前目录

- 路径：`apps/panel/src/react/lib/sidecar.ts:322-327`；`apps/panel/src/react/pages/files/files-page.tsx:124`、`138-140`；`apps/panel/sidecar/hermes_panel_sidecar.py:5752-5769`、`5827-5838`。上传 413 另有修复负责人，本条不是体积限制。
- hash：`files-page.tsx` `4617db93a25a8acad348669c9f0356591b18f18e1c89ee0b9748b0c8976170be`（已改）；sidecar `11840a85bd8ce3a5be3a9abd4797b74cea1050895b19e80a63e95bd1fde8ff82`（已改）。
- 证据：客户端 query 只有 `root` 和 `name`。服务端 `dir` 缺省时，`uploads` 落入当天日期目录，其它可写根（`workspace`）落入根。页面刷新的是当前浏览路径。用户已进入子目录时，新文件不在眼前。删除接口本身收 `root+path`，与列表字段一致，不是本条。
- 影响：在工作区子目录上传后列表不变，通知里的 `absPath` 指向根目录。日期目录里的上传同理：人在昨天的文件夹里上传，文件进今天的文件夹。
- 复现建议：打开「工作区」子目录，上传小文件，看列表与 `absPath`。不要用超过 50MB 的文件，避免和 413 混在一起。

### P2-03 confirmed-static 任务页已关闭却报等待

- 路径：`clawd_server/browser_native.py:64-83`、`160-161`；`apps/panel/src/react/docks/task-dock.tsx:275-279`、`300`、`618-620`。活实现是 `browser_native.live_task`，不是 sidecar 里同名旧函数，也不是 `_live_task_store`（已退役）。
- hash：`browser_native.py` `68fcc80acdd21355419277658cdd93d53e0a4add19321ff46512dd8f9e730e0f`（porcelain 未列）；`task-dock.tsx` `e0f12c0a41908591690b9dedda393ea0c0a8afae9ed2268226d00768e0a0047e`（porcelain 未列，按当前字节复核）。
- 证据：无画面时 status 先设成 `waiting`，只有抓到帧才改 `active`。CDP `target_closed` 直接 `return result`，status 仍是 `waiting`。dock 在 status 不是 `ended` 时继续轮询，徽章文案是「等待画面」，不是「已结束」。
- 影响：任务页已关，面板一直等画面，不释放「已结束」提示，控制按钮逻辑也按未结束处理。
- 复现建议：打开任务画面后关掉目标页，看下一次 `GET /panel-sidecar/live/task` 的 `status` 是否为 `waiting`。未运行。

### P2-04 confirmed-static 绑定失败恢复草稿会被中途输入吃掉

- 路径：`apps/panel/src/react/pages/chat/chat-page.tsx:951-954`、`969`、`987-994`、`1011-1014`；`project-send.ts:65-72`。
- hash：见 P1-02。
- 证据：发送一开始就清空 composer，再创建/绑定。失败时 `restore` 若发现草稿已非空就直接 return。绑定是网络往返，用户能在等待中打字。
- 影响：项目首发绑定或锁模型失败时，原消息和附件可能不回来，输入框只剩等待期间打的字。这和 P1-02 的幽灵会话是同一条失败路径上的另一段。
- 时序：清空草稿 → 创建会话 → bind 未返回时再输入一个字 → bind/lock 失败 → `restore` 跳过。未运行。

## 已核对但不是缺陷

- 首发成功路径：先 bind 再 lock 再 `start`，失败不派 `hp-session-created`，不选中新会话。`bindCreatedSessionOrBlock` 拒绝 `success !== true` 或 id 不一致，不降级普通模式。
- 编辑副本：`api_edit_branch` → `_publish_child_session_row` → `inherit_session_project`，失败变 `project_inheritance_failed`。
- 项目记忆 GET/POST 字段与 `projects.ts` 的 `session_id` / `operations` / `execution_generation` 对齐。
- 设置写响应含 `ok/saved/applied`；`savedNotice` 在 `applied === false` 时不声称本进程已生效。`useSetSetting` 无页面消费者，活路径是 `admin-api.setSetting`。
- 文件列表/删除字段（`root/path/absPath/entries`）与 `DirListing` 一致。删除页自己会刷新当前目录；mutation 额外 invalidate 文件路径本身，不是用户可见主路径。
- 活动列表字段与 `ActivityItem` 对齐。`get_activity` 仍是 sidecar 活函数。

## 覆盖矩阵

| 面 | 消费者 | 活处理 | 结论 |
| --- | --- | --- | --- |
| 项目创建/列表/补丁 | `projects.ts` `queries.ts` `session-list.tsx` | `clawd_project_routes.py` | 创建响应含 `archived`；见 P1-02 |
| 首发绑定恢复 | `project-send.ts` `chat-page.tsx` 交界 | bind + delete | P1-02、P2-04 |
| 分叉 | `session-list.tsx` | `api_server._handle_fork_session` | P1-01 |
| 编辑副本 | `queries.useEditBranch` | `api_edit_branch` | 继承闭合，不报 |
| 设置表单/YAML | `config-tab.tsx` `admin-api.ts` | `admin_settings` `admin_config` | P1-04、P2-01 |
| 冻结管理路由 | `admin-api.ts` 抽查 | `admin_routes.ROUTE_SPECS` | 路径名对齐，未逐项验回读 |
| 文件列表/删除/上传 | `files-page.tsx` `sidecar.ts` | sidecar `_upload_home` `list_dir` `delete_file` | P2-02；413 不重复 |
| 活动 | `activity-page.tsx` | sidecar `get_activity` | 字段对齐；pending 准确性未闭合 |
| 任务画面 | `task-live.ts` `task-dock.tsx` | `browser_native.live_task` | P2-03 |
| 后台进程 | `sidecar.sessionBackground` `chat-page` 交界 | `clawd_runtime_routes.handle_background` | P1-03 |

## 未查模块

模型/MCP/插件/配置档/webhook 全量回读，技能保存，定时任务，图片工作室，文件夹迁移执行/回滚，用量与日志，子代理翻页，TUI，`node_modules` / `.bundled` / 备份。`chat-page` / `shell` / `runs` / `transcript` 只读了项目绑定与后台横幅交界，未重审主/子会话切换。

## duplicate-current-fix（不重复开单）

- 档位读取/写回运行时状态。
- 上传 HTTP 413 / `client_max_body_size`。
- 主会话被子会话替换且不能切回。

## risk（未升格）

- 创建项目先 `allocate_project_workspace` 再写 `projects.db`。DB 失败会留下空的 `w_*` 目录。未构造失败，不算已确认用户可见缺陷。
- `get_activity` 把找不到 `role=tool` 结果的调用标 `pending`。未核对现行消息是否仍写 `tool_call_id`，不升格。
- 跨库继承：编辑副本在 state 事务回滚前已写 `projects.db`。代码注释已承认非原子。未给可触发的后续失败，不单列。

## 测试

此阶段未运行测试、类型检查或构建。上表「测试」只是静态看过断言是否覆盖该缺陷。无测试本身不是缺陷。
