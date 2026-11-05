# 面板设置与分页类型修复

静态修复和用例准备。未运行。没有执行本报告的用例，也没有执行测试、构建、类型检查、import、CLI、commit、push 或部署。准备好的用例不是通过。不是验收，也不是上线许可。

未删文件。未读生产凭据。未碰原面板、`/usr/local/lib/hermes-agent` 或 `/root/hermes-workspace/hermes-panel`。未改 `tsconfig`、依赖版本、`runs`、`api.ts`、聊天页、旧 `apps/panel/src/lib`。未用 `any`、`ts-ignore`、`ts-expect-error` 或 `!` 掩盖 `unknown` / `null`。

日志：`artifacts/final-acceptance/logs/panel-typecheck.log`。下面 6 处是该日志里的全部错误。行号是日志当时的位置；修复后行号会后移。

## 6 处错误与修正

| 日志 | 错误 | 修正 |
| --- | --- | --- |
| `admin-api.ts(418,94)` TS2345 | `getPluginConfig` 把未标注的 `request()`（`unknown`）直接交给 `<T extends object>` 的 `withoutSecrets`。泛型约束不是校验。 | `request<unknown>(...).then(readPluginConfigResult)`。guard 先核对对象、`name` / `key`、`fields` 数组和字段 schema，生效布尔必须已是布尔，秘密行有 `value` 就拒绝。缺 `fields` 抛 `plugin_config_invalid`，不编空数组。收窄后再 `withoutSecrets` 剥顶层秘密键。 |
| `sidecar.ts(532,7)` TS2322 | `page.total` 是 `number \| undefined`，不能赋给 `number \| null`。 | `assertSubagentPage` 改为 `asserts view is SubagentsView & { total: number }`。运行时本来就会在 `total` 不是数字时抛 `subagents_unavailable`，类型现在跟上这个事实。 |
| `sidecar.ts(533,11)` TS18047 | 赋值没收窄，`total > SUBAGENT_MAX_CHILDREN` 时 `total` 仍可能是 `null`。 | 用已证明的 `pageTotal: number` 做上界比较，通过后再写入 `total`。缺 total 仍是显式错误，不编 0。 |
| `sidecar.ts(551,21)` TS18047 | 循环后 `seen.size > total`，`total` 仍可能是 `null`。 | 比较前 `if (total === null) throw subagentUnavailable()`。不用 `!`。 |
| `config-tab.tsx(38,11)` TS2430 | 本地 `AdvancedView extends WriteFacts`，`stickyProfile` 含 `null`，和 `WriteFacts` 的 `string \| undefined` 冲突。 | 删掉本地接口。页面改用 `AdvancedRead`。 |
| `config-tab.tsx(110,25)` TS2345 | `savedNotice` 要 `WriteFacts`，开放对象视图传不进去。 | `AdvancedRead = EffectNoticeFacts & { value: PluginPublicValue; stickyProfile?: string \| null; ... }`。`EffectNoticeFacts = Omit<WriteFacts, "stickyProfile">`。`savedNotice` 只读生效布尔和说明，不读 sticky。文案未改：已保存仍是已保存，不改成当前生效。 |

分页缺 `offset` 时用本次请求的 offset（`pageOffsetFact`），不另编 0；回显不一致仍抛 `subagents_unavailable`。收齐视图的 `offset: 0` 是从 0 走完的事实，不是用 0 填一个缺失的 total。收齐超过 50、中途失败不回空列表、profile 不能证明时省略且不写 `default`，都保留。

`queries.ts` 未改。`usePluginConfig`、`useAdvanced`、`useSubagents` 的返回已经是 `PluginConfigResult`、`AdvancedRead`、`SubagentsView`，没有需要再推导的缺口。

开放对象界面布局、文案、掩码回退（`***` / 空字符串 / 删键保留原值）未改。`usage` 请求字节未改。

## 修改文件

- `apps/panel/src/react/lib/admin-api.ts`
- `apps/panel/src/react/lib/sidecar.ts`
- `apps/panel/src/react/pages/settings/config-tab.tsx`
- `apps/panel/tests/settings-wire-contracts.mjs`（只补了源码契约断言，未执行）
- `apps/panel/tests/settings-type-boundaries.mjs`（新增，覆盖未知响应、缺 total/offset、第 51 个 child、中途失败、掩码文案；未执行）
- 本报告

## 未运行

未运行 `settings-wire-contracts.mjs`、`settings-type-boundaries.mjs`、`subagent-pagination.mjs`。未运行 `scripts/run_tests.sh`、面板类型检查、构建、import、CLI、浏览器或 HTTP 探针。退出码未知。收齐后集中复测，本次不代替那次验收。
