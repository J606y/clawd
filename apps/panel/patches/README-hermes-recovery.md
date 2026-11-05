# Hermes公开源码恢复补丁

`hermes-agent-8edc5ea2-recovery.patch`针对公开仓库NousResearch/hermes-agent固定版本`8edc5ea255aa`，仅含以下三个文件：

- `gateway/platforms/api_server_runs.py`：admission观察者注册补齐interim_assistant_callback和reasoning_callback。公开汇报发布message.interim，思考仍发布独立reasoning.available；复用原有回调队列及过期generation拦截。
- `gateway/session_ingress.py`：在execution-only internal信任标记之前保存服务器内部普通API准入来源。不信任客户端metadata，不改变执行信任或控制授权。
- `gateway/response_filters.py`：在既有hidden、producer card优先级之后识别该私有来源；保留真实内部输入和heartbeat通知，不做历史数据迁移。

这不是已加载的热补丁。本任务禁止访问官方安装目录，因此只在工程工作目录的公开源码副本修改和测试，没有修改当前运行库，也没有重启网关。面板源码修复与此补丁的运行状态须分开汇报。

## 验证

使用公开固定版本源文件准备独立目录PUBLIC_SOURCE_DIR，保持原相对路径。除了上面三个文件，接缝验证器还需要同版本的`gateway/session_api_turn.py`、`gateway/session_display.py`和`gateway/run_turn_persistence.py`，它们不被补丁修改。原文件SHA256见`hermes-agent-8edc5ea2-public-source.json`。

在公开副本中运行`git apply --check /path/to/hermes-agent-8edc5ea2-recovery.patch`，再运行`git apply /path/to/hermes-agent-8edc5ea2-recovery.patch`。从面板仓库运行`python3 scripts/verify-upstream-recovery.py --source-root PUBLIC_SOURCE_DIR`。

验证器从实际公开函数AST加载必要接缝，使用合成输入，不导入Hermes安装库，不请求生产模型。它验证真实观察者注册表达式、公开/思考事件分发、工具回调不回归、迟到回调拒绝，以及信任标记→显示分类→用户记录持久化链路与隐藏/机器/心跳边界。这不是完整Hermes集成套件或真实模型端到端验证。

已经分别捕获原版回调和分类失败，修复后两项通过；随后在第二个原版公开副本干净应用此交付patch，逐文件比较候选并重跑两项通过。生产应用需在允许管理运行库的独立任务中进行，不在本次面板部署权限内。
