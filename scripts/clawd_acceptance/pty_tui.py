"""Real PTY /quit probe. Invoked only by the run phase, never on import."""

from __future__ import annotations

import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

from isolation import Layout, direct_env

UI_TIMEOUT = 60
QUIT_TIMEOUT = 20
ROWS, COLS = 40, 120


def _winsize(fd: int) -> None:
    fcntl_mod = __import__("fcntl")
    fcntl_mod.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))


def _replies(chunk: bytes) -> bytes:
    out = bytearray()
    if b"\x1b[c" in chunk or b"\x1b[0c" in chunk:
        out += b"\x1b[?1;2c"
    start = 0
    while True:
        index = chunk.find(b"\x1b[?", start)
        if index < 0:
            break
        end = chunk.find(b"$p", index)
        if end < 0 or end - index > 24:
            break
        mode = chunk[index + 3 : end]
        if mode.isdigit():
            out += b"\x1b[?" + mode + b";1$y"
        start = end + 2
    if b"\x1b[>0q" in chunk:
        out += b"\x1bP>|xterm(384)\x1b\\"
    return bytes(out)


def _read(fd: int, buf: bytearray, deadline: float, stop: bytes | None = None) -> None:
    while time.monotonic() < deadline:
        if stop and stop in buf:
            return
        ready, _, _ = select.select([fd], [], [], 0.3)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        buf.extend(chunk)
        reply = _replies(chunk)
        if reply:
            try:
                os.write(fd, reply)
            except OSError:
                return


def _product_group(pid: int) -> list[str]:
    try:
        text = subprocess.run(
            ["ps", "-o", "pid=,ppid=,args=", "-g", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
    except OSError as exc:
        return [f"ps-error {exc}"]
    rows = []
    for line in text.splitlines():
        if "gateway run" in line and "--idle-exit" in line:
            continue
        if line.strip():
            rows.append(line[:240])
    return rows


def run_pty(repo: Path, layout: Layout, log_path: Path) -> dict:
    dist = repo / "ui-tui" / "dist" / "entry.js"
    if not dist.is_file():
        return {
            "status": "blocked",
            "exit_code": None,
            "detail": "ui-tui/dist/entry.js missing; probe will not rebuild",
            "quit_written": 0,
            "quit_echoed": False,
            "forced": "not-started",
        }
    layout.ensure()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    master, slave = pty.openpty()
    _winsize(slave)
    env = direct_env(layout, repo)
    env["COLUMNS"] = str(COLS)
    env["LINES"] = str(ROWS)
    proc = subprocess.Popen(
        [str(repo / ".venv" / "bin" / "python"), str(repo / "clawd"), "--tui"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=str(repo),
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    buf = bytearray()
    quit_written = 0
    quit_echoed = False
    forced = ""
    code: int | None = None
    interactive = False
    try:
        _read(master, buf, time.monotonic() + UI_TIMEOUT, b"/help")
        interactive = b"/help" in buf or b" tools" in buf
        before = len(buf)
        if interactive:
            time.sleep(0.4)
            payload = b"/quit\r"
            quit_written = os.write(master, payload)
            _read(master, buf, time.monotonic() + 2)
            quit_echoed = b"/quit" in buf[before:]
            if quit_echoed:
                deadline = time.monotonic() + QUIT_TIMEOUT
                while time.monotonic() < deadline and proc.poll() is None:
                    _read(master, buf, time.monotonic() + 0.4)
                code = proc.poll()
        if code is None:
            code = proc.poll()
        text = buf.decode("utf-8", "replace")
        storage = "storage_unavailable" in text
        if storage:
            status = "fail"
            detail = "storage_unavailable remains a failure even if a later prompt appeared"
        elif not interactive or quit_written <= 0 or not quit_echoed:
            status = "blocked"
            detail = "probe did not confirm /quit was received; cleanup is not a product failure"
            forced = forced or "not-confirmed"
        elif code == 0 and not _product_group(proc.pid):
            status = "pass"
            detail = "natural exit 0 after echoed /quit"
        else:
            status = "fail"
            detail = f"exit={code}; forced cleanup is not a clean pass"
            forced = forced or "not-clean"
        log_path.write_text(
            "\n".join(
                [
                    f"exit={code}",
                    f"status={status}",
                    f"interactive={interactive}",
                    f"quit_written={quit_written}",
                    f"quit_echoed={quit_echoed}",
                    f"forced={forced or 'none'}",
                    f"storage_unavailable={storage}",
                    "---- screen ----",
                    text,
                ]
            ),
            encoding="utf-8",
        )
        return {
            "status": status,
            "exit_code": code,
            "detail": detail,
            "quit_written": quit_written,
            "quit_echoed": quit_echoed,
            "forced": forced or "none",
        }
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait(timeout=5)
        try:
            os.close(master)
        except OSError:
            pass
