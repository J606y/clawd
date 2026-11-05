# clawd 核心 native / FFI 内存与类型安全审查

审查模型：grok-4.7。只读。未运行测试、构建、编译、探针或导入。未改产品源码。未读 `/root/.hermes`、官方安装、生产数据或凭据。未按版本号报 CVE。

## 摘要

自有可审查的 C 实现只有 `native/fts5_cjk/fts5_cjk.c`。没有自有 C++，核心 Python 路径没有 Rust `unsafe`。安装器 `apps/bootstrap-installer` 有自有 Rust，不在核心 import 图内；生产 `unsafe` 已读，未发现可证明的越界或 UAF。不能因为核心没有更多 native 源码，就说依赖树内存安全。第三方原生轮子和 `node_modules`/`.venv` 未审计。

自有分词器在调用方保证 `nToken >= 1` 时，UTF-8 解码先看剩余长度，bigram 只用 3 个栈槽，创建失败会 `sqlite3_free`。默认 `SQLITE_MAX_LENGTH` 下没有可证明的越界、UAF 或有符号溢出。加载器不传 entrypoint；SQLite 文档会从 `libfts5_cjk.so` 推导 `sqlite3_fts5_cjk_init`，该符号已导出。注释里的 `sqlite3_ftscjk_init` 不是推导名，但不构成已证实的加载失败。

确认问题都在 FFI/生命周期，不是把 Python 对象说成 C UAF。glibc < 2.41 时 `install_never_free_environ` 把每个不同的 `NAME=value` 永久留在 dict，网关按会话写 `HERMES_SESSION_ID` 会无界增长，这是资源泄漏。Windows 上两处未设 `argtypes` 却传入 `c_void_p` 实例，ctypes 传入的是对象地址而不是句柄，内核拒绝后输出排空直接返回、控制台被当成非交互。CUDA 探测把 `c_int` 实例当设备号，该路径失效并回退。Job 自附着未声明 `GetCurrentProcess` 的 `restype`，可能静默失败。PE 版本块未核对返回长度。无高危已证实的自有 C 内存破坏。

## 范围与方法

对照 `docs/clawd/source-map.md`、`docs/clawd/handoff.md`、`docs/clawd/dependency-source-inventory.json`（生成器未执行，只作清单边界）以及 `pyproject.toml`、`native/fts5_cjk/build.sh`、`setup.py` 检索。`setup.py` / `pyproject.toml` 没有 `Extension(`，核心不是 setuptools 编译的 Python 扩展。

扫描排除 `node_modules`、`.venv`、`build/`、`.git`。`build/lib` 是源码副本，不另审。测试只用来核对调用约定，不执行。

判定：能从自有源码和调用者证明的越界、生命周期、有符号/无符号、缓冲区长度、锁与错误分支才列问题。证据不足标需验证，不凑项。Python 对象留存只在能证明无界时列为资源泄漏，不称 UAF。

## 结构：语言与原生边界

| 边界 | 位置 | 归属 | 本审查 |
| --- | --- | --- | --- |
| 自有 C | `native/fts5_cjk/fts5_cjk.c`（252 行） | 核心，可审查实现 | 已逐行 |
| 第三方头 | `native/fts5_cjk/vendor/sqlite3.h` 标明 `SQLITE_VERSION` 3.50.4，及 `sqlite3ext.h` | 公开 SQLite 头，不是自有实现 | 未把 SQLite 本体当自有代码审；只核对 `fts5_api` 调用形状 |
| 构建 | `native/fts5_cjk/build.sh`：`gcc -shared -fPIC -O2 -Wall -Wextra`，缺系统头时 `-Ivendor`，安装到 `$HOME/.hermes/lib` 或 `$1` | 自有脚本 | 已读。未编译 |
| 加载 | `hermes_state_fts.py` `load_fts5_cjk_extension`；`hermes_state.py` 写连接与只读连接调用 | 自有 FFI | 已读 |
| 自有 Rust | `apps/bootstrap-installer/src-tauri/src/*.rs` | 安装器，非核心 import | 生产 `unsafe` 已读 |
| 核心 ctypes | 见下节文件表 | 自有 FFI | 已读调用点 |
| 第三方原生 | `pyproject.toml` 直接或额外依赖的轮子：`nemo-relay`、`pydantic`（pydantic-core）、`cryptography`、`Pillow`/`pillow-heif`/`resvg-py`、`psutil`、`httptools`、`uvloop`、`watchfiles`、`pywinpty`、`pywin32`、`ruamel.yaml.clib`，以及可选的 `brotlicffi`、`python-olm`、`faster-whisper`/`ctranslate2`/`onnxruntime`、`sherpa-onnx`、`numpy` | 黑盒 | 未覆盖。不报 CVE |
| Node | 自有 `package.json` 无 `node-gyp`/`binding.gyp`。`scripts/whatsapp-bridge` 锁文件含 `@emnapi/runtime` | 第三方 | 未进入 `node_modules` |

