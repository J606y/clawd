#!/usr/bin/env python3
"""Stage built panel/sidecar resources. Does not download, build, or install.

Exit 0 when the bundle is complete. Exit 2 when required inputs are missing
or a path is unsafe. Does not invoke a frontend toolchain or a package installer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clawd_server.resources import (
    PANEL_RESOURCE_ROOT_ENV,
    ResourceError,
    bundled_is_complete,
    resource_root_problems,
    source_problems,
    stage_distribution,
)


def _check(resource_root: str | None) -> int:
    panel = ROOT / "apps" / "panel"
    problems: list[str] = []
    if panel.is_dir() and not panel.is_symlink():
        problems.extend(source_problems(ROOT))
    else:
        chosen = Path(resource_root) if resource_root else None
        if chosen is None and os_env():
            chosen = Path(os_env())
        if chosen is None:
            problems.append(
                "找不到 apps/panel，且未提供资源根。"
                "不会把预置 bundled 当作已构建。"
            )
        else:
            problems.extend(resource_root_problems(chosen))
        license_path = ROOT / "LICENSE"
        if not license_path.is_file() or license_path.is_symlink():
            problems.append("仓库 LICENSE 缺失，拒绝打包无许可证的资源副本。")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    print("resources ready")
    return 0


def os_env() -> str:
    import os

    return os.environ.get(PANEL_RESOURCE_ROOT_ENV, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stage clawd panel and sidecar resources")
    parser.add_argument("--check", action="store_true", help="validate inputs only; do not write")
    parser.add_argument("--dest", default=None, help="bundle directory (default: clawd_server/bundled)")
    parser.add_argument(
        "--resource-root",
        default=None,
        help="built panel root used only when apps/panel is absent",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="swap a verified bundle at a non-canonical dest; never deletes unknown data",
    )
    parser.add_argument("--print-layout", action="store_true", help="print checkout paths and exit")
    args = parser.parse_args(argv)
    if args.print_layout:
        from clawd_server.resources import code_root, layout, panel_root, panel_static_dir

        print(f"layout={layout()}")
        print(f"code_root={code_root()}")
        print(f"panel_root={panel_root()}")
        print(f"panel_static={panel_static_dir(require_built=False)}")
        return 0
    if args.check:
        return _check(args.resource_root)
    dest = Path(args.dest) if args.dest else None
    resource_root = Path(args.resource_root) if args.resource_root else None
    try:
        staged = stage_distribution(
            ROOT,
            dest,
            replace=args.replace,
            resource_root=resource_root,
        )
    except ResourceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not bundled_is_complete(staged):
        print(f"staged bundle incomplete: {staged}", file=sys.stderr)
        return 2
    print(f"staged {staged}")
    kept = sorted(staged.parent.glob(f".{staged.name}.kept-*"))
    if kept:
        print(f"previous bundle retained at {kept[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
