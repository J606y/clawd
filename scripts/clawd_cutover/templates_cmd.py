"""Copy systemd/nginx examples into an explicit directory. Never installs them."""

from __future__ import annotations

import os
from pathlib import Path

from guard import GuardError, parent_exists
from manifest import discard_partial, utc_now, write_raw_json


_HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = _HERE / "templates"
TEMPLATE_FILES = (
    "clawd-panel.service.example",
    "clawd-nginx.conf.example",
)


def render_templates(out_dir: Path) -> dict:
    if out_dir.exists() and (out_dir.is_symlink() or not out_dir.is_dir()):
        raise GuardError("out-dir 必须是目录或不存在")
    if not out_dir.exists():
        parent_exists(out_dir)
        out_dir.mkdir(mode=0o700, parents=False)
    written = []
    for name in TEMPLATE_FILES:
        src = TEMPLATE_DIR / name
        if not src.is_file() or src.is_symlink():
            raise GuardError(f"缺少模板: {name}")
        text = src.read_text(encoding="utf-8")
        if "NOT_APPLIED" not in text:
            raise GuardError(f"模板未标记 NOT_APPLIED: {name}")
        dst = out_dir / name
        if dst.exists() or dst.is_symlink():
            raise GuardError(f"拒绝覆盖已存在模板输出: {name}")
        partial = dst.with_name(f".clawd-partial-{dst.name}-{os.getpid()}")
        if partial.exists() or partial.is_symlink():
            raise GuardError("临时模板输出已存在")
        try:
            partial.write_text(text, encoding="utf-8")
            os.replace(partial, dst)
        except Exception:
            discard_partial(partial)
            raise
        written.append(name)
    status = {
        "applied": False,
        "templates_only": True,
        "systemctl_invoked": False,
        "nginx_invoked": False,
        "files": written,
        "created_at": utc_now(),
        "note": "示例未应用。安装、替换占位符和重载服务都不在本工具内。",
    }
    write_raw_json(out_dir / "APPLIED.json", status)
    return status
