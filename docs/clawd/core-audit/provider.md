# Provider 传输审查

审查模型：grok-4.7（与任务要求一致，无模型不符）。
范围：默认与自定义 OpenAI 兼容路径，以及已支持的 Anthropic Messages、Codex Responses、Bedrock Converse 的客户端、传输、流、重试、关闭与错误脱敏。不含 effort 服务、UI 档位、凭据运维与上下文压缩。
方法：只读源码。未运行测试、构建、网络或模型探针。未读真实 `.env` / `config` / `auth`，未读 `/root/.hermes` 与官方安装目录。SDK 接口只读工作区 `.venv`（openai 2.54.0、httpx 0.28.1、anthropic 0.125.0）。boto3 未安装，Bedrock SDK 行为标未知。
基线：`HEAD` = `c314285f077e91937c995d419b9fa40af9eb4981`。下列审查文件相对该提交无 diff。工作区另有与本审查无关的改动（`agent/agent_init.py` 增加项目上下文挂接，约 +83 行），不改变传输路径。

## 中文摘要

模型调用的信任边界有三层。操作者配置的 `base_url` 与密钥被当作可信目的地，代码不拦截内网或元数据地址，因为本地端点是有意支持的。提供方响应是不可信输入：错误正文、SSE 分片和 tool 参数都可能不完整或夹带密钥。第三层是 SDK 默认值。主 OpenAI 客户端把 `max_retries` 设为 0，并把重试交给外层循环；但 Anthropic 客户端没有注入自己的 httpx 客户端，SDK 默认跟随重定向，而 httpx 只剥离 `Authorization`，不剥离 `X-Api-Key`。未知 provider 或未注册 transport 会被改写成 `chat_completions` 再发请求，端点能通不等于协议兼容。

超时不是单一总预算。共享 httpx 客户端的 read 默认为无限；流式请求会覆盖读超时，静默看门狗默认云端 180 秒、本地 900 秒，但任何 chunk 都会重置静默计时。`stream_max_lifetime` 默认关闭，所以慢速滴流可以一直占着连接和账单。取消靠标志加跨线程 `shutdown`，不把取消令牌交给 SDK。连接尚未出现套接字时，中止可能关到 0 个套接字，工作线程继续阻塞；内联调用不能丢弃线程。

最严重的代码缺陷是：流在已有 `finish_reason` 时会把截断的 tool 参数补括号修成合法 JSON，校验层只拒绝仍然非法的参数，于是缩短后的命令或路径会被执行。无 `finish_reason` 的半截调用不会执行，这条保护没有覆盖“有结束原因但参数为空或被修复”的情况。错误摘要的常见 SDK body 路径不脱敏，日志会打出完整异常，密钥掩码还保留前 8 后 4 位。流内重试、外层 `api_max_retries`（默认 3）和 5xx 非流探测会叠乘同一请求。本地工具在流中途重试前尚未执行，但上游若已接受请求，副作用是否重复属于协议不确定。本次未运行测试。

## 结构与信任边界

```
会话循环 conversation_loop
  -> 请求级客户端 client_lifecycle（OpenAI max_retries=0；Anthropic 另建）
       -> process_bootstrap.build_keepalive_http_client（共享连接池视图）
       -> 或 anthropic_adapter.build_anthropic_client（无自有 httpx，SDK 默认客户端）
       -> 或 bedrock_adapter（boto3，本环境未安装）
  -> transports/* 只做消息/工具转换与响应归一化，不拥有超时、取消、重试
  -> chat_completion_helpers._StreamingCall 消费 SSE
  -> error_classifier + turn_recovery 决定 429/5xx 是否再睡再发
```

