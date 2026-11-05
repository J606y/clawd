# clawd 统一验收命令计划 — 2026-11-05

未执行验收。下面的命令是准备，不是运行记录。没有退出码，没有日志，没有 pass。

观察时刻：2026-11-05T05:47:57+08:00。实现没收齐。六份 detail 报告还没有。旧门禁清单 `docs/clawd/implementation-completion-manifest.json` 仍是 03:07 的过期快照，`implementation_complete` 为 false。因此现在即使加上 `--implementation-complete`，`gate.py` 也会拒绝运行。这是正确拒绝，不是产品失败，更不是通过。

本计划不修改 `scripts/clawd_acceptance/`。`schema_version` 2 的总账不能替换门禁文件。

## 退出码

只看去重后的必需检查，空集不能通过：

| 结果 | 退出码 |
| --- | --- |
| 必需检查非空，且全部是 pass | 0 |
| 任一断言失败 | 1 |
| 没有失败，但有 blocked、unknown、not_run、interrupted、空结果，或必需检查为空 | 2 |

混合状态：只要有失败，就是 1，不能被 blocked 抵消。没有失败时，blocked 或未知不能写成 0。

`prepare` 的进程退出 0 只表示计划文件写上了。验收退出码仍是 2。`implemented-unverified`、`detail-pending`、`in-flight`、子代理自报、模块可导入、run 请求被接受、线程没重复启动，都不是 pass。

`test-not-llm`、日志里的 `hermes-agent` 广告名、空 MCP 响应，不能通过。缺 `runtime.model` 不是父代理不符，是 blocked。

35 秒或 240 秒杀掉面板 Node 全量，记编排错误，状态 blocked，不是产品失败。Node 墙钟下限 900 秒。

## 现在不能跑的原因

1. 设置、续跑、品牌仍在改；浏览器方案未定。
2. 六份 detail 未返回。没有这些报告，对应项保持 `detail-pending`，不能进 pass。
3. 旧门禁清单过期。集成人核对 detail 后再改它。本轮不改。
4. 没有非生产的模型/MCP 凭据、隔离 cutover journal、隔离安装前缀。缺了就 blocked，不读 `/root/.hermes`。
5. 没有新构建的 wheel 与 sdist。两个 `CLAWD_DISTRIBUTION_*` 都空。

工具链只在真正开跑时检查是否存在，缺失即 blocked，不安装、不回落系统 Python：

- `/root/hermes-workspace/clawd/.venv/bin/python`
- `/root/hermes-workspace/clawd/.dev-home/tools/node-26.12.1-linux-x64/bin/node`
- `/root/hermes-workspace/clawd/.dev-home/tools/node-26.12.1-linux-x64/bin/npm`

隔离：`HOME` 用验收账号目录，数据在其下 `.clawd`。`run_tests.sh` 的 `env -i` 转发 `HOME`，不转发 `CLAWD_HOME`。父进程里的 `CLAWD_HOME` 不能当隔离证明。`TMPDIR` 用验收根下的临时目录。不读生产 secret，不调用 `/root/panel-deploy.sh`，不碰 `/usr/local/lib/hermes-agent`。

## 命令

工作目录默认 `/root/hermes-workspace/clawd`。Node 必须来自上面的固定 bin，下面写成 `NODEBIN`。

只写计划，不跑阶段。进程 0 不是验收 0：

```sh
/root/hermes-workspace/clawd/.venv/bin/python \
  /root/hermes-workspace/clawd/scripts/clawd_acceptance/accept.py prepare
```

门禁允许之后，一次统一跑。缺 `--run` 或缺 `--implementation-complete` 必须退出 2，且不启动阶段：

```sh
/root/hermes-workspace/clawd/.venv/bin/python \
  /root/hermes-workspace/clawd/scripts/clawd_acceptance/accept.py run \
  --run --implementation-complete
```

这个入口今天不能当绿用。它的 package 阶段也还不是真正的脱源码检查。分项命令如下，仍不在本轮执行。

### lint / type / build

TUI `check` 已经包含 ink 构建、typecheck、i18n、vitest 和 eslint。只记一次，不要把内部步骤加成多次通过：

```sh
PATH="$NODEBIN:$PATH" npm run check --workspace ui-tui
PATH="$NODEBIN:$PATH" npm run build --workspace ui-tui
```

面板没有 `lint` 脚本。不要发明 `npm run lint`，也不要因为没有脚本就记 pass。typecheck 与 build 分开保存退出码；build 里再跑一次 `tsc` 不能覆盖前面的失败：

```sh
PATH="$NODEBIN:$PATH" npm --prefix /root/hermes-workspace/clawd/apps/panel --workspaces=false run typecheck
PATH="$NODEBIN:$PATH" npm --prefix /root/hermes-workspace/clawd/apps/panel --workspaces=false run build
```

核心没有单独的 lint 阶段。不要用 `py_compile` 代替。核心只发现一次 `tests`，`-j 1`，不要把竞态、文件、记忆、事件子目录再传一遍：

```sh
HOME="$ACCEPT_ACCOUNT" \
HERMES_HOME="$ACCEPT_ACCOUNT/.clawd" \
HERMES_RUNTIME_DIR=/root/hermes-workspace/clawd/.dev-home/tools \
HERMES_PYTHON=/root/hermes-workspace/clawd/.venv/bin/python \
TMPDIR="$ACCEPT_TMP" \
/root/hermes-workspace/clawd/.venv/bin/python \
  /root/hermes-workspace/clawd/scripts/run_tests.sh \
  -j 1 --file-timeout 300 --basetemp "$ACCEPT_TMP/pytest" tests
```

