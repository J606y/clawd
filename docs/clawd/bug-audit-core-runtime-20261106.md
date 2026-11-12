# clawd 核心缺陷隔离运行核验（2026-11-06）

不是全工程验收。未改产品源码、服务、nginx、登录、用户数据或 agent 配置。未调用付费模型。未计严重度总数。目标经 `list_servers` 确认为 `beqCprBT` / HUAWEI CLOUD SG SERVER。加载模块是 `/root/hermes-workspace/clawd/gateway/platforms/api_server.py`，不是 `/usr/local/lib/hermes-agent`。

## 可重跑命令

```
HOME=CLAWD_HOME=HERMES_HOME=/root/hermes-workspace/clawd-core-audit-runtime-home-20261106060518
TMPDIR=$HOME/tmp
PYTHONPATH=/root/hermes-workspace/clawd
/root/hermes-workspace/clawd/.venv/bin/python /root/hermes-workspace/clawd-core-audit-runtime-probe.py
```

进程 exit 0，elapsed 1.604s。MOSS job exit 0。探针 `bfb92b0957170ea56fb8f65dc91ca735025586b02406f0fd3a8694b8e84a6772`。结果 JSON `bb29da84bccfa364923317d809d2758fd1d7a5db28f9d83f5262a6dbca8a7aab`。日志 `/root/hermes-workspace/clawd-core-audit-runtime-probe.log`。临时库仍在上述 home，未读现网会话正文。

源码在探针后回读未变：`api_server.py` `bc60e3efc50429a3164508c277e89cc1680327405f2cc615168a595d1ac39872`；`api_server_runs.py` `554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b`；`api_server_openai_routes.py` `7c58fd232db426cbaed5deccf8ce0262af38d029f192ae5b6c9c7676991263a1`；`clawd_runtime_routes.py` `45ac61a9e568b3cc9cd0fb6a3d74c6886801b6b5117de6f84e43f13d231838ad`；`server.py` `6f02e7fdece74187ed121a4ac865c1f213919c9f605dd71d817c42f0d0e949ac`；`project-send.ts` `7b812ad4314a519696b908f6214565b569ad8602b59b8a6deb8f9986a1ae4f05`；`turns.tsx` `21b67d14595e07e897a3c71b40360fc5bf9bd48b5bd4b833a4cf6c732e1c9067`；`chat-page.tsx` `f7d2678bcf3776c95d3c18a92e183812ca7ce71ba77dcea9eddefa0d6cce5650`。

## 逐项

- CORE-01 **已复现**。层级：隔离 aiohttp TestClient → 真实 `_handle_fork_session`（`api_server.py:2824`，标题失败 `2858-2861`）；auth 桩为 None，不是现网登录。真实重名标题触发 `set_session_title` ValueError：HTTP 400 `invalid_title`，响应无 `probe_fork_core01`；源会话 `end_reason=branched`；子会话在且有 1 条消息。不推翻 P1-01 驳回。不影响截图三项；分叉失败原子性当前字节未修。未改源码。

- CORE-02 **已复现（admission）/ 写回未证**。真实 `_conversation_history_for_session`（`2544-2552`）在注入的读取异常后返回 `[]`；真实 `_handle_session_chat`（`3127-3128`）把该空列表交给 `_run_agent`。对照历史长度 1，helper 与 recorder 均为 0。HTTP 200 来自 recorder，不是模型；`writeback=not-run`。原“回合成功写回”不升格。不影响项目资源修复。

- CORE-04 **chat 已复现 / runs 笼统 completed 驳回**。受控注入 `_ProviderAuthResolutionError`，不是真实供应商 401。chat：`_run_agent:3861-3870` 返回无 `failed`/`error`/`completed=False`；真实 `_response_status:136-143` 得 `completed`；handler HTTP 200 `status=completed`。`gateway_runner` 为 None。runs：真实 `_execute_run`（`admission=None`，`1224-1227`）`run_status=failed`，事件 `run.failed`。未证：`session_authority` 路径是否改走 `final_response` 包装。不要求回滚批准的资源修复。

- CORE-05 **流封口已复现 / 产品失败驳回**。真实 `_execute_run:1228-1243` + 真实 `_RunStream`。注入 `AdmissionQueued` 后状态仍 `queued`；订户收到 `run.queued` 与 `None`；之后事件被丢弃。下一 owner 未跑，浏览器 SSE 未测。SSE 关闭不等于产品失败。不阻塞批准修复。

- CORE-06 **producer 已复现 / 展示未跑**。`runtime_native.run_wait` → `project_run_wait:122`。返回 `state=unknown, available=false, source=durable`，不是 `retrying`。`turns.tsx:601-604` hash 未变，仍只接受 `retrying`。浏览器未渲染。若批准项含重试展示，当前字节不能当已修。

- P1-02 **已复现**。真实 `DELETE /api/sessions/{id}`（`2736-2770`）加真实 `GET /api/projects/{id}/sessions`（`358-396`）。bind 成功后删除：state 行消失，`projects.db` 仍绑定，列表 200 且只回 `session_id`、无 title。`project-send.ts:68` 与 `chat-page.tsx:903` 清理仍是 `deleteSession`，不解绑。不是浏览器点击。影响批准的项目首发清理判定；本路不改源码。

- P1-03 **handler 已复现 / 界面未跑**。真实 `handle_background:276-287`，`gateway_runner is None`（与 `server.py:175-196` 不赋值一致）。GET 503 `process_registry_unavailable`。前端 `sidecar.ts:618-619` 走 `panel-api` 该路由；`chat-page.tsx:1224` 只在 `data.length>0` 时画横幅。现网面板进程与浏览器未打。不在项目资源三功能内。

未闭合：模型写回、authority 下的 runs、下一 owner 续跑、浏览器横幅与重试文案、现网服务。helper 结果未当成 E2E。
