"""Resume decisions. A live pid is never started again; a dead run is not retried blindly."""

from __future__ import annotations

import os
from pathlib import Path


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def decide(*, state: str, pid: int | None, resume_interrupted: bool, rerun: bool) -> str:
    """Return start, skip, attach, or refuse."""
    if rerun and state in {"pass", "fail", "blocked", "interrupted"} and not pid_alive(pid):
        return "start"
    if state == "running" and pid_alive(pid):
        return "attach"
    if state == "running" and not pid_alive(pid):
        return "start" if resume_interrupted else "refuse"
    if state in {"pass", "fail", "blocked", "interrupted", "in_progress"}:
        return "skip"
    return "start"


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"state": "absent", "pid": None}
    try:
        data = __import__("json").loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown", "pid": None}
    if not isinstance(data, dict):
        return {"state": "unknown", "pid": None}
    return data
