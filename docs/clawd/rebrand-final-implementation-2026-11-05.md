# 品牌遗留收口 — 2026-11-05

静态核对。未运行测试、构建、`py_compile`、导入、CLI、网络。未 commit / push / deploy。未删任何文件。未读生产凭据。未改原面板、线上服务或 `/usr/local/lib/hermes-agent`。命中次数不是缺陷数。未重扫 `rebrand-inventory.json`。

`apps/panel/sidecar.py` 不存在。侧车真实路径是 `apps/panel/sidecar/hermes_panel_sidecar.py`。本轮只定位，未写。未碰 `apps/panel/src/react/lib/sidecar.ts`。

## 本轮已改

- `tests/test_hermes_constants.py:617-619`：发行夹具目录改为 `clawd-0.0.0.dist-info`，METADATA `Name: clawd`。`project_venv_dir` 只认 `distributions(name="clawd")`（`hermes_constants.py:1531`），只改展示名或只改目录名都会让树外 venv 查找失败。
- `website/sidebars.ts:27`：可见分类标签 `Using Hermes` → `Using clawd`。与 `website/scripts/generate-llms-txt.py` 已有的 `Using clawd` 对齐。未改折叠、间距或文档 id。
- `tools/file_tools_write_guards.py:173`：用户可见提示不再指向生产 `~/.hermes/config.yaml`，也不再建议 `hermes config`。上一行已经给出被拒绝的实际路径；建议命令改为真实入口 `clawd config`。路径比对、函数名、`.hermes` 组件门禁未改。

## 本轮保留及原因

- 同一测试的检出目录名 `tmp_path / "hermes-agent"`（624、636、638、648）是 `direct_url.json` 比对的源码树路径，不是发行名。未改。
- `website/sidebars.ts` 里的文档 id 未改：`autonomous-ai-agents-hermes-agent`、`devops-hermes-s6-container-supervision`、`software-development-hermes-agent-skill-authoring`、`software-development-inspecting-hermes-desktop-dom`，以及 `guides/run-hermes-with-nous-portal` 等。这些是现存文档路径，改标签不改 id。
- `website/src/css/custom.css` 的 `.hero--hermes` 不在本轮可写名单，且是布局类名。不改。
- `file_tools_write_guards.py` 的 `hermes_config`、`get_hermes_home()`、注释里的 `~/.hermes`、项目树 `.hermes` 匹配未改。兼容脚本 `hermes` 仍在，本文件不再把它写成用户应走的入口。
- `build/lib/tools/file_tools_write_guards.py:173` 和 `build/lib/hermes_constants.py:1531` 仍是旧树副本。未改、未删。源码已改不能证明该树已更新。
- `hermes_cli/_launchers.py:577-578` 仍按名字 `hermes-agent` 发布到 `.hermes/bin`。这是兼容启动器，不是展示句。未改。

## 侧车：只定位

真实文件：`/root/hermes-workspace/clawd/apps/panel/sidecar/hermes_panel_sidecar.py`。

可见第一方旧文案仍在，下批若独占该文件只改展示，不改服务 id、`HERMES_BIN`、`_hermes()`：

| 行 | 现文案 | 下批 |
| --- | --- | --- |
| 63 | 展示「Hermes 网关」；id `hermes-gateway` | 只改展示为 clawd 网关 |
| 473 | `hermes {' '.join(args[:2])} 超时` | 只改超时前缀。调用是 config/mcp/plugins，不是 update |
| 515 | Hermes 不认识键，且撤回失败 | 展示名 |
| 516 | Hermes 不认识配置项 | 展示名 |
| 823 | 可能是 Hermes 自己改的 | 展示名 |
| 2833 | Hermes 返回的配置不是 JSON | 展示名 |
| 3279 | Hermes 返回的插件列表不是 JSON | 展示名 |
| 5047 | Hermes 环境不可用或超时 | 展示名 |
| 5051 | Hermes 版本可能变了 | 展示名 |
| 5242 | 读不到 Hermes 安装目录的版本信息 | 展示名 |

