# 用量 close 失败修复 — 2026-11-05

静态实现。未运行测试、构建、import、py_compile、CLI、产品命令。不是通过，不是上线许可。

观察时间 `2026-11-05T19:07:54+0800`。

## 错误优先级

只改 `session_usage` 的资源释放。`install` 仍把 `get_usage` 接到这个函数。非法 `days`、相对路径、缺失 home、生产路径拒绝都在打开库之前，不 close，也不调用 `get_hermes_home`。

打开后的优先级：

1. 主异常优先。summary 或打开后的其他异常已经变成 `mod.ApiError` 时，close 失败不得替换它。`finally` 不再抛第二个异常，所以不会把主因变成 `__context__`。
2. 非 `ApiError` 的 summary/打开失败仍是新的 503 `usage_unavailable`，`from None`。`__cause__` 为空，`__suppress_context__` 为真，原始异常留在被抑制的 `__context__`。公开文案仍是「用量暂不可用」。close 失败只附加证据，不改这条链。
3. summary 自己抛出的 `mod.ApiError` 原样再抛。status、code、message、已有 `__cause__` 保持原对象。close 失败不写入 `__cause__`，避免把清理错误说成主因。
4. 没有主异常时，close 失败不得返回 summary。抛新的 503 `usage_unavailable`，同样 `from None`。成功 payload 被丢弃。
5. close 成功且 summary 成功：返回 `summarize_usage` 的原对象，不改字段。
6. `SessionDB(...)` 抛错时赋值未完成，`db` 仍是 `None`，不 close。
7. `KeyboardInterrupt` / `SystemExit` 不是 `Exception`，不转成 503。中断仍可盖住主异常。这是故意的，不是把 close 的 `Exception` 吞掉。

侧车 `_dispatch` 只把 `ApiError` 的 status/code/message 送出。若 close 的原始异常逃出，会变成 500，并可能被 `log.exception` 打出路径。所以单独的 close 失败必须先收成 503 `usage_unavailable`。

## 可观测性

日志名 `clawd.runtime`，级别 `ERROR`，固定文案 `usage_db_close_failed (<类型名>)`。不传 `exc_info`，不写 `str(exc)`、路径、SQL、secret。不是 `logger.debug`，也不是 `except pass`。

侧车 `basicConfig` 在 INFO。这条 ERROR 会传到 root。没有 handler 时，3.11 的 lastResort 也会把 ERROR 打到 stderr。

异常上的证据：

- `add_note("usage_db_close_failed:<类型名>")`。traceback 可见，不含原文。
- `usage_close_failures` 保存原始 close 异常，供进程内核对。HTTP 只读 status/code/message，不会带上这个属性。不要把它抄进响应或日志。

公开错误和日志都不包含路径、SQL、secret。原始 close 文本只留在属性里，不进入显示链。

## 证据 hash

本路没有改 manifest。下面的旧引用不能再当当前字节。

| 记录 | 旧 sha256 | 状态 |
| --- | --- | --- |
| `docs/clawd/implementation-completion-manifest.json` 的 `runtime-usage-context-subagents` | `8b173e34628b0ad39990a0a6568f515d9dd5d8b0eb092d21847818e7fa54cb43` | 已过期。父代理统一重算前不要把它当现证 |
| `docs/clawd/resumption-runtime-detail-2026-11-05.md`、`docs/clawd/core-audit/remaining-background.json` | `dfbefdc9fcb8103d6c6d72ba87e4cbc3ac451825777493c410d78028b4b5132c` | 本次修改前就已经不是 manifest 那一版 |

当前 `clawd_server/runtime_native.py` sha256：`367e0ee31af8001fcffb2e308f18054ab2d37596428f5f05d602dd41e7a26956`（408 行）。这只是文件哈希，不是测试通过。

## 范围

改了：

- `clawd_server/runtime_native.py`：`session_usage`，以及本域 `_attach_usage_close_failure`、`_release_usage_db`、`clawd.runtime` logger。
- `tests/clawd_runtime_cases/test_usage_close_failure.py`：成功、summary 异常、close 异常、两者都异常（普通异常链和已有 `ApiError` 链）、未建成 db。调用真实 `session_usage`，不是只扫源码字面。sha256 `a96e9e49f4c1d4cbe5bc991f9e62ffe7cd9065f7d118bb0b926a61a479b60afc`。

未改：其他 runtime 函数、usage 聚合、routes、sidecar、`install` 的 `get_usage` 接线、manifest。同文件其余 `finally: db.close()` 不在本路，没有加同样的处理。

## 未运行

没有跑 pytest、构建、import 产品、py_compile、CLI。用例已写，结果未知。不要把本报告当成验收通过。
