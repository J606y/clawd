# clawd 最终验收矩阵

本文件与 `scripts/clawd_acceptance/` 一起写成。写完时没有执行 prepare、run、report，没有跑测试、构建、浏览器探针或模型回合。当前工程仍是并行实现中的中间态，不是最终成品。实现全部交付后，只做一次统一验收。

## 入口

```text
python3 scripts/clawd_acceptance/accept.py
python3 scripts/clawd_acceptance/accept.py prepare
python3 scripts/clawd_acceptance/accept.py run --run --implementation-complete
python3 scripts/clawd_acceptance/accept.py report
```

无参数退出 2，不写结果，不是通过。`prepare` 的进程退出 0 只表示计划已写入 `artifacts/final-acceptance/prepare-plan.json`，报告里的验收退出码仍是 2。`run` 必须同时有 `--run` 和 `--implementation-complete`，否则拒绝且不启动阶段。

机器报告 schema：`scripts/clawd_acceptance/report.schema.json`。总结果只看去重后的必需检查：全通过 0，任一失败 1，阻塞、未跑、未知、空结果或中断为 2。`blocked` 不改写成通过。

## 阶段

按顺序单路执行，不把委托并发 30 当成测试并行度。核心固定 `-j 1`。

| 阶段 | 做什么 | 缺依赖时 |
| --- | --- | --- |
| core | `scripts/run_tests.sh -j 1 --file-timeout 300 --basetemp <隔离目录> tests` | 只发现一次 `tests`。四组用例和若已创建的 `tests/clawd_auth_native`、`clawd_memory_native`、`clawd_event_native` 都在这次发现里，不另跑 |
| tui-check / tui-build / tui-pty | 固定 Node 26 的 npm check、build，再真实 PTY `clawd --tui` | 无 `node_modules` 或 `dist/entry.js` 则阻塞，不自动安装或重建 |
| panel-typecheck / panel-build | 面板 npm 脚本 `typecheck` / `build` | 同上 |
| panel-node | 固定 node，`tests/*.test.mjs`，墙钟 900 秒 | 见超时 |
| panel-python | 仓库 `.venv` 的 unittest，显式环境 | 不用系统 `python3`，缺失即阻塞 |
| panel-browser | `scripts/test-gates.mjs --target=browser --output-dir=<dir>` | 无 `/usr/bin/chromium` 不启动，不 mock |
| package | 隔离目录里 `build_wheel`，打开 wheel 核对成员 | 成员齐也不能证明面板脱离源码可运行 |
| license-inventory | `scripts/clawd_inventory/generate.py --checkout <repo> --output <隔离文件>` | 不改许可脚本，不造假全量数据，不跑 npm audit |
| verify-integration | 调用既有 `scripts/verify_clawd.py` | 其 blocked 原样保留 |
| real-model-mcp | 仅当显式 `--provider-config` 且不是生产家目录，并含非占位凭据 | 缺失即 blocked。请求体带 `model=grok-4.7`。不要求响应自带 `runtime.model`，也不用它核对当前父代理 |
| continuation | 只承认带请求模型戳的真实委派完成回合 | `test-not-llm` 不是通过。缺 `runtime.model` 不是父代理不符 |
| rollback | `scripts/clawd_cutover/cli.py rollback --allow-root --target --journal --confirm ROLLBACK` | 没有隔离 journal 则 blocked，不调用 `/root/panel-deploy.sh` |

中断恢复先看 `artifacts/final-acceptance/logs/*.state.json` 和 pid。进程还在则不另开；进程已死且未给 `--resume-interrupted` 则不重跑。不删除已有日志、产物或工具链。

## 门禁

共享契约文件 `docs/clawd/implementation-completion-manifest.json` 由另一路独占。本编排只读，不创建、不修改。`schema_version` 必须为 1。`items[]` 含 `id/scope/owner/status/evidence/blocking`。`status` 只能是 `implemented/unverified/pending/blocked`。全部 `blocking` 项为 `implemented`，且命令行有 `--implementation-complete`，契约门才开。`implemented` 只表示实现收齐，不是测试通过。

