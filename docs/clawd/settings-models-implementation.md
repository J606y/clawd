# 模型端点实现

静态实现记录，不是验收通过，也不是上线许可。本路未运行测试、构建、import、产品 CLI 或网络探针，未读生产配置或 `.env`。

## 文件

- 已改：`/root/hermes-workspace/clawd/clawd_server/admin_models.py`
- 已改：`/root/hermes-workspace/clawd/apps/panel/src/react/pages/settings/models-tab.tsx`
- 新增未运行：`/root/hermes-workspace/clawd/tests/clawd_settings_cases/test_models_contract.py`
- 未改：`admin_routes.py`、`admin_native.py`、`admin_common.py`、`admin-api.ts`、`sidecar.ts`、`queries.ts`、`api.ts`、原面板、官方目录、`settings.css`

冻结导出：`admin_models.create_custom_endpoint(mod, body)`。请求体只接受 `{id, baseUrl, apiKey?, models}`。成功体含 `hasKey`，以及 `saved: true`、`applied/hotLoaded/effectiveNow/loadedNow/processEnvChanged/restartRequired` 全为 false。不返回密钥、`key_env` 或明文预览。

## 调用链

真实消费者读的是当前 profile 的 `providers` 映射，不是面板内存：

1. `hermes_cli/config_providers.py` 的 `get_compatible_custom_providers` 把 `providers` 和遗留 `custom_providers` 列表合成运行时视图，不写回配置。
2. `hermes_cli/runtime_provider_custom.py` 的 `_match_new_style_provider` 按 `base_url`/`url`/`api` 找端点，再用 `key_env`/`api_key_env` 调 `get_secret_str`。`_key_env_secret` 只在声明了变量名时读该 profile 的秘密范围。
3. `hermes_cli/config.py` 的 `custom_endpoint_key_env` 把端点 id 收成 `HERMES_CUSTOM_<ID>_API_KEY`。写入用 `save_env_value(..., publish=False)`，注释写明不改 `os.environ`。
4. 下一会话才读到密钥：`gateway/run.py` 在回合里 `set_secret_scope`，从 profile `.env` 装范围，不把密钥灌进进程环境。TUI 的 `model_switch.py` 同样按 profile home 装范围。
5. 隔离：`admin_common.assert_process_scope` 拒绝请求线程上的其他 home 覆盖，也拒绝面板目录与进程 `HERMES_HOME` 不一致。`create_custom_endpoint` 在写之前调用 `panel_home`。请求体没有 profile 字段；多出来的字段直接 400，避免靠 URL 猜归属。

因此新建条目写成 `providers.<id>.{name,base_url,models}`，密钥只进当前进程 profile 的 `.env`，配置里只留 `key_env` 名字。不写 `model.default`、`model.provider`、`model.api_key`。已存在同名或同别名（大小写、空格折成连字符，含遗留列表的 `name`/`provider_key`）返回 409 `endpoint_exists`，不覆盖。回读不一致则撤回配置项和本次环境写入，错误文案不含密钥。

GET `/models` 仍走已绑定的 `get_models`。每个提供方增加 `hasKey` 布尔或 `null`。明文 `api_key` 只用来判断非空，不放进响应。`key_env` 只用来看当前 profile 环境文件里该变量是否非空，值不回传。

面板表单直接 POST `panel-sidecar/models/custom-endpoints`，不改集成人的客户端类型。密码框初始为空，`autoComplete="new-password"`，成功后立刻清空，不从 `provider` 回填。已有卡片只显示「已设置 / 未设置 / 状态未知」。保存文案固定为未热加载；若响应把 `hotLoaded`、`applied`、`effectiveNow`、`loadedNow` 或 `processEnvChanged` 标成 true，页面不把它当成当前进程已生效。

模型名按原样写入。`MODEL_ID_RE` 允许点号和连字符，因此 `gemini-4-argon`、`gemini-4-argon-high`、`gemini-3.8-flash` 这类 id 不会被剥后缀。本页不调用 `canonicalModel`。Ultra 档动画在 `effort.ts` 与聊天样式，本路未改那些文件，也没有在模型页加入 effort 控件。

## 缺口

- 路由未注册。`admin_routes.py` 仍只有 profile 删除；侧车 POST 表没有 `/models/custom-endpoints`；`admin_native.py` 没有绑定 `create_custom_endpoint`。集成人应注册 `POST /models/custom-endpoints`，调用 `create_custom_endpoint(mod, body)`。在此之前表单会收到 404，页面只显示服务端错误，不假成功。
- `ProviderInfo` 仍无 `hasKey`。页面用额外字段读取。集成人补类型前，旧响应没有该字段时显示「状态未知」，不猜未设置。
- 现有 `models` 为字典的端点，列表芯片仍只认字符串列表。这次新建写列表，运行时两种都读。没有把字典展平成列表，避免编辑保存丢掉 per-model `context_length`。
- 契约没有 `api_mode`。只写 Chat Completions 默认能用的端点。Responses-only 或 Anthropic 兼容主机仍可能 404。没有加假的协议开关。
- `_shadowed_by_builtin`：id 与内置规范名相同时，裸名称不会选中这条自定义端点，`custom:<id>` 才会。没有新增「当前默认模型」入口去写 `model.provider`。
- 遗留 `custom_providers` 若不是列表，运行时会忽略，本路不改写它。列表里的同名别名会 409。
- `commit_set` 按键分次写，只持有本进程 `write_lock`。跨进程插写不是原子事务。
- 若日后密钥启发式把 `key_env` 叶子判为秘密，带密钥的创建会 403 并撤回，不会改把明文 `api_key` 写入 yaml。
- 进程未重载。磁盘上的 `get_models` 要等现有进程重新加载该模块后，GET 才会带 `hasKey`。本路不重启服务。
- 没有删除、更新或 reveal。冻结契约只有创建。

## 未运行

未执行 `tests/clawd_settings_cases/test_models_contract.py`，也未跑其他测试、构建、import、产品 CLI、浏览器或端点探针。静态用例只读上述两个源文件，不打开配置或 `.env`。
