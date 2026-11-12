# clawd 面板运行核验 2026-11-06

只读。未改产品、nginx、登录、服务、dist、真实数据。目标 `beqCprBT` HUAWEI CLOUD SG SERVER，online。层级不是浏览器 E2E。node3 探针未等待，不纳入结论。

重跑：删 `homes/<tag>` 后执行。Python `/root/hermes-workspace/clawd/.venv/bin/python`。Node `/root/.clawd/tools/node-26.12.1-linux-x64/bin/node`。每探针独立 HOME/CLAWD_HOME/HERMES_HOME/TMPDIR，均在审计目录内。

探针：`/root/hermes-workspace/clawd-panel-audit-runtime-20261106/probes/py_handlers.py` sha256 `2fb4d2f33a1b53e5c3a4065506522dc6e2c234d3a353c409366070bc66b1b05a`；`node_cache.mjs` sha256 `3a8a0caaa1189ae6866963b9008ed201a478f143144de80213c068001b74b98d`。

## P1-04 条件待证

源码指纹仍是原报告：`config-tab.tsx` `aed1240b4ae44aab91725036c1f89a4a9e290536bf3d309d0f8c4865cc13aa58`；`queries.ts` `49d7b18ea513a5c8e4c0eb4b2f881c3b4a5aaa7bc52f00a4844fb227f80a7098`。`commit` 只失效 `settings`，`saveYaml` 只失效 `config`，两个 hook 无条件挂载。跨页缓存探针未跑完：`logs/node2.json` 里 `p1` 在 react-dom `HTMLIFrameElement` instanceof 处失败。没有跨查询状态，不能称已复现。

## P2-01 已复现

活路径是 `bind` → `admin_native.install` 替换后的 `get_settings`/`set_setting`，不是 sidecar 旧 CLI。`admin_settings.py` `8e37d08a6e2de77bf51c24ac174cb3902aa02f3e41ba27100c8ffeed5192710a`。

命令：`py_handlers.py p2-01`，exit 0。日志 `logs/p2-01.json`。隔离 home 写入 `model.context_length: 4096`。回读 `model_context_length` value 4096、userSet false。`set_setting("model_context_length", 8192)` ok，文件出现 `context_length: 8192`，没有虚拟键 `model_context_length:`。再读 value 8192、userSet 仍 false。对照 `display.compact` userSet true。不是 E2E。

## P2-02 已复现

不是浏览器 E2E。服务：`py_handlers.py p2-02` exit 0，`logs/p2-02.json`。`bind` 后根在临时 home。缺 `dir` 时 workspace 落到 `probe-note.txt`，uploads 落到 `2026-11-06/probe-note.txt`；传入 `dir=kept-subdir` 才会进子目录。客户端：`logs/node2.json` `P2-02-client`。真实 `uploadFile(file, "workspace")` 查询只有 `root` 与 `name`，无 `dir`。当前 `files-page.tsx` `4617db93a25a8acad348669c9f0356591b18f18e1c89ee0b9748b0c8976170be` 调用点不传 `pathRef`。`sidecar.ts` `5b4984fbe252d8f6cbd69b79502380b4a9a5d21d30f87a3911bba98f894ceabd`。sidecar `11840a85bd8ce3a5be3a9abd4797b74cea1050895b19e80a63e95bd1fde8ff82`。

## P2-03 条件待证

view 已跑：`py_handlers.py p2-03` exit 0，`logs/p2-03.json`。`browser_native.live_task` 在 `page_ws` 抛 `CdpError target_closed` 后返回 `status=waiting`，`targetId=null`，`image=null`。未开浏览器。`browser_native.py` `68fcc80acdd21355419277658cdd93d53e0a4add19321ff46512dd8f9e730e0f`。dock 未闭合：`logs/node2.json` `P2-03-dock` 的 `taskReads=0`，`error=网络不可达`，没有吃到 waiting 帧，不能证明徽章或继续轮询。`task-dock.tsx` `e0f12c0a41908591690b9dedda393ea0c0a8afae9ed2268226d00768e0a0047e` 只作源码线索。

## P2-04 未完成

未跑通发送回调。不能称已复现。源码仍在：`chat-page.tsx` `f7d2678bcf3776c95d3c18a92e183812ca7ce71ba77dcea9eddefa0d6cce5650` 的 `restore` 在草稿非空时直接 return；`onSend` 清空后失败才调用它。切换是另一支：`onStop`/`restoredAdmission` 先看 `selectedSessionId`。`project-send.ts` `7b812ad4314a519696b908f6214565b569ad8602b59b8a6deb8f9986a1ae4f05`。未执行这两支。

## AUTH-GATE-01 驳回

`server.py` `6f02e7fdece74187ed121a4ac865c1f213919c9f605dd71d817c42f0d0e949ac` 第 291-299 行 `if denied`。`py_handlers.py auth-gate` exit 0，`logs/auth-gate.json`。aiohttp 3.14.5，`StreamResponse.__bool__` 恒为 True（`web_response.py` 651-652）。401/403/200/204 都为真。隔离 `PanelServer` 不调用 `start`，假上游 `127.0.0.1`，内存探针凭据未打印。匿名 GET 返回 401，恶意 Origin 返回 403；`_proxy_api` 与 `_proxy_sidecar` 的 `upstream_total=0`，没有注入内部凭据。当前版本不会因真假值放行。未改产品。
