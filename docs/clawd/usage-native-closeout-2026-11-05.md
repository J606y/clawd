# 用量原生化收口 — 2026-11-05

静态接线。未运行测试、构建、import、py_compile、CLI 产品命令、网络或真实数据库。不是通过，不是上线许可。

## 页面到库

`usage-tab.tsx` `useUsage(days, visible)` → `queries.ts:300` `sidecar.usage(days)` → `sidecar.ts:685` `request("api/runtime/usage?days=")`，走 `panel-api/`，不传 `BASE`。鉴权与 profile 与 context/subagents 相同，没有新传输。

`GET /api/runtime/usage` 在 `clawd_runtime_routes.py:185`。`handle_usage`（`:452`）先 `_auth`，再拒绝缺省、非法、布尔和重复 `days`，然后只用 adapter `_ensure_session_db_async` 的库。`hermes_state_usage_summary.summarize_usage` 经 `SessionDB._read_ctx` 读。允许集合仍是 7/30/90。窗口是 `started_at > now - days*86400`。日分组是 `date(started_at,'unixepoch','localtime')`。字段仍是 totals/daily/byModel/bySource/top/aux。读失败是 503 `usage_unavailable`，不回空结构或 0，响应不含 SQL、路径或异常原文。真空库是正常空结构。

`runtime_native.install`（`:77`）替换 `get_usage`。`session_usage`（`:336`）只用模块已绑定的绝对 `HERMES_HOME`，只读 `SessionDB`，不调用 `get_hermes_home`，不手开 sqlite。`native.py:41` 会调用这次 install。未 bind 的侧车函数体仍在，独立脚本不是产品路径，未删。

`hermes_state.py` 未改。`InsightsEngine`、`/api/analytics/usage`、`auxiliary_usage_by_task` 字段不同，未拿来冒充用量页。

根模块由 `setup.py::_root_py_modules()` 从根目录 `*.py` 发现。`pyproject.toml` 没有静态 `py-modules` 列表。`nix/lib.nix` 的 `pythonSrc` 不按文件名排除它。

## 清单

工具解析 `implementation-completion-manifest.json`：35 项。`implemented` 28，`pending` 1，`unverified` 1，`blocked` 5。blocking 25，全部 `implemented`。`blocking_not_implemented` 为空。因此 `implementation_complete=true`。这只表示代码已交，不是测试通过。

非 blocking 未改：`honcho-doc-final` pending，`cron-event-order` unverified，许可/真实模型 MCP/Chromium/进程 profile 切换/宿主拒绝仍是 blocked。

## 仍缺，不是这条产品链

- `apps/panel/src/lib/sidecar.ts:440` 仍请求侧车 `usage?days`。`tsconfig.json` 的 `include` 只有 `src/react`，不在编译根。未改。
- 侧车 `GET /usage` 的 query 解析仍在只读文件里：缺省 30，重复 `days` 取第一个。bind 之后函数不再手开 sqlite。用量页已不走这条。
- 用例已写未跑：`tests/clawd_runtime_cases/test_usage_native.py`、`test_usage_wire.py`。

观察时间 `2026-11-05T18:41:40+0800`。
