# 镜像准入规则收口 — 2026-11-05

静态改稿与字节对照。未运行 `tests/website/test_catalog_rules_mirror.py`、`scripts/run_tests.sh`、文档站构建、类型检查、`py_compile`、导入、`clawd` / `hermes` 命令或网络。未 commit / push / deploy。未删文件。未读生产凭据。未改原面板、线上服务、`/usr/local/lib/hermes-agent`。未重扫全树。本文件不是通过记录，也不构成上线授权。

入口只读：`clawd_cli/main.py` 只拒绝顶层 `update` / `dashboard` / `desktop` / `gui` / `webapp`。`hermes_cli/subcommands/plugins.py` 注册 `plugins update` 与 `plugins validate`。`hermes_cli/subcommands/auth.py` 注册 `auth add`。因此块内这三类命令改为 `clawd`，没有把被拒绝的顶层 `update` 写成 `clawd update`。

## 已改

- `plugin-catalog/README.md` 的 `<!-- admission-rules:start -->` … `<!-- admission-rules:end -->` 内文：第一方产品名改为 clawd；`` `hermes plugins update` `` / `` `hermes plugins validate` `` / `` `hermes auth add` `` 改为已注册的 `` `clawd plugins update` `` / `` `clawd plugins validate` `` / `` `clawd auth add` ``。规则句子未重写。`Hermes's` 写成 `clawd's`，没有沿用错误的 `clawd'ss`。
- 同文件标记块外一行：`integrates into Hermes` 改为 `integrates into clawd`。上一轮留它，是为了不和未改的准入块各说各话；块已收口后这句再留 Hermes 会重新分叉。不是规则正文。
- `website/docs/developer-guide/plugins/catalog-submission.md` 标记块内文：开工时已是 clawd 命令，但所有格是 `clawd'ss`。只把这些所有格改成 `clawd's`，使两块内文逐字相同。标记块外 “Before you submit” 里同一处 `clawd'ss` 一并改掉。frontmatter、`## Admission rules`、仓库链接未动。

## 镜像一致证据

两块起止标记文本相同。标记之间的内文（含换行，不含标记行）逐字节相同。

- 内文字节：11671
- SHA-256：`a49552590a08315b32c0db29dc02fdf96dd540d9ed17d09a9a0dbc48a071d907`
- 空白折叠后也相同。`tests/website/test_catalog_rules_mirror.py` 比较的就是折叠后的内文；本次未运行该测试。

两边内文计数相同：`hermes-agent` 2，`@hermes/plugin-sdk` 1，`window.hermesDesktop` 1，`requires_hermes` 4，`clawd plugins update` 2，`clawd plugins validate` 4，`clawd auth add` 1。内文不再有 `Hermes`、`hermes plugins`、`hermes auth`、`clawd'ss`。

## 保留及理由

- 官方仓库名 `hermes-agent`，以及页外 `https://github.com/NousResearch/hermes-agent` 与 `https://hermes-agent.nousresearch.com/...`。未新造仓库或 issue URL。块内 “clawd issue or PR” 只是第一方产品措辞；页外 issue 链接仍指向 `NousResearch/hermes-agent`。
- SDK 与桥标识：`@hermes/plugin-sdk`、`window.hermesDesktop`。
- 字段名 `requires_hermes`。这是目录 schema，不是命令。
- OAuth 要求未改：`oauth:`、`authorize_url`、`token_url`、`hosts` 仅 https、第三方 API 客户端、不得自持或自刷新 token。只把登录命令从已注册的 `auth add` 换了产品入口。
- 作者与第三方要求未改：扫入条目的作者控制权、Nous 维护 `official`、密码管理器必须来自厂商、第三方仓库不得被路过提交。`clawd maintainers` 是第一方维护者角色，不是作者署名。
- 布局：网站页 frontmatter（`sidebar_label` / `title` / `description`）、标题 `#admission-rules` 的来源标题、标记注释、既有链接与 id 未改。

## honcho：只定位，未改

允许路径 `website/docs/optional-skills/autonomous-ai-agents-honcho.md` 不存在，`website/docs/optional-skills/` 目录也不存在。

实际页不在允许名单，未改：

- `website/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-honcho.md`（页首写明由 `SKILL.md` 生成）
- 同主题另有 `website/docs/user-guide/features/honcho.md`，以及 `website/i18n/zh-Hans/...` 下的对应副本。均未改。

冲突：上述生成页写 `clawd honcho setup|status|sync|map|...`。`optional-skills/autonomous-ai-agents/honcho/SKILL.md` 仍是 `hermes honcho` 这些子命令。`plugins/AGENTS.md` 写明核心已删除硬编码 honcho argparse。目录条目 `plugin-catalog/honcho.yaml` 指向外部仓库 `https://github.com/plastic-labs/honcho`，subdir `hermes-plugin-honcho`；本检出没有该插件源，未拉取。

事实边界：上游插件示例，当前 clawd CLI 兼容未验证。没有把这些子命令改成或写成已注册的 `clawd honcho`。`clawd plugins install` 与 `clawd memory setup` 是另一组已注册命令，不证明 `honcho` 子命令存在。

## 仍未知

- 已安装 honcho 插件实际 `register_cli_command` 拼写，以及 `honcho setup|status|enable|disable|peer|peers|mode|tokens|sessions|map|identity|sync|migrate|strategy` 在当前 `clawd` 入口是否注册。未读插件仓库，未跑 CLI。
- 网站生成页与 optional skill 谁更新。生成页不在允许名单，源 `SKILL.md` 也不在本次可写名单。

## 未验收

未跑测试、构建、导入或命令。镜像测试若仍失败，原因不在这两块内文；本次没有跑它，不能把字节相同写成测试通过。本文件不是通过记录。
