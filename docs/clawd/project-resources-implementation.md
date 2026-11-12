# 项目资源接口实现说明

状态：代码已写入新加坡开发检出 `/root/hermes-workspace/clawd`。未运行测试，未构建，未提交，未部署，未重启服务，未写生产数据。

## 结论

空项目不能用，是因为旧的 `/api/projects/{id}/memory` 把面板操作绑在「当前 profile 的 session authority + 已绑定对话」上。独立 `APIServerAdapter` 可以没有 `gateway_runner`，空项目也没有对话。新接口不再走这条身份链。

面板人类项目管理现在走已经存在的受保护入口：`register_project_routes()`，每个处理函数先 `_check_adapter_auth()`，数据位置用请求当时的 `get_hermes_home()`。不新建 Principal，不放宽 agent memory 审批，不改旧的 session-bound `/memory`。

## 根因

- `handle_get_project_memory` / `handle_update_project_memory` 调用 `_memory_principal()`。没有 `gateway_runner` 时直接 401 `missing_identity`。有 runner 时还要求一条已绑定本项目的 session。项目页若借用 `items[0]`，空项目仍然失败。
- 档案写入 `write_project_archive()` 只接受文本，上限 `_ARCHIVE_LIMIT = 100_000` 字符。面板要的是工作目录里的原文件（PDF/二进制），不能只靠手写文本。
- `list_project_archives()` 在工作目录不可用时返回 `[]`。资源 GET 若沿用这个行为，会把错误报成空成功。
- `MemoryStore` 的条目是纯文本，没有单条更新时间。用 `MEMORY.md` 的 mtime 冒充每条记忆的更新时间是伪造。

## 注册是否接上

接上了。没有改 `api_server.py` / `server.py`。

真实启动链：

1. `PanelServer._start_api()`（`clawd_server/server.py`）创建 `APIServerAdapter` 并 `await adapter.connect()`。
2. `APIServerAdapter.connect()`（`gateway/platforms/api_server.py`，调用点原先在约 4038–4039 行）无条件执行 `register_project_routes(self._app, self)`。
3. `gateway_runner` 只在其后 `if self.gateway_runner is not None` 时放进 app。资源路由不读它。
4. 同一函数为每条路由再注册 `/p/{profile}` 镜像。profile 隔离仍由已有 prefix middleware 把 `get_hermes_home()` 指到该 profile，本改动不另造一套 profile 解析。
5. 面板 `API_BASE` 是 `panel-api/`（`apps/panel/src/react/lib/api.ts`）。`/panel-api/api/projects/...` 由 `_proxy_api` 注入 Bearer 后转到 adapter 的 `/api/projects/...`。

因此新路由只要加在 `register_project_routes()` 里就会进入面板所用的 adapter。未改 frontend。UI 应通过现有 `request()` 调用下面的相对路径，不要新开登录门，也不要打旧的 `/memory`。

## 接口

认证与现有项目列表/创建相同：adapter `_check_auth`。检查器缺失是 401，不是放行。错误体是 `{error, message}`，不是空的资源对象。

时间是 Unix 秒。文件 `updated_at` 来自 `lstat().st_mtime` 的整数截断。记忆没有可信的单条时间时 `updated_at` 为 `null`，不用文件 mtime 填。

### GET `/api/projects/{id}/resources`

```json
{
  "project_id": "p_abc",
  "archived": false,
  "instructions": "",
  "files": [
    {"name": "note.pdf", "size": 1200, "updated_at": 1700000222, "content_type": "application/pdf"}
  ],
  "memories": [
    {"id": "<sha256 hex of the exact entry>", "title": "第一行有意义短标题", "content": "完整条目", "updated_at": null}
  ],
  "limits": {"file_bytes": 100000, "instructions_chars": 8000}
}
```

