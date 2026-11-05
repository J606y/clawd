# clawd 工作交接

> 当前会话交接入口：[2026-11-05 最终交接](session-handoff-2026-11-05.md)。下文保留历史流水，包含已过期的运行状态、并发30与审查假设；不得直接作为当前完成或验收结论。本次交接时无活动子代理，临时并发已恢复10并回读；设置实现、五次续跑限制移除和浏览器授权缺口仍未完成。

## effort/准备工具/竞态用例返回与新接线批次

deleg_bf2985d8和deleg_d15a7bb6返回。父代理回读effort路由与实施文档、api_server注册区、accept.py、旧契约报告及竞态审批/恢复测试关键断言。用例确实走核心并明确test executor，仍未运行不能算通过。契约报告观察时刻03:07，多数缺失项已被后续落地覆盖，不可照抄进完成状态。

发现effort鉴权用了Response truthiness、pending在无面板时未交核心生效、run state/profile与并发lock更新待核；runtime负责api_server仍在写，父不抢文件，已steer sa-0-31395273挂register_effort_routes（项目路由后catchall前）并协调idle准入围栏提交pending。

续派deleg_ac7020e7：sa-0-3a6a9a0f effort安全收口（只effort文件，不改既有API/state）；sa-1-4b90adac 附件根与前端消费（turns.tsx、artifacts两副本、侧车_root/_artifact/_upload相关helper，仅此路改侧车文件边界）；sa-2-a9db1df4 准备工具整合（scripts/clawd_acceptance与clawd_cutover及原专属文档）。后者对齐真实clawd_projects/migration.py，不存在folder_migration.py；验收报告必须绑定代码/产物指纹和明确pass，不能随意JSON假绿。全部仍先实现不测试/build/迁移/部署。旧完成清单不提前提升状态。

## 用户新增活动页子代理统计问题

用户截图活动页显示子任务7。父代理视觉确认只见计数和会话活动加载中，不能由截图推出实时人数；只读current指向20261103-221623-c28f2f8，原面板主检出c28f2f8，未修改旧面板或现网。源码确认activity-page显示health.background_queues.active_delegations；clawd api_server._readiness_work_counts用async_delegation.active_count，函数说明统计completion UNITS（批/单条），不是child人数。列表ActivitySubagents useSubagents('',12,poll)，0 running时停轮询；get_subagents限12条后才计算running，UI又按已加载行数算live。均是统计/刷新缺口，截图7的实时服务响应尚未采样，不声称确证当时实际人数。

已steer sa-0-31395273在clawd运行路补统一核心child统计/列表与0任务刷新，额外拥有pages/activity/{activity-page,subagents}.tsx；不改drain/续跑active_count的批单元语义，不机械替换active_task_count而忽略部分完成计数。旧面板线上保持只读。全部实现齐后再统一测试。

## 运行/管理/迁移首批返回与接线

deleg_731e8e67三路返回。父代理回读runtime_native/admin_native及实施文档、迁移scope与恢复文案、queries.useCommentary、api_server.bind_run_store。已在native.install按browser -> runtime -> admin顺序接钩子，未运行测试/build。发现get_session_execution/pidfd停止未实现、useCommentary仍调用拒绝接口、全局_RUN_STORE多profile待核、config_set仍打印包装、迁移文档归档恢复方案与归档占路径互相矛盾，因此未标完成。

续派deleg_ea6f1644：sa-0-31395273运行收口（旧运行文件/活跃消费者，effort文件禁止改）；sa-1-fc070b8f管理收口（admin_*与必要纯函数，config_set由admin.install赋值，禁止父native）；sa-2-95cbe74d迁移恢复（migration.py/迁移路由UI，禁止service/projects_db，遇依赖报告）。重点补创建回执前与绑定回执前崩溃、ABA保护、同路径安全重试，不删除项目或迁生产。三路均只实现静态核对，原审批/absPath/UI冻结保留。

## 浏览器/分发首批返回与父代理静态整合

deleg_42ef07bd两路返回后父代理实际回读native/bind/resources/browser_native/setup与Nix片段，不把自报视作完成。已修改native.install接browser_native.install；bind.load_sidecar用panel_root，prepare_env清除影子库env并定位PANEL_LIVE_STORE_MODULE；server默认静态资源用panel_static_dir并对ResourceError退出2；setup.py在两个Nix构建守卫后执行stage_distribution。批量替换的wheel缩进曾错，已根据差异立即修正；cmdclass类型推断告警加显式类型修正。未运行测试/build/探针。

未完成：浏览器云autospawn/dialog/OOPIF/supervisor创建入口已派deleg_f8a2f85b/sa-0-4d3e684f，拥有原浏览器工具文件，不碰父入口；分发Nix接线、固定.partial/rmtree风险与bundle完整性已派deleg_758a1e0a/sa-0-f02e10e3，拥有resources/backend/资源脚本/Nix/分发测试。公共入口仍归父代理。两项保留pending，不声称迁移已完成。

## 用户授权临时提高并发与新增批次

