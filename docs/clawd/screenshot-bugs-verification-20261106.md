# clawd 截图故障与项目资源：集中验收状态

范围：新加坡 beqCprBT `/root/hermes-workspace/clawd` 与嵌套 `apps/panel`。按批准方案修改已有脏检出；未提交、推送、部署、重启服务或更改 nginx。Hermes 服务与站点未改。

## 已验证

- 核心定向套件：9 文件，118 项通过，零失败。日志 `/root/hermes-workspace/clawd-core-verification-20261105T215559393110Z/core.log`。真实档位 HTTP 路由、未知状态拒写、运行中模型锁、恢复/压缩谱系、项目资源鉴权/隔离/清理均在其中。
- 项目文件：新二进制限额50MiB，旧文本限额100000不动；全局10MB HTTP门仅对真实注册的两个项目文件 multipart POST 路由按50MiB+16KiB放宽。已实际上传、下载并比对超过旧全局上限的字节，跨项目拒绝通过。
- 项目指示：`test_instructions_roundtrip_prompt_and_frozen_snapshot` 验证 INSTRUCTIONS.md 持久化、回读、不写全局记忆、冻结快照不变及新项目上下文包含指示；限额、威胁检查、并发替换及HTTP路由回归通过。
- 前端集中回归115项通过、typecheck通过、独立构建通过。日志 `/root/hermes-workspace/clawd-bugs-verification-20261105T220247350023Z/{panel-node,typecheck,build}.log`，构建目录为该次独有 `bundle`，未覆盖服务读取的dist。
- 上述同批的旧browser进程因Chromium启动/退出清理问题超时，summary.passed=false，保留原始失败，不能把该批描述成全绿。该项随后独立修复并复测，见下一条。
- 390x844 Chromium手机布局最终exit0：`/root/hermes-workspace/clawd-mobile-verification-20261105T221531198946Z/browser.log`、`summary.json`。三个资源入口真实CDP触摸命中，modal顶层与尺寸、44px控件、安全留白、空档案、指示空态、记忆列表详情返回、读取失败不当空内容、已有文件入口、跨项目迟到响应隔离均过断言。
- 补齐测试机缺失的中文字体后，四张截图已下载到主代理并人工视觉核查：中文可读、无明显重叠裁切、指示0/8000、底部上传按钮、详情返回入口、两处“更新时间未知”均可见。本机证据目录 `/root/hermes-workspace/clawd-mobile-evidence-20261105T221531198946Z`，四张原图及contact.png；远端图在最终mobile-verification目录的screenshots。图是隔离夹具，不是线上截图。

## 本轮前端修复复核

- transcript保留输入泛型T并运行时校验消息身份；无as any，未回改chat-page。
- 空success缺project_id与真实跨项目分别报错，缺身份仍不放行。文件名原源码并无双转义；旧怀疑已驳回，当前使用显式码点校验并补正负向测试。
- 错配订阅不伪造failed、不发RUN_FINISHED、不stop上游；本地释放保留原ID/恢复记录。ownerConfirmed同时校验run/session，重新确认正确归属后清理错配提示和本地释放状态，回归已包含在115项中。
- Node日志存在Vite依赖扫描别名诊断；115项断言仍全过，类型及正式独立构建成功。未将该诊断描述成零告警或全站浏览器验收。

## 浏览器夹具修复与环境记录

初始TMPDIR过长使Chromium的SingletonSocket超出Unix socket路径限制；缩短自有临时路径并让子浏览器使用该TMPDIR。退出清理同时识别signalCode，SIGTERM后有界升级SIGKILL，启动错误带stderr。失败时保存页面状态、console异常、请求记录和PNG。

fetch夹具原只接受string/Request，不识别真实客户端传入的URL，误返回空项目ID；已按真实URL契约修正。CDP等待将DOM节点布尔化，避免序列化React引用链；内容隔离断言限定实际panel，不读包含script的整页textContent；记忆等待限定真实行，防止源码文案造成假通过。

本轮仅安装测试依赖Chromium及fonts-noto-cjk，安装日志在各自workspace子目录；没有重启产品服务。所有修改前按文件保存独有备份，未删除用户数据。

## 未完成／授权阻塞

- 原截图附件413由 `/etc/nginx/sites-enabled/clawd.20051212.xyz.conf` 默认1MiB导致；52MiB候选未应用，nginx未重载。因此线上大附件问题仍未修复。
- 主子会话已通过核心谱系及前端身份、迟到事件和订阅恢复回归，但真实完整会话页面的打开/返回/刷新验收尚未完成，不能以辅助函数回归代替。
- 新接口和新网页均未部署，当前服务仍旧代码，不声称用户现在可见。
- 两路只读深查的最终报告与运行证据仍需收到后由主代理核证归并；新增AUTH-GATE-01仅是待核线索，不是已确认或已修复缺陷。

当前派工入口 `/root/hermes-workspace/clawd-bugs-dispatch.md`；原批准计划 `/root/.hermes/plans/2026-11-06_042844-clawd-bugs.md`，后续项目资源追加范围及参考图见派工记录。保留所有未完成任务，不以本报告替代部署授权。