`build.sh` 默认写入 `~/.hermes/lib`，加载器用 `get_hermes_home()/lib` 或 `HERMES_FTS5_CJK_SO`。若 home 已改到 `CLAWD_HOME`，脚本默认路径和加载路径可能不一致。这是安装位置问题，不是内存缺陷。`.so` 由进程所有者路径 `dlopen`，本审查不把它升格为远程代码执行：代理进程已能执行命令。

`fts5_api` 方法偏移由编译期头决定，指针来自运行中的 libsqlite。`build.sh` 优先系统 `sqlite3ext.h`。头与运行库不一致时，`xFindTokenizer` 可能跳错。本次未比较系统头与运行库，此项保持未验证，不列为缺陷。

## 逐文件

### `native/fts5_cjk/fts5_cjk.c`

sha256 `814e7dd9ab1b4c4f69673715ed147a2f8203e3443661c0152d36ecfabcb4768f`。

- `cjk_utf8_decode`（52–69 行）在读第 2/3/4 字节前检查 `n`。调用点 `cjk_emit` 只在 `i < nToken` 时调用，故 `n >= 1`。`n <= 0` 会读 `p[0]`，但现有调用者进不来。
- CJK 窗口 `bounds[3]`（132–153 行）只使用 0/1/2。`nChars` 不超过 `nToken`。
- `CJK_CLAMP_END`（113 行）是有符号加法。默认 SQLite 最大长度 1e9，两个合法偏移之和小于 `INT_MAX`。提高 `SQLITE_MAX_LENGTH` 到使 `iStart+v` 溢出才有 UB。现有调用链没有这种配置。不列为缺陷。
- `cjkCreate`（173–194 行）分配失败返回 `SQLITE_NOMEM`；`xCreate` 失败释放 `p`。内部 tokenizer 失败时是否已写出 `pInner` 取决于 SQLite，头文件不能证明。不把第三方契约失败报成自有泄漏。
- `cjkDelete`（196–202 行）先删内部对象再 `sqlite3_free`。
- 注册（231–251 行）导出 `sqlite3_ftscjk_init` 与 `sqlite3_fts5_cjk_init`。没有 `sqlite3_extension_init`。
- 无锁。回调在 SQLite 调用栈上，不保存 `pToken`。

未发现可证明的自有越界、UAF 或整数溢出。

### `hermes_state_fts.py` 与调用者

sha256 `8e6a1776d5d8c8cc13bf23b0fdd2a69da0e7ed9e010c7bda46cc0d7e9123dd3d`。

`load_fts5_cjk_extension`（113–128 行）在 `enable_load_extension(True)` 与 `False` 之间 `load_extension(str(path))`，异常则返回 `False`。写连接：`hermes_state.py:819`。只读连接：`hermes_state.py:889`，仅当写连接已加载。`hermes_state_repair.py:789`、`1100` 同样调用。

SQLite《Loading an Extension》：`zProc == NULL` 时先找 `sqlite3_extension_init`，再从文件名推导。`libfts5_cjk.so` 推导为 `sqlite3_fts5_cjk_init`，源码 249 行已导出。C 注释（21 行）写默认入口是 `sqlite3_ftscjk_init`，与文档推导名不符，但推导名存在。未执行加载，不把注释差异报成缺陷。很旧的、只认 `sqlite3_extension_init` 的 libsqlite 会加载失败并 fail-open；本次未探测链接的 libsqlite。

