# clawd 依赖来源清单 schema

本文件只说明 `scripts/clawd_inventory` 的离线输出。不是生成器运行结果，不是全量锁摘录，不是许可证法律结论，不是安全扫描。

## 验收接线（本次不执行）

工作目录为检出根。标准输出是 JSON。不要把 `--output` 指到 `docs/clawd/dependency-source-inventory.json`，除非另行要求替换快照。

```text
python3 scripts/clawd_inventory --offline
python3 scripts/clawd_inventory --local-only
```

`--offline` 与 `--local-only` 相同。未给 `--checkout` / `--input` 时，只读脚本所在检出的固定文件列表，不扫描 `node_modules`、`venv`。有输入时不额外读取检出。退出码：可读且解析成功为 0；路径被拒、不可读或解析失败为 2。根 `NOTICE` 不存在不是错误。

`"network_audit": false`

测试命令，本次不执行，未运行不是通过：

```text
python3 -m unittest scripts/clawd_inventory/test_generate.py
```

## 字段

`schema_version` 仍为 1。新增字段只追加。`license` 与 `packaged` 恒为 `unknown`。锁或清单里的 license 字符串只进入 `declared_license`，是元数据，不构成许可判定。缺字段保持 `unknown`，不填核心 MIT。

依赖行：

- `ecosystem`：`pypi`、`npm` 或 `requirements`
- `relation`：`direct`、`transitive`、`first_party` 或 `unknown`
- `install_class`：`runtime`、`optional`、`development`、`build`、`peer`、`sidecar_pin`、`mixed` 或 `unknown`
- `possible_install_classes`：无法单一判定时的候选，不含猜测许可证
- `scope_certainty`：`declared`、`exact`、`marker_dependent`、`lock_flag` 或 `unknown`
- `source` / `source_type`：只抄锁里出现的值。Python 见过 `registry`、`url`、`git`、`editable`。npm 见过 `registry` 与 `link`；`resolved` 若为其他 `https` 记 `url`，若以 `git+`、`git://`、`github:`、`gitlab:`、`bitbucket:` 开头记 `git`，若以 `file:` 开头记 `file`。仓库 npm 锁未观察到 git `resolved`，不能把未执行的解析说成已核对
- `version`、`git_rev`、`integrity`、`artifacts`：没有就不填。`integrity` 缺省为 `unknown`，不下载、不计算缺失文件的哈希
- `lock_dev` / `lock_optional` / `lock_peer`：只反映锁条目上的布尔字段
- `lock_file` / `lock_path`：来源锁与 npm `packages` 键

Python 的 `transitive` 表示从项目依赖、optional-dependencies、dev-dependencies 或边的 `extra` 走到。走不到的锁包是 `unknown`，不叫传递。npm 的 `transitive` 表示锁里有直接依赖声明、而该条目不是直接声明；这是锁成员关系，不是逐条父边证明。

`components.license` 只有同目录 NOTICE 标题为 `MIT License` 且含 copyright 时才记 MIT，且 `license_basis` 为 `sibling_notice_file`。该判定不外溢到面板、TUI、`@hermes/ink`、`@hermes/shared` 或其他依赖。这些内部包许可证仍未知，须用户确认，本路不新写 LICENSE。哪些依赖进入 wheel 或前端包，一律 `unknown`。
