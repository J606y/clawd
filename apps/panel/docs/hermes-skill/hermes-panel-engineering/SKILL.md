---
name: hermes-panel-engineering
description: Use whenever you edit, commit, push, build or deploy the user's self-built Hermes web panel (GitHub J606y/hermes-panel, clone at /root/hermes-workspace/hermes-panel) — NOT the built-in Hermes desktop app under /usr/local/lib/hermes-agent. Panel changes go ONLY to the dedicated `hk` branch (push `hk`, never `main` or `deploy`), and you must NEVER deploy yourself: stop after pushing `hk` and wait for the user's local reviewer to merge and deploy. This skill itself is protected: never modify, delete, rename or replace it without the user's explicit authorization.
version: 2.1.0
author: Hermes Shadow Architect (workflow rewritten with the user's reviewer, 2026-10-07)
license: MIT
metadata:
  hermes:
    tags: [panel, frontend, git, hk-branch, vite]
    related_skills: [fleet-patrol, github-pr-workflow]
---

# Hermes 面板工程指南（hk 分支流程）

> ⚠ **本技能受保护：未经用户明确授权，禁止修改、删除、重命名、覆盖本技能（见铁律 7）。**

## 何时使用
只要任务涉及**用户自研的 Hermes 面板**——改界面、改 sidecar、修 bug、加功能、提交、推送、构建、部署——先读这份技能再动手，严格照做。
用户说"面板""侧边栏""实时画面""对话页"，指的都是这个仓库（源码在 `src/`，Lit + TypeScript）。
`/usr/local/lib/hermes-agent/apps/desktop`（Hermes 自带的 desktop 应用）是**另一个项目**，不是用户要改的面板。2026-10-07 曾有会话改错到那里，还在服务器上跑了重构建，把这台 2 核机器拖到负载 8。

## 1. 环境与路径
- **源码：** `/root/hermes-workspace/hermes-panel`（GitHub `J606y/hermes-panel`）
- **线上目录：** `/var/www/hermes-panel/current`（指向 `releases/<时间戳>-<提交>`）——**只读，不要改**，上线由用户在本地审核后统一做。
- **边界：** 严禁搜索、检查或修改官方上游目录 `/usr/local/lib/hermes-agent`。

## 2. 铁律（任何一条做不到就停下，问用户）
1. **只在 `hk` 分支提交，只推送 `hk`**（`git push origin hk`）。
2. **不提交到 `main`，不推送 `main`、`deploy` 或任何标签。**
3. **不要自己部署**：不运行 `/root/panel-deploy.sh`、`npm run release`；不往 `/var/www/hermes-panel/` 拷贝产物、不切换 `current`、不清理 `releases/`。推送 `hk` 就是你这一环的终点。
4. **不要绕过保护**：不用 `git push --no-verify`，不删改 `.git/hooks/pre-push`，不重设 `credential.helper`；不用 `curl` 直接调 GitHub API 写仓库；GitHub MCP 的写类工具（`push_files`、`create_or_update_file`、`merge_pull_request`、`delete_*`）不要用来写 `main`/`deploy`（读类用途没问题）。
5. **不碰令牌**：不读取、不打印、不修改 `GITHUB_PERSONAL_ACCESS_TOKEN`（在 `/root/.hermes/.env` 和 `config.yaml` 里）。
6. **不在服务器上跑重构建**（尤其 `desktop.mjs`）：面板自己的 `npm run build` 只要几秒，可以跑。
7. **未经用户明确授权，禁止修改本技能**：不得修改、删除、重命名、覆盖本技能文件（`SKILL.md`）或它的备份（`/root/.hermes/skills-backup/`），不得用技能管理工具改它，也不得另建内容冲突的面板技能来替代它。你认为规则需要调整时，**只把理由和建议的改法告诉用户**，等用户明确回复"同意修改"之后才能动手；经授权的修改也必须原样保留本条。

## 3. 标准流程
### 开工前
```bash
cd /root/hermes-workspace/hermes-panel
git switch hk
git fetch origin && git merge origin/main     # 先追上主线；有冲突就停下，告诉用户，别硬解
git status -sb                                 # 应该是干净的、在 hk 分支
```
### 构建验证
```bash
npm run typecheck
npm run build
```
必须都通过，且 `dist/index.html`、`dist/assets/` 正常生成。**不要推送一个构建不过的版本。**
### 提交
只 `git add` 本次任务相关的具体文件，不要 `git add -A`。提交说明第一行以 `[hk]` 开头，正文三段：
```
[hk] 一句话说明做了什么

改了什么：……
怎么测的：……（跑了什么、看到什么结果）
没测什么：……（如实写，不确定的别写成"已验证"）
```
作者名已固定为 `hermes-hk`，不要改 `git config`。
### 推送并汇报
```bash
git push origin hk
```
汇报：提交号、改动的文件、上面那三段话，并明确说"**已推送到 hk，等你审核后部署**"。**不要说"已部署"或"已上线"。**

## 4. 改的不是仓库里的文件
服务器上的其他文件（例如 `/usr/local/bin/hermes-live-browser-daemon.py`、nginx 配置、systemd 单元）：改之前先备份（`cp -p 原文件 原文件.bak-$(date +%Y%m%d-%H%M%S)`）；改完告诉用户改了哪个文件、为什么、备份在哪、怎么回滚；同时在仓库 `HANDOFF.md` 对应位置补一条记录，也提交到 `hk`。

## 5. 只有用户明确说"回滚"时
线上出问题、用户明确要求回滚网页时，可以运行 `/root/panel-deploy.sh --rollback`（只回滚网页，不回滚 sidecar）。除此之外不要运行部署脚本。

## 6. 更新兼容约定
更新中保留侧边栏项 `实时画面`、路由 `/live-browser` 及其 `/live/` 浏览器流目标；上线后（由审核者）验证侧边栏链接和实际画面。快照清理只涉及 `/root/.hermes/state-snapshots`，先看范围和回滚方式，不要盲目删除正在使用的恢复数据。官方 Hermes 更新要用 `hermes-agent` 技能，且范围不得越过上面的边界。

## 7. 经验
- **iOS PWA 缓存：** 主屏幕启动的 iOS WebKit 会积极缓存内存里的资源，部署后要在多任务界面上滑彻底关掉再打开，才会加载新资源。
- **滚动位置锚定：** 往顶部插入更早的聊天历史时，DOM 更新前后各算一次容器高度差（`scrollHeight - before`），保持阅读位置不跳。
- **GitHub 令牌：** 现在是只授权 `hermes-panel` 一个仓库的 fine-grained PAT（到期 2027-01-05）。认证失败（401/403）说明令牌过期或被作废——告诉用户，**别去找别的令牌**。
- **遇到问题：** `git push` 被本地钩子拒绝，说明你在往 `hk` 以外的分支推送，别绕过，检查当前分支；`git merge origin/main` 有冲突，停下把冲突文件和两边改动告诉用户；不确定属于哪种情况，先问用户，不要猜。