### `hermes_bootstrap.py`

sha256 `f242036b2d35b92283b9a7e97c7059e537607fc4b02026f657a6d00cb4683f69`。

`install_never_free_environ`（336–431 行）在导入时调用（478 行），仅当 glibc < 2.41（324–333 行）。它替换 `os.putenv`/`os.unsetenv`，把每个新的 `NAME=value` 放进 `lines`，并把发布过的 environ 数组放进 `arrays`，故意不释放，以避免 glibc 在 `getenv` 行走时释放旧数组。这不是 UAF 修复不完整的证据；代码写明同一 `NAME=value` 复用。

缺陷是值变化。键是完整的 `prefix + val`（385–386 行），没有淘汰。注释（347–348 行）说同名反复 set/del 在第一轮后不再分配，对同名新值不成立。`gateway/session_context.py:90` 的 `set_current_session_id` 每次把新的 `HERMES_SESSION_ID` 写入 `os.environ`。长寿命网关因此无界保留 ctypes 缓冲区。`arrays` 只在新增名字且旧数组没有空位时增长，会话 ID 替换不走那条路径。

`_unsetenv` 仍调用真实 `unsetenv`（424–427 行）。代码假定 glibc 只移动槽位不释放自有缓冲区。本次没有 glibc 源码，不把这一点报成 UAF。aarch64 普通存储重排是代码自己标明的残余（411–413 行），不另报。

`enable_windows_vt`（286–290 行）把 `get_osfhandle` 的整数传给未设 `argtypes` 的 `GetConsoleMode`。整数走默认 `c_int`，常见控制台句柄能通过。不与下面“传入 `c_void_p` 实例”的缺陷混为一谈。

### `tools/environments/base_output.py`

sha256 `e951f16776fa76589e03ea035d8ec40543993eb85e80ea609963190781a8d0b5`。

`_drain_fd_windows`（450–461 行）构造 `ctypes.c_void_p(raw_handle)`，未设置 `PeekNamedPipe.argtypes` 就传入。ctypes 对未声明原型的 ctypes 实例传入对象缓冲区地址，不是句柄值。64 位上该地址不是合法 HANDLE，调用失败，函数 `return`。Windows 管道输出排空因此看不到数据。内核会拒绝非法句柄，所以这是类型错误导致的逻辑失败，不是已证实的进程内写越界。仓库内没有别的地方给 `PeekNamedPipe` 设 `argtypes`。`windll` 是进程级缓存，若第三方先设了原型，表现会变；本仓库没有这种赋值。

### `tools/mcp_oauth.py`

sha256 `b25655d6a2ca717b3697f2fe98b5f817da5605c203ebdce8823da55398a8b99f`。

`_stdin_is_console`（329 行）同样把 `c_void_p` 实例传给未设 `argtypes` 的 `GetConsoleMode`。失败被当成“不是控制台”（330–331 行）。注释（312–316 行）要求用该 API 区分真实控制台和 NUL。真实控制台也会失败，交互式 OAuth 判断为假。方向是少启动浏览器，不是放行；仍然是 FFI 类型错误。仓库内没有 `GetConsoleMode.argtypes` 赋值。

### `hermes_cli/local_runtime/hardware.py`

sha256 `83ba7592a640ba6b2d1502ec7febe1006232dea26c361a1ee0ca703d3b66f923`。

`_cuda_driver_pool`（302–312 行）未设 `argtypes`。`cuDeviceGet` 的 `byref` 本身可用；随后把 `dev`（`c_int` 实例）传给 `cuDeviceTotalMem` 和 `cuDeviceGetAttribute`。未声明原型时，ctypes 实例被当成指针传入，设备号不是 `dev.value`。驱动返回错误后函数返回 `None`。调用者（361 行）回退到 `_engine_device_pool`。这是类型错误，不是已证实的驱动内越界。第三方 `libcuda` 未审计。

`GlobalMemoryStatusEx`（130–139 行）用 `byref` 且设置了 `dwLength`，结构与 `MEMORYSTATUSEX` 对齐。未单列为缺陷。

### `hermes_cli/process_identity.py`

