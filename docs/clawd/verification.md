# clawd 验证记录

## 已验证基线

上游固定 c314285f077e91937c995d419b9fa40af9eb4981。Python 环境由项目 PM 建立，build_env 进程退出 0；实测 Python 3.14.9、pytest 9.1.1、aiohttp 3.14.5、fastapi 0.133.1。Node 由同一隔离 PM 工具库准备，实测 v26.12.1、npm 11.20.0。

解释器：/root/hermes-workspace/clawd/.venv/bin/python
Node PATH 前缀：/root/hermes-workspace/clawd/.dev-home/tools/node-26.12.1-linux-x64/bin
构建工具库：/root/hermes-workspace/clawd/.dev-home/tools

在尚未合并任何核心变更时运行：

HOME=/root/hermes-workspace/clawd/.test-home HERMES_HOME=/root/hermes-workspace/clawd/.test-home/state HERMES_RUNTIME_DIR=/root/hermes-workspace/clawd/.dev-home/tools HERMES_PYTHON=/root/hermes-workspace/clawd/.venv/bin/python scripts/run_tests.sh -j 2 --file-timeout 60 tests/gateway/test_api_server.py tests/gateway/test_api_server_runs.py tests/gateway/test_api_server_sse_keepalive.py tests/gateway/test_api_server_jobs.py

结果：4 文件、200 测试通过、0 失败、退出码 0。完整日志 artifacts/baseline/api-tests.log。这是改造前基线，不是 clawd 整合验收。

## 尚未完成

核心裁剪/独立入口回归；TUI 品牌、构建与真实PTY；面板类型检查/构建/Node/Python/浏览器测试；clawd服务实际启动与鉴权；核心与面板运行契约、并发/重试/审批/断流恢复；外部模型、MCP和实时画面的真实整合；隔离版本回滚演练。尚未执行或缺少依赖的项目都不得标为通过。

不做生产部署或迁移，未触碰生产数据库和服务。所有最终数字以实际测试输出为准。
