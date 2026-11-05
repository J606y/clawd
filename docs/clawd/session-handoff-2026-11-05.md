# clawd 会话最终交接 — 2026-11-05

交接检查点：2026-11-05T05:09:08+08:00。此文记录工程交接，不是完成声明、验收报告或上线许可。后续若有新用户指令，以新指令为准。

## 先读结论

本会话子代理已全部结束：交接前 `delegate_task(action=list)` 返回 `count:0` / `No live subagents`。这仅证明没有活动子代理，不证明所有任务成功或功能完成。未派新的接续任务。

临时并发已通过受支持 CLI 恢复：`hermes config set delegation.max_concurrent_children 10` 返回成功；随后独立只读目标字段，回读 `delegation.max_concurrent_children=10`。第一次命令超时且回读仍30，第二次成功后才回读10；没有手改 YAML、改模型或重启服务。该项可以完成，不要在新会话再按“仍30”恢复一次。未额外派任务证明运行时是否重载配置。

工程仍未完成，未进行本轮统一验收、提交、推送、部署、安装到生产或实际迁移。设置补全目前只有契约；成功连续续跑5次的门槛仍在；浏览器云弹窗与 daemon 强制任务授权仍缺实现。不要把所有代理已结束表述为所有工作已完成。

## 1. 目标与授权

用户要从 Hermes 核心派生独立产品 clawd：裁剪官方 Web/Desktop，保留品牌化 TUI，自研面板成为唯一 Web，与核心共用真实能力和运行事实。包括核心拥有委派完成续跑、可信运行/会话/项目归属、真实目录项目、全局加项目记忆、显式迁移回滚、管理服务、浏览器任务所有权及完整分发。

最新追加要求：

1. 第一方产品全面更名 clawd，真实来源、第三方、版权、必要兼容标识不机械替换。
2. 设置按当前内核补全。不是仅填占位，也不是把内核没有的能力画成假开关。
3. 取消“成功连续自动继续5次后必须人话继续”。主代理自主实现、测试、修复、复测，到可交付后结束；保留取消、审批、插话、失败预算与无工作不自激。
4. 本会话所有子代理结束后先写交接，用户将新开对话。本次在这个交接边界停止扩派；后续实现交给新会话接续。
5. 八路核心安全审查需有代码位置、严重度、触发条件、建议代码、置信度。先收齐独立复核再归并；审查报告不是修复授权或已修证据。

执行约束：主代理负责派活、调度、文件归属、状态与交接，不自行扩为实现工程师。独立叶子任务统一 grok-4.7，禁止再 delegate_task，禁止 commit/push/deploy。按文件分配 owner，公共启动/路由/类型只有一个集成人；新会话原 owner 均已释放，不要再因旧“正在写”摘要永久跳过文件。

严格先完成全部批准范围实现，再统一做测试、构建和探针；实现阶段只静态核对、准备用例，不逐路反复测试。最终验收不豁免。真实阻塞或新增授权才停，不按固定续跑次数要求用户再说“继续”。

保留面板原布局、间距、动画和既有语义，不自行重设计；设置全量汉化。保留附件 absPath、Ultra id及动画、Argon/Flash 后缀兼容。禁止猜 profile/default、项目或浏览器归属。任何删除先列具体路径并取得同意；归档不隐式删除内容。

不检查或修改 `/usr/local/lib/hermes-agent`；不改原面板 `/root/hermes-workspace/hermes-panel`、线上 `/var/www/hermes-panel`、nginx或生产服务。不读生产配置/凭据补测试。不能自动搬迁历史数据、会话或秘密；不能伪造测试/API输出。临时并发恢复是本会话唯一获准的现用 Hermes 配置恢复动作，不能扩张授权。

## 2. 实际工程定位与状态

核心 `/root/hermes-workspace/clawd`，本次工具回读分支 `clawd`、HEAD `c314285f077e91937c995d419b9fa40af9eb4981`。这是上游基线加大量未提交改动，不是已提交的新产品版本。只统计跟踪文件的 `git status --porcelain=v1 -uno`：修改1196，删除4960，共6156项；不含未跟踪新文件和嵌套仓库内部的全部变化，不据此声称完整文件总数。