sha256 `0e30d07f84e2bae9dee41fd6260e2e83b791a46b0a0042a665a6b72f25f143be`。

`attach_self_to_kill_on_close_job`（800–810 行）调用 `CreateJobObjectW`、`SetInformationJobObject`、`AssignProcessToJobObject`、`GetCurrentProcess`、`CloseHandle`，都没有 `argtypes`/`restype`。`hermes_platform/host/facts.py:62–63` 写明：不设 `restype` 时，ctypes 把 `GetCurrentProcess` 的伪句柄截成 `c_int`。伪句柄是 `(HANDLE)-1`。截断后的 `-1` 在 x64 上是否被符号扩展成完整伪句柄，本次未执行，不能当已证实失败。失败被 `except Exception` 吞掉并返回 `False`（814–816 行），kill-on-close 不生效，子进程可在父进程死后继续。这是可能的资源泄漏，不是 UAF。

`Affinity` 声明为 `POINTER(ULONG)`（788 行），真实字段是 `ULONG_PTR`。两者同宽，且 `LimitFlags` 不含 affinity 位，零值可忽略。不报越界。对照：`hermes_cli/local_runtime/processes.py:15–26` 用 `c_size_t` 并设置了 `argtypes`，那一处没有同一缺陷。

### `hermes_platform/resolver/app.py`

sha256 `ee5ff2a8d9d67534df22e722d7a7235e4c98810aeeedaf2d3948ea5d53db0063`。

`_pe_version`（267–271 行）调用 `VerQueryValueW` 后保存 `length`，但在 `ptr+8`/`ptr+12` 读取 `DWORD` 前不用它。117 行写明厂商 PE 是不可信输入。`except Exception` 接不住访问违例。Windows 对根路径 `\\` 成功时是否保证至少 16 字节，本次未验证。`create_string_buffer(size)`（258 行）也没有上限，恶意资源可要求巨大分配。列作需验证，不升成已证实越界。

`_pid_alive`（369–376 行）设置了 `OpenProcess`/`CloseHandle` 的类型并关闭句柄。未单列。

### 已读且未单列缺陷的 FFI

