# 上下文压缩 / 截断 / 提示缓存 / 会话恢复审查

审查模型：grok-4.7（与指定模型一致，无不符项）。只读源码，未调用模型，未运行测试、构建、探针，未写产品代码。基线提交 `c314285f077e91937c995d419b9fa40af9eb4981` 仅作对照许可；下列缺陷不以“不是新引入”排除。已修复的项目继承事务回滚不复述为未修缺陷。

观察时点：首轮对 83 个相关源文件做 SHA-256，写完前复核这 83 个文件 **0 变更**。下文引用、但不在那 83 个清单内的文件另计指纹，写报告前未再改。若这些文件在本报告落盘后被并发改写，对应结论标待复核。

| 文件 | SHA-256（前 16） | 字节 |
|---|---|---|
| `agent/conversation_compression.py` | `796494d28180659f` | 249759 |
| `agent/context_compressor.py` | `74cd0a55ad94bf8b` | 331086 |
| `agent/agent_runtime_helpers.py` | `36914aaf5c7ac3ed` | 205957 |
| `agent/turn_preflight.py` | `5909b32461c37fa5` | 21901 |
| `agent/turn_overflow.py` | `14d483d7043f1fc3` | 26566 |
| `agent/turn_context.py` | `812d5019ecff0790` | 75149 |
| `agent/turn_context_compaction.py` | `5bff21fbe3a7064f` | 24253 |
| `hermes_state_compression.py` | `8b6e2df32259d1d1` | 55740 |
| `agent/session_persistence.py` | `ea1eb6a430a9ef73` | 35594 |
| `tools/mcp_tool_agent.py` | `cddaeb9e8e035c29` | 26575 |
| `agent/prompt_refresh.py` | `7460877c2aeab04c` | 5690 |

完整 83 文件清单：`/root/.hermes/cache/scratch/context-audit-hashes.txt`。

## 1. 结构

默认 `compression.in_place=True`（`agent/agent_init.py:1567-1569`）。自动压缩不换 session id；只有显式关掉 in-place 才走父会话结束、子会话发布。

```
入口
  turn_preflight.pre_api / compress_after_tool_results
  turn_overflow.compress          # 413 / context overflow，bypass_cooldown
  turn_context_compaction         # idle / 手动边界
  conversation_compression_manual # /compress
  gateway/session_mutation_compress.prepare_compress  # 只准备摘要，发布在另一事务
        |
        v
compress_context                         agent/conversation_compression.py:3989
  闸门：cooldown / breaker / lease / commit_fence / 硬中断
  ContextCompressor.compress             agent/context_compressor.py:5434
        剪旧 tool 结果 → 尾窗 → 摘要 → _sanitize_tool_pairs
  重建 system prompt + 刷新 tools        # 在提交之前
  _commit_compaction
        in-place: archive_and_compact
        rotation: publish_compression_child
              └─ inherit_session_project  # 同一 state 事务内，失败则整笔回滚
  返回 (messages, system_prompt) 给调用方，调用方决定是否再打模型
        |
        v
发出前
  turn_iteration_prep：修 role 交替、重锚 current_turn_user_idx
  sanitize_api_messages：按位置配对 tool_call / tool，丢孤儿、补 stub
  session_persistence._db_flush_collect：靠 _DB_PERSISTED_MARKER，不靠切片
```

真正会拒绝或丢掉消息的路径只有下面几条，不能把“某 provider 不允许连续 user”当成所有 provider 的错误：

| 路径 | 何时丢/改 | 作用对象 |
|---|---|---|
| `sanitize_api_messages` → `_drop_invalid_roles` | role 不在 `{system,user,assistant,tool,function,developer}` | 每次 API 的副本，`run_agent.py:1129` |
| `repair_message_sequence` | 连续 assistant、连续 user 合并；游离 tool 结果删除；未应答 tool_call 剪掉 | **活历史**，所有走这条预检的 provider |
| `_pair_tool_calls_positionally` | 不紧跟对应 assistant 的 tool 结果直接丢，并补 stub | 每次 API 副本，**不按 provider 分叉** |
| `ContextCompressor._sanitize_tool_pairs` | 全局 id 对不上的 tool 结果删除；非尾部未应答 tool_call 剪掉，不补 stub | 压缩产物 |
| `moa_alternation.is_role_alternation_rejection` | 仅当错误被分类为 `role_alternation` 时重试一次 | 不是预检拒绝 |