| 边界 | 谁产生 | 代码是否当不可信 | 缺口 |
|---|---|---|---|
| 操作者 `base_url` / 密钥 | 配置解析，本审查未读真实配置 | 当作可信目的地 | 无内网/元数据拒绝；未知 transport 仍发密钥 |
| 提供方 SSE / 错误正文 | 上游或中间代理 | 部分脱敏、部分当成功正文 | body 字典路径不脱敏；`finish_reason=stop` 的错误正文可当回答 |
| tool 参数分片 | 上游流 | 无结束原因时不执行 | 有结束原因时修复后执行 |
| SDK 默认 | openai/anthropic/httpx | 主路径覆盖了重试次数 | Anthropic 跟随重定向；OpenAI 客户端构造失败时回落 SDK 默认客户端 |

已核对、不列为缺陷的行为：主 OpenAI 与辅助客户端 `max_retries=0`（`agent/agent_runtime_helpers.py:2040`，`agent/auxiliary_client.py:203`）；请求级客户端由属主线程关闭，陌生线程只 `shutdown`（`agent/client_lifecycle.py:407-411`）；流中途本地工具尚未执行（`agent/stream_delivery.py:398-400` 只通知界面）；元数据探测跨源会去掉 `Authorization`（`agent/model_metadata_http.py:38-41`）；Z.AI 过载会抬高重试上限（`agent/turn_recovery.py:1937-1938`）。

## 代码缺陷

按严重程度排序。修复示例只写在报告里，未改产品代码。

### 高 — 有结束原因的截断 tool 参数被修复后执行

- 位置：`agent/chat_completion_helpers.py:3259-3273`，`agent/message_sanitization.py:253-270`，`agent/turn_tool_validation.py:170-236`
- 置信度：确定（代码路径）；官方模型是否经常在 `length` 时给出可闭合前缀，属触发频率，不影响缺陷成立
- 描述：`finish_reason is None` 时，非法参数标成截断且不修复。一旦上游给了 `length` / `stop` / `tool_calls`，同一函数会调用 `_repair_tool_call_arguments` 补上未闭合的引号和括号。修复结果不是 `{}` 就写回参数，`has_truncated_tool_args` 保持假。校验层只在 `json.loads` 仍失败、且结尾不是 `}` / `]` 时拒绝执行（`turn_tool_validation.py:178-203`）。补全后的 JSON 能通过校验，随后 `turn_tool_round.py:171` 执行。
- 触发：输出上限或代理在 tool 参数写到一半时附上结束原因。例如参数停在 `{"command":"rm -rf /tmp/foo`，修复后变成带缩短命令的对象并执行。无结束原因的干净 EOF 会走 stub，不执行（`chat_completion_helpers.py:3313-3324`），两条路径不一致。
- 影响：非幂等工具（删文件、发消息、改配置）按缩短后的参数产生真实副作用。外层截断重试最多 4 次（`agent/turn_truncation.py:411`），修复成功的那次不会进入该拒绝路径。
- 最小修复：

```python
# chat_completion_helpers._assemble_tool_calls
except json.JSONDecodeError:
    has_truncated_tool_args = True  # 无论有无 finish_reason，都不把前缀修成可执行参数
```

未来用例：流以 `finish_reason=length` 结束，参数为未闭合 JSON；断言不调用工具，且消息里的 arguments 仍是原始前缀。

### 高 — 有结束原因但参数为空时按 `{}` 执行

- 位置：`agent/chat_completion_helpers.py:3282-3285`，`agent/turn_tool_validation.py:167-168`
- 置信度：确定（空参数如何被放行）；哪些网关先发 name 和 finish、后丢 arguments，需对照该网关流，标为触发条件需验证
- 描述：注释写明“名为到、参数字节为 0、且没有 finish_reason”会误执行 `{}`，因此只在 `finish_reason is None` 时标截断。有结束原因时参数保持空字符串，校验层把空字符串改成 `{}` 并继续。
- 触发：兼容端点把 tool 名和 `finish_reason=tool_calls` 放在同一 chunk，arguments 分片丢失或为空。
- 影响：无参工具被当成一次合法调用执行。
- 最小修复：参数去空白后为空时，无论 `finish_reason` 为何都设 `has_truncated_tool_args = True`，不要在校验层把空串升级成可执行对象。