`5354` 的 `hermes update --plan 超时` 保留。调用仍是 `[HERMES_BIN, "update", "--plan"]`（5347–5354）。`clawd update` 被拒绝，不能只改字符串。

续跑显示：核心 `gateway/clawd_continuation_decision.py:18` 仍是 `MAX_STREAK = 5`，334–335 仍 `block/streak_limit`。这不是已落地的新规则。侧车 `AUTO_CONTINUE_LIMIT = 5`（4116）、`continuation_trigger`（4157–4182）、`main()` 6175 仍会起 `auto-continue` 线程。活路径不进入这条线程：`clawd_server/bind.py:135-143` 把 watcher/tick 换成抛错，并拒绝 `sidecar.main()`；`clawd_server/server.py:256` 固定 `legacyWatcher=disabled`。本轮不改侧车常数，不复活旧 watcher，也不把面板提示改成「限制已取消」。`CONTINUE_MAX_ATTEMPTS = 5`（4111）是 HTTP 失败重试，不是成功次数。

## 已改未验

下列是既有收口声称已写、本轮只抽查到新字面，没有运行验收。不是通过记录。

- 发行查找与 extra：`hermes_constants.py:1531` `distributions(name="clawd")`；`agent/transports/codex_app_server_session.py:847`、`plugins/platforms/qqbot/utils.py:16` `version("clawd")`；`hermes_cli/main_agent_cmds.py:179`、`agent/monitoring/otlp_exporter.py:277`、`gateway_health_export.py:292` 为 `clawd[otlp]`。`build/lib/` 同路径仍是旧名。
- CLI 入口抽查：`hermes_cli/_parser.py:435` `prog="clawd" if clawd else "hermes"`。`hermes` 分支是兼容脚本，保留。
- 插件文案抽查：`plugins/AGENTS.md`、`plugins/google_meet/README.md`、`plugins/security-guidance/README.md` 已写 `clawd plugins`。
- locales 抽查：`locales/en.yaml:879` 关机通知已是 clawd。33 个 yaml 的键/占位符对照见 `rebrand-locales-closeout.md`，本轮未重跑。
- 网站文档抽查：安装、更新、Desktop、Web Dashboard、Windows、Desktop Plugin SDK 页首有「不是本检出 / 不是 clawd 产品」边界，正文保留上游 `hermes` 命令。这是保留，不是漏改。

## 真正遗漏（下批独占，本轮不可写）

### 已收口树里的漏网

- `skills/autonomous-ai-agents/hermes-agent/references/configuration.md:58`：仍是 `` `hermes plugins install <name>` ``。同树其他抽查已是 `clawd plugins`。只改这一处命令，不改技能名 `hermes-agent`。

### 侧车

见上表。文件不在本轮可写路径。

### `plugin-catalog/`

889 个清单文件、4034 次是旧扫描次数，不是缺陷张数。本轮按文件头分类，未改。

社区或外部仓库条目 948 个：第三方真名、repo、SHA、`requires_hermes`、OAuth、许可证。不列路径，不一概替换。

Nous 维护的 26 个上游目录条目（`maintainer: NousResearch` 或 repo 在 `github.com/NousResearch/`）描述的是外部插件仓库，不是本检出产品 chrome。`name`、repo、`requires_hermes`、Desktop 事实不改。其中 7 个含命令拼写，下批必须逐条判断，不能整文件替换：

- `plugin-catalog/advanced-delegation.yaml:10` `` `hermes delegation config` ``
- `plugin-catalog/agent-importers.yaml:5,11,13`：`` `hermes import-agent` ``、`` `hermes claw migrate` ``、`` `hermes claw cleanup` ``、`` `hermes setup` ``。文件写明插件保留 `hermes claw` 拼写。
- `plugin-catalog/bubblewrap.yaml:9`、`singularity.yaml:7`：`` `hermes config set terminal.backend ...` ``。若确认走当前入口，可改为 `clawd config set`；不要改后端 id。
- `plugin-catalog/mcp-serve.yaml:5,9`：`` `hermes mcp-serve` `` / `` `hermes mcp serve` `` 是 MCP 客户端参数，先核对插件自身拼写。
- `plugin-catalog/spotify.yaml:6`：`` `hermes spotify login|status|logout` `` 是插件子命令。
- `plugin-catalog/github-copilot.yaml:5` 只是「for Hermes」产品句，没有当前入口命令。