Anthropic / Codex 若允许与 OpenAI 不同的 role 或 tool 相邻关系，预检仍会先改副本。这是 Hermes 自己的门，不是上游协议的统一规定。

## 2. 已核对、不作为未修缺陷

项目继承：`publish_compression_child` 在同一写入事务里插入子行、调用 `inherit_session_project`（`hermes_state_compression.py:372-380`、`455-458`），父会话 `end_reason='compression'` 在绑定之后（`477-484`）。绑定失败抛出则事务回滚，父会话不被结束。调用方在 `publish_compression_child` 返回之后才写 `agent.session_id`（`conversation_compression.py:3290-3318`）。这与 `docs/clawd/project-inheritance.md` 一致，不把“子行残留 / session id 已切走”再报一遍。

下面仍要报的是：回滚之后，**这一轮仍然把新提示交给模型，并把 flush 基线写成“已经轮换成功”**。

其它核对后不成立的项：

- 压缩路径没有 `pickle` / `yaml.load` / `marshal.loads`。`json.loads` 用于工具参数、会话 JSON 和 `json.dumps` 深拷贝，不是不安全反序列化。
- Python 整数不存在 C 式预算溢出或 UAF。未把 token 估算偏差写成越界。
- `prompt_refresh.rebuild_for_refresh` 只在操作者显式 epoch 大于已应用 epoch 时重建，并记一次 cache break（`agent/prompt_refresh.py:80-118`）。不是回合中偷偷换提示。
- 回合间 MCP 刷新带 `preserve_prefix=True`（`agent/turn_context.py:563-579`）。压缩边界的刷新故意不带，但刷新发生在提交成功之前，见缺陷 1。
- 硬中断 / `commit_fence` 在 `begin_commit` 失败时恢复快照并不改会话（`conversation_compression.py:4158-4176`）。fence 一旦进入，提交继续，这是已写明的语义，不是死锁。
- 插话有独立队列 `_pending_steer`，预 API 时 `_drain_pending_steer`（`agent/turn_iteration_prep.py:179`）。压缩快照里的 steer 由 `_ensure_compressed_has_user_turn` 从原列表末尾找回（`conversation_compression.py:2402-2409`）。未看到“插话与压缩锁互锁死”的成对加锁。
- `truncate_head_tail` 的通知写在 `max_chars` 之外是函数自己的契约（`tools/tool_output_truncate.py:25-32`），不单独算预算溢出缺陷。

## 3. 缺陷（按严重程度）

### 3.1 高 / 确定：提交失败仍执行模型，并破坏提示缓存与 flush 基线

**位置**

- 重建发生在提交前：`agent/conversation_compression.py:4198`，实现 `3082-3137`，工具刷新 `168-175`
- 工具 pin 在刷新成功时立刻写会话行：`tools/mcp_tool_agent.py:190-194`、`217-228`
- 失败处理不设“本轮应停止”信号：`agent/conversation_compression.py:3708-3758`
- 失败后仍把 in-place 标志写成 `False`：`3434-3435`
- 失败后仍返回新 system prompt：`4210-4239`
- 中止语义要求 `None` 才保留旧 flush 基线：`2133-2149`，以及 `3906-3913` 的注释
- 调用方只看列表身份 + lock/transient，然后继续打模型：
  - `agent/turn_preflight.py:147-191`
  - `agent/turn_preflight.py:315-360`
  - `agent/turn_overflow.py:154-178`
- idle 路径不改 history，但已换上新 prompt：`agent/turn_context_compaction.py:205-213`

**触发消息形状**

