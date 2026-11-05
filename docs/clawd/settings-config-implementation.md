# 配置页开放对象与 43 条汉化

静态核对，未运行测试、构建、import、CLI 或探针。这不是验收，也不是上线许可。

## 文件

- `clawd_server/admin_settings.py`：新增 `get_advanced` / `set_advanced`。既有 `get_settings` / `set_setting` / `reset_setting` 保留。
- `apps/panel/src/react/pages/settings/config-tab.tsx`：补充翻译回退、开放对象编辑，并消费三个冻结分组组件。原表单行序未改，没有新的设置顶层 tab。
- `apps/panel/src/react/lib/settings-zh-supplement.json`：矩阵 `zhMissing` 的 43 条中文。未改 `settings-zh.json`。
- `tests/clawd_settings_cases/test_advanced_contract.py`：只准备静态契约。未执行。

报告路径：`docs/clawd/settings-config-implementation.md`。

## 契约接线

允许对象只取矩阵 `openContainers` 里 `advanced-object` 与 `masked-advanced`，顺序与矩阵一致，共 38 项。`providers`、`model_catalog.providers`、`model_overrides` 仍归模型页。`monitoring.export.otlp.headers_env`、`secrets.onepassword.env`、`onboarding.seen` 不进这个编辑器。

导出：

- `admin_settings.get_advanced(mod, key)` 对应 `GET /settings/advanced?key=`
- `admin_settings.set_advanced(mod, key, value)` 对应 `POST /settings/advanced`，体为 `{key, value}`

不在名单返回 `403 advanced_key_denied`。键缺失或不是字符串返回 `400 invalid_body`。值不是对象、超限或类型不对返回 `400 advanced_invalid`。核心不认识该键返回 `400 unknown_key`。秘密策略函数缺失返回 `503 secret_policy_unavailable`，不另造名单，也不放行。读失败返回 `503 settings_unavailable`，不用空对象冒充。回读不一致返回 `500 advanced_unverified`。

秘密子键用公共 `secret_name` / `secret_key` 判断。掩码固定为 `***`，不把明文放进响应或日志。提交 `***`、`已设置`、`未设置`、空字符串，或直接删掉秘密键，都保留原值，不用掩码覆盖。`null` 才清除。非秘密键以提交对象为准，省略即删除。`masked-advanced` 的全部叶子都按秘密处理。列表里的掩码按索引合并；长度变化会丢掉秘密时拒绝，不写入。

写入只 `_set_nested` 这一个键，不替换整份配置。不写环境变量，不改 `os.environ`，不改 sticky。成功体保持 `saved: true`、`applied: false`、`processEnvChanged: false`、`hotLoaded: false`、`effectiveNow: false`、`loadedNow: false`、`processSwitched: false`。`restartRequired` 沿用 `effect_fields`，这 38 个键不是环境项，预期为 false。不能把 saved 当成当前进程已生效。

profile 以本进程目录为准：`profileHome`、`configPath`、`profileSource: process-home`。`runningProfile` 只由该目录派生。值为 `default` 只表示核心把这个目录判成默认家目录，不是从请求猜的。sticky 文件缺失或读失败时 `stickyProfile` 为 null，不补 `default`。配置文件不在本进程目录，或线程有冻结快照，返回 `409 profile_scope_unsafe`。

页面调用集成人已导出的 `getAdvanced` / `setAdvanced`，不另写一套请求。`setAdvanced` 会剥掉响应顶层的秘密字段名；开放对象的掩码值在 `value` 里，不在那些顶层字段。`settings-zh.json` 优先，缺项才读 supplement。开放对象编辑放在现有表单行下面，仍在「表单 / YAML 原文」之内。工具集、记忆提供方、连接也在这里，不新造顶层 tab。

## 缺口

- 路由还没接到 `admin_routes.py` / `admin_native.py`。本路没有改这两个文件。集成人需要把 GET/POST `/settings/advanced` 接到 `get_advanced` / `set_advanced`。客户端函数已经在 `admin-api.ts`，但没有注册时页面会显示服务端错误，不会把空对象当成成功。
- `ToolsSection`、`MemorySection`、`ConnectionsSection` 已由各自 owner 导出且无必填 props。配置页直接渲染，不再套一层同名标题。它们的接口仍要集成人注册；组件自己显示加载错误，本路不把空列表写成成功。
- 秘密启发式误锁仍由集成人的 `secret_key` 决定。本路没有复制 `panelFalseSecret` 例外名单。
- 开放对象的真实读取时机没有运行验证。响应不宣称下一会话或当前进程已生效。
- 面板上限：深度 8、节点 800、单字符串 8000、总体 256KiB、单层 200 键、列表 200 项。超过就拒绝，需要走 YAML 原文。这是面板安全边界，不是核心 schema 上限。
- 真实秘密如果恰好等于 `***`，提交掩码会保留原值，无法用这个字符串更换。
- 表单 schema 加载失败时，分组和开放对象仍留在表单视图里；首次加载中不显示。没有因此重排已有行。

## 未运行

未运行 `tests/clawd_settings_cases/test_advanced_contract.py`，未做构建、类型检查、import、CLI、浏览器或配置读写探针。准备好的用例不是通过。
