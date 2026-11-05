# 会话存储静态审查（storage）

- 基线：`c314285f077e91937c995d419b9fa40af9eb4981`（当前 `HEAD` 即此提交）
- 审查模型：`grok-4.7`（与指定模型一致，无不符项）
- 方法：只读源码。未打开任何 `state.db`，未创建数据库，未导入产品模块，未跑测试、构建或探针。
- 稳定缺陷只引用相对基线未改的文件。工作区里正在改的文件只记入「动态观察」，不当成稳定缺陷。
- 代码哈希是对应行范围（含末尾换行）的 SHA-256 前 16 位。无运行证据的并发/崩溃后果标为需验证。
- 本报告不宣布数据安全，也不覆盖项目库迁移。

## 中文摘要

本次只静态对照基线 `c314285f077e91937c995d419b9fa40af9eb4981` 的会话存储，没有打开 `state.db`，没有导入模块，也没有跑测试或探针。稳定结论只来自相对基线未改的文件。正在改的 `hermes_state_sessions.py`、`runtime`、`usage`、`compression`，以及 `api_server` 和 TUI 会话文件，只作动态观察，不当成稳定缺陷。存储分三层：路由索引按 `session_key` 指向会话 id，`SessionDB` 对单个 `state.db` 用 `BEGIN IMMEDIATE` 提交消息，旁边还有 JSONL 和项目库。一次写回调在同一个库里才是原子的。分块复制、先保存路由再结束数据库行、项目库绑定，都不是同一个事务。WAL 只表示可以多读单写，不能证明多进程写入安全；探测失败时辅助函数还会返回 `wal` 而不改文件。

已证实的高严重问题有六类。多 profile 进程用裸 `session_id` 选库时，索引和 hint 都没有这条 id，写入会落到环境库或启动库，消息可能进入别的对话，或因外键失败而丢失。同一个 id 若出现在多条路由上，扫描返回字典里的第一个；压缩重定向会改写全部分配；脏队列也只按 id 合并。`rewrite_transcript` 在找不到库时返回 `True`，调用方会以为重试或压缩重写已经落地。`/branch` 先提交子会话行，再按 500 行一块复制；复制异常被吞掉，然后仍然切换会话，回复条数来自内存而不是数据库。`state_meta` 盖章遇到 SQLite 错误只打 debug，`application_id` 保持 0，同 inode 的文件替换检测因此关闭。`close()` 失败被吞掉后仍清空 WAL 代际身份；注册表除一种捕获错误外，也把关闭失败打成 debug。

中等严重问题是：分块复制在崩溃后留下半份子会话；路由索引先落盘，数据库结束或重开失败只打 debug。`rollback` 异常被丢弃，之后 FTS 脚本可能先 `COMMIT`，这组组合没有运行证据，标为需验证。busy 重试是否造成双写，源码不能单独证实，因为提交后的维护错误不会回到重试循环。写连接关掉了同线程检查，但正常写路径持有进程内锁；锁外碰连接是否真的损坏，同样需验证。未改的消息写入路径没有把用户文本拼进 SQL，也没有 `pickle`；坏的 `tool_calls` JSON 只会变成空列表。Telegram 的 `session_id` 唯一索引会拒绝第二次绑定，不是静默串库。按路径缓存的注册表不会把两个 profile 的库合成一个句柄，它修不了按 id 选错路径。单条追加和一次 `replace_messages` 仍在同一个写事务里，外键会挡住不存在的会话。生图库在 home fd 校验之后仍按路径 `sqlite3.connect`，这是窄的本地替换窗口，不是请求方选库，不因重开本身升为高危。报告只给了最小修复片段，没有改产品。项目库 ABA、备份和生产库不在本次范围。不能据此宣布会话数据安全。

## 1. 结构、入口与文件关系

会话持久化有三层，不要混成一个事务。

```text
调用方（gateway / TUI / CLI）
  ├─ SessionStore 路由索引（进程内 _entries + sessions 目录 JSON）
  │    session_key → session_id，并可按 key 里的 profile 打开对应 state.db
  ├─ SessionDB（hermes_state.py + mixin）
  │    一个文件一个写连接；_execute_write = BEGIN IMMEDIATE … commit
  │    WAL 且磁盘模式确认后，读走只读连接池，不拿写锁
  └─ 旁路：JSONL divert、pending spool、项目库（本次不审）
```

