# clawd 来源与文件归属

上游：NousResearch/hermes-agent，固定提交 c314285f077e91937c995d419b9fa40af9eb4981；克隆在 /root/hermes-workspace/clawd，分支 clawd。保留上游许可证和历史关联，不推送。

面板：独立 hk 克隆 apps/panel，提交 c28f2f8bf6faa11c1e3901a39db6d0826b78bc29；核验时主检出干净且远端 hk 同一提交。面板主检出只读，未改动。旧目录 hermes-panel-rootcause-20261024 是日志/探针产物目录，不是 Git 克隆；未清理。hermes-panel-state 保留。

核心裁剪/入口归属：clawd-core-work 隔离 worktree，负责官方 web/apps/desktop 专用文件、hermes_cli 入口、hermes_constants 路径、clawd_cli、新 clawd 启动器、根清单与锁文件及核心隔离测试。不得删除 apps/shared 或共享后端服务。

TUI 归属：clawd-tui-work 隔离 worktree，负责 ui-tui 用户可见品牌及必要 tui_gateway 显示字符串，保持行为和协议。内部 @hermes/ink、@hermes/shared 不机械改名。

绑定归属：apps/panel 独立克隆和新增 clawd_server、tests/clawd_server、scripts/verify_clawd.py、docs/clawd/api-contract.md。保持 panel-api/、panel-core/、panel-auth/ 路由。核心入口调用 clawd_server.__main__.main(argv)。

新增核心续跑归属：clawd-resume-work，负责 gateway/wake.py、gateway/platforms/api_server.py、api_server_runs.py 与新增 gateway/clawd_continuation.py 及同前缀模块和 tests/gateway/test_clawd_continuation*.py。用户已明确授权从面板迁移。面板旧自动续跑区域暂留只读，绑定代理不得启动它；退役/兼容清理由父代理整合时统一处理。

父代理负责环境、来源记录、跨路核验、差异合并与实际整合测试。新源码不得访问 /usr/local/lib/hermes-agent，不写生产 ~/.hermes，不使用生产 state.db，不修改系统服务或 nginx。

实际变更文件列表与消费者在整合完成后据 Git 差异补录；当前是开发分工，不代表实现完成。
