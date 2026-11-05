# panel-python 修复记录 2026-11-05

来源：`artifacts/final-acceptance/logs/panel-python.log`。两套结果是 `341 tests, failures=68, errors=40, skipped=10` 与 `61 tests, failures=6, errors=9`。subtest 重复未计为独立漏洞。本轮未跑测试、未装依赖、未改 `runtime_native.py`、未改 `tests/clawd_file_auth_cases`。

## 分类

1. 文件根不在验收 `CLAWD_HOME`。首条：`_open_root_fd` 对 `account-home/.clawd` 做 `relative_to`，夹具临时根在外面，于是 artifact / workspace / audit 在进入 deny、签名、MIME 之前就 `outside_root`。这是夹具装载，不是放宽权限。
2. home 模式工作区策略过宽。`.gitignore` 被点文件拒绝；`nested/..` 列表被 `_split_rel` 判成 400；指向 `.git` 的链接被当成 404；列表 `absPath` 被注入前缀替换。
3. 影子 store / 旧 guard 钩子已退役。`Store()` 直接 `ShadowStoreRetired`；guard 不再注册 `on_session_end` / `pre_llm_call`，也不改写 `browser_exec`。两套不能同时启用。
4. YAML。`_redact_yaml` 在 `yaml is None` 时固定 503 `yaml_parser_unavailable`。6 个失败是语法用例期待 500，但解析器未装上，校验根本没跑。不是实现改去漏秘密。

## 已修

- 夹具把 `CLAWD_HOME` 设为临时 home，根放在 `home/workspace`、`home/uploads`、`home/cron/output`、`home/images`。权限检查仍走真实 `_open_root_fd` / no-follow，没有 mock。
- 工作区浏览只拒绝 `_WORKSPACE_BROWSE_DENY_PARTS` 和密钥名；`.gitignore` 可列、可下。附件路径仍用 `_segment_denied`。
- 列表允许词法 `..`，落到拒绝段仍是 403 `denied`；下载里的 `..` 仍是 400 `invalid_path`。
- 符号链接只 `readlink`，不 follow-open。指向 `.git` / `hermes-panel-state` 的列表为 denied；`link-notes` 仍出现在名单里，下载仍是 `unsafe_file`。
- 列表 `absPath` 回到已打开目录的配置路径，并用 inode 对上 fd。上传 `absPath` 仍对齐 `clawd_server.server._artifact_roots`。
- 附件请求正好打到根目录时返回 404 `not_found`，不再误报 `outside_root`。
- 浏览器用例改为调用 `clawd_server.browser_native.install` 与 `tools.browser_task_registry`。旧插件不再注册 ownership 钩子。`install` 后恢复 sidecar 原入口，避免污染同进程其他用例。影子库文件不得被创建。

## 未修

- YAML：隔离环境既没有 PyYAML，`clawd_server.yaml_adapter` 也依赖 `ruamel.yaml`。sidecar 导入顺序是 `import yaml`，失败再 `from clawd_server import yaml_adapter`。两者都失败才是日志里的 `yaml is None`。保持 503 拒绝原文，不回退正则。本轮不 pip。验收需提供其中一个解析器后，语法用例才会走到 500 `config_parse_failed`。
- 旧 preamble（`__LIVE_TASK_PREAMBLE__`、生成脚本里的 flock、`timeout_s >= 360`、原始 CDP `switch_tab` 的 `PermissionError`）没有搬回插件。那会和 native ownership 双开。`browser_native._HARD_LEASE_S` 现为 300，该文件不在本轮可改名单。
- `close_owned` 不等待用户租约再关页。实现在 `tools/browser_task_registry.py`，本轮不能改。夹具只断言关一个 task 不关另一个，且持锁时 takeover 为 pending。
- 10 个 skip 的原因不在日志明细里，未当成故障。

## 文件

- `apps/panel/sidecar/hermes_panel_sidecar.py`
- `apps/panel/tests/test_artifact_files.py`
- `apps/panel/tests/test_workspace_deny.py`
- `apps/panel/tests/test_audit_backend.py`
- `apps/panel/tests/native_browser_fixture.py`
- `apps/panel/tests/test_live_task_store.py`
- `apps/panel/tests/test_browser_window_guard.py`
- `apps/panel/tests/test_live_task_api.py`
- `apps/panel/tests/test_live_task_http.py`
- `apps/panel/tests/test_live_task_pause.py`
- `apps/panel/tests/test_live_task_chain.py`

未改 `apps/panel/plugins/browser-window-guard/__init__.py`：它只拦未登记工具，不是兼容断裂。未改 `clawd_server/{auth,bind,server,native}.py`：文件桥本身没有新的装载缺陷，失败在夹具根和 sidecar 列表策略。