| 角色 | 文件 | 相对基线 |
|---|---|---|
| 连接、写重试、关闭、代际戳 | `hermes_state.py` | 未改 |
| 消息插入/替换 | `hermes_state_messages.py` | 未改 |
| WAL 探测与回退 | `hermes_state_wal.py` | 未改 |
| schema / FTS 重建里的 commit | `hermes_state_schema.py` | 未改 |
| 共享连接注册表 | `hermes_state_registry.py` | 未改 |
| 按 key / 按 id 选库 | `gateway/session_persistence.py` | 未改 |
| 追加、重写、脏队列 | `gateway/session_transcript.py` | 未改 |
| 路由切换 | `gateway/session.py` | 未改 |
| `/branch` | `gateway/slash_commands_session.py` | 未改 |
| 脱离会话 hint | `gateway/session_detached.py` | 未改 |
| Telegram 绑定 | `hermes_state_telegram.py` | 未改 |
| 会话行、runtime、usage、compression、api_server、若干 gateway/session_*、TUI session 文件 | 见第 6 节 | 正在改 |

写路径的真实提交链：

1. 普通追加：`SessionStore.append_to_transcript` → 按 `session_id` 入脏队列 → `SessionDB.append_message` → `_execute_write`。
2. 编辑/重试：`rewrite_transcript` → `replace_messages` → 同一次 `BEGIN IMMEDIATE` 里归档或删除再插入。这一次调用在**同一个** `state.db` 里是原子的；选错库则整次写到错误文件。
3. 分叉：`/branch` 先 `create_session`（一次提交），再 `append_messages_batch(chunk_rows=500)`（每 500 行一次提交），失败被吞掉，然后 `switch_session` 先保存路由索引，再尽力 `end/reopen` 数据库行。
4. 关闭：注册表 `release` 归零后 `SessionDB.close`。共享实例的 `close()` 只减引用。

单库与跨库：

- **单库原子**：一次 `_execute_write` 回调里的语句，在 `commit()` 成功且之前没有人把事务提前提交时，同时成功或同时回滚。
- **单库但不是一个事务**：分块 `append_messages_batch`、`create_session` 之后再复制消息、`switch_session` 的索引保存和 `end_session`/`reopen_session`。
- **跨库，不能叫原子事务**：路由 JSON 与 `state.db`；SQLite 与 JSONL divert；`state.db` 与项目库。项目库绑定的已知 ABA 缺口本次不重复，也不把它算进本报告的通过项。

WAL 不是并发安全证明。WAL 允许多个读者和一个写者。多个写者仍靠 `BEGIN IMMEDIATE` 和 busy 重试互斥。进程内 `self._lock` 不约束其他进程。`apply_wal_with_fallback` 在探测失败时可以返回 `"wal"` 而不改文件（见问题 7）。

## 2. 已证实代码问题

按严重程度排列。置信度「确定」只表示控制流在源码里成立；没有运行记录的损坏形态另标需验证。

### S1 高 — 按 session_id 选库，未知 id 落到环境库

- 位置：`gateway/session_persistence.py:227-246`，回落规则在 `203-217`
- 哈希：`5e8b24f496958401`（227-246）
- 置信度：确定
- 调用链：`rewrite_transcript` / `append` 的后续刷新 / `transcript_tail_role` / `slash_commands_session.py:506` → `_db_for_session_id` → `_owner_key_for_session_id` 找不到 → `_db_for_key(None)` → `self._db`，或在 multiplex 且有 `routing_home` 时打开启动库的 `state.db`
- 触发：多 profile 进程里，用会话 id 而不是 `session_key` 读写；该 id 尚未进入 `_entries`，hint 也没有。压缩子 id 在发布前就是这个窗口，源码注释自己写了 by-id 会打到环境库（`session_transcript.py:231-232`）。
- 数据影响：消息、重写或计数写进不拥有该会话的库。目标库没有对应 `sessions` 行时，外键会拒绝插入，调用方若把「没有库」当成成功（S3），用户看到的是丢失而不是报错。有同名行时，内容进入另一段对话。
- 修复建议（只报告，不改）：

```python
def _db_for_session_id(self, session_id, *, profile_home=None):
    key = self._owner_key_for_session_id(session_id)
    if key is None and profile_home is None:
        raise RuntimeStoreError("session_id has no profile-scoped owner")
    if profile_home is not None:
        return self._open_session_db_for_active_scope(db_path=Path(profile_home) / "state.db")
    return self._db_for_key(key)
```

未知归属必须失败，不能回落 `self._db`。

### S2 高 — 同 session_id 的进程级索引不带 profile

