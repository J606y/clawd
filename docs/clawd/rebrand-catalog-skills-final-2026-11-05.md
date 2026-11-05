# 目录与技能文案收口 — 2026-11-05

静态核对。未运行 `scripts/run_tests.sh`、文档站构建、类型检查、`py_compile`、导入、`clawd`/`hermes` 命令或网络。未 commit / push / deploy。未删文件。未改运行逻辑、解析器或安装包。未读生产凭据。未改原面板、线上服务、`/usr/local/lib/hermes-agent`、用户安装技能、`plugin-catalog/*.yaml`、上游 Desktop 专页。未重扫全树，未改 `rebrand-inventory.json`。本文件不是通过记录。

入口依据只读源码：`clawd_cli/main.py` 在进解析器前拒绝顶层 `update` / `dashboard` / `desktop` / `gui` / `webapp`。其余词进入 `hermes_cli.main`。`clawd plugins` 有 `search` / `browse` / `install` / `update` / `validate`，没有 `catalog`。`clawd config set`、`clawd skills search|install|update`、`clawd memory setup|status|off`、`clawd profile create`、`clawd kanban show <task-id>`、`clawd mcp add|list|install|configure`、`clawd chat`（含 `-Q -q --provider -m -t`）、`clawd status`、`clawd tools`、`clawd --tui`、`clawd gateway` 均在当前解析器。`pm` 在 `hermes_cli/main.py` 模块级按 `argv[1]==pm` 转给 `pm.cli`，`clawd` 不拒绝它。

## 已改

- `skills/autonomous-ai-agents/hermes-agent/references/configuration.md`：只把 `` `hermes plugins install <name>` `` 改为 `` `clawd plugins install <name>` ``。技能名未改。
- `plugin-catalog/README.md` 镜像块外的目录说明：标题与首段产品名改为 clawd；发现命令改为已注册的 `` `clawd plugins browse` / `clawd plugins search` ``，安装改为 `` `clawd plugins install <name>` ``。没有把不存在的 `plugins catalog` 写成 `clawd plugins catalog`。
- `optional-skills/autonomous-ai-agents/honcho/SKILL.md`：标题与可见产品句改为 clawd。`plugins install`、`memory setup|status|off`、`profile create` 改为 `clawd`。
- `optional-skills/autonomous-ai-agents/agent-merge-conflict-arbiter/SKILL.md`：两处 `kanban show` 改为 `clawd kanban show`。
- `optional-skills/devops/actual-setup/SKILL.md`：`config set`、`chat`、`status` 与可见产品句改为 clawd。`~/.hermes/.env` 未改。
- `optional-skills/payments/stripe-link-cli/SKILL.md`：`mcp add` / `mcp list` 与可见产品句改为 clawd。
- `optional-skills/productivity/telephony/SKILL.md`：`skills search` / `skills install` 与可见产品句改为 clawd。示例号码未改。
- `optional-skills/productivity/property-listings/SKILL.md`：只改 `skills install`。
- `optional-skills/creative/unreal-mcp/SKILL.md`：`mcp install` / `configure` 与可见产品句改为 clawd。
- `optional-skills/creative/anatomy/SKILL.md`：可见句改为 clawd；`skills update` 改为 `clawd skills update`（子命令，不是被拒绝的顶层 `update`）。
- `optional-skills/creative/ip-as-logo/SKILL.md`：适应说明与 `tools` 改为 clawd。上游正文未改。
- `optional-skills/finance/excel-author/SKILL.md`、`pptx-author/SKILL.md`：检测与调用一起从 `hermes` 改为 `clawd`，避免分支检测一个名字、执行另一个。
- `optional-skills/software-development/hermes-agent-skill-authoring/SKILL.md`：标题、`skills install`、「clawd tools」改为当前产品。技能名与仓库路径未改。
- `optional-skills/software-development/node-inspect-debugger/SKILL.md`：`hermes --tui` 改为 `clawd --tui`。
- `optional-skills/software-development/python-debugpy/SKILL.md`：调试启动改为本检出启动器 `python clawd` / `python clawd --tui`，网关示例改为 `clawd gateway`。

## 保留及理由