1. 默认 in-place：`archive_and_compact` 在打上 `in_place_committed` 之前抛错（锁丢失、`HeldRowRewrittenError`、存储错误）。活列表被深拷贝回提交前快照（`3738-3739`），`session_commit_succeeded` 仍为假，`compacted_in_place` 仍为假。
2. `compression.in_place=false` 的轮换：`publish_compression_child` 抛 `ProjectInheritanceError` 或租约/父会话错误。`agent.session_id` 还没切换，父行回滚。轮换前的 `_flush_messages_to_session_db`（`3277-3278`）已经把当前回合写进父会话并给**当时的 dict** 打了 `_DB_PERSISTED_MARKER`；回滚用的是更早的深拷贝，标记不在这份拷贝上。

两种情况下 `compress_context` 都不走 `refused_prompt` 早退（那只覆盖“摘要变长”）。`_begin_compression_attempt` 已把 `_last_compression_attempt_recorded=True`、`_last_compression_attempt_in_place=None`。`None` 的含义是中止、保留原 flush 基线。`_finish_compaction_boundary` 随后无条件写成 `False`。

**后果**

- `conversation_history_after_compression` 看到 `recorded=True` 且 `in_place is False`，返回 `None`（`2140-2145`）。注释写明：清空基线会把未打标记的已持久化行再 INSERT。轮换失败时，当前回合已 flush、快照无标记，下一次 flush 会再写一遍。
- `turn_preflight` / `turn_overflow` 只在“同一列表对象 **且** lock 或 transient”时推迟。失败路径不设置 `_compression_skipped_due_to_lock` / `_compression_blocked_transient`（后者只在闸门函数 `1544-1557` 设置）。列表对象又被回滚成原来的那个，于是走进 else，当成压缩已完成，**继续调用模型**。
- 模型看到的 system prompt 是提交前重建的新串。`_cached_system_prompt` 和 `agent.tools` 已经换过。工具 schema 有变化时，`persist_agent_tool_names` 已把新 pin 写入**父会话**行。压缩没提交，提示缓存前缀和 tools[] pin 却变了。这违反“只有成功压缩才允许改历史提示/工具集”。
- 继承失败本身不会留下可聊天的子会话，也不会改内存 session id。缺口是：**失败关闭之后，本回合仍对父转录执行模型，并可能重复提交当前回合。**

**最小修复（只写在本报告，未改产品）**

把提示/工具刷新挪到提交成功之后；失败时恢复中止哨兵，并让调用方不要继续当成功。

```python
# compress_context：先提交，成功后再重建提示
commit = _commit_compaction(...)  # 此时不要调用 _rebuild_system_prompt_at_boundary
if not commit.session_commit_succeeded and agent._session_db:
    agent._last_compression_attempt_in_place = None  # 保留 flush 基线
    _restore_messages_snapshot(messages, messages_before_compression)
    return messages, _existing_system_prompt(agent, system_message)
new_system_prompt = _rebuild_system_prompt_at_boundary(agent, system_message)
```

`_refresh_agent_tool_definitions` 在 pin 持久化之前必须看到提交已成功；失败分支不要调用它。调用方在 `session_commit_succeeded is False` 时走与 lock-skip 相同的推迟，不要 `return continue`。

**未运行用例**

- in-place：`archive_and_compact` 在返回前抛错。断言不调用模型、`_cached_system_prompt` 与 `tool_names` 不变、`conversation_history` 仍是调用前对象、活跃行数不增加。
- rotation：`inherit_session_project` 抛 `ProjectInheritanceError`。断言父行 `ended_at` 为空、`agent.session_id` 不变、无子行、当前回合不出现第二份、本回合无模型请求。
- 冷恢复且行尚未打 `_DB_PERSISTED_MARKER` 时重复上述失败，断言不会把整段转录再 INSERT。

### 3.2 中 / 确定：预检按位置丢掉仍然存在的 tool 结果，且不区分 provider

**位置：** `agent/agent_runtime_helpers.py:2911-2982`，总入口 `3106-3117`。压缩侧全局配对在 `agent/context_compressor.py:4518-4556`，两者策略不同。