- 项目不存在：404 `project_not_found`。
- 工作目录不可用、指示/记忆不是普通文件、记忆读失败、档案超过列出上限：409，不返回空列表冒充成功。
- 记忆 `id` 是条目原文 UTF-8 的 sha256。`title` 是第一条非空短行，最多 80 字，去掉行首 `#`。不编造分类。
- 条目来自该项目 `projects/<id>/memory/MEMORY.md`，解析器用 `MemoryStore._parse_entries`。没有这个文件就是空列表，并且 GET 不创建该目录。
- 列出上限沿用 `_ARCHIVE_LIST_LIMIT = 40`。超过则整次 GET 失败（`archive_list_exceeded`），避免静默丢掉后面的文件。这不是新限额；要放宽只改这一处现有常数。
- 隐藏文件、`.tmp`、符号链接、目录不列入。

面板路径：`api/projects/${id}/resources`。

### PUT `/api/projects/{id}/resources/instructions`

请求：`{"content": "文本"}`。`content` 必须是字符串，空字符串表示清空。

成功：

```json
{"success": true, "project_id": "p_abc", "instructions": "去掉首尾空白后的文本"}
```

- 仍走 `write_project_instructions()`：8000 字、`first_threat_message(..., scope="strict")`、拒绝指示路径符号链接。
- 保存改为同目录唯一临时文件 + `os.replace`。并发写不会把两份内容撕进同一个 `.tmp`。最后一次完整写入获胜，读回一定是其中一份全文。
- 只写 `projects/<id>/memory/INSTRUCTIONS.md`。不写全局 `MEMORY.md` / `USER.md`。
- 不调用 `ProjectMemoryStore.load_from_disk()`。已密封的回合快照不会因为这次保存被换掉；下一次新建 store 才会读到新指示。
- 已归档：409 `project_archived`。超长：400 `instructions_too_long`。威胁：400 `instructions_rejected`。

### POST `/api/projects/{id}/resources/files`

`multipart/form-data`，第一个字段必须叫 `file`，并带文件名。

成功：

```json
{
  "success": true,
  "project_id": "p_abc",
  "file": {"name": "note.pdf", "size": 1200, "updated_at": 1700000222, "content_type": "application/pdf"}
}
```

- 字节上限就是现有 `_ARCHIVE_LIMIT`：100000 字节。按块计数，超限删除临时文件并 413，不留下半截文件。
- `Content-Length` 大于上限加 16KiB 时，在读 body 之前 413。
- 同名普通文件 409 `file_exists`，不覆盖。同名符号链接 409 `symlink_rejected`，不跟随、不替换。
- 文件名拒绝路径、`..`、控制字符、引号、反斜杠、前导点。落点必须是该项目工作目录下的普通文件。
- 用 `O_EXCL|O_NOFOLLOW` 写临时文件，再用 `os.link` 独占发布。工具能在这个工作目录里读到原字节。不承诺把 PDF/二进制自动注入提示词。
- 归档项目拒绝写入。

### GET `/api/projects/{id}/resources/files/{name}`

返回该文件的真实字节，按 fd 分块读，不先把整个文件读进一个 `bytes`。

下载头固定为：