- `hermes_platform/host/gpu_adapters.py`（sha256 `1dfbff101084ab49ddefd4f3a938c4d52a6caa68269d0b0969dd652811be8383`）：`EnumAdapters1` 槽 12、`GetDesc1` 槽 10、`Release` 槽 2 与 IDXGIFactory1/IDXGIAdapter1 布局一致。失败路径释放 COM 对象。`CreateDXGIFactory1` 未设 `argtypes`，但参数是 `byref`。未发现可证明的槽位错误。
- `hermes_platform/host/darwin_pci.py`（sha256 `2ecbf4c68873f16f6de855794503c3ba781e3f26ea8da910a8ce5cd513f96fcb`）：匹配字典由 `IOServiceGetMatchingServices` 消费，迭代器在 `finally` 释放。`CFStringCreateWithCString` 失败时 `CFRelease` 收到空指针（59、75–76 行）。CoreFoundation 对空指针的 `CFRelease` 是未定义行为。触发是创建字符串失败，常见于内存耗尽。见问题 6。
- `hermes_platform/host/facts.py`：`sysctlbyname` 先取长度再写入不大于该长度的缓冲区；增长则第二次调用失败并返回空。`IsWow64Process2` 设置了 `HANDLE` 返回类型。`GlobalMemoryStatusEx` 设置了 `dwLength`。
- `hermes_platform/host/summary.py`：`GetUserNameW` 缓冲区 257，等于 `UNLEN+1`。超长显示名使 `GetUserNameExW` 失败并返回空，API 不会越过传入长度。`CFStringGetCString` 传入 64。`CFRelease` 在 `finally`。
- `hermes_state_dbfile.py`：`proc_listpids`/`proc_pidinfo` 在 `used < size` 后才解析，缓冲区按返回长度增长。偏移注释写明是对活描述符标定的，不是从头文件推导。没有当前 macOS 头，不把偏移报成错误。无上限的 `size *= 2` 只在 API 始终返回 `used >= size` 时增长；声明的 `argtypes` 与文档签名一致，不单列。
- `hermes_state_holders.py`：Restart Manager 结构大小与 `CCH_RM_*` 一致。`RmGetList` 在 `ERROR_MORE_DATA` 时重试。若 `count` 大于数组长度，ctypes 抛 `IndexError`，外层 fail-open，不是原生写越界。会话在 `finally` 结束。
- `hermes_dir_acl.py`：`process_elevation` 设置句柄类型并关闭非伪句柄。授权走 pywin32，并拒绝 reparse。未发现 ctypes 长度错误。
- `gateway/runtime_bootstrap_windows.py`（sha256 `7f39768e31e6082714304b32d75c175e3534d23d07aa1b1b31ac9e645668e32f`）：`_api` 设置 `argtypes`/`restype`。`TOKEN_USER` 首字段是 `PSID`，从缓冲区读指针再 `ConvertSidToStringSidW`，缓冲区仍在栈上。`RevertToSelf` 失败抛出专门异常。`LocalFree` 在 `finally`。ACE 在类型 0/1 时从 `ace+8` 读 SID，未检查 `AceSize`。命名管道在全局命名空间（`gateway/control_socket.py:51`），其他本地用户可抢先创建。内核通常拒绝过短 ACE；不能证明恶意 DACL 能穿过 `GetSecurityInfo`。不升成已证实越界。`CreateNamedPipe` 把 `ctypes.addressof` 交给 `_winapi`；未读 CPython `_winapi`，失败会抛异常。不列缺陷。
- `hermes_cli/_launchers.py`：`CreateEnvironmentBlock` 在 `finally` 销毁，Python 字典是拷贝。游标按 UTF-16 字节前进。未发现 UAF。
- `hermes_cli/update_lock.py`：`PROCESSENTRY32W.dwSize` 已设，`szExeFile` 260。`LockFileEx` 原型已设。`_windows_close`（821 行）对另一 `WinDLL` 调用未设 `argtypes` 的 `CloseHandle`。句柄通常落在 32 位内；大于 `2**31-1` 时 ctypes 对默认 `c_int` 抛 `OverflowError`，独占锁留到进程退出。见问题 7。
- `hermes_cli/local_runtime/devices.py`：ggml 符号设置了 `argtypes`。`c_char_p` 返回值会被 ctypes 拷贝。子进程退出即释放库。ggml 本体未审计。
- `hermes_cli/local_runtime/processes.py`：Job 限制结构用 `c_size_t` 表示 affinity，并设置了原型。`close` 只 `CloseHandle`，依赖 `KILL_ON_JOB_CLOSE`。与声明一致。
- `agent/sleep_clock.py`：`QueryUnbiasedInterruptTime` 的参数是 `byref`。忽略失败时时钟为 0。不构成越界。
- `hermes_cli/mem_trim.py`：`malloc_trim` 设置了 `c_size_t` 参数。未审 glibc。
- `plugins/web/ddgs/_search_worker.py`：`PyDLL` sleep 只在测试钩子，且设置了 `c_uint`。不是生产路径。
- `plugins/platforms/discord/adapter.py`：只用 `find_library`，没有缓冲区调用。
- `gateway/run_shutdown.py` 内嵌监视器把 `OpenProcess` 的 `restype` 设为 `c_void_p`，但 `WaitForSingleObject`/`CloseHandle` 无 `argtypes`。常见句柄值可用。与问题 7 同类，不另开高置信项。

### 安装器 Rust（非核心 import）

`bootstrap.rs` sha256 `d7f3eed7cf67ba5a40c36b160e778d864be631aeb0b26cf62d8ff5a42505dea4`。`marker.rs` sha256 `b3ad059a8c711ab7bba3e36517f28f2c412c9d6a5593e6ce586d9a75b1e6435e`。

生产 `unsafe`：`GetStdHandle` 后检查空和 `INVALID_HANDLE_VALUE`；`flock` 使用已打开文件的 fd；`LockFileEx` 使用零初始化的 `OVERLAPPED`；`proc_pidinfo` 在 `written == size` 后才读字段；`OpenProcess` 空句柄即返回并在成功路径 `CloseHandle`；`kill` 前把 pid 收成正的 `pid_t`，信号为 0。`marker_tests.rs` 的 `unsafe` 不是产品路径。未发现可证明的越界或 UAF。未审计 tauri/libc/windows-sys 本体。