- 位置：
  - `gateway/session_persistence.py:233-236` 扫描 `_entries`，返回第一个 `session_id` 相等的 key
  - `gateway/session_transcript.py:264-270` 压缩重定向改写**所有**匹配项，哈希 `9cb70307ee705777`
  - `gateway/session_transcript.py:181` 脏队列只以 `session_id` 为键，哈希 `8ae6c87269faa860`（160-181）
  - `gateway/session_detached.py:297-298` hint 只以 `session_id` 和 tip 为键，哈希 `730f420d183e9df0`
- 置信度：控制流确定。两个 profile 真的生成同一个 id 的频率需验证。id 形如时间戳加 6 位十六进制，同一秒跨 profile 碰撞空间约 2^24，不是零。hint 还会把压缩 tip 写进同一个字典，tip 与另一 profile 的 id 相撞时后写覆盖先写。
- 触发：一个 gateway 进程的 `_entries` 同时放多个 profile（`session_persistence.py:193-196` 写明后台清扫就是这样）。然后发生 id 相撞，或两个路由项指向同一 id 但属于不同 profile。
- 数据影响：后续 `_db_for_session_id` 打开错误 profile 的 `state.db`。`_publish_transcript_reroute` 会把另一个 profile 的路由改到这次压缩的子 id，并 `_save()`。脏队列里两个 profile 的待写消息会排在同一个 list 里，刷新时一起写入先被选中的库。
- 修复建议：索引和 hint 的键改为 `(profile_home 或 session_key, session_id)`。重定向只更新 `session_key` 前缀属于同一 profile 的项。

```python
for entry in self._entries.values():
    if entry.session_id == session_id and same_profile(entry, owner_key):
        entry.session_id = child_id
```

### S3 高 — 重写在没有库时返回成功

- 位置：`gateway/session_transcript.py:549-551`
- 哈希：与 540-572 同一函数；关键三行即 `if not db: return True`
- 置信度：确定
- 调用链：`/retry`、`/compress` 的持久化 → `rewrite_transcript` → `_db_for_session_id` 得到 `None`（S1 的失败关闭，或打开失败）→ 返回 `True`。同函数 568-569 把真实 SQLite 异常打成 debug 并返回 `False`，两条路径不一致。
- 触发：profile home 解析失败、数据库打开失败，或 id 尚未有 owner 且 `_db_for_key(None)` 也返回空。文档字符串写明调用方会根据返回值决定是否继续压缩重定向。
- 数据影响：调用方认为转录已替换。磁盘上的旧消息仍在，或者根本没写到该 profile。这是错会话/丢更新，不是连接层的使用后释放。
- 修复建议：没有库时返回 `False`，不要把「没写」报成「写了」。

```python
db = self._db_for_session_id(session_id)
if not db:
    logger.error("rewrite_transcript: no store for %s", session_id)
    return False
```

### S4 高 — `/branch` 吞掉复制失败，仍切换并按内存条数报成功

- 位置：`gateway/slash_commands_session.py:1248-1283`
- 哈希：`b91b96d863382a5b`（1248-1276）
- 置信度：确定
- 调用链：`_handle_branch_command` → `create_session`（已提交）→ `append_messages_batch(..., chunk_rows=500)` 被 `contextlib.suppress(Exception)` 包住 → `set_session_title` 同样被吞 → `switch_session` → 回复里的 `msg_count` 来自内存 `history`，不来自数据库。
- 触发：复制中途 `SQLITE_BUSY` 耗尽耐心、外键失败、磁盘满、FTS 触发器错误。超过 500 行时，前面的块已经提交（S6），后面的块失败也被吞掉。
- 数据影响：聊天已经指向子会话，子会话可能是空的或只有前缀。用户看到的条数是父会话的内存条数。父会话没有被这次失败回滚。这是错会话加部分转录，不是单库原子失败。
- 修复建议：复制失败则不要 `switch_session`；报告数据库实际行数；已提交的空子行要显式删除并把删除失败一并返回。不要 `suppress(Exception)`。

```python
try:
    await self._session_db.append_messages_batch(new_session_id, rows, chunk_rows=500)
except Exception as exc:
    await self._session_db.delete_session(new_session_id)  # 失败则把两个错误一起返回
    return t("gateway.branch.create_failed", error=exc)
```

### S5 高 — state_meta 盖章失败后 application_id 保持 0，替换检测失败开放