用户明确准许临时加派。hermes-agent并发诊断参考确认无硬编码10路上限；先前工具描述不能作为实际运行限制。只读配置确认原值10，经hermes config set delegation.max_concurrent_children 30写入成功并独立回读30，没有重启服务/更改模型。本批全部并行任务结束必须恢复10并回读，待办clawd-temporary-concurrency-restore保留。

新增deleg_31f40e8e四路：sa-0-adbc5773 文件/鉴权未运行用例（tests/clawd_file_auth_cases）；sa-1-ccee3ed1 项目记忆scope未运行用例（tests/clawd_memory_scope_cases）；sa-2-45a1998e 事件恢复未运行用例（tests/clawd_event_recovery_cases）；sa-3-4012f5da 许可证/依赖清单（scripts/clawd_inventory及专属文档）。全部禁止测试/build/探针及提交推送部署，父代理统一审查再在实现齐后验收。不因额度而凑满30路。

## 第十路：竞态用例准备

用户说明最高可并发30路；本会话delegate_task工具声明max_concurrent_children=10，未修改配置，不声称已能开30路。核对原九路均running且model=grok-4.7后派deleg_d15a7bb6 / sa-0-4b1399b5补审批/续跑故障注入用例，仅拥有tests/clawd_race_cases/与docs/clawd/race-case-preparation.md，不改产品源码、不执行任何测试。验收编排路最终接入此目录，由父代理在全部实现收齐后统一运行，不以编写用例冒充通过。

## 新增并行批次（用户要求批量交 Grok）

用户要求在额度刷新前把剩余实现与后续准备批量交Grok，不为消耗额度重复已有工作。原五路继续；新增deleg_bf2985d8四路：sa-0-7dfefca9 effort持久化（拥有effort/model-effort/chat-page、新effort路由与服务，禁止既有api_server/state）；sa-1-bd80db8f 切换回滚工具（仅scripts/clawd_cutover与专属文档）；sa-2-772596c7 最终验收编排（仅scripts/clawd_acceptance与专属文档）；sa-3-236f4dfc 只读契约遗漏与docs/clawd/implementation-completion-manifest.json。

共享清单契约schema_version=1，items含id/scope/owner/status/evidence数组/blocking布尔，status为implemented/unverified/pending/blocked。implemented只是实现收齐，不是测试通过。验收run要求blocking项均implemented且显式implementation-complete确认。父代理待全部返回统一核对接线，不让任何路抢改native/bind/server等公共入口。九路本阶段均禁止测试/build/探针、commit/push/deploy和二次派发。已经通知runtime路保留effort注册给父代理最终接入。

## 执行顺序纠正：先收齐实现，再统一测试

用户已明确要求暂停开发期间专项/全量/构建与探针，剩余功能全部完成后再集中验收。不要用测试数量代表实现进度，不自动提交、推送或部署。

deleg_731e8e67：运行事实原生化（sa-0-fb421060，拥有api_server/消息与state核心、runtime_native.install）；管理服务原生化（sa-1-7c803b11，admin_native.install）；显式项目迁移（sa-2-7900d665，migration.py/项目路由/项目列表）。deleg_42ef07bd：浏览器所有权（sa-0-94563a10，browser工具/插件工程副本、browser_native.install）；安装资源（sa-1-6e165d26，pyproject/MANIFEST/home入口/资源脚本）。各路只实现静态核对，不跑任何测试构建。父代理拥有clawd_server/native.py/bind.py/server.py跨路最终接线；runtime路拥有api_server，不并发修改。会话effort持久化仍需补齐或指派。返回代码不能直接当已验收。

此前面板Node代理自报407通过，父代理全量复跑被中断，真实执行终态未核实，不记为父代理全量通过；按新要求不立即重跑。


## 当前父代理检查点

deleg_bb6b89a1 两路已返回并由父代理复验：artifacts/parent-review/continuation-final.log 为6文件43测试通过；tui-exit-final.log记录exit=0、clean_exit=True、quit_written=6、quit_echoed=True、storage_unavailable=False、group_alive=0。旧/quit超时来自探针没有发命令，不是已确认产品退出故障。

配置读取已复用核心ruamel.yaml的AST适配（clawd_server/yaml_adapter.py），保留原侧车循环/深度/别名/重复键脱敏检查；native-yaml-regression.log为18测试通过。面板tests新增包标记避免核心tests同名遮蔽，panel-python-full-4.log全量Python退出0。整合探针config-read HTTP200，continuation真实started，仍未完成外部模型/完成通知模型回合/MCP/浏览器，退出2而非伪绿。

面板Node逐文件69文件62通过，7个失败或超时；deleg_ccbca777 / sa-0-a7b06d10正在处理指定7测试及必要helpers，父代理不要并发改这些文件或vite.config.ts。证据artifacts/parent-review/node-files/results.json。返回后须亲自复跑完整套件。未提交、推送、部署。


## 最新父代理复验（2026-11-04，第二批返回后）

用户确认其他 agent 曾清理磁盘；原 artifacts/parent-check 丢失按外部清理记录，不归因于测试误删。新的父代理证据写入 artifacts/parent-review，旧日志路径仅保留历史记录，不当作仍可交付的证据。