## 问题

### 1. 无界保留 environ 字符串

- 文件：`hermes_bootstrap.py:373-386`，导入调用 `478`；写入点 `gateway/session_context.py:90`
- 严重程度：中
- 置信度：确定
- 描述：`lines` 以完整 `NAME=value` 为键，永不删除。这是 Python/ctypes 缓冲区留存，不是 C UAF。注释声称同名 churn 不再分配，对同名新值不成立。
- 触发：Linux 且 glibc < 2.41（函数否则直接返回），进程长寿命，并反复 `os.environ[同一名字] = 新值`。网关父进程对每个会话调用 `set_current_session_id`。glibc ≥ 2.41、非 Linux、或环境值集合有界时不增长。
- 修复建议（未改源码）：按名字保留一块可复用缓冲区；新值更长时再分配，并给历史条目一个上限，超限记日志。不能在仍可能被并发 `getenv` 读取时 `free`。

```python
slot = by_name.get(name)
if slot is None or len(slot) < len(prefix) + len(val) + 1:
    if len(by_name) >= CAP:
        raise RuntimeError("environ retention cap")
    slot = ctypes.create_string_buffer(max(len(prefix) + len(val) + 1, 64))
    by_name[name] = slot
slot.value = prefix + val
```

### 2. Windows 输出排空把对象地址当管道句柄

- 文件：`tools/environments/base_output.py:450`、`458`
- 严重程度：中
- 置信度：确定
- 描述：未设 `argtypes` 时，`c_void_p` 实例作为“指向对象”的指针传入。`PeekNamedPipe` 收到的不是 `raw_handle`。失败后函数返回，Windows 子进程输出不被排空。不是已证实的原生写越界。
- 触发：Windows 上走到 `_drain_fd_windows`。本仓库没有事先给该函数设置原型。
- 修复建议：

```python
kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.PeekNamedPipe.restype = wintypes.BOOL
ok = kernel32.PeekNamedPipe(raw_handle, None, 0, None, ctypes.byref(available), None)
```

传入整数句柄，并声明 `HANDLE`。不要包一层未声明原型的 `c_void_p` 实例。

### 3. 控制台探测同样传错句柄

- 文件：`tools/mcp_oauth.py:327-329`
- 严重程度：中
- 置信度：确定
- 描述：与问题 2 同一 ctypes 规则。`GetConsoleMode` 失败，`_stdin_is_console` 返回假。真实控制台被当成非交互，注释所依赖的区分失效。
- 触发：Windows，stdin `isatty()` 为真，且该函数未被其他模块预先设置 `argtypes`。
- 修复建议：

```python
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.GetConsoleMode.restype = wintypes.BOOL
return bool(k32.GetConsoleMode(handle, ctypes.byref(mode)))
```

### 4. CUDA 设备号传成指针

- 文件：`hermes_cli/local_runtime/hardware.py:302-312`
- 严重程度：低
- 置信度：确定
- 描述：`dev` 是 `c_int` 实例，未设 `argtypes` 时按指针传入。`cuDeviceTotalMem` / `cuDeviceGetAttribute` 得不到设备序号，函数返回 `None`。361 行有引擎回退。第三方 CUDA 驱动未审计。
- 触发：进程能加载 `nvcuda.dll` 或 `libcuda.so`，且 `cuInit`/`cuDeviceGet` 成功。
- 修复建议：

```python
getter.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_int]
getter.restype = ctypes.c_int
cuda.cuDeviceGetAttribute.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int]
if getter(ctypes.byref(total), dev.value) != 0:
    return None
```

### 5. kill-on-close Job 未声明句柄类型

