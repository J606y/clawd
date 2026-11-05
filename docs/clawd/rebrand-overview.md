# clawd 品牌残留清单

本文件是一次性收口交接，不是再扫指令。产品文件未改。未跑测试、构建、导入或命令探针。

## 已改（只静态核对）

根入口已改成产品名 clawd，安装和使用示例用 `./clawd` / `clawd`。上游一行安装器仍标明是 Hermes Agent，不是本检出。未新造官网、仓库或安全联系方式。`LICENSE` 未改：MIT，Copyright (c) 2025 Nous Research。

- `README.md`、`README.zh-CN.md`、`README.es.md`、`README.ur-pk.md`
- `CONTRIBUTING.md`、`CONTRIBUTING.es.md`、`SECURITY.md`、`SECURITY.es.md` 只加派生说明，正文仍是上游流程
- `README.ur-pk.md` 第 34、95 行在扫描后改成兼容/上游表述。扫描计数仍含这两处，未重扫

`AGENTS.md` 自述 12k 上限，未改，未跑 `scripts/check`。`SOUL.md` 未改。

## 四类计数

来源：`docs/clawd/rebrand-inventory.json` 的 `counts`。去重键 `category|path|line|kind|start|end`。脚本重算与扫描一致。这是命中次数，不是缺陷张数。未扫描树不声称全无残留。

| 类 | 去重次数 |
| --- | ---: |
| 必须改第一方品牌 | 29241 |
| 需兼容稳定 id | 99106 |
| 第三方真实名 | 8 |
| 来源许可证历史 | 2255 |

扫描 15346 个文本文件。排除 `.git`、`.venv`、`node_modules`、`artifacts`、`build`、`dist`、日志、锁文件。拼写防护「不是 clawed」8 处不计入。

必须改按 owner：web 16593，cli_tui 6115，plugins 4258，skills_content 1468，unassigned 310，packaging 271，tests 132，runtime 83，docs 9，events 1，attachments 1。browser/memory 的展示名多数落在 tools 或 web 路径，见收口节。

`hermes_cli` 在兼容类，28013 次，不是第三方。`HERMES_HOME` 8843 次，兼容环境名。`@hermes/ink`、`@hermes/shared`、`hermes-tui`、`hermes-panel`、`hermes-agent` 是内部包或发行名，不盲改。发行名与作者 Nous Research、MIT 是上游归属。

第三方 8 次都是 README 里的 HermesClaw / hermesclaw，社区项目真名，保留。

## 结构

- 命令：检出启动器 `./clawd`；`pyproject.toml` 脚本 `clawd = clawd_cli.main:main`，另有兼容脚本 `hermes`。拒绝 `update`、`desktop`、`dashboard`、`gui`、`webapp`。`clawd panel` 进 `clawd_server`。
- 配置：产品根 `CLAWD_HOME` 或 `~/.clawd`。`isolate_inherited_hermes_env` 把树外 `HERMES_HOME` 映到该根，不回落生产 `~/.hermes`。Windows `%LOCALAPPDATA%\hermes` 未执行。
- 协议：`panel-api`、`panel-sidecar`、`panel-auth` 保留。`gateway/platforms/clawd_runtime_routes.py:237` 的 `hermes.session.lease` 是协议对象名，不改。
- 来源：上游 `github.com/NousResearch/hermes-agent`，钉 `c314285f077e91937c995d419b9fa40af9eb4981`（见 `docs/clawd/source-map.md`）。`package.json` 仓库 URL 仍是 `NousResearch/Hermes-Agent.git`。

## 收口：不要按全文命中改

### 已交，不重复派

`sa-1-da9b592e`：`gateway/session_ingress.py`、`gateway/session_settlement_recovery.py`、`gateway/relay/adapter.py`、`agent/estop.py`、`agent/shell_hooks.py`、`hermes_state.py`、`hermes_cli/web_routers/local_models.py`。

### 已由 runtime 声明完成，不碰

- `clawd_server/runtime_native.py` `run_wait` 第 218 行。同文件第 14 行生产路径拒绝清单是兼容 id。
- `gateway/platforms/clawd_runtime_routes.py` `project_run_wait` 第 122 行。第 237 行协议名保留。
- 未复跑，不把声明写成测试通过。

### 侧车 `apps/panel/sidecar/hermes_panel_sidecar.py`

未改。复核后：应更名 11，兼容 129，来源 45。全文命中不是缺陷。

应更名（owner web）：

- 63 只改展示标签「Hermes 网关」；服务 id `hermes-gateway` 保留
- 473、515、516、823、2833、3279、5047、5051、5242 用户可见错误
- 5354 超时文案。若调用仍是 `hermes update`，不能只改字符串；`clawd update` 被拒绝

保留：`HERMES_HOME`、`HERMES_BIN`、`/root/.hermes`、`/usr/local/bin/hermes`、`/etc/hermes-panel`、`hermes-dashboard` 历史单元、`hermes-panel-state`、生产路径拒绝清单、第 5213 行版本正则。内部函数 `_hermes` 不是第三方。

### 工具提示 `tools/**/*.py`

未改。复核后应更名 55（runtime 47，browser 8），兼容 104，来源 30。另有 2380 处实现标识未列入提示清单。

应更名的是描述、返回给模型/用户的句子，以及提示里的 `hermes tools` / `hermes model` 示例。`hermes` 控制台脚本不删。官方 Desktop 提示不要改成 clawd Desktop。`metadata.hermes.blueprint`、User-Agent、OAuth `client_name`、git 作者 `Hermes Checkpoint`、技能 author 约定，都不盲替换。

browser 路径上的 8 处交 browser，只报告。其余交 runtime，只报告。逐行在 JSON `closeout.tool_prompts.items`。

## 其他 owner

- web：网站文档和 `website/src/data/userStories.json` 是必须改的大头。按文件分组改展示和命令示例，不改协议前缀。
- cli_tui：`locales/*.yaml` 每种语言约 250–314 处。`hermes_cli` 与 `@hermes/*` 保留。
- packaging：发行名和兼容脚本不从本清单推导改名。
- docs：只剩 `AGENTS.md`。`SOUL.md:1` 被规则漏记，补为 runtime 应更名，未改文件。
- plugins、skills_content：用户可见文案交后续收口，本路未改。

## 未验证

无测试、构建、wheel、Windows 分支、导入器子目录、`scripts/check`、横幅重绘、runtime 品牌复跑。扫描计数不是零残留证明。
