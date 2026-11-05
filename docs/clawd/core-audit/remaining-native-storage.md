# 剩余 FFI、输入与存储复核

只读。未运行测试、构建、导入、FFI 或探针，未改产品。解释器是 CPython 3.14.9。转换规则对照已下载的 `v3.14.9` 源码，简单类型 `paramfunc` 另对照 `v3.11.9`。参考文件在 `artifacts/core-audit-native-reference/`。不宣布内核或第三方 native 安全。

计数：14 条。仍成立 9（高 0，中 2，低 7），已修复 0，误报 3，需验证 2。与活跃项无重复。

## 误报

`nat-peek`、`nat-console`、`nat-cuda` 的原机制不成立。CPython 对简单类型拷贝对象内部值。`c_void_p` 的类型符是 `P`，ffi 类型是 `ffi_type_pointer`，传入的是句柄值，不是对象地址，也不是截成 `c_int`。`c_int` 实例传入的是设备序号整数，不是地址。结构体才传缓冲区地址。这三处没有在 Windows 或 CUDA 驱动上执行，不把调用判成已成功，也不替 `libcuda` 背书。

## 仍成立

- `inp-empty-key` 低：默认 profile 空密钥时请求检查返回 None。`connect()` 和面板 `_start_api` 在监听前拒绝空密钥或弱密钥。网关走 `adapter.connect()`。没有找到跳过门禁的生产监听。不是已确认的未授权入口。
- `inp-webhook-v1` 低：V1 仍接受无时间戳的 body HMAC。空 secret 拒绝。没有稳定 delivery id 时会生成新 uuid，重放不会被当成重复。这是持有一次有效签名后的重放，不是未授权。
- `inp-password` 低：固定前缀 SHA-256，无每用户盐，无拉伸。文件模式 `0o600`，cookie 就是哈希。同用户读文件可以直接当 cookie，不升格。
- `inp-lazy` 低：默认 true。sink 是登记表里的 extra，版本和 sha256 来自 lockfile。未知包名是 KeyError。不是请求指定的任意包。
- `nat-cf` 低：创建失败得到空指针后仍 `CFRelease`。Apple 文档写明 NULL 会崩溃。触发是分配失败，不是请求参数。
- `nat-close` 低：`HANDLE` 是 `c_void_p`，关闭用另一个未声明 `argtypes` 的 `WinDLL`。放不进 Windows `unsigned long` 的句柄整数会 `OverflowError`，独占锁留到进程退出。大于 `2**31-1` 一律抛错这句过宽。常见小句柄不触发。
- `stor-s8` 中：路由索引先经独立 `_execute_write` 提交，结束和重开失败只打 debug，函数仍返回成功。没有共同事务。
- `stor-s9` 中：rollback 异常被丢弃且连接不销毁。3.14.9 在 LEGACY 且事务仍开时，`executescript` 先发 `COMMIT`。磁盘损坏没有运行复现。
- `stor-a1` 低：库是 home 下固定名 `panel_image_studio.db`，不是 `state.db`。请求不能选路径。检查 fd 关闭后按路径重开。`BEGIN IMMEDIATE` 在 connect 之后，锁不住刚才校验的 inode。同用户本来就能写这个文件，不升格。

## 需验证

- `nat-job`：默认 `restype` 把伪句柄截成 Python int `-1`，这一步确定。`-1` 再按 `ffi_type_sint` 传入。官方 x64 ABI 只说 32 位参数的高位可被忽略，没有证明 64 位 `HANDLE` 寄存器会被符号扩展成 `(HANDLE)-1`。未读 libffi Win64。不能把附着失败写成已证实。
- `nat-pe`：缓冲长度来自 `GetFileVersionInfoSizeW`。`VerQueryValueW` 的 `length` 未使用，随后读偏移 8 和 12。成功时是否保证至少 16 字节，本次没有契约。不升成已证实越界。

## 证据边界

未执行任何请求、安装、句柄调用或数据库失败注入。3.11 的 `executescript` 源码没有下载。详细条目、建议代码和哈希在 `remaining-native-storage.json`。
