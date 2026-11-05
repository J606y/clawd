# 定时任务、更新、日志实现记录

静态核对，未运行。这不是验收通过、接线完成或上线许可。

报告路径：`/root/hermes-workspace/clawd/docs/clawd/settings-jobs-logs-update-implementation.md`

## 改了什么

- `apps/panel/src/react/pages/settings/jobs-tab.tsx`：创建和编辑补 `deliver`、`skills`、`repeat`。
- `apps/panel/src/react/pages/settings/update-tab.tsx`：去掉立即更新按钮和确认层。
- `apps/panel/src/react/pages/settings/logs-tab.tsx`：文件来源保留；journal 不再作为可选项。
- `clawd_server/admin_logs.py`：新增 `get_logs`。文件源沿用尾读；journal 源 501。
- `tests/clawd_settings_cases/test_jobs_logs_contract.py`：准备用例，未执行。

未改网关、公共 `api.ts` / `queries.ts` / `sidecar.ts`、`admin_routes.py`、侧车、样式表、生产配置、原面板和官方安装目录。未删文件。

## 调用链

### 定时任务

页面 `JobsTab.save` → 既有 `useCreateJob` / `useUpdateJob`（`queries.ts`，本路未改）→ `api.createJob` / `api.updateJob` 对传入对象做 `JSON.stringify` → 网关 `POST /api/jobs` 或 `PATCH /api/jobs/{id}`。

网关创建（`gateway/platforms/api_server.py` `_handle_create_job`）实际放行：

- `name`、`schedule` 必填；`prompt` 过长度和注入扫描，网关本身不要求非空。
- `deliver`：字段缺省为 `"local"`。键存在时原样传给 `create_job`，创建处理函数不枚举校验。
- `skills`：真值才放入 kwargs。空列表不会传。
- `repeat`：出现时必须是 `int` 且 `>= 1`，否则 400 `Repeat must be a positive integer`。`null` 视为未出现。
- 创建还会接受 `paused` / `paused_reason`。本页不传。

网关更新白名单 `_UPDATE_ALLOWED_FIELDS`：`name`、`schedule`、`prompt`、`deliver`、`skills`、`skill`、`repeat`、`enabled`。更新处理函数不再检查 `repeat` 类型；`cron.jobs.update_job` 会把 `null`、`<= 0` 收成不限次数。

页面对齐创建这条更严的域，创建和编辑都只送整数或留空，不送 `"forever"` 这类字符串，也不送布尔值。

- 创建：投递、技能、次数为空则省略。投递省略后网关默认 `local`。次数省略后核心视为不限；一次性时间点由 `create_job` 改成 1 次，页面已写明。
- 编辑：清空投递写成 `local`，否则省略会留下旧目标。清空技能送 `[]`，由核心把 `skills` / `skill` 对齐清空。清空次数送 `null`，核心收成不限。不送 `skill`、`enabled`；暂停和恢复仍走原按钮。

`CronJobInput` 仍只有 `name` / `schedule` / `prompt` / `enabled?`。公共类型不在本路。页面用 `asJobInput` 断言后把同一对象交给既有 mutation。断言不删除运行时字段，`JSON.stringify` 会带上这三个键。这依赖客户端不按旧类型裁剪 body。当前 `api.ts` 没有裁剪。

### 更新

`UpdateTab` 只保留 `useUpdateInfo` 和 `useUpdateRun` 两个既有读钩子，以及重新检查按钮。落后时显示不可点击的警告行，不再渲染「立即更新…」、确认层、`useUpdateStart`、`useUpdatePlan`，也不再调用 `start.mutateAsync`。

`sidecar.updateStart` → `update/start` → `start_update` 仍在公共库和 `admin_native` / `bind` 里。后者对 `start_update` 返回 403 `update_disabled`。本页不再调用它。没有安全的进程内更新或重启接口，本路没有新造。

### 日志

页面仍用 `useLogSources` / `useLogs` → 侧车 `GET /logs/sources` 与 `GET /logs`。公共客户端和路由不在本路，没有改成调用 `admin_logs`。

