# clawd 分发更名（静态）

本文件只记录分发元数据更名。未安装、未 build、未跑测试、未 `uv lock`、未 Nix eval。不是验收通过。资源契约 v2 与 `MANIFEST.in` 的打包声明未重做。

## 已改

- Python 分发名 `pyproject.toml` `[project].name`：`hermes-agent` → `clawd`。版本仍 `0.0.0`。作者仍 Nous Research，`license = "MIT"`，`license-files = ["LICENSE"]`。未加官网或新仓库。
- 自引用 extra：`hermes-agent[...]` → `clawd[...]`（`all`、`termux`、`termux-all`）。依赖版本未改。
- 入口 `clawd = "clawd_cli.main:main"` 保留。没有改到 `hermes_cli`。
- `setup.py` 拒绝文案里的产品名改为 clawd。上游文档 URL 仍是 `https://hermes-agent.nousresearch.com/docs/getting-started/installation`。`HERMES_NIX_BUILD` 未改。PowerShell 提示改为 `./activate.ps1`，只是文案，守卫逻辑未改。
- `uv.lock` 本项目条目 `source = { editable = "." }` 的 `name` 及同包 optional-dependencies 自引用改为 `clawd`。版本、hash、URL、第三方包名未改。该块仍留在原字母位置，未重排、未重解。
- 根 npm：`package.json` 与 `package-lock.json` 顶层及 `packages[""].name` 改为 `clawd`。`repository` / `bugs` / `homepage` 仍是 `github.com/NousResearch/Hermes-Agent`。license 仍 MIT。依赖版本未改。
- `apps/panel`：`package.json` 与其 lock 的两处 `name` 改为 `@clawd/panel`。未加 license 字段，未套用核心 MIT。
- `apps/bootstrap-installer`：name 改为 `@clawd/bootstrap-installer`。无独立 lock，仓库内无该 npm 名的其他引用。
- Nix：`nix/python.nix` 的 uv2nix 属性与 venv 成员改为 `clawd`（否则求值找不到包）。venv 推导名改为 `clawd-env` / `clawd-editable-env`。`flake.nix` description 标明 clawd，作者仍 Nous Research。`nix/hermes-agent.nix` 的 `meta.description` 同步说明。

## 保留及理由

- `[project.scripts]` 仍有 `hermes`、`hermes-agent`、`hermes-acp`。密封 Nix 命令图和 `nix/checks.nix` 直接要求 `bin/hermes`、`bin/hermes-agent`。删掉会让已终态的包装解析断。这些名字会在安装时占全局命令。本路未安装、未改系统。不要把本发行装到已有生产 `hermes` 的机器。
- 文件名 `nix/hermes-agent.nix` 未删未改名。`pname`、`mainProgram = "hermes"`、`pkgs.hermes-agent`、`services.hermes-agent`、`programs.hermes-agent`、`HERMES_BIN`、`HERMES_NIX_BUILD`、`HERMES_PYTHON_SRC_ROOT` 是外部配置和检查的兼容引用。
- `nix/devShell.nix` 仍把仓库脚本 `hermes` 放进 dev shell PATH。只改了提示，未安装到系统。
- Desktop / webapp 的 `pname`（`hermes-desktop`、`hermes-desktop-renderer`、`hermes-webapp`）和 homepage `https://github.com/NousResearch/hermes-agent` 未改。checks 按 `pname == "hermes-desktop"` 过滤。
- `[tool.hermes]` 未改。`pm/features.py:88` 读 `tool.hermes.opt-in-extras`，`pm/extras.py:115` 读 `tool.hermes.extras-platforms`。
- `@hermes/shared`、`@hermes/ink`、`hermes-tui` 未改。TUI 直接依赖它们；只改 apps 名会让 workspace 解析断。许可仍未知，未写 LICENSE。
- `MANIFEST.in` 未改。`prune hermes_cli/web_dist` 是模块路径，不是分发名。
- 内部导入 `hermes_cli`、`run_agent` 未改。第三方包 id、来源、license、版本未改。`pm/uv.lock` 的 `hermes-pm-runtime` 不是本项目，未改。

## 跨 owner 接点

分发名查找，安装为 `clawd` 后会找不到旧名：

- `hermes_constants.py:1531` `distributions(name="hermes-agent")`
- `agent/transports/codex_app_server_session.py:847` `version("hermes-agent")`
- `plugins/platforms/qqbot/utils.py:16` `version("hermes-agent")`

仍打印旧 extra 名（不阻断解析，文案过时）：

- `hermes_cli/main_agent_cmds.py:179`
- `agent/monitoring/otlp_exporter.py:277`
- `agent/monitoring/gateway_health_export.py:292`
- `plugins/platforms/dingtalk/adapter.py:330`

测试字面量会在统一测试时失配：

- `tests/test_project_metadata.py:132`
- `tests/agent/test_bedrock_integration.py:271`
- `scripts/clawd_inventory/test_generate.py:74,83,84,138,153`

TUI 若要改 scope，必须同一变更改这些，不能只改 `apps/shared`：

- `ui-tui/package.json:3` `hermes-tui`，`:25` `@hermes/ink`，`:26` `@hermes/shared`，`:36` `npm:@hermes/ink@0.0.1`
- `ui-tui/packages/hermes-ink/package.json:2`
- 根 `package-lock.json` 的 `apps/shared`、`node_modules/@hermes/ink`、`node_modules/@hermes/shared`、`node_modules/hermes-tui`、`ui-tui` 条目

不要当包名改的协议与启动器：

- `apps/panel/src/lib/auth.ts:8` 与 `apps/panel/src/react/lib/auth.ts:8` 的 `hermes-panel:v2:`
- `hermes_cli/_launchers.py:577-578` 仍发布命令名 `hermes-agent`

文档残留，本路未改：`docs/clawd/distribution-notices.md`、`docs/clawd/dependency-source-inventory.json` 仍写旧分发名。范围外未改：`tests-js` 的 `@hermes/root-tests`，`plugins/platforms/photon/sidecar` 的 `@hermes-agent/photon-sidecar`，`scripts/whatsapp-bridge` 的 `hermes-whatsapp-bridge`。

## 未验证

未跑 `uv lock --check`、`npm ci`、Nix eval/build、wheel、测试。不能说锁排序、uv2nix 属性或 `@clawd/panel` 安装后解析已通过。`pm` 从 pyproject 动态读 `[project].name`（`pm/environment.py:122`），这条是读码结论，未运行。
