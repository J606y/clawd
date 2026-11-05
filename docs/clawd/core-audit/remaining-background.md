# 审批与后台剩余复核

未运行测试、构建、导入、探针、调度或网络。9 项函数已读。仍成立 2，误报 7，已修复 0，需验证 0。与已有 active 不重复。

## 逐文件

`tools/approval.py:1211-1216` 只在完全无标记时自动同意。CLI、网关、API、续跑、ACP、TUI、托管 worker、cron 都先绑标记，或走无人值守默认拒绝。`check_dangerous_command` 无产品调用者。误报。

`approval_gateway_wait.py:248-261` 在出队锁内收下截止后落入的选择。这是真实同意，删除后不能重放，不是把超时哨兵当成功。误报。

`tool_executor.py:952-958` 与 `1493` 在打中断位后等 3 秒。已完成的 future 提交真实结果；未开始的后续调用被中断检查跳过。误报。

`clawd_continuation_gates.py:47-51` 缺计划探针时返回 False。仓库没有 plan_mode 工具，也没有计划侧表或外持有者接线。产品只 arm 一套续跑。误报。

`clawd_continuation.py:350` 只读最新 300 条，且不含祖先。`gates.py:68-81` 用原始 session id 查租约，写入在压缩谱系根。投递会解析到子会话尖端。仍成立。

`api_server_runs.py:631` 队列键是 run id，但 `906` 已存 session_id，`1059` 在等待前写成 waiting_for_approval。authority 走 FIFO，不并行执行。键不一致不是绕过。误报。

`wake.py:231-235` 吞掉续跑通知异常。外层把投递标完成，不 release、不重试。`evaluate` 宽异常只 fail_closed。仍成立。

`async_delegation.py:591` 缺行返回 True。回放只读仍在的账本行，剪掉的行进不了另一进程。同进程无第二认领者。误报。

`hermes_panel_sidecar.py:6175` 直接 main 会起旧线程。clawd 只 `serve_forever`；bind 替换 watcher 并拒绝 main；`runtime_native.install` 不调用 main。误报。

## 按严重度

中：bg-window。低：bg-swallow。其余 7 项误报。详情与哈希见 `docs/clawd/core-audit/remaining-background.json`。
