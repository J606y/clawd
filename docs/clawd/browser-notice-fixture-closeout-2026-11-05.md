# NOTICE 哈希与旧 PYTHONPATH 夹具收口 — 2026-11-05

本文件是静态收口记录，不是验收，不是上线许可，不是浏览器迁移完成。

**未运行。** 未跑 pytest，未 build，未 import 产品模块，未 py_compile，未启动 CLI，未做 CDP、网络、浏览器、dialog 或 frame 探针。未 commit、push、deploy。未删文件。未改 browser 产品源码、其他 vendor 字节、LICENSE、母函数 `_base_subprocess_env`、启动门禁 `verify_vendor_tree`。未读生产配置、秘密、已安装包、`/usr/local/lib/hermes-agent` 或原面板。未把集成报告里的哈希抄进 NOTICE。

活浏览器是否发出 `frameId`、dialog 是否真正应答，本轮没有探针。不能写成 Chrome 已发送或 dialog/frame 验证成功。

## 哈希

对照的是当前文件字节和锁定 sdist 成员字节，不是报告原文。

| 物件 | 角色 | sha256 | 本轮 |
|---|---|---|---|
| sdist `artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz` | 原件来源 | `284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3` | 114905 字节，与 NOTICE 原记录一致，未改 |
| vendor `LICENSE` | 原件 | `5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631` | 与 sdist 相同，未改 |
| `daemon.py` 未打补丁 | 原件 | `7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97` | sdist 成员 40951 字节。NOTICE 原样保留 |
| `daemon.py` 本副本 | 补丁后 | `781609813fa0f0d20dda3c5a634d3bc6a01cc7403d8d7b303874903b15434d45` | 当前树 54824 字节。NOTICE 从旧记录 `fd5e6dd82870d98235a0038243d60a295d375620c9f52759c49c4f8430008d5a` 改为这个值 |
| `helpers.py` 未打补丁 | 原件 | `5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3` | sdist 成员 27779 字节。NOTICE 原样保留 |
| `helpers.py` 本副本 | 补丁后 | `171744c2ff9c86321d1487c80eb975b433ee4aca9a02b26857fe11be40bd0a4a` | 当前树 31550 字节，与旧 NOTICE 一致，未改数字 |
| `_ipc.py` | 原件，不是补丁 | `b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3` | 当前树与 sdist 都是 9158 字节，同一哈希。未改文件 |

上游标签 `v0.1.13`、先前提交 `c24e5072ee66f8499bacd663f4f4bcb089bc4492`、版权行 `Copyright (c) 2026 Browser Use`、MIT 全文位置都保留。本轮未联网复核该提交。补丁仍不移除上游版权。仓库根 Nous LICENSE 不覆盖这份第三方许可。

NOTICE 现在写明：未打补丁哈希是 sdist 原件；本副本哈希是当前补丁后字节。二者不是同一份。

## 断言

`tests/tools/test_browser_use_cli.py` 的 `test_subprocess_env_replaces_parent_python_import_paths` 仍在，没有删 case，没有改成只 `contains`。

旧断言是 `env["PYTHONPATH"] == "/harness-site"`。现断言是整表相等：

```text
parts == [str(verify_vendor_tree()), "/harness-site"]
parts[0] == vendor
parts[1] == "/harness-site"
```

`_harness_site_dir` 仍被钉成 `/harness-site`。父进程注入的 `/hermes`、`/hermes/venv/lib/site-packages` 不得作为路径项留下，`PYTHONHOME` 仍不得出现，`KEEP_ME` 仍须保留。母函数和启动门禁没改。这只是夹具准备，本轮没有执行它。

## 路径

报告：`/root/hermes-workspace/clawd/docs/clawd/browser-notice-fixture-closeout-2026-11-05.md`

NOTICE：`/root/hermes-workspace/clawd/tools/vendor/browser_harness_0_1_13/NOTICE`

用例：`/root/hermes-workspace/clawd/tests/tools/test_browser_use_cli.py`

## 未做

- 未运行上述用例、`--version`、全量、构建、CLI 或真实浏览器。
- 未验证 dialog 或跨域 frame。缺 `frameId` 时的失败关闭仍是既有缺口，不是本轮结果。
- 未改 `daemon.py`、`helpers.py`、`_ipc.py`、LICENSE 或其他 vendor 字节。
- 不能把本文件说成浏览器迁移完成。
