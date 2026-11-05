# 表面品牌收口

静态字面收口。未跑测试、构建、py_compile、导入或探针。未 commit / push / deploy。未改 chat-page、sidecar.ts、run_wait、project_run_wait。未把官方 Desktop 写成 clawd Desktop。未把 `hermes update` 改成 `clawd update`。

`src/react/shell/live-dock.tsx` 不存在。同名文件在 `apps/panel/src/react/docks/live-dock.tsx`，只改三处可见字面，未改样式或 DOM。

## 侧车（本路零改动，交新 owner）

中途交 `apps/panel/sidecar/hermes_panel_sidecar.py` 给续跑限制专属 owner。本路没有写入，也没有回滚。工作树里该文件已有非本路脏改（约 815 增 / 15 删），不查看续跑逻辑、不续改。

下列展示行在交接口径时仍是旧文案，行号仍对得上清单：

| 行 | 现文案 | 建议（本路未做） |
| --- | --- | --- |
| 63 | `("hermes-gateway", "Hermes 网关")` | 只改展示「clawd 网关」。服务 id `hermes-gateway` 保留 |
| 473 | `f"hermes {' '.join(args[:2])} 超时"` | 只改超时展示前缀为 clawd。不改 `HERMES_BIN`、不改 `_hermes()` 调用。现有调用是 config/mcp/plugins/profile/webhook，不是 update |
| 515 | `Hermes 不认识 {key}，且自动撤回失败…` | 展示名改 clawd |
| 516 | `Hermes 不认识配置项 {key}，未写入` | 展示名改 clawd |
| 823 | `可能是 Hermes 自己改的` | 展示名改 clawd |
| 2833 | `Hermes 返回的配置不是 JSON` | 展示名改 clawd |
| 3279 | `Hermes 返回的插件列表不是 JSON` | 展示名改 clawd |
| 5047 | `Hermes 环境不可用或超时` | 展示名改 clawd |
| 5051 | `Hermes 版本可能变了` | 展示名改 clawd |
| 5242 | `读不到 Hermes 安装目录的版本信息` | 展示名改 clawd |
| 5354 | `hermes update --plan 超时` | **保留**。调用仍是 `[HERMES_BIN, "update", "--plan"]`。`clawd update` 被拒绝。不要只改字符串。升级管理未完成，不在本路发明 |

其余侧车命中（兼容 id、`HERMES_*`、生产路径拒绝、版本正则、开发注释）按清单保留，本路未动。

## Web 字面（已改，无样式/DOM 变更）

- `composer.tsx` 243–244：占位符 Hermes → clawd
- `pickers.tsx` 388–389：产品名 clawd；命令示例 `clawd config set`（不是 update）
- `status-page.tsx` 201：标题 clawd，版本绑定未改
- `files-page.tsx` 216、302：说明与 title
- `docks/live-dock.tsx` 206、1187、1193：三处可见句
- `tests/live-confirm-hit-fixture.html` 41：仅标题品牌，与对话框字面一致。未跑

这六个文件内已无 hermes/Hermes。面板测试目录里只有该 fixture 含旧标题，已改。其他测试断言未扩改、未跑。

## 工具提示 55 条

保留类 134 条未改。原因沿用 JSON：内部名/环境变量 80、开发注释 22、路径 12、对外协议身份 10、上游 URL 4、Desktop 来源注释 3、技能 author 2、git 作者 1。

### 已改字面（不改调用、授权、稳定键）

命令示例改为 clawd，兼容脚本 `hermes` 未删：`tools`、`model`、`chat -q`、`skills publish`、`pm install`、`curator unpin`、`vault add`、`auth`。`metadata.hermes.blueprint` 保留。

- `transcription_local.py` 111–113：进程用户/安装属主 clawd；`` `clawd tools` ``
- `mcp_tool_node_abi.py` 66
- `file_tools_write_guards.py` 171（只这一行）
- `session_search_tool.py` 667、784
- `approval.py` 964；`terminal_tool_sudo.py` 96、443；`write_approval.py` 226；`approval_context.py` 217：`` `clawd chat -q` ``。同行 `HERMES_INTERACTIVE` 等未改
- `mcp_tool_costs.py` 6；`mcp_oauth_provider.py` 166；`mcp_tool_sampling.py` 255；`terminal_tool.py` 115
- `video_generation_tool.py` 252；`xai_video_tools.py` 102；`image_generation_tool.py` 445、725；`browser_use_cli.py` 1454：`` `clawd tools` ``
- `lazy_deps.py` 11：relaunch clawd。未写成 clawd update
- `browser_use_cli.py` 1441：描述 clawd-managed
- `mcp_tool_config.py` 293：展示名 + `` `clawd pm install` ``。`with_hermes_node_path` 未改
- `process_registry_notifications.py` 460：回退标签 clawd。事件字段未改
- `blueprints.py` 170：两条清单同一行，只改 `` `clawd skills publish` ``
- `browser_vault_tool.py` 470、797：产品名；503：`` `clawd vault add` ``，Desktop 设置标明上游 Hermes desktop、不是 clawd
- `browser_url_policy.py` 122：docstring 产品名。策略函数未改
- `code_execution_tool.py` 912；`kanban_tools_schemas.py` 355；`voice_live.py` 16、20
- `transcription_cloud.py` 269：`` `clawd model` ``
- `cronjob_tools.py` 1125、1141：`` `clawd model` ``。`/model` 未改
- `skill_manager_guards.py` 151、159
- `computer_use/cua_backend_driver.py` 147
- `connectors/catalog_tool.py` 39；`connectors/catalog.py` 184；`connectors/mcp.py` 122

官方 Desktop，未写成 clawd Desktop：

- `read_preview_tool.py` 20：not available in clawd (upstream Hermes desktop only)
- `tour_tool.py` 97：surface 标明上游 Hermes desktop, not part of clawd
- `set_theme_tool.py` 61：同上
- `read_window_tool.py` 16、18、29：同上

### 保留（有原因，未改文件）

- `browser_launcher_holders.py` 210、`browser_launcher.py` 1124：执行路径 logger，不是工具描述/提示，不改授权与执行代码
- `code_execution_env.py` 190：执行日志；下一行 hermes root 是环境路径，不是提示
- `mcp_oauth.py` 858：错误描述的是仍以 Hermes 身份发出的 Client ID Metadata Document。同文件 `client_name` 默认仍是 `Hermes Agent`（协议身份，不盲替换）
- `skills_hub_official.py` 283：上游目录真名 Hermes Skills Index。`SOURCE_ID=hermes-index`、类名、`hermes-index.json` 是稳定 id，不改

### 相邻未扩（不在清单行）

- `file_tools_write_guards.py` 173：`~/.hermes/config.yaml` 必须保留。同行 `'hermes config'` 是命令示例，未扩改
- `read_preview_tool.py` 24–25 仍提到 desktop app / update it。未改成 clawd update
- `browser_vault_tool.py` 471 仍写 “the Desktop app”，未写成 clawd Desktop
- `voice_live.py` 1、5、10 与 `read_window_tool.py` 2 的模块说明不在清单行，未扩

## 未验证

无测试、构建、类型检查、导入。扫描计数不是零残留。侧车展示与升级文案未由本路完成。