**触发形状**

```text
{"role":"assistant","tool_calls":[{"id":"call_1",...}]}
{"role":"user","content":"..."}          # 压缩摘要、插话、修复合并都可能插入
{"role":"tool","tool_call_id":"call_1","content":"<真实输出>"}
```

或 tool 结果出现在对应 call 之前。注释 `2918-2920` 写明压缩窗口会把结果挪到 user 之后。

**后果**

`_pair_tool_calls_positionally` 在 `2964-2966` 丢弃这份结果，在 `2936-2945` 插入 `"[Result unavailable — see context summary above]"`。这是每次 LLM 调用的副本，不是只给 DeepSeek/Kimi。存储转录可以还在，但本请求模型看不到真实输出。随后的 `_dedupe_tool_call_ids`（`2985-3054`）再按“未完成 call”丢第二次结果。llama.cpp 复用常量 id 时，注释声称用 outstanding 语义保护第二次合法结果；位置丢弃发生在去重之前，第一次错位已经把真结果换掉。

压缩产物里的 `_sanitize_tool_pairs` 用全局 id，不要求紧邻。同一条转录可以在压缩时保留结果，在下一次 `sanitize_api_messages` 再丢掉。尾部 in-flight assistant 的未应答 call 被压缩保留（`4532-4536`），预检再补 stub。若执行器把真实结果追加到压缩返回的**新列表**之外的旧列表，模型只看到 stub。本次未找到“压缩返回后仍往旧列表追加”的调用点，该半句标需验证。

**最小修复**

位置约束只对会 400 的端点启用。其它端点沿用 `_classify_tool_call_orphans` 的全局 id：结果在转录里存在就保留，只有真正没有 call 的结果才丢，只有真正没有结果的 call 才补 stub。

```python
def sanitize_api_messages(messages, *, positional_tools: bool = False):
    messages = _drop_invalid_roles(messages)
    ...
    if positional_tools:
        messages = _pair_tool_calls_positionally(messages)
    return _realign_tool_result_names(messages)
```

`positional_tools` 应由该请求的 provider/api_mode 决定，不能默认对所有路线为真。

**未运行用例**

- 上述三行形状分别喂给声明不要求紧邻的路线和 DeepSeek/Kimi 类路线。前者请求体仍含原始 tool 内容；后者才是 stub。
- 压缩把 tool 结果挪到摘要 user 之后，下一次 API 副本不得把 DB 里仍在的结果换成 stub，除非该路线明确要求紧邻。

### 3.3 低 / 可能：lean 尾部 skill_view 降级只按 `id` 找技能名

**位置：** `agent/context_compressor_tail.py:55-68`、`101-104`。

`skill_view_names_by_call_id` 只登记 `_tc_get(tc, "id")`。结果侧用 `tool_call_id`。同文件其它配对要求同时认 `id` / `call_id` / `response_item_id`（`agent/context_compressor.py:4507-4516`）。Codex 结果若只带 `call_id`，降级 stub 仍会替换正文（`105`），但不会附上 `[SKILL_PRUNED]`，后续 `with_skill_safety_rule` 也可能看不到标记。

未对真实 Codex 行验证 id 拼写，故不升为确定。修复是查找时用 `tool_call_id_variants`，而不是只读 `id`。

## 4. 逐文件