- 文件：`hermes_cli/process_identity.py:800-810`
- 严重程度：中
- 置信度：可能
- 描述：`GetCurrentProcess` 无 `restype`。同仓库 `facts.py:62-63` 认为这会把伪句柄截成 `c_int`。`AssignProcessToJobObject` 也无 `argtypes`。失败被吞掉并返回 `False`，子进程树不会随本进程结束。不是 UAF。x64 调用约定若把 `-1` 符号扩展为完整伪句柄，则此路径仍可能成功。
- 触发：Windows，本函数第一次被调用，且截断后的句柄不被内核接受。
- 修复建议：按 `local_runtime/processes.py:51-60` 设置 `restype`/`argtypes`，`GetCurrentProcess` 的 `restype` 用 `wintypes.HANDLE`。`Affinity` 改为 `c_size_t`。不要用裸 `except` 把附着失败当成已附着。

### 6. 空 CFString 仍 CFRelease

- 文件：`hermes_platform/host/darwin_pci.py:59`、`75-76`
- 严重程度：低
- 置信度：可能
- 描述：`CFStringCreateWithCString` 失败返回空。`finally` 对每个 key 调用 `CFRelease`。空指针的 `CFRelease` 未定义，通常崩溃。
- 触发：macOS，IOKit 路径已创建迭代器，随后字符串创建失败。
- 修复建议：

```python
for key in keys.values():
    if key:
        cf.CFRelease(key)
```

### 7. PE 固定版本信息未核对返回长度

- 文件：`hermes_platform/resolver/app.py:258`、`267-271`
- 严重程度：低
- 置信度：需验证
- 描述：`length` 已取出但未使用。随后按偏移 8 和 12 读 `DWORD`。117 行把厂商 PE 当不可信输入。若 `VerQueryValueW` 在 `length < 16` 时仍成功，读取会超出该块；若块位于缓冲区末尾，则超出 `buf`。Windows 是否允许这种成功返回，本次未验证。`size` 也没有上限。
- 触发：Windows，解析器对一个带版本资源的 PE 调用 `_pe_version`，且资源被 API 接受但固定信息短于 16 字节，或 `size` 极大。
- 修复建议：

```python
if size > 1_048_576:
    return Observation(CheckState.ERROR, detail="version resource too large")
# after VerQueryValueW succeeds:
if length.value < 16:
    return Observation(CheckState.UNAVAILABLE, detail="fixed file info too small")
```

### 8. 更新锁 CloseHandle 未声明句柄宽度

- 文件：`hermes_cli/update_lock.py:818-821`
- 严重程度：低
- 置信度：可能
- 描述：`CreateFileW` 的 `restype` 是 `HANDLE`，返回 Python int。关闭时用另一个未设 `argtypes` 的 `WinDLL("kernel32").CloseHandle`。大于 `2**31-1` 的句柄会 `OverflowError`，独占文件锁留到进程退出。常见句柄值不会触发。
- 触发：Windows，`CreateFileW` 返回值大于 `2**31-1`，随后 `_windows_close`。
- 修复建议：复用已设置 `CloseHandle.argtypes = [wintypes.HANDLE]` 的那个 `kernel32`。

## 严重程度汇总

| 严重程度 | 数量 | 编号 |
| --- | --- | --- |
| 高 | 0 | 无已证实的自有 C/Rust 越界、UAF 或可利用整数溢出 |
| 中 | 4 | 1 确定；2 确定；3 确定；5 可能 |
| 低 | 4 | 4 确定；6 可能；7 需验证；8 可能 |

问题 1 是资源泄漏，不是 UAF。问题 2–4 是 FFI 类型错误；2 和 3 的已证实后果是 API 失败后的逻辑错误，不是已证实的进程内写越界。

## 未覆盖

- 未编译 `fts5_cjk`，未调用 `load_extension`，未在 Windows/macOS 上执行 ctypes。
- 未审计 SQLite、glibc、CUDA、ggml、DXGI、IOKit、Win32、tauri、libc、windows-sys 的实现。
- 未审计 `pyproject.toml` 中的原生轮子，不根据 pin 或文件内 CVE 注释报漏洞。
- 未进入 `node_modules`、`.venv`、`build/`、官方安装或生产数据。
- `hermes_state_dbfile.py` 的 Darwin 字段偏移只记录为经验值，未对照当前 `libproc.h`。
- 系统 `sqlite3ext.h` 与运行中的 libsqlite 是否同一 `fts5_api` 布局，未验证。