面板是嵌套独立仓库 `/root/hermes-workspace/clawd/apps/panel`，本次回读分支 `hk`，HEAD `c28f2f8bf6faa11c1e3901a39db6d0826b78bc29`。不要把隔离嵌套面板的 hk 误当原面板主检出，也不要 checkout main/deploy。历史 worktree 与 artifacts 不清理、不删除。

历史环境：`.venv/bin/python` 3.14.9；Node `.dev-home/tools/node-26.12.1-linux-x64/bin` 26.12.1、npm11.20.0；仅历史记录，本次未重新执行版本命令。系统 `python` 无 PyYAML，不要为了交接随意装包；报告 JSON 可用标准库解析。

旧 `docs/clawd/handoff.md` 是历史流水，顶部将链接本交接；其中“正在运行”“并发30”“Response真值绕鉴权”等段落可能已过期，不能单独作为当前状态。旧 artifacts/parent-check 曾因外部清理丢失，历史路径不能保证仍有证据；新历史证据主要为 artifacts/parent-review。不要删除 artifacts 或无损回滚资产。

## 3. 设置：契约已返回，产品尚未补齐

本次父代理实际回读：

- `docs/clawd/settings-core-contract.md`
- `docs/clawd/settings-implementation-worklist.json`

对应完整字段矩阵 `docs/clawd/settings-core-matrix.json`。上述是提案/静态核对，不代表新接口已注册或控件已实现。新会话实现前核当前 schema 与真实消费者，尤其验证秘密策略和 profile隔离，不能把契约里的声明当运行事实。

作者静态 AST 统计：schema914叶、表单872叶、空对象44项未进表单、43条汉化缺项。914不是914个功能。只在允许读取文件中命中44个点号键，余870不等于“未实现功能”，也不证明已被运行时消费，不按这个差集机械补开关。

需实施的实际缺口：开放对象安全编辑、秘密启发式误锁、汉化补丁、自定义模型端点、MCP新增、技能启用不再调用已拒绝CLI、已装插件非秘密配置、工具集/记忆提供方/连接配置组件、定时任务 deliver/skills/repeat 字段、更新页与日志页的真实不可用边界。

保留现有 tab，不为工具/连接/记忆另造顶层tab；不新造项目记忆页面。秘密只写不读，GET仅状态/掩码，不移植 env/reveal、不记日志。保存成功与本进程已生效分开；按实际消费区分下一会话、待重启，不能把 saved 当 hotLoaded/applied。

公共集成人独占：`clawd_server/admin_routes.py`、`admin_native.py`、`admin_common.py`及面板`admin-api.ts`、`sidecar.ts`、`queries.ts`、`api.ts`。worklist 已按技能/MCP/模型/高级配置/汉化/工具/记忆/连接/插件/定时任务/更新/日志分文件，下一会话据此派发，不再重新做一次全量盘点。具体路由、导出与响应见contract，需先核契约一致性再并行实现。

静态判定暂缺安全进程内接口：宿主更新重启、进程级profile切换、插件热加载/安装器、记忆setup命令、电脑控制授权、MCP目录安装、消息配对。Webhook投递日志仍501；不许改空列表假成功。不应把这些直接从用户总体目标中静默删除：新会话明确哪些仍属于实现缺口、哪些确实需要新增架构/授权。clawd入口拒绝 update，不能把真实旧hermes update替换成不可运行clawd update后称升级可用。

## 4. 五次续跑限制：确认未移除

批次 `deleg_ede3e86b` 返回，只准备测试与文档，没有改产品实现。原因是实际决策文件不在原叶子允许名单。这不是用户未授权取消限制，而是派发文件范围过窄；下一会话给正确文件owner后直接实施已批准需求，不要求用户重复批准。

父代理实际回读 `docs/clawd/continuation-no-five-cap.md`。活路径硬门禁：`gateway/clawd_continuation_decision.py:18,334-335`，`streak_count>=MAX_STREAK(5)` 返回 `streak_limit`。`gateway/clawd_continuation.py:294-298` 对此写空run_id回执，后续pending消失。

不仅删除比较：必须同时核 journal 恢复。旧回执不记原因，`cancelled`、`attempts_exhausted`也可能是run_id=NULL，不能全删这些回执以解开次数锁。需要有原因的可证明恢复设计，无法归因的旧记录不得绕过取消/失败预算。修改应包括decision、消费点、journal、相关用例，涉及界面通知时给chat-page和两份transcript明确单owner。

