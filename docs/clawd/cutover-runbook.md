# clawd 切换准备手册

本手册只描述离线准备工具。它不是切换授权，也不是验收通过记录。

状态：**未验证**。编写时没有运行本工具、没有运行配套测试、没有构建、没有迁移、没有部署。工具自己的 `verification.tool_tests` 固定为 `not_run`，没有开关可以改成通过。

入口：

```text
python3 scripts/clawd_cutover/cli.py <命令> [显式路径与选择]
```

没有默认数据目录，也没有默认 profile。下面的 `/isolated/clawd-root` 只是占位，必须换成这次隔离演练自己的目录。不要把占位符换成生产 Hermes 数据目录。

## 不做的事

- 不读、不写、不迁移生产 Hermes 数据。
- 不改现网面板检出、不安装单元、不重载站点、不调用系统服务命令。
- 不自动复制凭据，不把秘密值写入清单或标准输出。
- 不猜测 profile，不列出未选中的 profile 名。
- 不实现旧文件夹迁移。真实模块是 `clawd_projects/migration.py`，本工具只记录、不调用。
- 不删除目录，不覆盖未知文件，不把跨盘重命名降级成复制后删除。
- 不生成最终验收通过文件。`scripts/clawd_acceptance` 由另一代理准备；这里引用它，但不假定它已存在或已验证。
- 不修改 `docs/clawd/implementation-completion-manifest.json`。

## 只读核对过的启动契约

这些值来自现有 `clawd_server` 与 `docs/clawd/api-contract.md`，本工具不导入、不启动服务。

- 入口是 `python -m clawd_server`，默认子命令 `serve`。
- `--host` 默认 `127.0.0.1`，`--port` 默认 `8787`。
- 必须有显式 `CLAWD_HOME`。`HERMES_HOME` 未设置时才映射到同一目录；已设置则必须相同。
- 生产 home 被拒绝。配置与技能命令走该 home 下的 `bin/clawd`，不是系统 hermes。
- 应用内不重启系统单元。

模板把这些写成占位符，状态是 `NOT_APPLIED`。

## 命令

| 命令 | 默认行为 | 确认词 |
| --- | --- | --- |
| `gates` | 只打印闸门，不读业务目录 | 无 |
| `inventory` | 只读清单 | 无 |
| `plan` | 只读计划；缺少迁移选择时退出 2 | 无 |
| `backup` | 无 `BACKUP` 时只出计划 | `BACKUP` |
| `restore` | 无 `RESTORE` 时只出计划 | `RESTORE` |
| `apply` | 无 `APPLY` 时只出计划 | `APPLY` |
| `rollback` | 无 `ROLLBACK` 时只出计划 | `ROLLBACK` |
| `render-templates` | 无 `RENDER_TEMPLATES` 时只出计划 | `RENDER_TEMPLATES` |

复制凭据还要第二词 `COPY_CREDENTIALS`。只加 `--copy-credentials` 不会复制。

退出码：

- `0`：计划已写出，或变更已完成。完成不等于测试已通过。
- `2`：闸门拒绝，目标未改。
- `1`：已进入变更且失败。会尝试把已改名的树改回去。不删除目录。

计划示例：

```text
python3 scripts/clawd_cutover/cli.py plan \
  --allow-root /isolated/clawd-root \
  --source /isolated/clawd-root/source \
  --target /isolated/clawd-root/target \
  --migration-allowed no \
  --out /isolated/clawd-root/plan.json
```

`--out` 不得已存在，也不得位于 source、target 或 backup 里面。

## 确认闸门

`apply` 在确认词齐全后，仍须全部阻塞闸门通过。缺一项就退出 2，不改名。

1. `explicit_paths`：路径显式给出，无默认值。
2. `paths_checked`：在 `--allow-root` 内。拒绝生产 home、`/etc`、系统安装目录、系统单元目录、原面板检出和过宽的根。符号链接先按字面检查，再读链接目标；目标落入受保护路径则拒绝，不列举该目录。
3. `distinct_trees`：source、target、backup、输出不得相同或嵌套。
4. `target_parent`：父目录已存在，且在 allow-root 内。本工具不创建父级链。
5. `migration_choice`：`plan` 与 `apply` 必须显式 `--migration-allowed yes|no`。
6. `credentials_policy`：默认不复制凭据名文件。
7. `profile_policy`：未给 `--profile` 就是不处理，不是默认 profile。
8. `version_compatible`：两边版本都有且相同才算兼容。缺失或不一致会拒绝 apply，除非显式 `--allow-version-unknown` 或 `--allow-version-skew`。
9. `acceptance_passed`：apply 重核完整验收报告的 schema、结果、时间、源代码指纹和构建产物。短 pass JSON 不能放行。`prepare` 退出 0 不是验收全绿。
10. `confirm_token`：确认词必须完全一致，不忽略大小写。
11. `backup_slot`：执行 backup/apply 时备份目录必须尚不存在，父目录必须已存在。
12. `tree_complete`：source 不得有符号链接、特殊文件、超过 `--max-file-bytes` 的文件、跨文件系统子树或不可读目录。
13. `source_nonempty`：当前策略下要有可复制内容。
14. `systemd_nginx_not_applied`：模板未应用。清单若把 `applied` 写成 true，写出前会被拒绝。
15. `folder_migration_not_invoked`：真实模块是 `clawd_projects/migration.py`。选择 `yes` 时 apply 仍阻塞，因为本工具不调用它。选择 `no` 表示这次目录切换明确不含旧文件夹迁移。