日志里看不到 `tests/clawd_race_cases`、`tests/clawd_file_auth_cases`、`tests/clawd_memory_scope_cases`、`tests/clawd_event_recovery_cases`，核心不能记 pass。目录空或只有准备文档，是 blocked/2，不是通过。续跑旧测试仍断言 streak 硬停；次数门槛没改完之前，核心失败是预期失败，退出 1，不能删断言。

### 面板、TUI、恢复

面板 Node 用固定 node，墙钟至少 900 秒：

```sh
cd /root/hermes-workspace/clawd/apps/panel
PATH="$NODEBIN:$PATH" node --test --test-concurrency=1 tests/*.test.mjs
```

面板 Python 不用 `package.json` 的 `test:python`，那个脚本调用系统 `python3`：

```sh
cd /root/hermes-workspace/clawd/apps/panel
/root/hermes-workspace/clawd/.venv/bin/python -B -m unittest discover -s tests -p 'test*.py'
/root/hermes-workspace/clawd/.venv/bin/python -B -m unittest discover -s sidecar -p 'test*.py'
```

若 `tests` 包名和核心冲突，先修夹具路径再重跑。只改 `PYTHONPATH` 不能压过另一个同名正规包。这不是本轮已验证的事实。

浏览器门没有 `/usr/bin/chromium` 就不启动，不 mock：

```sh
cd /root/hermes-workspace/clawd/apps/panel
PATH="$NODEBIN:$PATH" node scripts/test-gates.mjs --target=browser
```

TUI 真实退出走现有 PTY 探针，不手写一个只搜索连续 `ready` 的脚本。探针必须先写入 `/quit` 并看到回显，再判超时。强制杀掉不是干净退出。`storage_unavailable` 不能被后来的 ready 抵消。命令形态是：

```sh
/root/hermes-workspace/clawd/.venv/bin/python /root/hermes-workspace/clawd/clawd --tui
```

本轮没有执行它，也不证明 `clawd` 脚本的 TUI 参数可用。

恢复与竞态不单开第二套 pytest。它们必须出现在上面那一次 `tests` 发现里。审批代次、重复唤醒、取消、人话、重启恢复由 `tests/clawd_race_cases` 覆盖。续跑的真实模型回合另外记，注入执行器不能代替。

### 脱源码分发

不要跑现有 package 阶段那条只导入 `build_wheel` 的命令，并把它的 0 当成打包通过。

以后开跑时：

1. 在隔离目录用项目 venv 实际构建 wheel 和 sdist。本轮不给出会副作用构建的具体 setuptools 调用，避免被当成已授权执行。
2. 构建失败是 1。没有新档案、或不在隔离目录，是 blocked/2，不要启动档案测试。
3. 两个变量都指向这次的新档案后，才跑：

```sh
CLAWD_DISTRIBUTION_WHEEL="$WHEEL" \
CLAWD_DISTRIBUTION_SDIST="$SDIST" \
/root/hermes-workspace/clawd/.venv/bin/python \
  -m pytest -q tests/clawd_server/test_distribution_package_manifest.py
```

核对 `clawd_server/bundled/RESOURCE_CONTRACT.json` 的每条 `files[].path`：普通文件、大小、sha256、无符号链接、无清单外残留。清单文件本身必须在两个档案里。四个内部包许可未知就不能标 MIT。Nix 是否把同一份资源打进 wheel，以 distribution detail 为准，不在这里模拟。

许可提取，不联网审计：

```sh
/root/hermes-workspace/clawd/.venv/bin/python \
  /root/hermes-workspace/clawd/scripts/clawd_inventory/generate.py \
  --checkout /root/hermes-workspace/clawd \
  --output "$ACCEPT_TMP/license-inventory.json"
```

脚本缺失或四个内部包仍未知：blocked，不套 MIT。

### 外部依赖

这些缺依赖就是 blocked/2，不模拟：

- 非生产 `--provider-config`，请求模型 `grok-4.7`，且完成的是真实回合
- 真实 MCP 会话；空列表或空 body 保持 blocked
- `/usr/bin/chromium`
- 隔离安装前缀；源码树里有面板不能代替
- 隔离 cutover journal。没有 journal 就不调用：

```sh
/root/hermes-workspace/clawd/.venv/bin/python \
  /root/hermes-workspace/clawd/scripts/clawd_cutover/cli.py rollback \
  --allow-root --target "$ISOLATED_TARGET" --journal "$ISOLATED_JOURNAL" \
  --confirm ROLLBACK
```

不调用 `/root/panel-deploy.sh`。

`scripts/verify_clawd.py` 的 blocked 原样保留。静态首页必须同时核对构建产物和 `__CLAWD_ARTIFACT_ROOTS__` 注入，不能只看 HTTP 200，也不能和原始 HTML 逐字节比较。

## 开跑前还要有的证据

- 六份 detail 已落盘，集成人已把过期门禁清单改成当前字节，且本总账里的 `detail-pending` 已被那次核对替换。替换之前不能标 pass。
- `uninstalled-native-bypass-still-reachable` 有明确结论：活路径已安装，绕过 `bind` 的旧分支要么不可达，要么被记成未退役。不能只写“install 函数存在”。
- 无安全接口的设置项仍是拒绝或 501，没有假成功。
- 成功续跑 5 次硬停已从决策、消费点和可归因恢复里去掉，保护测试还在。
- 浏览器 daemon 授权不是只靠客户端登记。
- 没有 commit、push、deploy、生产迁移。验收通过也不构成这些授权。
