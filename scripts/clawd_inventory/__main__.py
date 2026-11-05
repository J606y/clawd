#!/usr/bin/env python3
"""离线入口。不联网，不下载许可证，不覆盖 dependency-source-inventory.json。

验收日后执行，本次不执行:
  python3 scripts/clawd_inventory --offline
  python3 scripts/clawd_inventory --local-only

测试日后执行，本次不执行，且不在 pyproject.toml 的 testpaths:
  python3 -m unittest scripts/clawd_inventory/test_generate.py

"network_audit": false
"""

from __future__ import annotations

import sys

from generate import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
