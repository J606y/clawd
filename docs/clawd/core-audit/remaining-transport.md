# 剩余传输与上下文复核

纯静态。未运行测试、构建、导入、探针，未改产品，未复现。操作者配置的 `base_url` 指向本机不算 SSRF。不与已确认的 Anthropic 重定向项重复计。

哈希见同目录 `remaining-transport.json` 的 `meta.files`。写报告后再核一次源码哈希，无漂移。

## 计数

| 状态 | 数 | ID |
|---|---|---|
| 仍成立 | 8 | ctx-positional, prov-drip, prov-pool, prov-cancel, prov-url, prov-retry, prov-finish-error, prov-jitter |
| 误报 | 1 | ctx-skill-id |
| 需验证 | 1 | prov-name |
| 已修复 | 0 | |

`prov-jitter` 的抖动行与 `prov-retry` 是同一处，修一处即可，不要把低危再加进叠乘的严重度。`prov-url` 只计构造失败后的重定向回落，不计本机 URL。

## 逐项

### ctx-positional 仍成立 / 中 / 确定

`sanitize_api_messages` 无条件做位置配对。`user` 行先补 stub 再清空待配对表，后面的真实 tool 结果被丢掉。压缩侧用全局 id，会保留这份结果；下一次 API 副本再换成 stub。两个调用点都没有 provider 门闩：`turn_request_assembly.py:157`，`chat_completion_helpers.py:2169`。原报告“往旧列表追加”半句未找到调用点，不计入。

### ctx-skill-id 误报 / 确定

查找函数只读 `id`。当前 Codex 写入把存储 `id` 和结果 `tool_call_id` 设成同一个配对 `call_id`。事件投影同样同值。`[SKILL_PRUNED]` 会命中。未读生产历史库。

### prov-drip 仍成立 / 中 / 确定

不沿用原报告的高。共享客户端 `read=None`；流式另设读超时，默认 120 秒，静默更大时抬到静默值。每个 chunk 和进度帧都刷新静默钟。`stream_max_lifetime` 默认 0，看门狗直接返回。间隔小于这两层的滴流没有总截止。这是占线程和连接，不是已证明的无限账单。

### prov-pool 仍成立 / 中 / 确定

超过 32 槽后新建的传输不进字典，却被 `close` 不关内部池的视图包住。`close_shared_transports` 看不见它。httpx 的 `HTTPTransport.close` 存在，超限路径没有调用者。httpx/httpcore 无 `__del__`。不把“必漏到进程退出”写成已证明。

### prov-cancel 仍成立 / 中 / 确定

取消标志在清扫前设真，找不到套接字时是警告，不是记成成功。流式在响应头后会再读标志并补中止，所以“标志已被消费”不成立。计入的是非流工作线程：清扫一次，没有头后补中止，也不钉 `read=stale`。调用方离开后线程仍可能读。`tcp_force_closed=0` 的频率未测。

### prov-url 仍成立 / 低 / 确定

本机、元数据、`file://` 不升格。计入的是 keepalive 构造任何异常都返回 `None`，随后 OpenAI SDK 默认客户端 `follow_redirects=True`。注入成功的客户端不跟随。跨源只剥 `Authorization`。失败频率未测，路径确定。不与 `prov-redirect` 重复。

### prov-retry 仍成立 / 中 / 确定

纯运算：外层默认 3 次尝试；每次传输类流失败最多 3 次 POST，加上兼容重试为 12。5xx 不进流内重试，60 秒内最多一次非流探测。`Retry-After` 帽在 600 秒后仍重试，但循环会在次数用尽时停，不是无限每 10 分钟。Z.AI 天花板 8 是有意的。上游是否已计费或执行托管工具，缺协议证据。

### prov-name 需验证

装配分支在：name 覆盖、`index is None` 当 0、非字符串 arguments 会在 `join` 处失败。工作区 SDK 要求 `index: int`，`arguments` 为可选字符串。经模型校验的官方 chunk 到不了缺 index。缺一份已支持端点的原始分片，证明这些形状会进入 `feed()`。

### prov-finish-error 仍成立 / 中 / 确定

错误文本只在 `finish_reason` 为 `error` 或 `error_finish` 时提升。`stop` 会装成成功回答。空 choices 加 `error_type` 另有提升。路由超时只认一个精确句子。其它代理是否发这种正文，不改变门闩。

### prov-jitter 仍成立 / 低 / 确定

流内 `jitter_ratio=0.0`，附加抖动为 0。与 `prov-retry` 同一行，不另计。配置 `api_max_retries: 0` 会被抬成 1，循环只跑一次尝试，不是关不掉的额外重试。原报告这半句不成立。