原侧车AUTO_CONTINUE_LIMIT=5仍存在，但clawd bind已禁用watcher/main，server不进sidecar.main；不能恢复第二调度器。另一个 MAX_ATTEMPTS=5/BACKOFF_S 是故障预算，不是成功次数门槛，保留。现网原文“让Hermes继续”不是隔离clawd通知，未改现网。

未运行 `tests/clawd_continuation_cases/test_no_consecutive_resume_cap.py` 已准备第5/6/8次、旧回执恢复、重复通知、无工作、审批/取消/人话和draining保护；次数用例对当前实现预计失败，未执行。旧 `tests/gateway/test_clawd_continuation.py` 仍断言streak硬停，后续需同步合法新行为，不降低保护测试。

## 5. 浏览器：依赖来源已查清，授权补齐仍未实现

`deleg_9ba8f27d` 第3路定位 `browser-harness==0.1.13`。证据 `artifacts/browser-daemon-source/`、`docs/clawd/browser-daemon-dependency-boundary.md`。作者报告锁定sdist SHA256 `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3`，tag v0.1.13 commit `c24e5072ee66f8499bacd663f4f4bcb089bc4492`；这些是来源代理证据，本交接未重新联网验hash。

已返回的实现包括已有daemon IPC画面/输入/关闭、vault复用登记daemon，未开第二签名连接。缺：云dialog的target/session/frame关联、daemon强制任务授权。daemon忽略未知字段、丢sessionId、事件清空且无targetId，POSIX0600 socket并非任务token。仅客户端登记、preamble、owner_task_id不足以宣称daemon已授权。

定位路建议fork或同时vendor daemon.py/helpers.py并保留现有IPC，而不是改已安装第三方包；本路没有改产品、依赖锁或运行路径。下一会话先核既有产品授权与供应链/分发方案，再派明确owner实施，不读生产包或用新协议绕过。禁止Browser.close/dispose误关用户浏览器、认领未知页或回落VNC；原45秒人工租约/300秒硬顶保留。依赖兼容未验，不得声称浏览器迁移完成。

## 6. 已返回但未验收的核心/面板实现

以下来自代理完成结果与此前会话记录，除明确标注外，本次只检查交接材料，没有亲自重新审源码或跑测试：

- 运行与effort：在_prepare_session_chat及/v1/runs冻结model_options前，用真实authority.db、authority和admission_held，在keyed_turn与mutation_lock围栏下提交pending effort；删turn_facade旧两参数提交。不能沿旧快照再报同一签名高危。安装native之后run-wait读持久run/lease，事实缺失unknown。未安装native分支的侧车旧get_session_run/get_run_wait仍需核可达性。幂等库仅打开同state.db目录已有runs_idempotency.db，无则不可用，不造影子库。
- 计数：active_count仍是completion units，不能为UI人数破坏drain。真实child加未入库回执按child_session_id去重，读失败null；active_task_count仅本进程回执，不是真全局人数。活动页刷新与统计待统一验收。
- 管理：native安装覆盖、config_set、profile删除preview/confirm的120秒单次凭证/fingerprint已有静态实现；未执行实际预览/删除。当前进程切换/热加载/投递日志/宿主升级重启仍缺，不得报全量完成。
- 迁移：projects_db与migration同事务write_token/消耗表、条件回滚/崩溃认领；旧NULL不认领，无force绕idle。get_session_write_token(conn,session_id)与service转发已静态对齐。条件释放的project_id表示期望当前项目，非应改None。不是跨projects.db/state.db整体原子事务；没有实际迁移或崩溃恢复验收。
- 记忆API：真实SessionAuthority/profile/session/project/generation，缺身份拒绝；管理编辑无需agent审批，但agent写仍审批、child只读。不猜home/default/URL项目或同项目第一条会话。无项目记忆页，客户端函数存在不能称UI已接。
- 最新记忆改绑：`deleg_9ba8f27d` 第1路在turn_facade租约准入后、conversation_loop前补空闲retarget；按state.db父目录定位profile，剥全局store再attach，解绑仅全局、child只读，绑定变化重建提示。agent_init解绑/嵌套问题由这条路径补偿，并未整体改agent_init。文档memory-final-wiring.md；归档GET用例与test_turn_rebind_context.py已改/新增但未跑。
- todo投影：`deleg_9ba8f27d` 第2路在hermes_state_subagents.py核child_session_id及父会话、model_config和child_links，输出delegationId/todoId/executionStatus；profileKey仅从库身份。chat-page只查询成功且profile非unknown才交投影，不以加载中空数组抹状态。returned是待验收，不写completed。自定义home若不能证明profile，图标不亮，不猜default。文档todo-final-wiring.md，test_projection_wiring.py未跑。
- 事件：runs身份校验、错配立即退出不伪completed、不清思考；当前身份最多3次250ms重订，SSE iter_any及no-cache/no-buffer，未实测。
- 附件：list_dir/image根fd、O_NOFOLLOW及uploads写入边界已返回；absPath保留。余SQLite窗口是home下固定panel_image_studio.db，非state.db；需有home固定文件替换能力，BEGIN IMMEDIATE在connect后不能保护路径窗口。不要直接当远程高危。
- 分发：唯一stage/锁、完整性v2、Nix构建期资源进wheel、运行读包内bundle已有静态实现。MANIFEST递归配合include-package-data覆盖深层资源，曾判非缺陷，不重复盲改。真实新clawd wheel/sdist未验；设CLAWD_DISTRIBUTION_WHEEL及CLAWD_DISTRIBUTION_SDIST后必须实际开档案核资源/hash/许可。许可提取脚本和测试未运行，四个内部包许可未知不能套MIT。

