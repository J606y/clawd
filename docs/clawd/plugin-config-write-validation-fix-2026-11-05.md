# 插件配置写回校验

静态修复和用例准备。未运行。没有执行测试、构建、类型检查、import、CLI、浏览器或 HTTP 探针。没有 commit、push、deploy。准备好的用例不是通过，也不是验收或上线许可。

未删文件。未读生产凭据、`/root/.hermes`、原面板或 `/usr/local/lib/hermes-agent`。未改 `sidecar.ts`、`queries.ts`、`config-tab.tsx`、Python、核心、路由和其它测试。未用 `any`、`as PluginConfigResult`、`!`、`ts-ignore` 绕过。

## 真实 PUT schema

客户端打 `panel-sidecar/plugins/config`。sidecar 原 `_route` 没有这条路径；`clawd_server.native.install` → `admin_native.install` → `admin_routes.install` 包住 `Handler._route`。未挂上时 `native.install` 抛错，不是回 `{}`。

`PUT /plugins/config` 不是仅 ack。`set_plugin_config` 成功体是：

- `ok: true`，`saved: true`
- `name`、`key` 非空字符串
- `written`：已写键的字符串数组
- `fields`：`_public_fields` 字段数组。秘密行只有 `valueSet` 和合法 `env`，没有 `value` / `default`
- `_not_loaded()`：`applied`、`processEnvChanged`、`hotLoaded`、`effectiveNow`、`loadedNow`、`restartRequired` 都是 false
- `note`：已写入配置档，本进程没有加载或热加载
- 没有 `managed`。那是 GET 字段

`_write` 再经 `ensure_write_facts`：缺的生效布尔补 false，不把已有 `saved: true` 改掉；缺 `ok` 才补 false。本函数已带这些字段，只多一个 `processSwitched: false`。路由 `_drop` 只删顶层 `token` / `password` / `apiKey` / `api_key` / `secret`，不扫字段数组。缺 `loadedNow` 时路由补 false；这里已经有。

GET 同为字段数组，但 `saved: false`，带 `managed`，没有 `ok` / `written`，不走 `ensure_write_facts`。空清单可以是 `fields: []`，这是真空 schema，不是缺字段。

## 页面消费

`plugins-tab.tsx` `saveConfig` 把 `setPluginConfig` 的返回值同时交给 `pluginConfigNotice` 和 `asConfig`。提示要 `saved === true`、`ok !== false`、`loadedNow === false`，且加载类布尔不是 true；有 `note` 就用 `note`。`asConfig` 要 `name`、`key` 和字段数组。

因此不能把 `{ok, saved}` 当成配置，也不能在 PUT 后再 GET。GET 固定 `saved: false`，页面会显示「没有确认已保存」。PUT 已带回读后的 `fields`，直接校验即可。

`queries.ts` 的 `useSetPluginConfig` 也调用同一函数，本次未改该文件。页面本身是直接调用。

## 改动

`setPluginConfig` 改为 `request<unknown>(...).then(readPluginConfigResult)`。空 HTTP 体在 `request` 里是 `undefined`，不再经 `{...undefined}` 变成 `{}`。缺 `fields`、`fields` 非数组、秘密行带 `value`、生效布尔不是布尔，都抛 `plugin_config_invalid`。行级拒绝在前，顶层 `withoutSecrets` 仍在 guard 末尾，不能代替行级拒绝。

不编 `fields: []`，不把布尔收成 true。显式 `fields: []` 仍接受，否则无 schema 的 GET 会被改掉。GET 函数和 `savedNotice` 文案未改：有说明用说明，没有说明仍是「已保存。applied 为 false，本进程未生效。」，不改成当前生效。

## 用例准备

`settings-wire-contracts.mjs` 切开 `setPluginConfig`，要求这条 return，并拒绝 `request<PluginConfigResult>`、`.then(withoutSecrets)`、函数内再 GET。GET 切片仍走 `request<unknown>`。

`settings-type-boundaries.mjs` 同步该切片。运行断言准备覆盖：`undefined`、`{}`、仅 ack、缺 `fields`、`fields` 对象、秘密行非空 `value`（无顶层秘密键）、`applied` 非布尔、以及带 `written` / 秘密行 / 公开值和顶层 `apiKey` 的完整 PUT 体。成功路径只打一次 PUT，不带回读，顶层秘密去掉，行内明文不出现，`saved` 保持 true，生效布尔保持 false。

未执行这两份用例。退出码未知。