- 位置：`hermes_state.py:1180-1220` 与 `1237-1247`
- 哈希：`adad8f2ccbec4c5e`（1180-1247）
- 置信度：控制流确定。之后一次同 inode 覆盖是否真的把写入打进新文件，需验证。
- 调用链：`SessionDB.__init__` → `_open_writer` → `_ensure_db_file_generation`。`sqlite3.Error` 只 `logger.debug` 后返回。`_db_file_application_id` 初始就是 0（626 行）。`_db_file_was_replaced` 在 `recorded_app == 0` 时直接 `return False`。注释写明 inode 用来抓 `mv`，`application_id` 用来抓同 inode 的 `cp`。
- 触发：打开时 `state_meta` 插入或 `PRAGMA application_id` 遇到锁、满盘或 I/O 错误。成功路径还会在 checkpoint 失败时同样吞掉（1216-1218），但内存里的 id 已经设上；错误路径连内存 id 都不设。
- 数据影响：这个句柄不再拒绝同 inode 的文件替换。后续 `BEGIN IMMEDIATE` 可以写到已被替换的内容上。WAL 是否打开与这个判断无关。
- 修复建议：盖章失败必须让句柄拒绝写入，不能把 0 当成「尚未 checkpoint」。

```python
except sqlite3.Error as exc:
    self._db_file_application_id = 0
    raise sqlite3.OperationalError(
        f"state_meta generation stamp failed for {self.db_path}: {exc}"
    ) from exc
```

`_db_file_was_replaced` 在 recorded id 为 0 时也应拒绝写入，而不是返回 False。

### S6 高 — close 失败被吞掉，随后仍宣告代际已结束

- 位置：`hermes_state.py:567-575`（哈希 `ad60aa08f34c935b`），`1581-1585`（含于 `13e74aa0c9b5550d`，1523-1585）；`hermes_state_registry.py:115-125`（哈希 `3f7eda1e3fb8243d`）
- 置信度：吞异常并清空 `_db_sidecar_identity` 是确定的。关闭失败后文件描述符是否仍占着旧 WAL，以及下一次打开是否因此撕裂页，需验证。
- 调用链：注册表最后一次 `release` → `_close_quietly` → `SessionDB.close`。1581 行先把 `self._conn` 置空，再调用会吞掉一切异常的 `_close_connection_quietly`，然后无条件 `self._db_sidecar_identity = {}`。同文件 577-582 的 `_close_conn_logged` 写明关闭失败会泄漏被跟踪的 fd，应记 WARNING，但生产 `close()` 没有用它。注册表除 `RetiredGenerationCaptureError` 外把关闭错误打成 debug。
- 触发：`Connection.close()` 抛错（已关闭、解释器拆除、SQLite 返回错误）。另一个线程可以在锁释放后按「已干净关闭」重新打开。
- 数据影响：句柄以为 WAL 代际已经结束。若失败的 close 没有释放描述符，新连接可能在旧代际仍被持有时建立新的 `-wal`。这正是源码自己引用的 SQLite howtocorrupt 场景。不能因为库处于 WAL 就认为关闭是并发安全的。
- 修复建议：关闭失败不要清空代际身份，也不要假装 `_conn` 已释放。

```python
conn, self._conn = self._conn, None
try:
    conn.close()
except Exception as exc:
    self._conn = conn  # 仍由本句柄持有，禁止按干净关闭处理
    logger.warning("state.db close failed for %s: %s", self.db_path, exc)
    raise
self._db_sidecar_identity = {}
```

### S7 中 — 分块复制不是一个事务

- 位置：`hermes_state_messages.py:430-445`
- 哈希：`1cbaaeb16c1a0ada`（396-449，含单条追加与分块）
- 置信度：确定
- 调用链：`/branch`（S4）和 CLI 的 `append_messages_batch(..., chunk_rows=500)`。每个块各自 `_execute_write`。块内有回滚；块与块之间没有。
- 触发：进程在第 N 块提交后、第 N+1 块前提交前崩溃，或后一块抛错。
- 数据影响：子会话已持久化不完整转录。这是同一个 `state.db` 上的多次提交，不是跨库原子，也不是 WAL 能补上的。S4 还会把这个部分结果交给用户。
- 修复建议：要么一个事务复制完整历史，要么持久化「复制未完成」标记，未完成的子会话不得被路由引用。busy 重试只重试当前未提交的块，不能让调用方把已提交的块再插一遍。

### S8 中 — 路由切换先落索引，数据库结束/重开失败只打 debug

- 位置：`gateway/session.py:1204-1214` 与 `1327-1332`，哈希 `e66ddc4828edc077`（1292-1348）
- 置信度：确定
- 调用链：`switch_session` 在锁内 `_replace_route_locked` → `_save()`，然后才 `end_session` / `reopen_session`。数据库异常被 lambda 打成 debug。
- 触发：索引保存成功后进程崩溃，或 `state.db` 忙/损坏。`/branch`、`/resume` 都走这里。
- 数据影响：路由已经指向目标 id，目标行可能仍是结束状态，旧行可能仍开着。下次进入会写到调用方认为已经换掉的会话，或写进已结束行被拒绝。这是两个存储（路由文件与 `state.db`）没有共同提交。
- 修复建议：先在数据库里完成 reopen，成功后再 `_save()`。数据库失败必须返回 `None`，不要只打 debug。