### 高 — Anthropic 兼容端点重定向会带走 `X-Api-Key`

- 位置：`agent/anthropic_adapter.py:385-391`；SDK `anthropic/_base_client.py:909`（`follow_redirects` 默认 True）；`anthropic/_client.py:366` 写入 `X-Api-Key`；httpx 0.28.1 `_client.py:552-556` 跨源只删除 `Authorization`
- 置信度：确定（本地 venv 源码，未发请求）
- 描述：`build_anthropic_client` 不注入自有 httpx 客户端，因此使用 SDK 默认客户端并跟随重定向。httpx 对跨源重定向剥离 `Authorization`，但不剥离 `X-Api-Key`。307/308 还会保留方法和正文。主 OpenAI 路径注入的客户端未设置 `follow_redirects`，httpx 默认 False，这条不适用于正常 OpenAI 注入路径。OpenAI 客户端构造失败时 `build_keepalive_http_client` 返回 None（`process_bootstrap.py:330-331`），会回落 SDK `DefaultHttpxClient`，其 `follow_redirects=True`（`openai/_base_client.py:873`）。自定义头里的密钥同样不会被 httpx 剥离。
- 触发：配置的 Anthropic 兼容 `base_url`，或 OpenAI 客户端构造失败后的回落客户端，收到指向其他源的 301/302/307/308。密钥值不得写入日志，下文用 `[REDACTED]`。
- 影响：`X-Api-Key: [REDACTED]` 被送到重定向目标。操作者配置的 URL 本身是信任边界，但“端点返回重定向”不是操作者显式授权的第二次目的地。
- 最小修复：

```python
kwargs["http_client"] = httpx.Client(
    timeout=_client_timeout(timeout),
    follow_redirects=False,
    trust_env=False,
)
```

OpenAI 回落路径同样禁止使用带 `follow_redirects=True` 的默认客户端。未来用例：本地 httpx mock 对 `/v1/messages` 返回 307 到另一 host，断言出站请求不含 `X-Api-Key` 且默认不跟随。

### 高 — 未知 provider 被改写成 chat_completions 后仍发密钥

- 位置：`hermes_cli/providers.py:473-482`
- 置信度：确定
- 描述：已知 provider 的 transport 若不在 `TRANSPORT_TO_API_MODE` 且未注册到 transport 表，函数返回 `chat_completions` 而不是拒绝。完全未知的 provider 名也落到同一返回。`get_transport` 对未知 `api_mode` 返回 None 并允许调用方走旧路径（`agent/transports/__init__.py:26-32`）。注释承认未注册方言曾经被静默改写。注册表修复没有覆盖“未知名字”。
- 触发：配置或插件给出未注册 transport / 未知 provider，同时带了 `base_url` 和密钥。`/v1/models` 或一次 200 不能证明工具流、结束原因和错误 chunk 兼容。
- 影响：密钥发往按 OpenAI Chat 解释的端点；协议不匹配时错误被当成可重试或可执行的 tool 调用。
- 最小修复：未注册 transport 返回显式错误，不要默认 `chat_completions`。自定义端点只有操作者显式写出 `api_mode` 才允许发请求。

### 高 — 慢速滴流没有总时限，连接与账单可一直占住

- 位置：`agent/process_bootstrap.py:292`；`agent/chat_completion_helpers.py:2896-2915`；`agent/chat_completion_stream_monitor.py:208-214`
- 置信度：确定
- 描述：共享客户端 `read=None`。流式请求会设置读超时（默认 `HERMES_STREAM_READ_TIMEOUT` 120 秒，本地或推理静默更大时抬到 stale/1800）。静默看门狗默认云端 180 秒、本地 900 秒，但每个真实 chunk 都刷新 `last_chunk_time`。`stream_max_lifetime` 默认关闭。因此每 119 秒一个字节的流既不触发读超时，也不触发静默终止，也没有总截止。显式 `stale_timeout_seconds` 或 `HERMES_STREAM_STALE_TIMEOUT` 还不吃 run-budget 上限（`chat_completion_helpers.py:690-700`）。
- 触发：上游或代理保持连接并偶尔发送空 delta、usage 帧或进度帧。内联非流路径有 stale 硬超时（`chat_completion_helpers.py:996-1000`）；工作线程非流路径只靠静默看门狗，abort 找不到套接字时没有同一层 read 硬超时。
- 影响：一轮可以无限占用工作线程、连接池槽和上游生成费用。取消若发生在套接字出现之前，见下文中等问题，滴流不会自行结束。
- 最小修复：给每次逻辑请求一个默认开启的总时限（连接+读+流内重试），与静默超时分开；进度帧可以刷新静默计时，但不能刷新总时限。