| 文件 | 结论 |
|---|---|
| `agent/conversation_compression.py` | 缺陷 3.1。lease、fence、would-grow 拒绝、空转录拒绝本身会停在提交前。失败提交的返回值把这些保护绕开。 |
| `agent/context_compressor.py` | `compress` / `_sanitize_tool_pairs` / 尾窗对齐本身会避开把 tool 组切在 assistant 与 tool 之间的早期 cut（`5026`、`4592-4600`）。最终 `5107` 若 cut 落在 tool 行会向前跳过 tool，理论上能把 assistant 留在窗口、结果留在尾部；本次没有证明现有锚点函数会把 cut 停在 tool 行，不单列缺陷。 |
| `agent/context_compressor_prune.py` | 提交失败返回原列表（`119-121`）；`StaleHeldHistory` 不覆盖新一代。未发现与 3.1 相同的“失败仍当成功”。 |
| `agent/context_compressor_tail.py` | 缺陷 3.3。降级保留 `tool_call_id`，配对 id 还在，丢的是技能重载标记。 |
| `agent/context_compressor_window.py` | 只计划窗口，不提交。 |
| `agent/turn_preflight.py` | 缺陷 3.1 的模型继续执行点。lock/transient 推迟是对的，失败提交没发这些信号。 |
| `agent/turn_overflow.py` | 同上。`compress()` 返回 `None` 表示调用方重试模型（`178`）。 |
| `agent/turn_context_compaction.py` | idle 只在列表身份变化时改 history（`210-213`），所以不会走 3.1 的 `None` 基线；新 system prompt 仍已赋值。 |
| `agent/turn_context.py` | `ProjectInheritanceError` 从 `_persist_under_lock` 原样抛出（`519-531`），这是另一条持久化门，不是压缩提交门。 |
| `agent/turn_iteration_prep.py` | 交替修复后重锚，避免前缀切开 tool 行（`243-254`）。steer 插在最新 tool 之后（`284-295`），不会插进 call 与 result 中间。 |
| `agent/agent_runtime_helpers.py` | 缺陷 3.2。`repair_message_sequence` 改的是活历史，连续同 role 会被合并；这是预检策略，不是某个 provider 的协议错误。 |
| `agent/session_persistence.py` | flush 以标记为准（`332-344`）。基线被设成 `None` 时，没有标记的已入库行会再进入 batch。 |
| `hermes_state_compression.py` | 子会话发布与继承在同一事务。继承失败回滚已实现。 |
| `agent/runtime_session_compression.py` | worker 把 `publish_compression_child` 交给 owner。owner 抛错会回到 3.1 的 except，不会单独把 session id 切走。 |
| `gateway/session_mutation_compress.py` | 只准备摘要；`json.loads(json.dumps(head))` 是深拷贝。发布不在此函数。未看到第二次提交。 |
| `tools/mcp_tool_agent.py` | 缺陷 3.1 的 pin 提前写入。 |
| `tools/tool_output_truncate.py` | 大输出头尾截断，通知另计长度。契约如此，不列缺陷。 |
| `agent/prompt_refresh.py` / `prompt_cache_boundary.py` | 显式刷新与进程内前缀表。成功压缩之外的缓存破坏来自 3.1，不是这两个模块自行改历史。 |
| `agent/moa_alternation.py` | 只在已分类的 role 交替 400 上重试一次。未把“连续 user”一律判错。 |
| `run_agent.py` | `_VALID_API_ROLES` 含 `function` 与 `developer`。非法 role 的丢弃点在 sanitizer，不在 `run_agent` 本体。 |

## 5. 未运行用例（禁止本任务执行）

1. in-place 提交抛错后的模型请求计数、prompt 字节、`tool_names`、活跃消息行数。
2. rotation + `ProjectInheritanceError` 后的父行、子行、session id、重复 flush、模型请求。
3. 冷恢复无标记行 + 失败提交的重复 INSERT。
4. 错位 tool 结果在“要求紧邻”和“不要求紧邻”两条真实路线上的请求体差异。
5. Codex `call_id` 与 `id` 不一致时，lean 降级 stub 是否带 `[SKILL_PRUNED]`。

## 6. 摘要

默认压缩不换会话。子会话继承失败会回滚发布，内存 session id 不切换；这已实现。缺口在回滚之后：`compress_context` 仍返回新建的 system prompt，把中止哨兵改成“已轮换”，预检和 overflow 因此继续打模型，并把 flush 基线清空。轮换路径上，当前回合可能被再 INSERT。工具 pin 可能在提交前写入父会话。预检还会不区分 provider 地丢掉不紧邻的真实 tool 结果并换成 stub。未运行上述用例，未改产品。
