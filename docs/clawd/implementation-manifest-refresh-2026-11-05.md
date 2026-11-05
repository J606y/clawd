# 实现完成清单刷新 — 2026-11-05

静态核对。未跑测试、构建、import、CLI、门禁或探针。`implemented` 只表示代码已交付，不是通过，不是上线许可。

清单：`/root/hermes-workspace/clawd/docs/clawd/implementation-completion-manifest.json`

观察时间：`2026-11-05T18:17:53+0800`。`schema_version=1`。`implementation_complete=false`。

## 计数

工具解析 35 项：`implemented` 27，`pending` 2，`unverified` 1，`blocked` 5。blocking 且未实现只有 1 个。

## 挡住门禁的代码缺口

`runtime-usage-context-subagents` 仍是 `pending`，`blocking=true`。上下文和子代理已经接到 `/api/runtime/...`，不要重做。用量没有。

活消费者：

`usage-tab.tsx` → `queries.ts:297` `useUsage` → `sidecar.ts:684` `usage()` → 侧车 `GET /usage` → `hermes_panel_sidecar.py:2853` `get_usage` 私有打开 `state.db`，路由在 `:6081`。

`runtime_native.install` 没有替换 `get_usage`。`clawd_runtime_routes.http_routes` 没有用量路由。修这条链即可。在此之前即使带上旗标，门禁也会拒绝。这是正确拒绝。

## 不是缺口

- 凌晨 `absent` 和旧行号已过期。`bind` 经 `native.install` 挂上 browser、runtime、admin，再 `file_roots_under_home`。effort / project / runtime 路由在 `/p/{profile}` 兜底之前。`build/lib` 不是活入口。
- 五次成功硬停不在 `decide()`。`continuation-no-five-cap.md` 不能当当前证据。失败预算 `MAX_ATTEMPTS` 仍在。
- `windowId` / IO handle 无映射不是已批准能力回归。没有消费者用它们给任务页寻址。`Browser.getWindowForTarget` 只在 daemon 的 targetId 参数类里。`IO.read` 只是脱敏路径。电脑控制的 `window_id` 是另一套，且属于批准排除。
- 活浏览器是否发出 `frameId` 未探针。缺身份时 dialog 失败关闭。不能写成 Chrome 已发送。
- 宿主更新/重启、进程 profile 切换、热加载、安装器、memory setup、电脑控制授权不存在，也不算本轮缺失。journal 与投递保持 501，不是空列表。
- 许可未知、真实模型/MCP、外部 Chromium 仍是验收入口里的必需检查，状态 `blocked`，未删。它们不把代码门禁永久锁死；运行时缺了就是退出 2。
- 9 个 Honcho 页等文档 owner 终态。未取中间哈希。不宣称全站完成，也不证明 `clawd honcho` 已注册。
- 审查 ID `bg-receipt` 等未重审、未关闭。本清单不把它们写成已修。

## 将来一次启动

现在不要跑。用量接线改完并更新本清单、blocking 项都是 `implemented` 之后：

```sh
PY=/root/hermes-workspace/clawd/.venv/bin/python
REPO=/root/hermes-workspace/clawd
"$PY" "$REPO/scripts/clawd_acceptance/accept.py" prepare
"$PY" "$REPO/scripts/clawd_acceptance/accept.py" run \
  --run --implementation-complete
```

缺 Chromium、真实模型/MCP 或隔离 journal 时，对应检查保持 blocked/2，不要模拟，不要另写第二套脚本。