### 高 — 用户可见错误的常见路径不脱敏

- 位置：`agent/api_error_summary.py:217-221` 与 `248`；`agent/turn_recovery.py:1508-1511`；`agent/api_error_summary.py:257-259`；`agent/chat_completion_helpers.py:351`
- 置信度：确定（调用缺失）；上游正文是否回显密钥为可能，但同文件 223-227 行已把该风险写成相邻路径必须脱敏的理由
- 描述：`response.text` 路径会 `redact_sensitive_text`。SDK `error.body` 为 dict 时，221 行直接返回 `message` 前 300 字，不脱敏。248 行回落同样不脱敏。重试日志把完整异常格式化进 warning。`_mask_api_key_for_logs` 保留前 8 与后 4 字符。流错误的 `raw_text` 在 `_provider_stream_error_from_text` 中未脱敏，而 JSON 解码路径有脱敏（263 行）。
- 触发：代理或提供方在 `error.message` 中回显 `Authorization` / `x-api-key` / 请求 JSON。用户可见摘要和日志都会带上 `[REDACTED]` 本应被替换的原文。
- 最小修复：221 与 248 行返回前调用 `redact_sensitive_text`；日志只记 `type(api_error).__name__` 与状态码；掩码改为全 `***`；`raw_text` 入库前脱敏。

### 中 — 共享连接池满后新建的传输关不掉

- 位置：`agent/process_bootstrap.py:33`、`247-254`、`223-226`
- 置信度：确定
- 描述：`_SHARED_TRANSPORTS_MAX` 为 32。锁内构造传输，超过上限则不放入字典。`_SharedTransport.close` 只把视图标关闭，不关闭内部池。未登记的池没有别的关闭入口，`close_shared_transports` 也看不到它。
- 触发：超过 32 种 `(verify, proxy, codex)` 身份，或大量不同 `SSLContext` 对象（按 `id` 区分）。
- 影响：进程寿命内泄漏连接池和文件描述符。客户端 `close()` 返回成功，资源仍在。
- 最小修复：超限时不要包进不关闭内部池的视图；要么拒绝新身份，要么让该视图的 `close()` 关闭它独占的内部传输。

### 中 — 取消在套接字出现前丢失，内联调用不能丢弃线程

- 位置：`agent/client_lifecycle.py:428-436`；`agent/chat_completion_helpers.py:2967-2984`；`agent/chat_completion_stream_monitor.py:72-80`
- 置信度：确定（控制流）；`tcp_force_closed=0` 的实际频率需在集成环境验证，本审查未运行
- 描述：陌生线程禁止 `close()`，只 `shutdown`。找不到套接字时打警告并返回，工作线程仍阻塞在连接或读上。响应头到达后有补中止（`_reabort_if_cancelled`），覆盖的是 connect 赢了竞态之后，不覆盖 DNS/连接尚未产生套接字的窗口。同一尝试被杀 3 次仍不返回时，守护线程被抛弃，提供方可能继续生成；内联调用不能抛弃，只是停止报告活性，等不活动看门狗。看门狗结束回合不等于套接字已关闭。
- 触发：`/stop` 或静默杀死发生在 `create()` 尚未登记套接字时，且客户端 read 为 None。
- 影响：取消不传播到上游；账单和连接继续。工作线程非流路径没有内联路径那种 read=stale 硬超时背书。
- 最小修复：每次请求把取消标志传给可中断的等待；abort 返回 0 时对工作线程路径也设置与内联相同的硬 read 超时。不要在找不到套接字时把中止记成已完成。

