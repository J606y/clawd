<p align="center">
  <img src="assets/banner.png" alt="clawd" width="100%">
</p>

# clawd

本检出的产品名是 **clawd**。不是 clawed，也不是上游产品 Hermes Agent。

clawd 派生自 [Nous Research](https://nousresearch.com) 的 [Hermes Agent](https://github.com/NousResearch/hermes-agent)。[`docs/clawd/source-map.md`](docs/clawd/source-map.md) 记录的来源钉是 `c314285f077e91937c995d419b9fa40af9eb4981`。本文件不改写版权或许可证。`LICENSE` 仍是 MIT，`Copyright (c) 2025 Nous Research`。

`assets/banner.png` 仍是上游图，本路没有重做。本检出不声明 clawd 官网、安装脚本主机或问题跟踪器。下文凡指向 `hermes-agent.nousresearch.com` 或 `github.com/NousResearch/hermes-agent` 的链接，都是上游 Hermes Agent 事实。

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Upstream%20docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="上游 Hermes Agent 文档"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-Nous%20Research-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Nous Research Discord"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge" alt="English"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

## 实际入口

| 事实 | 值 |
| --- | --- |
| 产品命令 | `clawd` |
| 检出启动器 | `./clawd`（不替换系统里的 `hermes` 命令） |
| 控制台脚本 | `pyproject.toml` 中 `clawd = "clawd_cli.main:main"` |
| 本检出的 Web 界面 | `clawd panel` |
| 数据根 | `CLAWD_HOME`；未设置时为 `~/.clawd` |

共享 CLI 仍注册了名为 `hermes` 的兼容脚本。本文示例只用 `clawd`。该兼容脚本是否保留，归包装元数据路。

`clawd_cli/main.py` 拒绝 `update`、`desktop`、`dashboard`、`gui`、`webapp`。这些路径会恢复官方界面，或覆盖已有的 Hermes Agent 安装。官方 Desktop 和官方 Web 仪表盘不是 clawd 的产品。写本文时没有执行这些命令。

启动器会先调用 `isolate_inherited_hermes_env()`。默认只认 `CLAWD_HOME`。继承来的、位于 clawd 树外的 `HERMES_HOME` 会被改写到该根，不会回落到生产目录 `~/.hermes`。映射之后，内部代码仍可能读取 `HERMES_HOME` 这个名字。那是兼容标识，不是产品路径。本文件不迁移已有数据或密钥。

上游 Windows 安装文档中的 `%LOCALAPPDATA%\hermes` 和 `~/.hermes` 是 Hermes Agent 路径。本检出的代码默认是 `Path.home() / .clawd`。这里没有执行 Windows 分支。

## 安装

不要用上游一行安装命令来安装 clawd。那条脚本安装的是 Hermes Agent：

```bash
# 上游 Hermes Agent 安装器，不是本 clawd 检出。
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

`https://hermes-agent.nousresearch.com/install.ps1` 同样是上游安装器。本检出没有另一条 clawd 安装 URL。

在本检出中：

```bash
./clawd
```

若日后的包装安装注册了控制台脚本，产品命令是 `clawd`。本文不声称已经打出 wheel。

## 使用

除上面拒绝的子命令外，这些写法会分发到共享 CLI。写本文时没有运行。

```bash
clawd                 # 终端界面
clawd model           # 选择提供商和模型
clawd tools           # 配置工具
clawd config set      # 设置单项配置
clawd config get      # 打印单项配置
clawd gateway         # 消息网关
clawd setup           # 设置向导
clawd doctor          # 诊断
clawd panel           # 绑定的 Web 界面
```

`clawd update` 被拒绝。上游 Hermes Agent 文档里的 `hermes update` 不是 clawd 的更新器。

`clawd setup --portal` 是共享设置命令的产品拼写。Nous Portal（`https://portal.nousresearch.com`）是 Nous Research 的服务，不是 clawd 站点。Tool Gateway 说明仍在上游页面：<https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway>。

## 继承来的行为

下表来自 Hermes Agent，本文件编写时没有用构建或运行复核。仅 Desktop 的设置不是 clawd 的界面。

| 方面 | 继承行为 |
| --- | --- |
| 终端 | TUI：多行编辑、斜杠补全、历史、中断、流式工具输出 |
| 消息 | 网关进程，覆盖 Telegram、Discord、Slack、WhatsApp、Signal 等 |
| 学习闭环 | 技能、记忆提醒、会话搜索。[Honcho](https://github.com/plastic-labs/honcho) 与 [agentskills.io](https://agentskills.io) 是第三方名称 |
| 定时 | 内置 cron，可投递到平台 |
| 委派 | 隔离子代理；Python 工具 RPC |
| 终端后端 | 上游文档列出 local、Docker、SSH、Modal、Daytona、Vercel Sandbox，以及 Singularity 等插件 |

## CLI 与消息

用 `clawd` 启动终端界面，或启动网关后从已连接的平台发消息。斜杠命令共用。上游指南按 Hermes Agent 书写；产品命令换成 `clawd`，不要把被拒绝的子命令也换进去。

| 操作 | CLI | 消息平台 |
| --- | --- | --- |
| 开始 | `clawd` | `clawd gateway setup`，然后 `clawd gateway start` |
| 新对话 | `/new` 或 `/reset` | `/new` 或 `/reset` |
| 模型 | `/model [provider:model]` | `/model [provider:model]` |
| 人格 | `/personality [name]` | `/personality [name]` |
| 重试 / 撤销 | `/retry`、`/undo` | `/retry`、`/undo` |
| 上下文 / 用量 | `/compress`、`/usage` | `/compress`、`/usage` |
| 技能 | `/skills` | `/skills` |
| 中断 | `Ctrl+C` | `/stop` |

上游命令参考：<https://hermes-agent.nousresearch.com/docs/reference/cli-commands>。上游环境变量参考：<https://hermes-agent.nousresearch.com/docs/reference/environment-variables>。那些页面仍使用 Hermes Agent 和 `HERMES_*`。本检出的产品数据根是 `CLAWD_HOME`。

## 迁移示例

这是共享 CLI 的拼写，不是已经跑过的迁移：

```bash
clawd plugins install agent-importers
clawd import-agent openclaw
clawd import-agent openclaw --dry-run
clawd claw migrate
```

上游文档把技能导入写到 `~/.hermes/skills/openclaw-imports/`。在 clawd 中，隔离后的进程数据根是 `CLAWD_HOME` 或 `~/.clawd`。本文不声称已复核导入器的具体子目录，也不把 clawd 指向生产 Hermes 数据。OpenClaw、Claude Code、Codex、Cursor 是其他产品的名称。

## 文档

<https://hermes-agent.nousresearch.com/docs/> 是上游 Hermes Agent 文档，不是 clawd 文档站。上游仓库的贡献流程仍写在 [CONTRIBUTING.md](CONTRIBUTING.md)。这里不声明 clawd 的 GitHub 仓库。

## 社区

- [Nous Research Discord](https://discord.gg/NousResearch) — 他们的服务器，不是 clawd 的服务器
- [agentskills.io](https://agentskills.io)
- [上游问题跟踪](https://github.com/NousResearch/hermes-agent/issues) — Hermes Agent 的跟踪器，不是 clawd 的跟踪器
- [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — 第三方 MCP 服务
- [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — 第三方微信桥。这个名字属于该项目，不是本产品

## 许可证

MIT — 见 [LICENSE](LICENSE)。

Copyright (c) 2025 Nous Research。上游 Hermes Agent 由 Nous Research 构建。本检出的显示名是 clawd。