### S9 中 — 写事务的 rollback 异常被丢弃

- 位置：`hermes_state.py:1052-1056`，哈希 `8cc2ab0daa7215d4`；所在重试循环哈希 `62dc49ded38b85d9`（1010-1127）
- 置信度：吞异常确定。rollback 失败后事务是否仍开着，需验证。
- 调用链：任意 `_execute_write`（`append_message`、`replace_messages`、`end_session`）。回调或 `commit()` 抛错后，`rollback()` 的异常变成 `pass`。
- 触发：连接已处于错误状态，`rollback()` 再抛错。若原错误是锁错误，循环会继续，下一次 `BEGIN IMMEDIATE` 可能因「事务中不能再 BEGIN」失败并传出。连接上可能仍留着未提交语句。
- 数据影响：调用方看到失败，但连接没有被证明已回滚。后续同一连接上的 `executescript` 按 Python sqlite3 的契约会先发 `COMMIT`（`hermes_state_schema.py:683-694`，哈希 `5bbf645c3a506e73`，由 `retry_deferred_fts_recovery` 在持有 `self._lock` 时调用）。那一次 COMMIT 是否把「已报告失败」的部分写入变成持久数据，需验证，不能当成已复现的损坏。
- 修复建议：rollback 失败则作废该连接，禁止在同一连接上重试或跑 FTS 脚本。

```python
except BaseException:
    try:
        self._conn.rollback()
    except Exception as roll_exc:
        self._conn = None
        raise sqlite3.OperationalError(f"rollback failed: {roll_exc}") from roll_exc
    raise
```

锁/busy 重试会整段重放回调。`append_message` 的 `INSERT` 没有幂等键。提交之后的 checkpoint 和 FTS merge 自己吞异常，不会把已提交插入再放一遍。因此「busy 重试必然双写」没有在源码里被证实，只留在第 5 节。

## 3. 逐文件要点

### `hermes_state.py`（未改）

写连接 `check_same_thread=False`，写路径用非重入 `self._lock` 串行 `BEGIN IMMEDIATE`。读池只在 `_wal_active` 为真时借出单独的只读连接；`_wal_active` 要求 `apply_wal_with_fallback` 返回 `wal` **并且**磁盘 `journal_mode` 也是 `wal`（809-813，哈希 `4d50a0d5a6fe5b8f`）。探测失败不会单独因为返回值 `"wal"` 就放开无锁读。这是对的，但不能外推成「文件在 WAL 上所以并发安全」。

`append_message` 不在回调里 `commit()`。单条消息的插入和计数在同一次事务里。外键是 `messages.session_id REFERENCES sessions(id)`，写连接打开了 `foreign_keys=ON`。不存在的会话插不进消息，不会静默写成孤儿行。

`_halt_db_corrupt` 在 `_execute_write` 的锁外调用 `_disable_close_time_checkpoint`，后者读取 `self._conn`（1377）。另一线程此时可以持锁使用同一连接。这是跨线程使用写连接的窗口，损坏形态需验证，见第 5 节。

`get_meta` / 无 cursor 的 `set_meta` 会再拿 `self._lock`。锁不是 `RLock`。本次没有在未改文件里找到从 `_execute_write` 回调再调用它们的稳定链，所以不把死锁列为已证实缺陷。

### `hermes_state_messages.py`（未改）

`append_message` 在事务外编码，事务内检查守卫、插入、增加计数。busy 重试若发生在提交前且 rollback 成功，不会双写。`_execute_transcript_write` 会在每次尝试前恢复 `_row_id`，避免回滚后的 rowid 被下一轮占用、从而把刷新认到别人的行。单条 `append_message` 不走这个恢复，它也不靠 `_row_id` 更新已有行。

`replace_messages` 的归档或删除、插入、计数更新在同一次回调里，单库原子。`expected_active_ids` 不一致会拒绝，避免把过期重写盖到并发追加上。调用方若没传这个钉，并发追加仍可能被重写盖掉；那是调用方契约，不是存储层偷偷提交。

分块行为见 S7。`_carry_parent_timestamps` 吞掉所有异常，只影响时间戳，不把行写到别的 `session_id`。