### 中 — 模型客户端不拒绝危险 URL，失败回落会跟随重定向

- 位置：`agent/auxiliary_client.py:2829-2843`；`plugins/model-providers/custom/__init__.py:86-92`；`agent/process_bootstrap.py:330-331`
- 置信度：确定（校验范围）；OpenAI 注入客户端在构造成功时不跟随重定向（httpx 默认 False，`FinalRequestOptions.follow_redirects` 默认 None，不会覆盖）。构造失败回落为需验证的线上频率，但代码路径确定
- 描述：`_validate_base_url` 只对 `http`/`https` 读端口，用来抓非法端口。`file://`、`http://169.254.169.254`、无 scheme 字符串都通过。自定义 `fetch_models` 会带着密钥访问该 URL。这与“操作者故意指向本机”相容，因此不把本机访问本身当成缺陷。缺陷是：没有把链接本地、云元数据、非 http(s) scheme 与“显式允许的本地端点”分开；客户端构造失败后的 SDK 默认客户端会跟随重定向。元数据探测路径有跨源去凭证（`model_metadata_http.py:38-41`），聊天主路径没有同等钩子。
- 触发：配置或插件把 `base_url` 指到元数据地址，或注入客户端构造抛错后 SDK 默认客户端跟随 307。
- 最小修复：允许的本地端点用显式主机名单；其余拒绝链路本地与非 http(s)。构造失败时失败关闭，不要换成 `follow_redirects=True` 的默认客户端。

### 中 — 重试预算叠乘，已接受的请求可能再发

- 位置：`agent/chat_completion_helpers.py:3721-3728`、`3642-3681`；`agent/agent_init.py:1429`；`agent/turn_api_error.py:152`；`agent/turn_recovery.py:1449-1473`
- 置信度：确定（次数）；上游是否已开始计费或执行托管工具，需验证，见文末协议不确定
- 描述：流内 `HERMES_STREAM_RETRIES` 默认 2，另加一次 `stream_options` 兼容重试。外层 `api_max_retries` 默认 3，且 `max(..., 1)` 不能关成 0。流上的 5xx 在无 delta 时每 60 秒再发一次非流探测。429/5xx 的 `Retry-After` 被帽在 600 秒（`retry_utils.py:182`）后仍重试，不会等到窗口结束。流内退避 `jitter_ratio=0.0`（3509 行），同时失败的会话会齐步重试。Z.AI GLM-5.2 过载会把上限抬到 8，这是有意行为，不是缺口。
- 触发：网关对已接受的请求返回 500，或连接在提供方开始生成后断开。本地工具尚未执行，所以不是本地工具双执行；上游托管工具或计费可能已经开始。
- 影响：同一用户回合可以对同一正文发出数次 POST。600 秒帽使“请 2 小时后再试”变成每 10 分钟打一次。
- 最小修复：5xx 探测与流内重试计入外层预算；`Retry-After` 大于剩余预算时停止而不是截成 600 秒再打；流内退避恢复抖动。无幂等键时，连接在首字节之后断开不得自动重发，除非确认上游未接受。

### 中 — tool 名覆盖与 index 缺失会合并或截断调用

- 位置：`agent/chat_completion_helpers_tool_calls.py:69-71`、`95-100`、`28`
- 置信度：代码行为确定；哪些已支持端点会分片 name、省略 index 或发送非字符串 arguments，需验证，不能从“端点能聊天”推出
- 描述：name 用赋值不用拼接，避免 MiniMax 重复全名。若端点把 name 分成 `read` + `_file`，结果只剩最后一片。`index is None` 时当成 0，没有不同 id 且不满足“新调用”条件的并行调用会并进同一槽。`arguments` 非字符串（对象、数字 0）时，真值对象被 append，`"".join` 在 `materialize` 抛 `TypeError`；假值 `0` 被丢掉。
- 触发：自定义 OpenAI 兼容网关不完全按 OpenAI 分片规则发送。
- 影响：错误工具名被执行，或两个调用合成一个，或整段流因类型错误失败后进入重试。
- 最小修复：name 只在空槽赋值，后续非空且不同的片段视为协议错误并中止该调用，不要覆盖。`index is None` 且 id 变化时开新槽。arguments 非 `str` 时不要 join，标为截断并不执行。