## 7. 品牌：多路更名已写，仍有遗留，未全量验收

首批 `deleg_f9e04d1c` 与收口 `deleg_a1dc300a` 均结束。第一方Python发行名clawd，root npm clawd、面板@clawd/panel、bootstrap @clawd/bootstrap-installer；uv editable/self-extra与Nix uv2nix属性已改。新metadata消费version/distributions查clawd；OTLP/dingtalk等extra提示及部分测试字面同步。

保留真实Nous/MIT、上游URL和第三方来源；hermes/hermes-agent/hermes-acp兼容脚本、Nix旧id、@hermes共享包/稳定metadata/protocol保留有依赖原因，不按字符串残留一刀切。默认CLAWD_HOME/~/.clawd与旧兼容身份区分。dist未重建、未发布。

详细收口证据：rebrand-{packaging,cli-tui,panel,overview}.md、rebrand-inventory.json，以及rebrand-{metadata,surface,locales,docs,plugin-content}-closeout.md。inventory命中数不是缺陷数/文件数，也不是全量完成证明。

明确遗留：

- `tests/test_hermes_constants.py:617-619` fixture仍Name:hermes-agent，未改，后续发行查找测试预计需同步。
- 侧车63/473/515/516/823/2833/3279/5047/5051/5242等可见旧文案。品牌owner已交续跑owner，但后者未改，当前不能称已收口。5354的hermes update --plan是真执行，不能盲换。
- `website/sidebars.ts` 仍Using Hermes，不在旧清单未改。
- `plugin-catalog/`、`optional-skills/`不在该路allowlist，未收口。需要区分真实第三方与第一方，不一概替换。
- `file_tools_write_guards.py:173`旧home/命令相邻残留未扩改；build/lib旧副本未动。不要删除未知build或bundle来制造干净。

作者报告33个locales品牌替换保留键/占位符；网站改718文件；插件技能改157文件。这些是静态代理统计，不是本次父代理运行验收。CLI首品牌路曾跑py_compile，偏离禁运行节奏，也绝非TUI行为验收。

## 8. 核心审查：报告已落盘，最终归并未做

八原始报告 `docs/clawd/core-audit/{execution,approval,storage,context,provider,background,input-security,native-safety}.md`。

初次独立复核 `reviewed-findings.json`、`reviewed-summary.md`、`review-coverage.json`：历史active27（高11/中15/低1）、fixed1、false_positive2、unverified33、original_mapping63。本次标准库解析再次确认初次coverage文档保留该计数，不能直接称最终总数。

三份后续报告已存在且本次用JSON标准库解析结构/计数，不执行产品：

- remaining-transport.json：10项，仍成立8、误报1、需验证1。
- remaining-background.json：9项，active2、false_positive7，另有mapping9；schema不是items数组，不能用items缺省[]误报覆盖0。
- remaining-native-storage.json：14项，仍成立9、误报3、需验证2；其meta定高0/中2/低7（仍成立）。另有CPython来源证据artifacts/core-audit-native-reference/。没有对第三方native整体安全背书。