内容反序列化：`\x00json:` 前缀用 `json.loads`。非法 JSON 退回原字符串，不执行代码。没有 `pickle`、`yaml.load` 或 `eval`。`tool_calls` 解析失败会变成 `[]`，这是结构丢失，不是代码执行。`display_metadata` 只接受对象；本地能写数据库的人可以改控制标志（例如排队标记）。这是本机库的信任边界，本次没有找到未改的远程接口把任意 metadata 直接绑进 SQL。

SQL：消息语句使用 `?`。f-string 只拼接内部片段（`+ 1` 或 `?`、占位符个数、固定的 `AND active = 1`）。未发现把会话 id 或消息正文拼进 SQL。

### `hermes_state_wal.py`（未改）

`apply_wal_with_fallback` 在磁盘模式探测失败且未要求必须 WAL 时 **返回 `"wal"` 且不修改文件**（322-330，哈希 `90850abe93853955`）。注释写的是「连接继承文件头里的模式」。调用方若只看返回值，会把 DELETE 或未知模式当成 WAL，从而做无锁并发读。`SessionDB` 又读了一次磁盘模式，读池有第二道门。其它直接相信返回值的打开路径不在本次已证实缺陷里，但这个返回值本身不能当并发安全信号。

`journal_mode` 配置只允许 `wal` 或 `delete`，否则回到 `wal`（157-167）。`PRAGMA journal_mode=` 的拼接值来自这个白名单或内部常量 `DELETE`/`WAL`，不是请求参数。`synchronous` 未配置时保留 SQLite 编译默认，本次不把它写成已证实的掉电丢提交。

WAL 与并发：一个写者加多个读者是 WAL 的模型。两个进程同时写靠 SQLite 的保留锁，不靠 Python 锁，也不靠「文件名以 `-wal` 结尾」。DELETE 回退时读路径故意回到写连接加锁，避免 busy 风暴。这个降级是对的。

### `hermes_state_schema.py`（未改）

`_init_schema` 在打开期间 `executescript` 再 `commit()`。此时还没有业务消息。FTS 恢复脚本自带 `BEGIN IMMEDIATE` … `COMMIT`，并由 `executescript` 执行。Python 的 `executescript` 会先发一次 `COMMIT`。它与 S9 的组合需验证，不单独升成已复现的双写。

`reconcile_state_schema`（1404-1405）在连接已有事务时直接 `commit()`。它会提交调用方尚未完成的事务。本次会话追加链不调用它；`tools/async_delegation.py` 会。那是相邻写入，不是本报告的消息主链，不展开。

### `hermes_state_registry.py`（未改）

按**路径**而不是 `session_id` 缓存 `SessionDB`。同一路径 inode 变了会退役旧代际，不把旧句柄借给新文件。这不能修复 S1：S1 是选错路径，不是同一路径的缓存串了。`release` 按对象 id 归还，双释放在持锁下不会把别人的引用减穿。关闭吞错见 S6。

### `gateway/session_persistence.py`、`session_transcript.py`、`session.py`、`session_detached.py`（未改）

见 S1、S2、S3、S8。按 `session_key` 选库的路径会解析 profile home，解析失败时拒绝回落环境库（216-221）。按 id 的路径没有这条拒绝。两套规则不一致，问题在按 id 的那套。

### `gateway/slash_commands_session.py`（未改）

见 S4、S7。创建子会话和复制消息是两次以上提交。线程创建在数据库写入之前，避免「建了行但没有线程」；反过来，数据库复制失败却没有撤销切换。

### `hermes_state_telegram.py`（未改）

`session_id` 上有 `UNIQUE INDEX`（149-151，哈希 `67185cf254fa9330`）。反向查找不带 `profile_name`（251-256，哈希 `874780bfd09e9d5a`），但唯一索引保证一个库里一个 `session_id` 至多一行。这是拒绝第二次绑定，不是把两个 profile 的话题绑到同一行后静默串数据。不列为缺陷。若多个 profile 共用一个 `state.db`，第二个 profile 无法绑定已被占用的 id，调用方会得到 `ValueError`。

### SQL 注入与反序列化（跨文件，已查）

未发现未改的消息/会话写入把用户文本拼进 SQL。`_sql_literal` 只做引号加倍，本次看到的调用点是常量前缀，不是请求参数。反序列化是 `json.loads`，失败路径是丢弃结构或抛存储错误，不是对象构造。不把 C 的释放后使用套到这些 Python 对象上。

## 4. 推测与需验证