### 中 — 只有特定 finish_reason 才把流内错误当失败

- 位置：`agent/chat_completion_helpers.py:61`、`338-367`；`agent/transports/chat_completions.py:119-155`
- 置信度：门闩确定；除精确字符串 `Connect timeout, please try again later.` 以外，哪些代理用 `finish_reason=stop` 夹带错误，需验证
- 描述：流内错误文本只在 `finish_reason` 为 `error` 或 `error_finish` 时提升为异常。空 `choices` 加 `error_type` 会提升（2918-2932），这覆盖了 DeepInfra 形状。`lastOne=true` 会把缺结束原因合成 `stop`（2939-2940）。路由超时 shim 只认一个精确句子。其他 HTTP 200 加错误正文会被当成成功回答。
- 触发：代理把超时或 502 写进 assistant content，并给 `finish_reason=stop`。
- 影响：失败被写入会话并可能触发下一轮工具调用。未知端点不能因为返回 200 就当协议兼容。
- 最小修复：content 含 `event: error` 或错误形状对象时，即使 finish_reason 是 `stop` 也提升为 `ProviderStreamError`。不要只用一个英文句子识别路由失败。

### 低 — 流内退避无抖动；重试次数不能关

- 位置：`agent/chat_completion_helpers.py:3508-3509`；`agent/agent_init.py:1429`
- 置信度：确定
- 描述：流重连等待是 1/2/4 秒且 `jitter_ratio=0`。`api_max_retries` 至少为 1。
- 触发：多会话同时遇到同一 429。
- 影响：重试齐步打到同一窗口。不能用配置把传输重试降到 0。
- 最小修复：流内调用已有的 `jittered_backoff` 默认抖动；允许显式 0。

## 上游协议不确定

以下不是已证实的代码写错，缺本地证据，不能当成兼容成功。

- boto3 未安装。Bedrock 的连接/读超时、取消是否传到 botocore、流事件里的错误 chunk 形状，只看到适配器把 `converse_stream` 交给 boto3（`chat_completion_helpers.py:731-736`）。SDK 行为未知。
- 官方 OpenAI 是否保证 name 只在首 chunk 全量出现、index 必填、arguments 必为字符串：本仓库注释按这个假设写，未对照 2.54.0 的服务端协议文档做现场流。自定义端点更不能继承该假设。
- 提供方托管工具（服务端 web_search、代码解释器）在 HTTP 500 或连接断开前是否已经执行：本地 `_fire_tool_gen_started` 不执行工具，这只证明本进程没执行。上游副作用未知。
- `Retry-After` 的 HTTP-date 与正文 `retry_after` 是否被所有代理保留：解析器存在（`retry_utils.py:30-64`），代理是否转发未知。
- Gemini `thought_signature` 缺失会 400，这是协议约束。传输层会保存 `extra_content`（`chat_completion_helpers_tool_calls.py:104-109`）。签名是否在每次分片都出现，未用官方响应样本验证。
- 端点 `/v1/models` 或一次聊天 200 不能证明：tool 参数分片、错误 chunk、`finish_reason`、重定向和取消语义兼容。`determine_api_mode` 的默认成功与此冲突，已列入代码缺陷。

## 超时、取消、关闭对照

