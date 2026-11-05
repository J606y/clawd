# 未运行

这些测试在本次交付中没有执行。没有退出码，没有通过数量，不能当成验收。

隔离运行示例（不要改成生产数据目录）：

```
python3 -m unittest scripts/clawd_cutover/tests/test_prepare.py
python3 -m unittest scripts/clawd_cutover/tests/test_static_policy.py
python3 -m unittest scripts/clawd_cutover/tests/test_acceptance_gate.py
```

`verification.tool_tests` 在工具输出中固定为 `not_run`。测试真的跑过之后，也要另有记录才能改这个结论；本工具没有自行标通过的开关。
