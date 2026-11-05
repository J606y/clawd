# HK 服务器上的改动怎么提交

**一句话：HK 上只在 `hk` 分支提交、只推 `hk`；`main` 和 `deploy`（发布）由本地统一合并、构建、发布。**

## 为什么
2026-10-07 发生过：HK 上的会话直接改了源码、推到 `main` 并部署，部署的是"它那份源码的构建产物"，
结果把本地当天所有改动从线上顶掉；本地随后部署又把它的功能顶掉。根因是两边都能改 `main`、都能发布。
分开后：HK 的改动永远集中在一条分支里，我一眼能看到"HK 有哪些东西还没合并"，不会和本地的工作混在一起。

## HK 上怎么做（`/root/hermes-workspace/hermes-panel`）
1. **只在 `hk` 分支上工作**：`git switch hk`。
2. **开工前先把 `main` 合进来**，保证基于最新代码：`git fetch origin && git merge origin/main`。
3. **提交说明以 `[hk]` 开头**；正文写三件事：**改了什么 / 怎么测的 / 哪些没测**。作者名固定为 `hermes-hk`（已配置）。
4. **只推 `hk`**：`git push origin hk`。
5. **不要在 HK 上运行 `npm run release` 或 `/root/panel-deploy.sh`。** 想让改动上线，推完 `hk` 后告诉本地，由本地合并、验证、发布。
6. 改动涉及服务器上的文件（不在这个仓库里，例如 `/usr/local/bin/hermes-live-browser-daemon.py`）：先备份，
   并把改了哪个文件、为什么、备份在哪、怎么回滚写进发版记录。不要再写 `HANDOFF.md`，该文件已从仓库删除。

**机制保证**：HK 副本的 `.git/hooks/pre-push` 会拒绝推送 `hk`（及 `hk/` 前缀）以外的任何分支，
所以即使忘了规则，**用 git 命令行**执行 `git push origin main` 和发布脚本推 `deploy` 都会被挡住。
发布脚本 `scripts/publish.mjs` 还会在云端 `main` 有本地没有的提交时拒绝发布。

**已知缺口（别以为已经 100% 挡住）**：
- 钩子只管 **git 命令行**。Hermes 的 GitHub MCP（`/usr/local/bin/github-mcp-server`，用 `GITHUB_PERSONAL_ACCESS_TOKEN`）是
  **直接调 GitHub API 写仓库**，不经过本地 git，钩子看不到；该 MCP 目前没有工具过滤（config.yaml 里 `tools: None`），
  理论上能直接写 `main`/`deploy`。同一个令牌也被 git 的凭据助手共用，令牌的权限范围决定了最坏情况。
- 2026-10-07 日志里没有它用 MCP 写过仓库的记录（那次是 git 命令行提交），所以这是潜在风险，不是已发生的事。
- **已缓解（2026-10-07 21:00）**：令牌已换成只授权 `hermes-panel` 一个仓库的 fine-grained PAT（Contents 读写，90 天，
  到 2027-01-05），旧的几乎全权限 classic 令牌已作废。所以 MCP/CLI 最坏也只能动这一个仓库，碰不到 `novelhub`、`moss` 等。
  但**仍然能直接写本仓库的 `main`/`deploy`**（`main` 分支没开保护）；彻底堵上要把 MCP 设为只读或过滤写工具，
  或给 Hermes 单独的 GitHub 账号 + 分支规则集。规则（Hermes 记忆里那条）和钩子目前是"防手滑"，不是"防恶意"。
- 令牌到期前要续（见 HANDOFF「令牌已更换」）。

## Hermes 怎么"知道"这些规则：技能，不是记忆
Hermes 的记忆只有 1375 字符且已满，放不下完整流程，所以规则写成了**技能** `hermes-panel-engineering`：
- 线上位置：HK `/root/.hermes/skills/devops/hermes-panel-engineering/SKILL.md`（Hermes 每次碰到面板任务会自己 `skill_view` 读它）。
- 规范副本在本仓库：`docs/hermes-skill/hermes-panel-engineering/SKILL.md`。**改规则时先改这份、提交，再同步到 HK**（HK 上的技能 Hermes 自己也能改，它会"自我优化"，所以以仓库副本为准，发现漂移就覆盖回去）。
- 注意：只能有**一份**面板相关的技能。2026-10-07 曾同时存在两份说法（一份教它自己部署），它选了旧的那份；已合并成一份，旧版备份在 HK `/root/.hermes/skills-backup/`。
- **技能受保护（v2.1.0 起，铁律 7）**：未经用户明确授权，Hermes 不得修改/删除/重命名/覆盖这份技能，也不得另建冲突技能；它想改只能先提议、等用户明确同意。验证过：给它一个会诱使它"顺手沉淀经验"的开放请求，它给了改进建议但明说"这份技能明确受保护，没有你的明确授权我不能直接改它"，文件指纹前后一致。
- **但这只是软约束，且 Hermes 里有后台"自我整理"机制会改技能**：账本 `/root/.hermes/skills/.curator_ledger.jsonl`（JSON 行，字段 `ts`〔UTC〕/`actor`/`action`/`skill`/`evidence.session_id`/`before`/`after`〔含文件 sha256〕）记录每一次技能改动。`hermes-panel-engineering` 这份技能最初就是 `actor: curator` 在 2026-10-07 20:14 HKT 自动创建的；21:09~21:26 `actor: agent` 在用户自己的对话（会话 `api_1791378469_dadfd3ab`，用户让它把记忆迁移进技能）里改了 6 个技能。**排查"技能是谁改的"先查这个账本。** 想硬锁：`chattr +i /root/.hermes/skills/devops/hermes-panel-engineering/SKILL.md`（不可变，连 root 也写不了；用户要授权修改时先 `chattr -i`，改完再 `+i`；代价是面板的技能编辑器也会写入失败）。
- 验证方法：给 Hermes 一个不提技能的模糊请求（"我想改面板侧边栏标题字号，先告诉我怎么改、怎么提交、怎么上线，别改任何文件"），看它是否自己读了技能、计划里是否只推 `hk` 且"不自行部署"。

## 本地（Claude Code）怎么处理 HK 的改动
```
git fetch origin
git branch -r --no-merged origin/main | grep -v 'origin/deploy'   # 还没合并的分支；hk 就是 HK 上待处理的改动（deploy 是产物分支，历史无关，永远显示为"未合并"，要排除）
git log --oneline main..origin/hk            # 看 HK 有哪些提交
git log --author=hermes-hk --oneline         # 或按作者名拎出 HK 的所有提交
git diff main...origin/hk                    # 审查具体改动
git merge origin/hk                          # 审过、没问题就合并
npm run release                              # 构建、验证后发布（发布前会检查云端 main 是否被覆盖）
git push origin main                         # 推送，让 HK 和别的会话看到
```
合并后告诉 HK 的会话：`git switch hk && git merge origin/main`，让 `hk` 追上 `main`。

## 约定
- `main`：本地与云端共同的主线，唯一的发布来源。
- `hk`：HK 服务器专用，只放 HK 上做的改动，不直接发布。
- `deploy`：只含构建产物，由 `npm run release` 生成，**任何人不要手动改**。