| 项 | 主 OpenAI 流 | 内联非流 | Anthropic | 未知/自定义 |
|---|---|---|---|---|
| connect | 请求级 30 秒，或配置值帽 60 秒；客户端默认 15 秒 | 与 stale 帽 60 秒 | SDK 客户端 connect 10 秒 | 未注册则按 chat_completions |
| read | 默认 120 秒，本地/推理可抬到 stale 或 1800 | 内联钉死为 stale；工作线程路径未见同等钉死 | 配置值，否则 900 秒 | 同 OpenAI |
| 总时限 | 默认无；`stream_max_lifetime` 关闭 | stale 墙钟 | 无跨重试总预算 | 同左 |
| 取消 | 标志 + shutdown；套接字未出现时可能落空 | 同时有硬 timeout | 依赖 SDK 客户端，无自有取消令牌 | 同 OpenAI |
| 关闭 | 成功复用请求客户端；失败真关闭。共享池视图 close 不关池 | 同左 | `client.close()`，陌生线程只 shutdown | 构造失败可能留下 SDK 默认客户端 |
| 429/5xx | SDK 不重试；外层按分类睡，帽 600 秒 | 同左 | `max_retries=0`，外层负责 | 若被改写成 chat，同样重试 |

## 审查文件与基线

相对 `c314285f077e91937c995d419b9fa40af9eb4981`，下表文件 `git diff` 为空。SHA-256 为审查时工作区内容。

| 文件 | SHA-256 |
|---|---|
| agent/transports/base.py | a6cfd8f58ac16dcdef7b2098c5a8254472a050b06a8c4dac8cc5f2186c0b1d02 |
| agent/transports/__init__.py | 0c00fe21466cc3d49bc213ab3b5c9109e8d1a64592ddbbb79e21dec89f27cef3 |
| agent/transports/types.py | 208098a093cf7243e2168681ad3d0ce8eafecdee65e0a6e19839fcdfbf4e3fa4 |
| agent/transports/chat_completions.py | 52b9cc97f9a5f1717ea9377fa46a96f4fa608720b4853569b3a307ce5671b913 |
| agent/transports/anthropic.py | 8dbd8dbfdda682a1452ee4fa8107ea96aae876d544a3e32ea438a4336c84a6f5 |
| agent/transports/codex.py | c1b27f589d0021c60448721a82d8e9ec86b389730fc1673616509f25db026b09 |
| agent/transports/bedrock.py | 0189636f70bdb8499c12f1a08e6effa6a849fe44a1c7be74a67e3cbd5828542d |
| agent/retry_utils.py | 4c643880c012dd536dca08041f1bae93b6a58477186b5cf0b4e27ec3c640014c |
| agent/client_lifecycle.py | e401d42b69c843d48fc43814c4ab1b6c704c0b2bf317abc9972118634c9cb8c6 |
| agent/process_bootstrap.py | baa6239634aede3eff2a1628d07070df7beeb0bf1d93fe5cbad8ee3014192499 |
| agent/chat_completion_helpers.py | 521197b064685590a239277be83070c04dbb6ff683625dfd7b884cc587fbd98b |
| agent/chat_completion_helpers_tool_calls.py | eadf07162dbf3a1506a894a7e9ac26ec9d8295ab126977a0504007644a3d7903 |
| agent/chat_completion_stream_monitor.py | 1c74313db79042c0831a94b2a008074d0cd5f2b30ec51dd252fe905582c74112 |
| agent/api_error_summary.py | 52b242bc394d0081d351c7e57a7b91b11c13eef14f4fcb937ab5052da56b3ce4 |
| hermes_cli/providers.py | e16d4b818c1af482024bb891003bc134f2ab0b347908abbdb2386e02391d1c8e |
| hermes_cli/timeouts.py | 5181b1dad8c0fdf670da01365f9a5dff4692665e5564177bf66fb956a59d559e |

未观察到硬编码的真实密钥。`aws-sdk` 与 `no-key-required` 是哨兵，不是密钥。插件目录检索 `sk-` 长令牌无匹配。

## 未运行

未运行单元测试、构建、网络探测或模型调用。上表结论来自源码与工作区 venv 中的 SDK 源码，不来自现场请求。