本检出目录说明是第一方可见遗漏，下批只改命令示例：

- `plugin-catalog/README.md:1,3,5,6,25,43,60,73,123,131` 的 `hermes plugins catalog/search/install/update/validate`。同文件的 `hermes-agent` 仓库名、`@hermes/plugin-sdk`、`window.hermesDesktop`、Desktop SDK 限制保留。

### `optional-skills/`

190 个文件、833 次同样不是缺陷张数。作者行 `Hermes Agent`、OAuth `client_name`、`hermes dashboard` / `hermes update`、Desktop 专页、上游 Docker/s6 用户、模型名 Hermes 3/4 不改。

下批可改的第一方当前入口命令（作者含 Hermes Agent 或 Nous Research，且不是被拒绝的 update/dashboard/desktop）：

- `optional-skills/autonomous-ai-agents/honcho/SKILL.md:35,41,48,57,132,214,227,401,404,422-437`；标题 3、17
- `optional-skills/autonomous-ai-agents/agent-merge-conflict-arbiter/SKILL.md:37,79`
- `optional-skills/devops/actual-setup/SKILL.md:59,60,64,82,83,87,139,141,143`；描述 3
- `optional-skills/payments/stripe-link-cli/SKILL.md:164,167`。72 行 `--client-name "Hermes"` 是 OAuth 客户端名，保留
- `optional-skills/productivity/telephony/SKILL.md:143,144`
- `optional-skills/productivity/property-listings/SKILL.md:37`。17 行 desktop transcript 不改成 clawd Desktop
- `optional-skills/creative/unreal-mcp/SKILL.md:64,116`
- `optional-skills/creative/anatomy/SKILL.md:41`（`hermes skills update`，不是顶层 `hermes update`）
- `optional-skills/creative/ip-as-logo/SKILL.md:50,69`
- `optional-skills/finance/excel-author/SKILL.md:38`；`pptx-author/SKILL.md:39`。同行「Hermes Docker image」是上游镜像，保留
- `optional-skills/software-development/hermes-agent-skill-authoring/SKILL.md:35`；标题 14。84–86、190 的作者约定 `Hermes Agent` 保留
- `optional-skills/software-development/node-inspect-debugger/SKILL.md:202,208`
- `optional-skills/software-development/python-debugpy/SKILL.md:32,299,309`

明确不改成 clawd 产品：

- `optional-skills/software-development/inspecting-hermes-desktop-dom/SKILL.md:3,14`：上游 Desktop，改了会冒称 clawd Desktop
- `optional-skills/mcp/mcp-oauth-remote-gateway/SKILL.md:81,82,184`：`hermes dashboard` 与 OAuth `client_name`。标题 14 是产品句，可另判，不要动 schema
- `optional-skills/devops/hermes-s6-container-supervision/SKILL.md:52,70,85,127`：镜像内系统用户和实际拦截命令。76、170 若改，必须先证明镜像入口已是 clawd，否则会写错运行事实

其余第一方技能正文里的「Hermes tools / in Hermes」与 `skills/` 收口是同一类，但不是命令契约。下批按 `rebrand-plugin-content-closeout.md` 的规则改可见句，不要按全文命中替换。社区作者、无 Nous/Hermes Agent 署名的 optional skill 不在这份第一方清单里。

## 未验证

未运行 `scripts/run_tests.sh`、文档站构建、类型检查、导入或 `clawd`/`hermes` 命令。夹具与侧栏标签只做了静态回读。`build/lib/` 旧副本未重建。