- `Content-Disposition: attachment; filename="..."`
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: sandbox; default-src 'none'`
- `X-Download-Options: noopen`
- `Cache-Control: no-store`

HTML/SVG（`.html` `.htm` `.xhtml` `.svg` `.svgz`，以及对应的 `text/html` / `application/xhtml+xml` / `image/svg+xml`）下载时的 `Content-Type` 强制为 `application/octet-stream`。列表里的 `content_type` 仍是按扩展名猜的类型，供展示，不能拿来 inline 打开。符号链接 400，不返回目标内容。

## 改动文件

| 路径 | 动作 |
| --- | --- |
| `clawd_projects/resources.py` | 新增。资源读、指示保存、流式文件写入/打开 |
| `gateway/platforms/clawd_project_routes.py` | 四个处理函数加入 `register_project_routes` |
| `clawd_projects/service.py` | 只改 `write_project_instructions` 的临时文件，使并发替换不共享一个 `.tmp` |
| `tests/clawd_project_resources/test_project_resources.py` | 新增，未运行 |
| `docs/clawd/project-resources-implementation.md` | 本说明 |

未改：`project_memory.py`、`memory_authorization.py`、`server.py`、`bind.py`、`effort_service.py`、frontend、旧 `/api/projects/{id}/memory`。

`project_memory.py` 不需要新接口。冻结行为已经在 `ProjectMemoryStore.load_from_disk()`：第一次加载后 `_snapshot_sealed`，再次加载不再重建 system prompt。测试覆盖这一点，但测试未执行。

## 备份

改前副本，本地与新加坡开发机各一份，哈希一致：

`/root/hermes-workspace/clawd-project-resources-backup-20261105T210514Z-e01740e1/`

- `gateway/platforms/clawd_project_routes.py` `24deb163ae07c768a64d5c37e5c2716495eb0f52efc7622240fc32efd704c95f`
- `clawd_projects/service.py` `a5ece8c7887c7e1d2947d97800ead60cef07d39870a572eaa6d4575de556e5bc`

回滚只覆盖这两个已有文件。`resources.py`、测试和本说明是新增，回滚时删除即可。不要用这份备份覆盖 `/opt/clawd` 或任何 profile 数据目录。

## 父代理可执行的测试

在新加坡开发机上，不要在本 Hermes 网关机上跑：

```bash
cd /root/hermes-workspace/clawd && scripts/run_tests.sh tests/clawd_project_resources/test_project_resources.py
```

覆盖：空项目且不创建 memory 目录、缺认证/错误 Bearer/没有 `_check_auth`、profile home 隔离、两个项目互不可见、二进制分块往返、同名不覆盖、超限不留临时文件、路径与符号链接、指示读回与密封快照不换、新 store 的 prompt 含指示、真实记忆条目且 `updated_at is None`、归档只读、旧 `/memory` 在无 runner 时仍 401 `missing_identity`、`connect()` 调用点不在 `gateway_runner` 判断之后才注册。

实现时做了本地和上传后的 `python3 -m py_compile` 语法检查，并确认四个处理函数属于 `ClawdProjectRoutes`。`scripts/run_tests.sh` 没有跑。

## 未做 / 缺口

- 未跑上述测试，未构建面板，未重启 `PanelServer` 或 adapter。当前进程要等下次启动 `connect()` 才会挂上新路由。
- 前端三个入口还没有调用这些路径。建议 UI owner 用 `apps/panel/src/react/lib/api.ts` 的 `request()`：
  - `GET api/projects/${id}/resources`
  - `PUT api/projects/${id}/resources/instructions` JSON `{content}`
  - `POST api/projects/${id}/resources/files` multipart 字段 `file`
  - `GET api/projects/${id}/resources/files/${name}` 用 blob 下载，不要按列表里的 `content_type` inline 打开 HTML/SVG
- `clawd_server/server.py` 的 `_proxy()` 仍是 `body = await request.read()` 再转发。核心处理函数按块执行限额，但面板这一跳会先把请求体读进内存。100000 字节在功能上能通过；这不是端到端流式。要改这一跳必须动 `server.py`，本次范围禁止修改。建议：把 `_proxy` 改为转发 `request.content`，不要整包 `read()`。下载方向代理已经按块转发。
- 记忆没有写入接口。历史条目没有单条时间，所以 `updated_at` 保持 `null`。没有为了时间去改 `MEMORY.md`，也没有挂 agent 写入钩子，避免碰到审批门。
- 档案列表超过 40 个时，整个资源 GET 是 409，不是截断后的 200。这是现有 `_ARCHIVE_LIST_LIMIT`，不是新业务系统。
- 二进制文件不做文本威胁扫描。威胁扫描仍只用于会进入项目指示的文本。文件进入工作目录后是否被工具读取，沿用工作目录既有能力，本接口不额外授权。