- **busy 重试造成双写**：`_execute_write` 在回调已经开始后仍会因锁错误重试，而 `INSERT` 不是幂等的。若 `commit()` 已经成功又抛出可重试错误，会再插一行。源码把提交后的 checkpoint/FTS 错误吞掉，不进入重试。没有运行记录证明 `commit()` 成功后仍抛锁错误。置信度：需验证。不要按已证实双写修复以外的路径去改插入语句，除非先补一个会失败的 rollback/commit 探测。
- **rollback 失败后的 FTS `executescript` 把失败写入提交掉**：两段源码都在，组合是否在真实错误上发生，需验证。
- **close 失败后的 WAL 代际撕裂**：控制流已证实，物理损坏需验证。
- **`_disable_close_time_checkpoint` 与另一线程同时使用 `self._conn`**：窗口已证实，是否造成 SQLite 内部状态错乱需验证。
- **跨 profile 的 session_id 碰撞频率**：键的形状已证实；生产里有没有撞上，需验证。没有碰撞时 S1 的「未知 id 回落环境库」仍然成立，不依赖碰撞。
- **非重入锁死锁**：结构上存在。未改调用链里没有找到从写回调再入 `get_meta`/`_execute_write` 的稳定例子。

## 5. 动态观察（正在修改，不是稳定缺陷）

下列文件在工作区里已改或未跟踪。其中的行为可能在本次审查期间变化，不记入第 2 节。

- 已改：`hermes_state_sessions.py`、`hermes_state_runtime.py`、`hermes_state_usage.py`、`hermes_state_compression.py`、`hermes_state_mutation_branch.py`
- 未跟踪：`hermes_state_api_edit.py`、`hermes_state_execution.py`、`hermes_state_last_call.py`、`hermes_state_lease_read.py`、`hermes_state_subagents.py`
- 已改的直接消费者：`gateway/platforms/api_server.py` 及其 run/idempotency 文件，`gateway/session_ingress.py`、`session_local.py`、`session_local_recovery.py`、`session_policy.py`，以及 `tui_gateway/methods_session.py`、`server.py`、`session_workdir.py`

只记观察，不升格：

- `hermes_state_usage.py` 的 token 线程通过 `_execute_write` 拿同一把锁，写连接没有第二套无锁写入。应用失败被吞掉，丢的是计数，不是消息正文。文件正在改。
- `tui_gateway/methods_session.py` 的分叉补偿删除失败只打 debug。文件正在改，补偿是否还在，以合并后的文件为准。
- `api_server.py` 的 `_session_dbs` 注释写的是按 profile home 缓存。文件正在改，不把当前缓存键当成稳定结论。
- `hermes_state_sessions.py` 的 `create_session` / `end_session` / 项目绑定复制正在改。跨库项目继承的已知缺口不在这里重复。

## 6. 未覆盖

- 未打开、未查询任何生产或开发 `state.db`，没有并发或崩溃实验。
- 项目库 `projects_db.py` / `migration.py` 及备份不在范围。跨库项目绑定不能称为原子事务，本次不把它重新审成新的 ABA 结论。
- 正在改的 runtime（execution / last_call / subagents）不作为稳定缺陷。
- 未逐行审完 FTS 重建、repair、portability、search、Telegram 以外的平台表。
- 未跟踪 agent 运行时每一次 `_flush_messages_to_session_db` 的调用点；那些调用最终仍进入 `_execute_write`，选库错误来自 gateway 的按 id 解析，而不是插入 SQL 本身。
- 未读 `/root/.hermes`、官方安装目录或任何凭据。
- 附件审查只加了生图 `panel_image_studio.db` 这一条 connect 链。list_dir 与生图输出的 fd 方案已在最新代码里看到，但未测，不在本报告评分。

## 7. 有限补充：生图库在 home fd 校验后按路径重开

只核对最新的 `apps/panel/sidecar/hermes_panel_sidecar.py` 与 `image_studio.py` 里这一条 `sqlite3.connect` 及其直接调用链。不复述旧的 list_dir/生图输出 fd 快照，也不把那条未测的 fd 方案升成存储缺陷。`panel_folders.db` 和只读 `state.db` 的其它 `connect` 不在这条链上，不扩大审查。

调用链（最新源码）：

