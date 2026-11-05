"""Pytest entry for the real react/lib reducer cases.

Does not listen, install dependencies, or call a model. Not executed when written.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_react_event_reducer_cases() -> None:
    node = shutil.which("node")
    assert node, "node is required to load apps/panel react/lib; this case does not install it"
    script = Path(__file__).with_name("frontend-event-recovery.test.mjs")
    assert script.is_file(), script
    completed = subprocess.run(
        [node, "--test", "--test-concurrency=1", str(script)],
        check=False,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
