# 项目档案收口

结论：二进制档案上限已从旧文本档案 100000 改为 50MiB，临时文件和文件描述符在未 finish、阻断、断流和取消时都会释放。三份源码已写入新加坡 `beqCprBT`（本轮 `list_servers` 确认仍是 HUAWEI CLOUD SG SERVER），远端回读哈希与本地一致。测试没有跑，不能称为通过。

## 改动

- `clawd_projects/resources.py`：`FILE_BYTE_LIMIT = 50 * 1024 * 1024`。不再引用 `service._ARCHIVE_LIMIT`。文本档案上限未改。
- `ProjectFileWriter.__exit__` 即使没有异常、也没有 finish，也会 `abort()`。`abort()` 只关自有 fd、只删 `.upload-*.tmp`，同名目的文件不删。
- `finish()` 在 `fsync` 失败时先尝试 `close`，再清 `_fd`。关闭或 `unlink` 的非 `FileNotFoundError` 记入 `clawd_projects.resources` 日志，不替换原来的 `ResourceError`。发布仍用 `os.link`，不覆盖，符号链接仍拒绝。
- `gateway/platforms/clawd_project_routes.py`：上传处理的 `finally` 同步调用 `writer.abort()`。`CancelledError` 继续抛出，不改成 500。断流仍走 JSON 错误。
- 同文件 19 处鉴权改为 `if (auth_err := self._auth(request)) is not None`。`_check_adapter_auth` 只在检查器明确返回 `None` 时放行；缺失、异常、`False` 或其他非 `Response` 结果失败关闭并记日志。返回的 `web.Response` 原样交回，即使它为假值。
- 旧 memory 身份和审批函数未改。检查器返回 `None` 后，旧 memory 门仍按原逻辑要求会话身份。
- 测试只写入 `tests/clawd_project_resources/test_project_resources.py`。常量断言改为 50MiB，并锁定 `_ARCHIVE_LIMIT == 100000`。超限用例把限额打到 48 字节，不分配 50MiB。另有 >1MiB 上传/回读字节一致用例，未执行。

## 哈希

写前远端与备份一致：

- `clawd_projects/resources.py` `cdd8935c435f6ac8c0059e87cb5cee9397cca9edeb527062938e88fcd64b5299` 19649
- `gateway/platforms/clawd_project_routes.py` `e2ffecb0e9d438352ea097acb179068d36d2b17200c6d42285b277412315c29b` 36919
- `tests/clawd_project_resources/test_project_resources.py` `46008994d399146f0409c51d86122f50e0139113a8570c94eef3aaa3244cbd3d` 21889

写后远端回读：

- `clawd_projects/resources.py` `749a1fcc92e0b05b75f791c23131be52fc1aa463260a2978cb649eaae2ae8141` 20870
- `gateway/platforms/clawd_project_routes.py` `db8dff0943f2bc09f1c1e2f7004487ca23f109bbe8a3bde26dbc94f2db7f7843` 38247
- `tests/clawd_project_resources/test_project_resources.py` `f551eea65aa4511aa0d06dbbbddb38ccc53e3fe0bd0bfbce9a0a2e88b08ed9ca` 44238

## 备份

本机字节备份，未改远端其他脏文件：

`/root/hermes-workspace/clawd-project-file-final-backup-20261105T213649954692Z-a7c3`

内含上述三文件改前字节和 `SHA256SUMS`。

## 未跑

精确命令，未执行：

```bash
cd /root/hermes-workspace/clawd && scripts/run_tests.sh tests/clawd_project_resources/test_project_resources.py
```

未跑 pytest、HTTP、远程 `py_compile`、构建、提交、部署。本地只对将要上传的三份文本做了 `ast.parse`，这不是测试通过。

## 限制

- 线上项目路由挂在 `gateway/platforms/api_server.py`。该文件 `MAX_REQUEST_BYTES = 10_000_000`，`client_max_size` 和 body middleware 都会在进入新 handler 前拒绝更大的正文。本次未改 API 主文件。因此 10MB 到 50MB 的档案现在仍到不了新的 50MiB 写入器。`clawd_server/server.py` 的 52MiB 不注册这些路由，不能当成这条路径的接收上限。
- 未读、未改 nginx、面板 server、前端、登录门、`service.py` 文本档案/指示上限、旧 memory 审批实现。列表仍是 40 个文件，不分页。
- 取消发生在 `to_thread` 写入期间时，`finally` 里的同步 `abort()` 会等写线程放开锁再关 fd。这是为了避免关掉仍在使用的描述符，事件循环会在这一小段阻塞。
- 上次“资源接口没有前端调用”的说法已过期。本次没有改前端。
