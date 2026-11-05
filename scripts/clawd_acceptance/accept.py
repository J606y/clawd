#!/usr/bin/env python3
"""最终验收入口。无参数时不执行，退出 2。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from constants import ACCEPT_ROOT, REPO
from execute import prepare, report_only, run_all
from isolation import Layout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clawd-acceptance",
        description="clawd 最终一次验收。默认不执行。",
    )
    parser.add_argument("phase", nargs="?", choices=("prepare", "run", "report"))
    parser.add_argument("--run", action="store_true", help="与 phase=run 同时给出才允许进入运行")
    parser.add_argument("--implementation-complete", action="store_true")
    parser.add_argument("--resume-interrupted", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--provider-config")
    parser.add_argument("--rollback-entry")
    parser.add_argument("--install-prefix")
    args = parser.parse_args(argv)
    if args.phase is None:
        print("clawd 最终验收尚未执行。实现全部交付后再 prepare，然后显式 run --run --implementation-complete。")
        print("本次退出 2，不是通过。")
        return 2
    layout = Layout(ACCEPT_ROOT)
    if args.phase == "prepare":
        prepare(REPO, layout)
        print(f"prepare 只写计划，验收未执行：{layout.root / 'prepare-plan.json'}")
        return 0
    if args.phase == "report":
        code = report_only(REPO, layout)
        print(f"report exit {code}。这不是新的阶段执行。")
        return code
    if not args.run or not args.implementation_complete:
        print("run 被拒绝：必须同时给出 --run 和 --implementation-complete。未启动任何阶段。")
        return 2
    return run_all(
        REPO,
        layout,
        {
            "run": True,
            "implementation_complete": True,
            "resume_interrupted": args.resume_interrupted,
            "rerun": args.rerun,
            "provider_config": args.provider_config,
            "rollback_entry": args.rollback_entry,
            "install_prefix": args.install_prefix,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
