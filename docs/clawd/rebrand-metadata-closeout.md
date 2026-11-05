# clawd 发行 metadata 消费收口（静态）

未安装、未 build、未跑测试、未 `py_compile`、未探针。不是验收通过。锁、`pyproject.toml`、Nix、资源契约未改。`build/lib/` 是旧树副本，未改未删。

## 已改

发行查找改为已安装名 `clawd`（`pyproject.toml` `[project].name` 已是 `clawd`）。不是只改展示。

- `hermes_constants.py:1531` `distributions(name="clawd")`。同函数文档串同步为 clawd 发行。权限警告改为 “clawd install directory” / “clawd home directory”。
- `agent/transports/codex_app_server_session.py:847` `version("clawd")`。
- `plugins/platforms/qqbot/utils.py:16` `version("clawd")`。文档串改为 clawd 发行版本。

extra 安装文案改为 `clawd[...]`，与 `[project.optional-dependencies]` 自引用一致：

- `hermes_cli/main_agent_cmds.py:179` `clawd[otlp]`
- `agent/monitoring/otlp_exporter.py:5,277` `clawd[otlp]`
- `agent/monitoring/gateway_health_export.py:292` `clawd[otlp]`
- `plugins/platforms/dingtalk/adapter.py:330` `pip install 'clawd[dingtalk]'`

字面断言改为现发行名，未 skip、未放宽：

- `tests/test_project_metadata.py:132` `clawd[{extra}]`
- `tests/agent/test_bedrock_integration.py:271` `clawd[bedrock]`

`[all]` 现含 `clawd[cron|pty|mcp|uvloop|sms|acp|google|web|youtube]`，不含 `clawd[bedrock]`。这是读 `pyproject.toml` 的结论，未运行。

## 明确不改

- `partial_update_hint` 仍打印 `hermes update` 与 `https://hermes-agent.nousresearch.com`。`README.md` 写明 `clawd update` 被拒绝；`tests/hermes_cli/test_cli_partial_update_hint.py:85,117` 断言该命令串。URL 是上游地址。
- Codex `client_name="hermes"`、`client_title="Hermes Agent"` 是握手标识。`tests/agent/test_codex_usage_attribution.py` 断言 `(Hermes Agent)`。函数名 `_get_hermes_version` 未改。
- QQBot User-Agent 仍是 `Hermes/<version>`。只改了版本来源，不改线上格式。
- 钉钉句 “Hermes pins dingtalk-stream==0.24.3…” 不是 extra 规格，未改。
- `scripts/clawd_inventory/test_generate.py:74,83,84,138,153,258` 无须改。`generate.py:263,829` 从夹具自己的 `project.name` 取发行名，不读本仓库 `pyproject.toml`。`:258` 与夹具名成对；只改 138/153 会拆掉断言。同文件另有 `name = "demo"` 夹具，证明这不是本项目名契约。包装文档把它们列为可能失配，静态核对后不是。

## 兼容仍在（只记录，未删）

- `pyproject.toml:591-596` 保留脚本 `hermes`、`hermes-agent`、`hermes-acp`，注释写明密封 Nix/venv 命令图。
- 仓库根 `hermes` 仍调用 `hermes_cli.main`。`nix/devShell.nix` 把它装进 dev shell PATH。
- `nix/checks.nix` 要求 `bin/hermes` 与 `bin/hermes-agent`。`nix/overlays.nix` 仍有 `pkgs.hermes-agent`。文件名 `nix/hermes-agent.nix` 与 `HERMES_BIN` 未动。

## 仍阻塞统一测试

范围外，本路未改：

- `tests/test_hermes_constants.py:617-619` 仍写 `hermes_agent-0.0.0.dist-info` 与 `Name: hermes-agent`。`project_venv_dir` 现只认 `clawd`，这组 out-of-tree 夹具会找不到发行。目录名与 METADATA Name 都要改成 clawd，不能只改其中一个展示字段。
- `build/lib/` 下同路径旧副本仍查 `hermes-agent` / 旧 extra。源码已改不能证明该树已更新。
