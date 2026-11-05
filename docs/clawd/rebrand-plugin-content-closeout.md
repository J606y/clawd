# 插件与技能文案品牌收口

静态核对。未运行测试、构建、`py_compile`、导入、安装、探针。未提交、推送、部署。未再派发。未访问 `/root/.hermes`。未重扫全树。计数来自既有 `docs/clawd/rebrand-inventory.json` 与本次对 allowlist 的逐行处理。

## 覆盖

owner 为 `plugins` / `skills_content` 且分类为必须改第一方品牌：1275 个文件、5726 次。路径限定后 allowlist 是 `plugins/` 67 文件 / 224 次，仓库 `skills/` 129 文件 / 635 次，合计 196 文件、859 次、788 个清单行。

已改 157 个文件（插件 52，技能 105）：Markdown 112、YAML 31、Python 13、JSON 1。替换 708 处（产品名 126、命令或裸产品词 568、整行拒绝/裁剪改写 11，另补清单漏记的 `hermes proxy` 3 处）。整文件保留 39 个。

路径外不改：`plugin-catalog/` 889 文件 / 4034 次；`optional-skills/` 190 文件 / 833 次。

## 已改

用户可见产品名改为 `clawd`。确定走当前入口的命令示例改为 `clawd`（`config`、`gateway`、`photon`、`meet`、`plugins`、`auth`、`pets` 等）。`update` / `desktop` / `dashboard` / `gui` 不改成 clawd 命令，改成与 `clawd_cli` 相同的拒绝句；dashboard 指向 `clawd panel`。介绍句删掉 “native desktop app”，避免把已裁剪 Desktop 说成 clawd 功能。

编程文件只改字符串文案：客人名、邮件主题、A2A 默认描述、Photon 默认项目名、argparse 描述。Python 非字符串 token、YAML 键、`package.json` 的 `name` 未动。`@hermes-agent/photon-sidecar` 包名保留，只改 description。

## 保留及理由

- `metadata.hermes`、技能 `name: hermes-agent`、tags 里的 `hermes`、`hermes_cli` / `hermes_home` / `hermes_agent.memory_providers`。
- `HERMES_*`、`~/.hermes`、`./.hermes`、`/etc/hermes`、`/tmp/hermes-*`、`hermes-bot`（IRC 代码默认昵称）。
- 作者行、`commit authored as "Hermes Agent"`、`npx get-shit-done-cc --hermes`（第三方安装旗标）。
- 官方 URL、GitHub 真源、`X-Title` / `HTTP-Referer`、`X-Hermes-Sidecar-Token`、Matrix `device_name`、Nous Portal OAuth 文案。
- 唤醒词 “Hermes wake words”。`Hermes Docker` 仍指上游镜像。
- `plugins/hermes-achievements/`：第三方整包，自有 LICENSE，真仓库名，且命令是被拒绝的 dashboard。未改。
- `skills/.../desktop-plugins.md`：写的是已裁剪 Desktop，改成 clawd 会冒称支持。未改。
- `plugins/platforms/qqbot/utils.py`、`dingtalk/adapter.py` 不在本 owner 的必须改清单里，本路未写。

清单漏记、已在同一命令块补上：`SKILL.md` 两处与 `cli-reference.md` 一处 `hermes proxy` → `clawd proxy`。未据此重扫。

## 未验证

未跑测试、构建、导入或命令探针。技能快照和插件描述断言可能仍期望旧产品名。本文件不是通过记录。