来源下拉只保留 `kind === "file"`。`kind === "journal"` 不进入 `<option>`，也不作为查询 `source`。因此这个页面不会把 `j-gateway` / `j-dashboard` / `j-sidecar` 交给侧车 `_journal_lines`，也就不会从这里执行 `journalctl`。

`admin_logs.get_logs(mod, source, lines, level, component, q)` 供集成人接到现有 `GET /logs` 查询：

1. `source` 属于 `LOG_JOURNAL` 时，在读取 home 之前 `fail(501, "journal_host_boundary", ...)`。不返回 `{"lines": []}`。
2. 未知来源 404 `unknown_source`，同样不返回空列表，也不读 systemd。
3. 文件源才调用 `panel_home`。目录用 `mod.LOG_DIR`，且必须落在面板 home 内，否则 409。缺文件沿用侧车：返回该文件源的空行，这不是 journal 成功。
4. 过滤、2000 字截断后再脱敏，与侧车文件路径相同。没有 `redact` 时 503，不回原文。

集成人必须让这个异常原样变成 HTTP 501。抓住异常后改成空列表就是假成功。本路不能改 `admin_routes.py`，所以这条函数还没挂上。

## 布局

沿用原卡片、标签、输入、工具条、药丸和警告行。没有改 `settings.css`，没有新顶层 tab。定时任务三个字段接在原名称、频率、内容之后。更新页去掉的是按钮和确认块，版本行和重新检查还在。日志工具条还在，只是不再有「系统服务」选项组。

## 缺口

这些没有用假开关或空列表抹掉。

1. `CronJobInput` 还没有 `deliver?`、`skills?`、`repeat?`。集成人应补上。在那之前，页面靠断言送出字段；若以后有人按旧类型裁剪 body，这三个字段会丢。
2. `GET /logs` 还没注册到 `admin_logs.get_logs`。线上文件日志仍走侧车。页面不再选择 journal，所以这条 UI 不会触发侧车的 `journalctl`。侧车函数本身还在，别的调用方仍可能读宿主 journal。那不在本路文件里。
3. `useLogs` 的第二个参数是跟随刷新，不是 `enabled`。没有文件来源时仍会用空 `source` 请求一次。侧车对空来源是 404，不是 journalctl，也不是空列表成功。本路不能改 `queries.ts`。
4. 来源列表仍由侧车给出，并把 journal 标成 `available: true`。页面忽略这个可用标记。若以后把 journal 标成 `kind: "file"`，现有侧车路径仍会读 journalctl。`admin_logs` 对已知 journal id 仍是 501，但页面现在还没走它。
5. 面板仍要求任务内容。网关创建允许空 prompt，只要 `create_job` 看到技能或脚本。本页没有脚本字段，也没有改成「只填技能即可保存」。
6. 编辑频率仍回填 `schedule_display`，不是原始表达式。这是原行为。显示文本若不能被核心再解析，保存会失败。
7. 网关不枚举校验 `deliver`。页面只把整段 `local` / `origin` / `all` 收成小写，其余原样送出。火时解析失败不会在创建时被网关拒绝。`origin` 在这个 API 上是 `platform=api_server`、`chat_id=api`，不一定是聊天频道。
8. 面板自加的上限不是网关限制：投递 500 字、技能最多 50 个且单个 128 字、次数 1 到 9 位正整数、拒绝控制字符。技能名里的逗号会被当成分隔符。网关更新还接受 `skill` 单值和更宽的 `repeat` 字符串；页面不送这些。
9. 更新检查和运行状态读取还在。它们不是 `start_update`。native 若把检查结果禁用，页面沿用原错误显示。没有新的更新或重启接口。运行中文案仍描述重启；本页已经不能发起那次更新。状态接口若回报非 idle，只是只读展示。
10. 文件日志在脱敏前先截到 2000 字，这是照侧车保留的，不是新加的安全改进。缺文件的空行只属于文件源。
11. Webhook 投递记录、宿主重启、进程级配置档切换都不在本路，仍是缺口。

## 未运行

未执行测试、构建、类型检查、import、`py_compile`、CLI、浏览器或网络探针。准备用例里的行为断言也没有跑。准备成功不是通过。
