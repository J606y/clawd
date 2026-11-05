# 模板状态

这两个示例的状态是 `NOT_APPLIED`。

`render-templates` 只把它们复制到显式 `--out-dir`，并写 `APPLIED.json`，其中 `applied` 固定为 false。它不替换 `@CLAWD_HOME@`，不写入系统目录，不调用系统服务命令。