编排器另加、但不改契约的门禁：清单非空且 id 不重复；四组用例目录和准备文档都已交付；`scripts/clawd_inventory` 声明 `--offline`、`--local-only` 或 `"network_audit": false`；固定工具链存在。任一不满足，`run_allowed` 为假，退出 2。

必须纳入、本路不创建也不提前跑的目录：

- `tests/clawd_race_cases`，文档 `docs/clawd/race-case-preparation.md`，主题为审批代次、重复唤醒、取消人工输入、重启恢复
- `tests/clawd_file_auth_cases`，文档 `docs/clawd/file-auth-case-preparation.md`
- `tests/clawd_memory_scope_cases`，文档 `docs/clawd/memory-scope-case-preparation.md`
- `tests/clawd_event_recovery_cases`，文档 `docs/clawd/event-recovery-case-preparation.md`

后三份文档须列出接口依赖。核心只把 `tests` 作为一次发现根，不把子目录再传一遍，避免重复执行。日志里看不到该目录则不能把核心记为通过。阶段注解里的 passed 数量不加入总成绩。

## 隔离与超时

验收数据目录是 `artifacts/final-acceptance/account-home/.clawd`。转发给 `env -i` 的 `HOME` 是 `account-home`。临时目录是验收根下的 `tmp` 与 `pytest-basetemp`。直接拉起的进程使用全新环境字典，不复制当前进程环境。

`prepare` 的进程退出 0 只表示计划已写入。报告 `plan_only=true`，`acceptance_green=false`，验收退出码仍是 2。不能把这次 0 当成验收全绿。

`run_tests.sh` 的 `env -i` 转发 `HOME`，不转发 `CLAWD_HOME`。核心在未设置 `CLAWD_HOME` 时解析为 `$HOME/.clawd`。验收因此把转发的 `HOME` 设成隔离账号目录，数据目录就是它下面的 `.clawd`。父进程里的 `CLAWD_HOME` 不是子进程隔离证明。父进程的 `HERMES_HOME` 设成该数据目录，避免包装脚本去读账号生产工具库。不读生产凭据。

指定验收模型 `grok-4.7` 只放进外部用例请求体。报告不假设自带 `runtime.model`，也不用它核对当前父代理。假执行器标记仍不能通过。未知或未运行不能写成 pass。

未应用的 systemd/nginx 模板不证明部署支持。`deploy-support` 保持 blocked。

写本文件时未运行本编排器，未跑配套测试，未构建。完成清单快照不由本路改成 implemented。


面板 Node 全量实测约 395 秒。墙钟下限 900 秒。35 秒或 240 秒杀掉记为编排错误，状态是阻塞，不能写成产品失败。pytest 每文件超时保持 runner 默认 300 秒，不用 35/240。

## 身份

期望模型 `grok-4.7` 是外部用例请求字段，不是报告必须自带的 `runtime.model`。请求体回显、日志广告名 `hermes-agent`、`test-not-llm` 都不能通过。没有回合时不编造父代理不符失败。

## 尚缺且只记 blocked

- 显式、非生产、含真实凭据的 `--provider-config`。本路不读 `/root/.hermes`。
- 真实 MCP 会话。空响应保持 blocked。
- `/usr/bin/chromium` 与隔离安装前缀。源码里有面板不能代替。
- 若尚未创建：`tests/clawd_auth_native`、`tests/clawd_memory_native`、`tests/clawd_event_native`。创建后只进入一次 `tests` 发现。
- 隔离 cutover journal。没有它就不调用回滚，也不调用生产部署脚本。
- 未应用模板，部署支持未验证。

## 未完成

写本文件时未运行本编排器。没有 commit、push、deploy。配套测试未运行。
