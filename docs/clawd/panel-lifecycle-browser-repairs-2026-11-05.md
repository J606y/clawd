# 面板生命周期与浏览器夹具修复（2026-11-05）

未跑测试、构建、CLI 验收或浏览器。下列「已修」只表示静态对齐了验收日志里的断言和真实路由，不是通过。

真实运行查询是 `GET /api/runtime/sessions/{id}/run`，不是旧的 `/sessions/run`。空 `{}` 会被客户端当成成功。子代理查询参数是 `parent`，页契约要有 `data/running/stalled/total/now/limit/offset`，且 `running <= total`。档位是 `GET /api/sessions/{id}/effort`，缺 `source/applied` 会抛「无法解析的档位」。未知 profile 不投影成 default。

## Node（17）

| 失败 | 根因 | 处置 |
| --- | --- | --- |
| approval 409/404，两份 runs，actual `running` expected `completed` | 409 后已有 `runStatus` 结算；验收时状态体只有 `{status:'completed'}`，身份字段缺失。404 分支本来就会把缺记录标成失败。 | 已修夹具：`conflict-completed` 补同 run 的 `run_id/session_id`。未改安全拒绝。未复跑。 |
| brand：还在找 `clawd update` / `hermes update --yes` | 更新页已明确禁用进程内更新，旧断言与拒绝契约冲突。 | 已修 `brand-panel.test.mjs`，改为匹配禁用文案，且不得出现 update 命令。未开放宿主更新。 |
| FE-04 / B2 / discovery / 后台仍 running / 停止两步 | 门夹具仍只匹配 `/sessions/run`，空 `{}` 被当成同步成功，页面停在「任务状态同步暂不可用」和「无法解析的档位」。后台 `{}` 也会被当成没有进程。 | 已修 `react-gate-fixture.tsx`、`react-chat-gate-fixture.tsx`：原生 run/background/execution/context/subagents/effort 按真实 schema 回应；未知路由 404，不再回空对象。未删产品门。 |
| FE-03 旧 offset 页追加进新窗口 | `loadEarlier` 在 await 之后仍把旧页接到已被替换的消息数组上。 | 已修 `chat-page.tsx`：await 前钉住 `reloadGen` 与消息引用，代次或窗口变了就丢弃。 |
| lost executor，30 tick 内仍 `running` | 未见事件的 404 先空等订阅窗口，sidecar 已有 `interrupted` 也来不及结算。 | 已修两份 `runs.ts`：`settleLostExecutor` 在等待前对同一 run 对账。查不到、仍 busy、身份不符不改成 idle/completed。 |
| follow 404 sidecar cancelled/failed/interrupted | 产品只接受带 `session_id` 的同一 run。夹具记录缺 `session_id`，落到「无法确认」。 | 已修 `runs-terminal.test.mjs` 补身份字段。completed 那条验收时已有身份仍是 `failed`；当前代码会把同一 signal 传给 `sessionRun`。未复跑，不称已闭合。 |

## Browser（13）

| 失败 | 根因 | 处置 |
| --- | --- | --- |
| artifacts 历史图不加载 | 侧车根与浏览器看到的附件根不一致；运行查询未打到原生路径。 | 已修 `artifacts-browser.mjs`：自有 profile 下的 uploads/workspace/images/cron，注入 `__CLAWD_ARTIFACT_ROOTS__`，并补 runtime/effort/subagents。未伪造 default profile。 |
| folders 重名 alert 等不到 | 第一个 `role=alert` 是预览失败，因为 `migrations/preview` 未实现。 | 已修 `folders-fixture.html`：`source_missing` 的只读预览。预览失败条消失后，重名 alert 才是第一个。 |
| folder-offpage unmatched 含 preview | 同上，真实路径漏路由。 | 已修 `folder-offpage-fixture.html`。 |
| image-preview 名称变成「图片 1.png」，芯片又出现 | 路径不在附件根，`uploadRef` 失败。 | 已修夹具路径到注入根 `/tmp/clawd-fixture-uploads/uploads/`。未改缩略图样式。 |
| subagent 卡片 0、时钟空、spin 无条、session-frame 超时、task-popup session 为空 | 产品请求 `parent`，旧夹具只读 `session`；缺 `total` 时收齐分页直接拒绝。 | 已修 card/spin/clock/session-frame/task-popup 夹具：认 `parent`，补分页字段。不把 profile 门改成 default。 |
| admission 新消息没有 POST | 发送前 `ensureSessionEffort` 因空档位失败；run 查询也漏路由。 | 已修 `react-admission-recovery-fixture.tsx` 的 runtime/effort/subagents。not-found 带 `resolution.state`，不是 `{}`。 |
| agent-close「页面无法打开」+ `ENOTEMPTY` | 上下文用量缺数字仍调用 `toLocaleString`；清理时 Chromium 还占着 profile。 | 已修 `pickers.tsx` 数字守卫（不改环和布局）；夹具补 runtime/subagents；清理先等自有 Chromium 退出再删自有目录，不杀其他进程。 |
| mcp-path setup 不是 4 张卡 | `.server` 把新建表单也算进去。页面文本里的 `/segment` 是长路径的 innerText，不是可改的样式。 | 已修测量选择器为 `.mcp-root .card.server` 且必须有 `.mono`。未改设置页样式。宽度命中未复跑。 |
| stream-perf | 场景 A 已画完仍是 frame p95 116.7、long task 82；B 在预算内未发完。不是缺路由。 | 未修渲染、出字和布局。只补了夹具 runtime/effort，避免再把同步失败算进同一帧。真性能回归留待统一复测定位。 |

## 未改

- `sidecar.ts`、`queries.ts`、settings 源码、侧车 Python：其他 owner。
- 未删门、未弱化断言、未跑浏览器、未 commit/push/deploy。
- `execution-input-recovery-fixture.tsx` 仍有空 `{}`，不在本次 13 个失败门里，未动。
