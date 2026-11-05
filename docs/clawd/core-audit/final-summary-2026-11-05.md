# clawd 核心审查最终归并 — 2026-11-05

写入时间：2026-11-05T05:51:45.311754+08:00。这是静态归并，不是修复、不是运行通过、不是上线许可。

## 结论

- 63 条原始映射全部入账。归并后唯一结论 63 条：仍成立 46（高 10 / 中 25 / 低 11），已修复 1，误报 13，无法确认 3。
- 不要把初次 active 27、transport 10、background 2、native-storage 14 相加。那不是漏洞总数。
- 初次 33 条需验证已逐条给最终状态，没有遗漏。
- 建议代码都未应用。报告不是修复授权。

## 五个重点

- 身份鉴权：默认 profile 空密钥时 `_check_auth` 返回 None，但 `connect()` 在创建 Application 和加路由之前拒绝监听。面板 `_start_api` 只在 connect 成功后记录地址。这不是已确认的远程绕鉴权，保持低。
- 重定向密钥：Anthropic 兼容客户端不关 follow_redirects。SDK 默认跟随。密钥在 X-Api-Key。httpx 跨源只删 Authorization。高，仍成立。
- FIFO：无 request_id 时 `queue.pop(0)`。按钮、/approve、无 authority 的旧 API 都能打到。斜杠未配置 allow_admin_from 时人人是 admin。高，仍成立。
- 压缩失败：提示重建和工具 pin 在提交之前。失败仍把 in_place 写成 False，flush 基线返回 None，预检不当成推迟，继续打模型。高，仍成立。
- create_task 回执：admit 在 create_task 后立即返回，调用方马上写回执。_go 不看 completed。高，仍成立。

## 不复活

- exec-effort：当前只在 session_api_turn 以 authority 与 admission_held=True 调用。turn_facade 哈希已变，但没有旧两参数调用。
- inp-auth-bool：当前 aiohttp Response.__bool__ 为 True。
- prov-short-string：未闭合字符串不会补成缩短命令。已闭合前缀另见 prov-prefix。
- stor-a1 的库是 home 下 panel_image_studio.db，不是 state.db。同用户可替换不是提权。

## 仍成立的高

- `appr-fifo` 不带 request_id 的同意按 FIFO 放行另一条待决命令。前提：同一会话有两条未合并的待决审批。用户点较新卡片的 once、发送 /approve，或无 authority 的旧 API 不带 request_id。斜杠门禁未配置 allow_admin_from 时 is_admin 对所有人为真。Discord 传了 request_id，不在此列。
- `ctx-commit-fail` 压缩提交失败仍换提示并继续打模型，flush 基线被当成轮换清空。前提：in-place 的 archive_and_compact 在提交标记前抛错，或 rotation 在切换 session_id 前失败。失败路径不设 lock/transient 标志。
- `prov-redirect` Anthropic 兼容客户端跟随重定向并带走 X-Api-Key。前提：配置的 Anthropic 兼容 base_url 返回指向另一 origin 的 301/302/307/308。不是操作者故意把 base_url 写成 127.0.0.1。未发请求。
- `bg-receipt` 续跑回执写在 create_task 返回时，早于回合结束。前提：回执写入后进程退出；或 _run_agent 返回的字典里 completed 为假。无 authority 时没有另一套账本重跑。
- `bg-lease` 续跑 claim 失败直接返回，不排下一次评估。前提：完成通知已持久化，本轮 claim 因他人持有或未过期租约失败，之后没有新事件。
- `bg-pid` 续跑把仍存在的 PID 当成可复用旧 run 并写回执。前提：续跑行停在非终态，原进程已死，内核把同一 pid 分给无关进程，随后评估走到 reused。
- `bg-stop` 停止打不中续跑回合。前提：对续跑 run id 调用 POST /v1/runs/{id}/stop，且走无 authority 分支或 authority 找不到对应 admission。
- `bg-human` 已发射的续跑不再服从更新的人话。前提：评估已决定 admit 之后、_go 进入模型之前，插入 id 更晚的人话。
- `stor-s1` 按 session_id 找不到 owner 时写入环境库。前提：多 profile 进程用会话 id 读写，该 id 尚未进入 _entries，hint 也没有，且不是 multiplex 下可解析的 routing_home。
- `stor-s3` 重写在没有库时返回成功。前提：rewrite_transcript 的 _db_for_session_id 得到空。

## 无法确认

- `prov-name` 分片合并分支在，官方 SDK 类型到不了缺 index 或非字符串参数。name 是赋值不是拼接。工作区 OpenAI SDK 的 index 是必填 int，arguments 是可选 str。经该模型校验的官方 chunk 不会以缺 index 进入 feed。不能从分支存在推出已支持端点会发出这种流。
- `nat-job` GetCurrentProcess 默认 restype 截成 32 位；x64 寄存器高位未证实。本函数的 WinDLL 没有设置 restype。facts.py 在另一个 WinDLL 实例上设置 restype，不作用于这里。restype 为 NULL 时 GetResult 读 32 位 int，伪句柄 -1 的截断是确定的。随后再按 ffi_type_sint 传入。截断确定，杀进程是否失败未证实。
- `nat-pe` 固定版本信息的返回长度未使用；成功时是否保证至少 16 字节没有契约。length 已取出，但在 ptr+8 和 ptr+12 读 DWORD 之前没有使用。不能把越界写成已证实，也不能把 Windows 判成会拒绝短块。路径来自解析器定位到的厂商 PE，不是 HTTP 请求体。

## 漂移

- 写报告时引用文件哈希与回读时一致。设置/品牌等未引用文件可能仍在被别的任务改写，不在本结论里。
- 初次复核里的 turn_facade.py 哈希 3adf4fab 已过期。当前 0c111854 已回读，不把旧行号当结论。

## 文件

- `/root/hermes-workspace/clawd/docs/clawd/core-audit/final-findings-2026-11-05.json`
- `/root/hermes-workspace/clawd/docs/clawd/core-audit/final-coverage-2026-11-05.json`
- `/root/hermes-workspace/clawd/docs/clawd/core-audit/final-summary-2026-11-05.md`