父代理重跑项目、核心入口/路径、打包、绑定及 API server/runs：12 文件、251 测试通过，退出 0（regression.log）。面板项目专项、typecheck、build 全部退出 0（panel-projects.log、panel-typecheck.log、panel-build.log）。已实际打开 wheel 核对包含 clawd_server、clawd_projects/inheritance.py、clawd_cli/main.py；不代表 wheel 脱离源码后面板可运行。

继承为 projects.db 先提交、state.db 后提交，不是跨库原子事务；state 回滚可能留下孤儿绑定。TUI PTY 日志出现 storage_unavailable，真实原因日志指向 gateway.session_local_recovery 的 restore_policy invalid policy，不能用随后 ready 视为通过。下一路 deleg_8436d4c2 / sa-0-b4939ba4 正在修复该错误及 run_agent/TUI seed 吞 ProjectInheritanceError 的边界；父代理不得并发修改这一路 runtime/session-policy 文件。未提交、推送、部署。

## 上一接续检查点（2026-11-04）

旧段落保留作来源；当前四路 core/TUI/resume/project 已整合到本工程，仍未提交、推送、部署。项目 API 已挂真实 adapter，父代理此前项目/记忆/API 回归退出 0。

面板项目列表代理 deleg_f1cce542 已返回；父代理补齐 chat-page 新会话首发，使用 prepareCreatedSession：创建后先绑定并核对 session_id/project_id，再锁模型和启动；失败清理新空会话，清理失败明确报错，绝不降级普通模式。原布局、出字动画未改。旧文件夹仅记录需显式目录映射，不自动迁移或删除。

父代理新复验：项目 Node 专项 11 通过（project-panel-final.log）、面板 typecheck/build 退出 0；验收退出码测试退出 0（verification-gate-test.log）。日志均在 artifacts/parent-check。尚未完成浏览器/真实模型端到端。

scripts/verify_clawd.py 不再把 blocked 算全绿；全通过 0、失败 1、未完成 2。首轮静态页逐字节比对误报，已查明服务合法注入 __CLAWD_ARTIFACT_ROOTS__，改为同时核对隔离根注入与构建产物，最终状态查 verify-clawd-final.log。续跑探针未实际执行完成回合、无获准模型提供方、MCP及浏览器未实连、配置读取缺 PyYAML均仍是阻塞，不宣称整合完成。

当前委派 deleg_8415f2e1 两路：sa-0-8b3ef4f8 负责核心 fork/edit/compression 项目继承（项目 service/db、state/branch、项目路由及必要 fork/edit 后端）；sa-1-354459f4 负责包收集、TUI启动与Desktop残留入口（pyproject/clawd_cli/TUI bootstrap及scripts入口）。父代理不能并发修改这两路文件。返回后必须亲自核查和复跑，不凭自报完成。子代理不能提交、推送、部署或再次派发。

## 原始实施记录

状态：方案已由用户批准，实施中，未推送、未部署。不能声称产品已完成。

需求：删除官方 Web 面板与 Desktop；保留 TUI，产品展示改 clawd；把自研面板作为唯一 Web 界面与核心深度绑定；保留面板原样式与出字动画。

用户追加授权：子代理完成后主代理自动续跑从面板迁到核心。新 worktree clawd-resume-work 独立负责 gateway/wake.py、API adapter生命周期与新 gateway/clawd_continuation 模块。绑定代理已收到禁用旧 sidecar 续跑线程的指令；父代理最终检查旧逻辑退役及消费者兼容。要求不依赖面板启动、持久化去重、重启恢复、取消/插话/待审批时不误唤醒，避免两套调度并存。未实现和验证前不可标为已迁移。

来源与文件归属见 source-map.md；批准方案 /root/.hermes/plans/2026-11-03_225412-clawd.md。

现状：上游和面板已独立克隆。核心、TUI 两个隔离 worktree 已派发；绑定代理仅改新工程里的面板克隆和新增后端。代理禁止自行提交、推送、部署和二次派发。父代理必须亲自验证。

工具链：系统 Python 3.13.5、Node 24.22.2；固定源码需要 Python 3.14 与 Node >=26。使用项目 PM 在 .dev-home/tools 准备工具链与 .venv 开发测试环境，所有 bootstrap 日志在 artifacts/bootstrap/。不动系统或官方安装环境。初始可用磁盘约 5.7GB，内存约 3.9GB；不得并发重型构建。

验证：Python3.14.9、Node26.12.1/npm11.20.0已亲自核验；未改动核心的 API 基线4文件200测试通过（退出0），详见 verification.md 和 artifacts/baseline/api-tests.log。尚未执行整合验收。外部模型、MCP、实时浏览器依赖未配置；任何需要这些能力的端到端验收不得报告通过。历史数据和凭据不自动迁移。

安全边界：CLAWD_HOME 单独解析，所有旧 HERMES_HOME 兼容适配仅指向 clawd 自己的数据。API 运行事实和 TUI 状态必须一致；保留鉴权、审批代次、幂等键和事件游标。生产路径、重启服务白名单与安装插件回落必须消除。