1. `image_history_view` / `image_generate_view` / `image_delete_view`（`hermes_panel_sidecar.py:5594-5661`）先 `_image_runtime()`。
2. `_image_runtime`（5552-5586）打开 home fd，用 `_proc_fd_path`（948-957）得到已解析目录，拼出 `identity / "panel_image_studio.db"`，然后在返回前关掉 home fd。
3. 三个 view 再打开一个新的 home fd，作为 `parent_fd` 传给 `list_history` / `generate` / `delete_history`。
4. 三者都进入 `_store`（`image_studio.py:563-606`，哈希 `42396f04e0b1f9d0`）。`parent_fd` 不为空时，用 `dir_fd` + `O_NOFOLLOW` 打开固定文件名，要求是普通文件，并且与 `os.stat(db_path, follow_symlinks=False)` 的 inode 一致，然后关掉这个 fd。
5. 紧接着 `sqlite3.connect(db_path, timeout=3)`。sqlite3 没有 `dir_fd`，这一下按路径重开，会跟随检查之后换上的符号链接。

库与路径：不是会话 `state.db`。是当前 home 身份目录下的 `panel_image_studio.db`，表 `generations`。路径不来自请求体；请求只能影响提示词、模型和记录 id。`db_path.name` 固定为 `panel_image_studio.db`。

### A1 低 — 校验 fd 关闭后，connect 不再绑定该 fd

- 位置：`apps/panel/sidecar/image_studio.py:573-588`；调用点 `hermes_panel_sidecar.py:5601`、`5629`、`5654-5660`
- 严重程度：低
- 触发：在 inode 比较通过并 `os.close(fd)` 之后、`sqlite3.connect(db_path)` 之前，有人把该 home 里的 `panel_image_studio.db` 换成指向其它 SQLite 文件的符号链接。connect 会打开链接目标，并在其上 `BEGIN IMMEDIATE` 和 `CREATE TABLE IF NOT EXISTS generations`。
- 攻击者可控前提：请求不能指定这个路径，也不能指定库名。要打这个窗口，必须已经能在校验过的 home 目录里替换这个固定文件名。与 sidecar 同一用户的本地进程本来就能写这个文件，不需要抢这几行。其它用户能否替换，取决于 home 目录权限；本链没有证明目录对其它用户可写。因此不能把「存在重开」写成高危绕过。
- 是否绕过 home 身份：稳定路径没有。`db_path` 来自当时的 `/proc/self/fd` 身份；稍后新开的 home fd 若和这条路径不是同一个 inode，`_store` 在 connect 之前就以 403 拒绝。绕过只存在于检查通过之后的路径重开，而且只绕过这一次 `O_NOFOLLOW`，不是每次请求都落到别的 home。
- 事务和锁：`BEGIN IMMEDIATE` 发生在 connect 之后，锁的是 connect 实际打开的那个文件，盖不住检查与重开之间的空档。`timeout=3` 只影响打开后的锁等待。`_GEN_LOCK` 只串行生图请求，列表和删除不拿它，它也不是跨进程的路径锁。对已经打开的同一个库，立即事务足够串行写者；对「打开的是不是刚才校验的那个文件」，不够。
- 置信度：代码窗口确定。非本用户能否在这个空档替换文件，需验证。没有运行证据，不把跟随符号链接后的写损坏写成已发生事实。
- 修复建议（只报告，不改）。sqlite3 不能接收 `dir_fd`，所以应在仍持有校验 fd 时用不可跟随的方式把库固定到不依赖原路径的名字，再连接那个名字；连接期间不要放开原文件被换成链接的机会。最小方向：

```python
# 仍持有 dir_fd 打开的普通文件 fd 时，把连接目标钉在 /proc/self/fd/<fd> 不可行：
# sqlite 需要在同目录创建 -wal/-journal。应在同一 dir_fd 上 O_NOFOLLOW 打开后，
# 用该 fd 的 fstat 身份循环复查，直到 connect 返回，并且 connect 后立刻再 fstat
# 连接对应的库文件；身份变了就关闭并失败，不要 BEGIN。
opened = os.open(db_path.name, os.O_RDWR | flag | os.O_CLOEXEC, dir_fd=parent_fd)
identity = os.fstat(opened)
conn = sqlite3.connect(db_path, timeout=3)
if os.stat(db_path, follow_symlinks=False).st_ino != identity.st_ino:
    conn.close()
    raise ImageStudioError(403, "unsafe_file", "生图记录路径不可用")
```

这仍有极窄的连接后复查窗口，需验证是否被接受为足够。更稳的是连接使用仅由该次 `dir_fd` 可见、且创建后不再经可替换路径解析的文件。在没有该能力时，应失败关闭，而不是把重开当成已绑定 home。

## 8. 结论边界

已证实的是源码里的提交和选库规则，不是一次事故复盘。单库一次 `BEGIN IMMEDIATE` 不能保护分块复制、路由索引和项目库。WAL 不能保护多写者，也不能在 `application_id` 检测被 0 关掉之后识别同 inode 替换。本报告不宣布会话存储或数据整体安全。
