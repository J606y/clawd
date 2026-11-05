# locales 与会话提示品牌收口

静态文本对照。未导入产品，未跑测试、构建、`py_compile`、CLI/TUI 探针，未提交、推送或部署。未改 `hermes_constants.py`，未改 CLI 逻辑、schema 键或其他 i18n。未重扫全树。

## allowlist

来源：`docs/clawd/rebrand-inventory.json` 的 `must_change_files`，筛 `owner=cli_tui` 且路径在 `locales/`、后缀 `.yaml`。代码计数 **33**，全部存在。没有 `locales/en.tui.yaml`（清单也没有）。其余 105 个 cli_tui 路径不在本路。

另按任务改 `hermes_state_user_copy.py` 的用户可见产品名和命令，不在上述 33 个文件里。

## 对照

对这 33 个文件用纯文本解析对照 `HEAD`：YAML 键列表、`{...}` / `%` 占位、`\n` 等转义、行数都相同。产品名命中从 **7162** 降到 **1407**，差值 **5755**。这是 token 次数，不是缺陷张数。

| 文件 | 原次数 | 保留 | 已改 |
| --- | ---: | ---: | ---: |
| locales/af.tui.yaml | 55 | 12 | 43 |
| locales/af.yaml | 369 | 65 | 304 |
| locales/ar.tui.yaml | 55 | 14 | 41 |
| locales/ar.yaml | 369 | 69 | 300 |
| locales/de.tui.yaml | 55 | 14 | 41 |
| locales/de.yaml | 370 | 70 | 300 |
| locales/en.yaml | 373 | 74 | 299 |
| locales/es.tui.yaml | 55 | 14 | 41 |
| locales/es.yaml | 369 | 70 | 299 |
| locales/fr.tui.yaml | 55 | 14 | 41 |
| locales/fr.yaml | 369 | 70 | 299 |
| locales/ga.tui.yaml | 55 | 14 | 41 |
| locales/ga.yaml | 369 | 69 | 300 |
| locales/hu.tui.yaml | 55 | 15 | 40 |
| locales/hu.yaml | 369 | 69 | 300 |
| locales/it.tui.yaml | 55 | 14 | 41 |
| locales/it.yaml | 370 | 70 | 300 |
| locales/ja.tui.yaml | 55 | 14 | 41 |
| locales/ja.yaml | 371 | 72 | 299 |
| locales/ko.tui.yaml | 55 | 12 | 43 |
| locales/ko.yaml | 369 | 67 | 302 |
| locales/pt.tui.yaml | 55 | 14 | 41 |
| locales/pt.yaml | 369 | 69 | 300 |
| locales/ru.tui.yaml | 55 | 14 | 41 |
| locales/ru.yaml | 370 | 70 | 300 |
| locales/tr.tui.yaml | 55 | 14 | 41 |
| locales/tr.yaml | 369 | 70 | 299 |
| locales/uk.tui.yaml | 55 | 14 | 41 |
| locales/uk.yaml | 369 | 70 | 299 |
| locales/zh-hant.tui.yaml | 55 | 14 | 41 |
| locales/zh-hant.yaml | 369 | 71 | 298 |
| locales/zh.tui.yaml | 55 | 14 | 41 |
| locales/zh.yaml | 369 | 71 | 298 |

已改的是用户可见产品名（`Hermes` / `Hermes Agent` → `clawd`）、真实入口命令（`hermes doctor` 等 → `clawd`），以及确认过的产品默认目录 `~/.hermes/<数据子目录>` → `~/.clawd/...`。依据是 `resolve_clawd_home`：`CLAWD_HOME` 或 `~/.clawd`，不读 `HERMES_HOME`。法/意省音 7 处写成 `de/que/le/del clawd`，避免 `d'clawd`。英文抽查：关机/重启已是 clawd，`~/.clawd/skills/` 已出现。

## 保留依据

- 唤醒词 `Hey Hermes` / `hey hermes`：`tools/wake_word.py` 默认短语和模型别名 `hey_hermes`，检测不看文案。不能改成 Hey clawd。
- `HERMES_*` 环境变量名：兼容 id，不是展示名。
- YAML 键 `hermes`、`hermes_cmd_not_found`、`hermesTag`、`runHermesModelToConfigure`：schema/协议键。`/hermes` 是 Slack 协议命令（`commands_platforms.py`），键不改，描述里的产品名已改。
- `hermes update` / `hermes dashboard` / desktop / gui / webapp，以及 `update:` 段里的更新文案：`clawd_cli/main.py` 在进解析器前拒绝这些命令。改成 `clawd update` 会把官方更新说成本产品。韩语助词贴在命令后，词边界没拦住，已把 `ko.yaml` 的 t079/t315/t316 改回 `hermes update` / `hermes dashboard` 和 `Hermes TUI`。
- 门户页名 `Hermes Agent page` 及各语对应说法，含德 `Hermes-Agent-Seite`、乌克兰 `сторінці Hermes Agent`。这是上游页面，不是产品名。乌克兰语曾被误改，已改回。
- `Hermes team` 及各语团队称呼：上游支持方，不新造 clawd 团队。
- `Nous Research Hermes 3/4`：真实模型名。
- `hermes-tui`、`hermes_tools`、`hermes_cli`、`hermes-agent` 等：包名或模块名。
- `.hermes.md`、`HERMES.md`、`.hermes/plans/`、`./.hermes/plugins/`：代码仍加载的协议路径。
- `~/.hermes/dashboard-themes/`、`~/.hermes/dashboard-plugins/`：官方 dashboard 路径。兼容脚本 `hermes` 不走隔离，不能改成 `~/.clawd`。
- 官方桌面专名（`Hermes-Desktop-App`、`app desktop di Hermes`、`Hermes 桌面应用` 等）：不改成 clawd Desktop。`deleted_wal` 里的 “Desktop app / dashboard” 是官方表面名词，产品进程名已改 clawd。
- YAML 注释不是用户可见文本。

`hermes_state_user_copy.py`：只改用户可见句。命令改为 `` `clawd {profile_arg}...` ``（10 处），产品进程名改为 clawd。保留 `Desktop app`、`dashboard`、`{profile_arg}`、`storage_*` 机器码、`STORAGE_RECOVERY_DOCS_URL`，以及 `hermes_state_errors` / `hermes_constants` 导入。文件里还剩 2 处 hermes，都是导入。

## 未验证

未启动 CLI/TUI，未跑测试或构建，未核对 Windows `%LOCALAPPDATA%\hermes`。匈牙利/土耳其语后缀只做词干替换（如 `Hermest` → `clawdt`），没有改语素和谐。分类桶合计与残留次数差 16，以本表 git 文本对照为准。扫描计数不是零残留证明。本文件不是验收通过记录。