- 作者行、`author: Hermes Agent` 约定、`metadata.hermes`、技能名 `hermes-agent` / `hermes-agent-skill-authoring`、`related_skills`、tags。
- OAuth `--client-name "Hermes"`（stripe-link-cli）。
- `HERMES_*`、`~/.hermes`、`hermes-docs-venv`、`<hermes-agent-repo>`、官方 URL、`requires_hermes`、`@hermes/plugin-sdk`、`window.hermesDesktop`。
- 「Hermes Docker image」与缓存目录名。上游镜像不是本检出命令。
- `configuration.md` 的 `` `hermes update` ``。顶层 `clawd update` 被拒绝；这句说的是迁移发生在上游更新路径，不是让用户去跑 `clawd update`。
- `actual-setup` 的 “old Hermes”：旧版本事实，不是当前入口。
- `property-listings` 第 17、28 行 Hermes desktop transcript / conversation。改成 clawd Desktop 会冒称已裁剪的 Desktop。
- Honcho 的 `hermes honcho *` 与 host block `hermes.coder`。核心已删掉硬编码 honcho argparse（`plugins/AGENTS.md`）；目录插件在外部仓库 `plastic-labs/honcho`，本检出没有插件源，未拉取。插件子命令未证，不改成 `clawd honcho`。`hermes.coder` 是配置键，不是命令。第 406 行 dashboard 是 Honcho 自己的 UI，不是 `hermes dashboard`。
- `plugin-catalog/README.md` 的 `<!-- admission-rules -->` 块（约 13–172 行）整段未改。`tests/website/test_catalog_rules_mirror.py` 要求它与 `website/docs/developer-guide/plugins/catalog-submission.md` 逐字相同，网站页不在可写名单。块内 `` `hermes plugins update` `` / `` `hermes plugins validate` `` / `` `hermes auth add` `` 因此保留。这些子命令在解析器里存在，但只改一边会拆开准入政策。
- 同文件 schema 后的 “integrates into Hermes”、`hermes-agent` 仓库名、Desktop onboarding 事实未改，避免和未改的准入块各说各话。
- 检出根 `hermes` 仍是兼容启动器，直接进 `hermes_cli.main`，不经过 clawd 拒绝表。技能改为 `python clawd` / `clawd pm`，没有删兼容脚本。`pm/cli.py` 的 `prog="hermes pm"` 和报错串不在可写名单，运行时仍可能打印 `hermes pm`。

## 目录 YAML：只判断，未写

不在可写名单，且是外部插件仓库描述，不是本检出 chrome。`name`、repo、SHA、`requires_hermes`、OAuth、许可证未动。

| 文件 | 判断 |
| --- | --- |
| `advanced-delegation.yaml` | `` `hermes delegation config` `` 不是核心子命令。插件源不在本检出。保留。 |
| `agent-importers.yaml` | 文件写明插件保留 `hermes claw`。`import-agent` 在 `LEFT_CORE.command`，但是插件注册名，源未核对。`` `hermes setup` `` 虽是核心命令，嵌在外部条目描述里，未单独改。 |
| `bubblewrap.yaml`、`singularity.yaml` | `` `clawd config set terminal.backend ...` `` 入口存在，后端 id 不该改。条目描述外部插件，未改 yaml。 |
| `mcp-serve.yaml` | `LEFT_CORE` 写明旧拼写 `hermes mcp serve`，插件命令是 `mcp-serve`，且是 MCP 客户端参数。未改，避免把客户端 argv 写成不存在的产品能力。 |
| `spotify.yaml` | `LEFT_CORE.cli=spotify` 只说明核心期望插件注册 `spotify`。插件源未核对。保留 `` `hermes spotify login\|status\|logout` ``。 |
| `github-copilot.yaml` | “for Hermes” 与 `` `hermes auth add copilot` `` 都在外部条目。`clawd auth add` 存在，但未改 yaml。 |

## 仍未知

- 已安装的 honcho / spotify / mcp-serve / delegation / agent-importers 插件实际 `register_cli_command` 拼写。未读插件仓库，未跑 CLI。
- `clawd pm environment documents` 是否在无网络、无 venv 时成功。只核对了分发点，未执行。
- `clawd skills install official/...`、`clawd mcp install unreal-engine` 的目录名是否仍能解析。未执行。
- 网站可选技能页已有 `clawd honcho`，与本次保留的 `hermes honcho` 不一致。以本检出插件源缺失为准，不跟着网站改。
- `build/lib/` 旧副本未重建。源码文案已改不能证明安装树或文档站已更新。

## 未验收

未跑测试、构建、导入或命令。夹具若仍断言旧产品名或旧命令，会失败；本次未改测试。本文件不是通过记录，也不构成上线授权。
