# Honcho 文档边界收口 — 2026-11-05

静态文案与源/生成页关系核对。未运行 `website/scripts/generate-skill-docs.py`、`scripts/run_tests.sh`、文档站构建、类型检查、`py_compile`、导入、`clawd` / `hermes` 命令或网络。未拉取外部插件。未 commit / push / deploy。未删文件。未读生产凭据。未改原面板、线上服务、`/usr/local/lib/hermes-agent`。未改 features 页、i18n、插件源码、schema、配置或 OAuth。未重改准入规则镜像。本文件不是通过记录，也不构成上线授权。

## 生成关系

`website/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-honcho.md` 页首仍写：由 `website/scripts/generate-skill-docs.py` 从技能 `SKILL.md` 生成，应改源不改页。只读该脚本 `render_skill_page`：正文经 MDX 转义与相对链接改写后原样嵌入，没有把 `hermes honcho` 改成 `clawd honcho`。它读的是 `metadata.clawd`；源 frontmatter 键仍是 `metadata.hermes`。因此磁盘上的生成页不是当前脚本的新鲜输出（作者行已是 `clawd`，故障句路径已是 `~/.clawd/config.yaml`，honcho 子命令曾被写成 `clawd honcho`）。本次不运行生成器，只手工对齐冲突的 honcho CLI 示例。页首生成注释未改。

## 已改

只动这两份：

- `/root/hermes-workspace/clawd/optional-skills/autonomous-ai-agents/honcho/SKILL.md`
- `/root/hermes-workspace/clawd/website/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-honcho.md`

源页沿原示例保留 `hermes honcho`。生成页把冲突示例从 `clawd honcho` 改回同一拼写，没有写成已注册的 `clawd honcho`。

同一未验证边界加在：Verify 代码块前、`map` 行、Backfill 代码块前、Troubleshooting 节首、CLI Commands 表前。两边该句各 5 处，逐字相同：

> 未验证边界：这是上游插件示例，当前 clawd 入口兼容未验证。

相邻句写明：这些 `hermes honcho` 示例不表示 `clawd honcho` 已注册。`clawd plugins install` 与 `clawd memory setup` 不证明这些子命令存在。CLI 表注明同表 `clawd memory setup` / `status` / `off` 不在该边界内。Troubleshooting 注明配置路径与 Honcho dashboard 不在该边界内。

改后技能正文与生成页正文 429 行，只剩 1 处不同：源仍是 `~/.hermes/config.yaml`，生成页仍是 `~/.clawd/config.yaml`。这是路径，本次不改。

## 未验证边界

这是上游插件示例，当前 clawd 入口兼容未验证。

覆盖的示例拼写仍是 `hermes honcho`：`setup`、`status`、`enable`、`disable`、`peer`、`peers`、`mode`、`tokens`、`sessions`、`map`、`identity`、`sync`、`migrate`。没有把兼容未知写成能力支持，也没有声明插件实际 `register_cli_command` 就是这个拼写。

静态只读、未执行：本检出 `add_parser("honcho")` 无命中；`clawd_cli` 无 `honcho`。`plugins/AGENTS.md` 写明核心已删除硬编码 honcho argparse。`plugin-catalog/honcho.yaml` 指向外部仓库 `https://github.com/plastic-labs/honcho`，subdir `hermes-plugin-honcho`；本检出没有该插件源，未拉取。`hermes_cli/main.py` 的会话名集合和 `hermes_cli/profiles.py` 的保留名里仍有字符串 `honcho`，这不是子命令注册。因此不能把“未注册”或“已支持”写成运行时结论。

已注册主命令未回退，依据是解析器源码而不是本次执行：`hermes_cli/subcommands/plugins.py` 的 `install`；`hermes_cli/subcommands/memory.py` 的 `setup` / `status` / `off`；`hermes_cli/subcommands/profile.py` 的 `create`。两页仍是 `clawd plugins install honcho`、`clawd memory setup honcho`、`clawd profile create … --clone`，以及表内 `clawd memory setup` / `status` / `off`。

## 保持及未改

- 配置键 `hermes.coder`。两边仍各 3 处。
- 作者/来源：源 `author: Hermes Agent`、`metadata.hermes`、`related_skills: [hermes-agent]`；生成页元数据 `Author | clawd` 与页首生成注释。均未改。
- 路径：`$HERMES_HOME/honcho.json`、`~/.honcho/config.json`、源 `~/.hermes/config.yaml`、生成页 `~/.clawd/config.yaml`。均未改。
- Honcho dashboard：`https://app.honcho.dev`。集成文档 URL `https://docs.honcho.dev/v3/guides/integrations/hermes#running-honcho-locally-with-hermes` 未改。
- 准入规则两块内文未重改。父已核 11671 字节；本次未再哈希、未跑镜像测试。
- 未改且仍把 `clawd honcho` 写成命令或已注册能力的页：`website/docs/user-guide/features/honcho.md`、`website/docs/user-guide/features/memory-providers.md`，以及 `website/i18n/zh-Hans/...` 下对应副本、CLI 参考和迁移页。不在本次可写范围。

## 仍未知

- 已安装 honcho 插件实际 `register_cli_command` 拼写，以及上述子命令在当前 `clawd` 入口是否出现。未读插件仓库，未跑 CLI。
- 生成页作者 `clawd`、故障句 `~/.clawd/config.yaml` 与当前生成脚本从源 frontmatter/正文会抄出的值不一致。未运行生成器，不能把手工对齐说成已重新生成。
- features / i18n 里“`clawd honcho` 仅在 provider 激活后注册”等句未核、未改。

## 未运行

未运行生成器、测试、构建、导入、CLI 或网络。本文件不是通过记录。
