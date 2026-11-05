# 文档站品牌收口

静态改稿。未重跑全盘扫描，未改 `rebrand-inventory.json` 与其它 `rebrand-*.md` 快照，未改 `docs/clawd/core-audit`、`source-map`、`handoff`。未跑测试、构建、`py_compile`、导入、服务、浏览器或网络探针，未 commit / push / deploy。29241 是扫描命中次数，不是缺陷张数，本文件也不把它当成全量证明。

## 范围

allowlist 来自 `docs/clawd/rebrand-inventory.json` 的 `must_change_files`，且路径以 `website/` 开头：722 个现存文件。`docs/` 下必须改第一方品牌为 0 行，除本收口外未改。根 `README` / `CONTRIBUTING` / `SECURITY` 不在本路。

## 已改

- 网站文档约 718 个 allowlist 文件：当前产品展示名改为 clawd；可用命令示例改为 `clawd`（如 `clawd setup`、`clawd chat`、`clawd panel`）。数据根写成 `CLAWD_HOME` 或 `~/.clawd`。
- 根 `AGENTS.md`：产品名与当前用法已改，去掉官方 Desktop 表述。字符 11577，字节 11735，低于自述 12k。许可证句未动。`HERMES_HOME`、`get_hermes_home()`、`hermes_cli/` 等代码名保留。`hermes update` 路由行保留，因为产品入口拒绝该命令。
- 根 `SOUL.md`：`You are clawd, built by Nous Research.` 只改产品名，保留 Nous Research。

## 保留及理由

- `website/src/data/userStories.json`：引述、标题、作者、URL、id 未改。页面与拼贴已标明这些是上游 Hermes Agent 案例，不是 clawd 用户。
- `website/static/oauth/client-metadata.json`：已注册的 OAuth `client_id` / `client_uri` / `client_name`，不造新客户端。
- 安装、更新、Termux、Desktop、官方 Web Dashboard、Windows 安装页：正文保留上游 Hermes 事实，页首标明不是本检出。`install.sh` / `install.ps1` 仍是上游 Hermes Agent 安装器，不是 clawd 安装器。未新造官网、仓库或作者。
- `hermes update` / `desktop` / `dashboard` / `gui` / `webapp` 未改成 clawd 命令。`clawd` 拒绝这些词。
- `HERMES_*`、`hermes-agent` 兼容脚本、`hermes://`、`hermes-skill-pick`、技能文件名、模型名 Hermes/Nomos/Psyche、唤醒词 `Hey Hermes` 保留。`~/.hermes/hermes-agent` 是上游安装器检出路径，不是产品数据根。
- 版权与 MIT 署名未改。既有外链只改了可见标签（Upstream / Upstream Desktop），没有新增 URL。布局 CSS 未改。

## 未改阻塞

- `website/sidebars.ts` 不在必须改清单里，侧栏仍是 `Using Hermes`。未越出 allowlist。
- `website/src/css/custom.css` 的 `.hero--hermes` 不在清单里，且是布局类名，未改。
- 中文 Desktop 页不存在，无从加边界。
- 未运行文档站构建，不能证明侧栏、搜索索引或生成页已跟上。`scripts/check` 的 12k 上限只做了字符计数，没有跑脚本。
