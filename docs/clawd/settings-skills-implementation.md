# 技能启用开关实现

静态核对，未运行。没有执行测试、构建、`py_compile`、import 探针、CLI、浏览器或写配置。这不是验收通过，也不是上线许可。

## 改动

- `/root/hermes-workspace/clawd/clawd_server/admin_skills.py`：新导出 `set_skill_enabled(mod, name, enabled)`。没有 `install`，没有路由注册。
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/settings/skills-tab.tsx`：切换失败不改本地开关，显示服务端错误，并回读列表。未加安装按钮，未改布局、间距、动画和编辑/备份流程。
- `/root/hermes-workspace/clawd/tests/clawd_settings_cases/test_skills_contract.py`：只读源码断言。未运行。

未改路由、`admin-api.ts`、`sidecar.ts`、`queries.ts`、`api.ts`、侧车、`native.py`、`admin_native.py`。

## 导出行为

`set_skill_enabled` 在写锁内读当前 `skills.disabled`，启用时从名单去掉该名，停用时加入。名单非空则 `commit_set(mod, "skills.disabled", json.dumps(sorted(...)))`，空名单则 `commit_unset(..., missing_ok=True)`。随后再读原始配置，名字是否在名单里必须与请求相反，两次读取不一致则 `500 skill_toggle_unverified`。

成功体固定：

- `saved: true`
- `applied`、`processEnvChanged`、`hotLoaded`、`effectiveNow`、`loadedNow`、`restartRequired` 全为 `false`
- `note` 写明新对话才读文件，本进程不热加载，也不把未标明的 profile 当成 default

不返回 `skills.disabled` 名单，不读秘密，不写日志明文。无 `CLAWD_HOME`/`HERMES_HOME`、两者不一致、面板未绑定 `HERMES_HOME`、或目录落在生产 Hermes / `/usr/local/lib/hermes-agent`，返回 `409 profile_scope_unsafe`。不调用 `get_active_profile`，不写 `os.environ`。

技能必须出现在面板 `list_skills()` 的 `name` 里，否则 `404 skill_not_found`。目录函数缺失或读失败是 `503`，不用空名单假装成功。`hermes-agent` 等 `ESSENTIAL_SKILLS` 停用请求在写入前 `400 skill_required`。原名单不是文本名单时 `400 skill_disabled_unreadable`，不覆盖原值。不调用已拒绝的 CLI，不加技能库安装器，不返回 `501`。

## 接线证据

POST `/skills/enabled` 仍不进入新导出。

- 侧车路由：`apps/panel/sidecar/hermes_panel_sidecar.py:6054-6059` 调用模块级 `set_skill_enabled(name, enabled)`。同文件 `:877-888` 的原实现仍是 `_hermes("config", "set"|"unset", "skills.disabled", ...)`。
- `clawd_server/native.py:30` 在 `install()` 里把 `mod.set_skill_enabled` 换成 `native.py:76`。该函数用 `save_disabled_skills`，不是 `commit_set`/`commit_unset`，成功体也没有 `applied`/`effectiveNow`。
- `clawd_server/admin_native.py:16-47` 的 `REPLACED` 不含 `set_skill_enabled`。`install()` `:99-136` 替换了 `_hermes` 为 `501 cli_retired`，但没有改绑技能开关。`native.install` 先绑定技能函数，再调用 `install_admin`，所以完整安装后活路径仍是 `native.set_skill_enabled`。
- 面板请求仍是 `apps/panel/src/react/lib/sidecar.ts:448` 的 `POST skills/enabled`。类型文件不在本路，未改。页面只用 `ApiError.message`，不依赖新字段。

集成人应在 `admin_native.install` 里、且在 `native.install` 已经执行之后，改绑：

```python
mod.set_skill_enabled = lambda name, enabled: admin_skills.set_skill_enabled(mod, name, enabled)
```

只删 `native.py` 的绑定会让路由落回侧车 CLI；而 `_hermes` 已被换成 `501`。必须改绑到本导出，不能停在“函数存在”。

## UI

`flip` 不再 `setQueryData`。开关的 `checked` 仍来自 `skills.data`。请求失败时用 `errText` 显示服务端 `message`，`Notice` 的 `bad` 为真，并 `skills.refetch()`；回读失败不覆盖已显示的服务端错误。成功后先回读列表，再提示“已保存…新对话生效。本进程未热加载”。不把保存画成本进程已生效。编辑、备份、冲突重载、搜索和 `skills-root` / `card list` / `editor` 保留。

源码已改，面板未构建。正在运行的页面不会因此更新。

## 缺口

1. 路由未接。见上。本路不能改 `admin_native.py` / `admin_routes.py`。
2. 即便接上，`commit_set` 当前也会拒绝该键。`hermes_cli/config_defaults.py` 的 `skills` 段（约 `:1435-1479`）没有 `disabled`。`hermes_cli/config_runtime_keys.py` 的 `CORE_RUNTIME_KEYS` 也没有 `skills.disabled`。`_validate_schema_key` 对已填充段的未知子键返回未知，`commit_set` 因而 `400 unknown_key`，不会写入。运行时读者（`agent/skill_utils.py` 的 `skills.disabled`、`hermes_cli/skills_config.py`）已经读这个键。建议授权后把 `"skills.disabled"` 加进 `CORE_RUNTIME_KEYS`，不要为了过校验去种一个空默认列表。本路未改这些文件。
3. 不写 `skills.platform_disabled`。面板列表只看全局 `skills.disabled`。
4. 技能库安装仍缺。本批明确不加安装器。
5. 用例未运行。`tests/clawd_settings_cases/test_skills_contract.py` 只准备了源码断言。

## 未运行

未运行测试、构建、import、CLI、写配置或部署。