不要直接把这几份数相加发布最终漏洞总数。下一会话需按原始mapping/ID、重复项、当前代码hash统一归并、更新初次unverified覆盖并保留不同结论。此次父代理仅核文档存在和JSON计数，没有逐条重读这些源码，最终审查验收仍未做。

已知误报/旧快照：aiohttp Response真值绕鉴权和未闭合tool字符串补成缩短命令执行已否定；effort旧两参数签名已修。无argtypes+c_void_p实例不自动说明句柄截断，按native补核的真实转换规则。默认空API key检查也不能跳过启动门禁前提称远程绕鉴权。问题需要精确触发前提，不能仅依据原作者“确定”升格。

已有复核高风险例（绑定报告hash，非本次动态复现）：跨源redirect携带X-Api-Key、无request_id审批FIFO错配、压缩提交失败仍换prompt、create_task即写续跑回执。仍须对最终字节核证据再给最终清单；报告建议代码不等于已应用修复。

## 9. 验收状态和下一会话顺序

历史有核心/项目/绑定/YAML/PTY/面板Python与Node通过记录，但随后大量代码变动，均不能代表当前稿。Node曾代理报407通过，父重复全量被中断，结果UNKNOWN；不要说父已全量通过。旧verify状态含blocked/unknown，未全绿。

当前本轮没有执行测试、build、CLI产品探针、浏览器、真实模型/MCP、实际迁移或生产切换。交接期间只读git/文档/JSON及恢复授权的Hermes并发设置。`execute_code`曾被unattended策略BLOCKED（tool_calls_made0），改普通工具解析本地报告，不改变approvals绕过。

下一会话接续：

1. 读本交接、clawd-engineering技能、现有todo与最新用户指令；用限范围状态核对文件，别全盘扫、别重做已返回盘点。
2. 将 settings-worklist 的可实现项派给明确owner；公共路由/类型集成人单独负责。核心无安全接口的能力作为真实缺口，不改成假按钮/假成功，也不静默缩减用户总体目标。
3. 给续跑decision/journal/消费点/必要显示与测试正确文件范围，取消成功次数硬停并保留保护。不要把开发许可范围错误当用户需求未批准。
4. 处理浏览器daemon来源/授权的实现方案和完整兼容；不能仅凭客户端registry过验收。补明确品牌遗留与发行测试fixture。
5. 归并核心审查报告、当前hash与mapping，审查与实际修复分开记录；修复范围按最新明确指令。
6. 收齐全部批准范围实现和静态接线，更新真实manifest；再统一运行lint/type/build、核心、TUI、面板、迁移恢复/竞态与脱源码分发验收。失败由相应owner闭环修复复测，不固定续跑次数要求人话。缺真实非生产模型/MCP/浏览器/安装前缀/cutover journal等依赖时标blocked，不读生产secret。
7. 验收工具遵守全通过0、失败1、blocked/unknown/未跑/空结果2。准备成功或implemented不是pass。测试通过不构成commit/push/deploy/迁移授权。

## 10. 任务清单接续索引

原总任务clawd-build、clawd-verify保持未完成，不清空或全勾。已有实现但待验收的工作还包括clawd-ui/tui/binding/core-resume/returned-gaps、native-runtime/admin/browser/effort、explicit-migration、distribution与notices、cutover/acceptance/contract/race/file-auth/memory-scope/event-recovery准备、file/auth/memory/event-native、todo-delegation-progress、project-build、panel-activity-delegation-count、core-audit各子域和full-rebrand各子域。

新增设置父项clawd-settings-core-completeness，子项contract-map/implementation/acceptance；契约文件已收到并回读，但未做实现或运行验收。clawd-continuation-remove-five-cap保持未完成。clawd-temporary-concurrency-restore已完成且回读10；本交接写入任务完成不代表clawd-build完成。

新会话不要再等待旧子代理返回：本次交接时已无live children。有报告但未收到单独通知不等于仍运行，可优先读上述落盘材料；不要轮询live transcript或自动重复派同任务。后续新派任务重新核文件owner，保持当前未提交改动，不reset/clean或删除其他代理资产。