最终切换还要人工确认：隔离服务、回滚演练和面板契约都已由父代理验收。本手册不能代替那次验收。

## 备份、恢复、切换、回滚

备份把选中的普通文件复制到新的备份目录，并写 `backup-manifest.json`。已有文件不覆盖。

`restore` 只创建清单中的缺失文件，或跳过哈希相同的文件。哈希不同、符号链接、非普通文件都拒绝，且在拒绝前不写。失败时只撤掉本次新建且哈希仍匹配的文件，留下本次新建的目录。

`apply` 顺序：

1. 再检查闸门。
2. 把 source 复制到新的 backup 目录；若 target 已存在，按同一跳过策略另存一份文件副本。
3. 在 target 同级创建 `target.clawd-staging-<id>` 并复制。
4. 写 `journal-pre.json`。
5. 若 target 已存在，把它改名为 `target.clawd-previous-<id>`，再把 staging 改名为 target。两步都只改到尚不存在的名字。
6. 成功后写 `journal-swapped.json`。previous 或失败槽位都保留。

改名失败时，若旧树已挪走且新名字还没占上，会把 previous 改回 target。staging 或备份目录若已创建，留在原地，不删除。跨文件系统改名直接失败，不改用复制后删除。

`rollback` 读取 `journal-swapped.json`。若该文件没写成，但 `journal-pre.json` 的路径与现场一致、staging 已不在、previous 已在，也可以把这份预写日记当作已完成切换。路径必须与 journal 一致。当前 target 改名为 `target.clawd-failed-<id>`，previous 改回 target。首次安装没有 previous：不要传 `--previous`，当前 target 只被改名到 failed 槽位，名字腾出，目录仍在。

文件副本受跳过策略限制，不能单独当完整回滚。完整回滚靠目录改名。

## 秘密与 profile

默认不打开、不复制这些名字：`.env*`、`config.yaml`、`config.yml`、`credentials*`、`auth.json`、`token.json`、`*.key`、`*.pem`、`secrets`、`.ssh`、`.aws`、`.gnupg`。清单可以有相对路径和“已跳过”，没有文件内容。

`--profile NAME` 只允许一个明确名字。未选择时不进入 `profiles/`，也不把兄弟目录的名字写入报告。不要用省略表示 default。

默认跳过 `.git`、`__pycache__`、`.venv`、`node_modules`、`.dev-home`、`artifacts`。需要纳入时显式 `--include-skipped`。

版本来自显式 `--source-version` / `--target-version`，否则只读 `pyproject.toml` 的 `project.version` 和 `.git/HEAD`。不执行 git。

## 旧文件夹接口

真实模块是 `clawd_projects/migration.py`。不存在 `clawd_projects/folder_migration.py`。本工具不导入、不调用、不读文件夹库，也不猜路径。

只读核对过的函数：

- `preview_folder_migration`
- `plan_folder_migration`
- `execute_folder_migration`
- `rollback_folder_migration`

回执成功只认 `success=true` 且 `status=applied`。回滚成功只认 `success=true` 且 `status=rolled_back`。预览或核对的 `success` 只表示读取完成。`partial` 不是成功。执行和回滚都要 `confirm=true`。home 必须是显式隔离 `CLAWD_HOME`。选择 `yes` 时 apply 仍阻塞，因为迁移不由本工具执行。

## 验收文件

apply 不接受下面这种短 JSON，即使四个字段都像通过：

```json
{"status": "passed", "final_acceptance": true, "covers_cutover": true, "producer": "scripts/clawd_acceptance"}
```

必须是 `scripts/clawd_acceptance` 写出的完整报告，并且本工具重核：

- `schema_version=1`、`kind=clawd-final-acceptance`、`producer` 完全一致
- `phase=run`、`executed=true`、`plan_only=false`、`acceptance_green=true`
- `summary.exit_code=0` 且 `summary.status=pass`
- 每个必需检查都是 `pass`；`not_run`、`unknown`、`blocked` 不能当成通过
- `generated_at` 与源代码 `captured_at` 都是 UTC 时间
- 源代码 `git_head`、`pyproject_version` 和指纹与当前检出一致
- 报告里的构建产物相对路径能重新算出相同 sha256
- `rollback` 检查实际通过；`deploy-support` 若被写成 pass 则拒绝
- `prepare` 进程退出 0 只表示计划已写，报告里的验收退出码仍是 2

没有这份报告时 apply 拒绝。回滚不依赖它。

## 模板

```text
python3 scripts/clawd_cutover/cli.py render-templates \
  --allow-root /isolated/clawd-root \
  --out-dir /isolated/clawd-root/templates-out \
  --confirm RENDER_TEMPLATES
```

复制 `scripts/clawd_cutover/templates/` 里的两个示例，并写 `APPLIED.json`。`applied` 固定 false。不替换 `@CLAWD_HOME@`，不写入系统目录。缺少确认词时只出计划。未应用模板不是部署支持已验证。确认词不能越过生产路径。

## 配套测试

未运行：

- `scripts/clawd_cutover/tests/test_prepare.py`
- `scripts/clawd_cutover/tests/test_static_policy.py`
- `scripts/clawd_cutover/tests/test_acceptance_gate.py`

说明见 `scripts/clawd_cutover/tests/NOT_RUN.md`。静态阅读不能代替运行结果。
