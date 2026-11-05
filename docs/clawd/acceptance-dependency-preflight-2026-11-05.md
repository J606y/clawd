# 验收依赖预检 2026-11-05

状态：ready。只补了固定 venv 缺失的已声明 YAML 解析器。未跑产品测试、未构建、未改源码/pyproject/lock、未 `uv sync`、未卸载其它包、未删缓存、未碰生产、`/root/.hermes` 或官方安装目录。

## 解释器

- 绝对路径：`/root/hermes-workspace/clawd/.venv/bin/python`
- 版本：Python 3.14.9；prefix=`/root/hermes-workspace/clawd/.venv`
- `pyvenv.cfg`：`uv = 0.13.5`，`include-system-site-packages = false`
- venv 内无 `pip`/`uv` 二进制，`python -m pip` 为 `No module named pip`
- 安装器：`/root/hermes-workspace/clawd/artifacts/runtime-boundaries/pty-home/tools/uv-0.13.5-linux-x64/uv`（`uv 0.13.5`）。未用系统 pip、Hermes 工具链 pip 或 `/usr/local/lib/hermes-agent` 的 pip

## 声明与安装选择

- 核心 `pyproject.toml` / `uv.lock`：`ruamel.yaml==0.18.16`，本机匹配 `ruamel.yaml.clib==0.2.15`。安装前 `env -i` 已能导入，未重装。
- sidecar `apps/panel/sidecar/requirements.txt` 与 `uv.lock`：`PyYAML==6.0.3`。安装前 `import yaml` 为 `ModuleNotFoundError`。只装这一份。
- PyYAML 无 Python 依赖。未顺带安装 lock 里依赖 pyyaml 的 `ctranslate2` / `huggingface-hub` / `transformers`。
- clib 的导入名是 `_ruamel_yaml`，不是 `ruamel.yaml.clib`。扩展已加载，不是隔离失败。

## 安装

```text
UV_CACHE_DIR=/root/.cache/uv \
/root/hermes-workspace/clawd/artifacts/runtime-boundaries/pty-home/tools/uv-0.13.5-linux-x64/uv \
  pip install --python /root/hermes-workspace/clawd/.venv/bin/python \
  --require-hashes --no-deps --only-binary PyYAML \
  --index-url https://pypi.org/simple \
  -r /root/hermes-workspace/clawd/apps/panel/sidecar/requirements.txt
```

exit=0。输出：`Resolved 1 package` / `Prepared 1 package` / `Installed 1 package` / `+ pyyaml==6.0.3`。

`--require-hashes` 对照 sidecar 哈希清单，其中包含 lock 的 cp314 manylinux x86_64 wheel `sha256:c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5`。安装后 WHEEL tag 为 `cp314-cp314-manylinux_2_17_x86_64` / `manylinux2014_x86_64` / `manylinux_2_28_x86_64`。INSTALLER=`uv`。

## env -i 复核

命令：`env -i PATH=/usr/bin:/bin HOME=/root /root/hermes-workspace/clawd/.venv/bin/python` 标准导入。exit=0。

| 包 | 版本 | 位置 |
|---|---|---|
| PyYAML (`import yaml`) | 6.0.3 | `/root/hermes-workspace/clawd/.venv/lib/python3.14/site-packages/yaml/__init__.py` |
| ruamel.yaml | 0.18.16 | `.../site-packages/ruamel/yaml/__init__.py` |
| ruamel.yaml.clib | 0.2.15 | `.../site-packages/_ruamel_yaml.cpython-314-x86_64-linux-gnu.so` |

`uv pip show` exit=0，location 同为该 site-packages。sys.path 含既有 `__editable__.hermes_agent-0.0.0.finder.__path_hook__`，但两个解析器都从 site-packages 解析，未被遮蔽。

前轮 `panel-python.log` 使用同一解释器，sidecar 因 `yaml is None` 报 `yaml_parser_unavailable`。本次只证明 `import yaml` 可用，未重跑该测试。

## 实时资源

核对时刻 2026-11-05T20:36:48+08:00。CPU 2。内存 available `2168254464` 字节。磁盘 `/` available `2034737152` 字节（约 1.90 GiB，90%），inode 剩余 804883（38%）。load `0.40 0.40 0.34`。无验收活进程。空间够用，未列清理路径，未删除，未连接用户 i9。

## 日志

`/root/hermes-workspace/clawd/artifacts/final-acceptance/logs/deps-preflight-20261105T203648+0800.log`
