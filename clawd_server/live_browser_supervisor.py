"""Core-owned supervision of the shared live browser.

The live browser controller (``clawd_server.live_browser_runtime``) stays a
separate process -- Chromium / Xvfb / x11vnc are never children of the panel
process -- but its lifecycle is owned by the core (``PanelServer``), not only
by the ``clawd-live-browser.service`` systemd unit:

* start: if the controller is already healthy on 127.0.0.1:39232, adopt it
  (monitor-only, never kill it); otherwise spawn it as a core child with the
  same argv the systemd unit uses.
* monitor: poll ``/health``; restart an owned controller that exits, with
  bounded backoff. Never restart on ``running == False`` -- that is the
  normal idle state (worker stopped, controller alive).
* wake: ``ensure_browser()`` opens a CDP connection through the 39234 proxy,
  which is the runtime's own wake path (``/health`` and direct 39233 do not
  wake). This keeps a cold start out of ``browser_exec``'s attach budget.
* idle shutdown stays inside the runtime (``IDLE_SECONDS = 10``): the
  supervisor never keeps the worker warm and never restarts a worker that
  the runtime stopped for idleness.
* stop: terminate only a controller this supervisor spawned. An adopted
  (systemd-owned) controller is left running.

Ports, display, profile and state directories are unchanged
(39232 controller / 39233 Chromium CDP / 39234 CDP proxy / :98 / 15900).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("clawd.panel.live_supervisor")

CONTROLLER_HTTP = "http://127.0.0.1:39232"
HEALTH_URL = "http://127.0.0.1:39232/health"
CDP_PROXY_HTTP = "http://127.0.0.1:39234/json/version"
HEALTH_TIMEOUT_S = 5.0
HEALTH_INTERVAL_S = 2.0
HEALTH_FAILURES_BEFORE_RESTART = 3
RESTART_BACKOFF_S = 3.0
RESTART_BACKOFF_CAP_S = 30.0
STOP_TIMEOUT_S = 20.0  # matches the systemd unit's TimeoutStopSec


class LiveBrowserSupervisor:
    """Own the live-browser controller process on behalf of the core."""

    def __init__(self, home: Path, repo_root: Path,
                 python_exe: str | None = None) -> None:
        self.home = Path(home)
        self.repo_root = Path(repo_root)
        self.python_exe = python_exe or sys.executable
        self.proc: asyncio.subprocess.Process | None = None
        self.owned = False
        self._monitor_task: asyncio.Task | None = None
        self._stopping = False
        self._backoff = RESTART_BACKOFF_S
        self._health_failures = 0
        self.last_error: str | None = None

    # -- health ---------------------------------------------------------
    async def _health(self) -> dict[str, Any] | None:
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            return None
        try:
            async with ClientSession(
                timeout=ClientTimeout(total=HEALTH_TIMEOUT_S)
            ) as session:
                async with session.get(HEALTH_URL) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("protocol") != "clawd-live-v1":
            return None
        if str(payload.get("home") or "") != str(self.home):
            return None
        return payload

    async def is_healthy(self) -> bool:
        return await self._health() is not None

    # -- start / spawn --------------------------------------------------
    async def start(self) -> str:
        """Adopt a healthy controller, or spawn one. Returns 'adopted'/'spawned'/'unavailable'."""
        payload = await self._health()
        if payload is not None:
            self.owned = False
            self._monitor_task = asyncio.create_task(
                self._monitor(), name="live-browser-supervisor")
            log.info("live browser controller adopted (already healthy)")
            return "adopted"
        spawned = await self._spawn()
        self._monitor_task = asyncio.create_task(
            self._monitor(), name="live-browser-supervisor")
        return "spawned" if spawned else "unavailable"

    async def _spawn(self) -> bool:
        if self._stopping:
            return False
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.python_exe, "-m", "clawd_server.live_browser_runtime",
                "--home", str(self.home),
                cwd=str(self.repo_root), env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            self.last_error = f"spawn_failed: {exc}"
            log.warning("live browser spawn failed: %s", exc)
            return False
        self.owned = True
        self._backoff = RESTART_BACKOFF_S
        log.info("live browser controller spawned pid=%s", self.proc.pid)
        # Wait briefly for the controller to bind; do not block panel start
        # on the worker (Chromium cold start is the wake path's job).
        for _ in range(10):
            if await self._health() is not None:
                return True
            await asyncio.sleep(0.5)
        log.warning("live browser controller spawned but /health not up yet")
        return True

    # -- monitor --------------------------------------------------------
    async def _monitor(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(HEALTH_INTERVAL_S)
                if self._stopping:
                    break
                payload = await self._health()
                if payload is not None:
                    self._health_failures = 0
                    self._backoff = RESTART_BACKOFF_S
                    continue
                self._health_failures += 1
                if self.owned and self.proc is not None and self.proc.returncode is not None:
                    log.warning(
                        "live browser controller exited rc=%s; restarting",
                        self.proc.returncode)
                    self.proc = None
                    await self._restart_owned()
                    continue
                if self._health_failures < HEALTH_FAILURES_BEFORE_RESTART:
                    continue
                # Adopted controller is gone (or never came up): try to own it.
                # If the systemd unit restarts it first, _spawn's bind will
                # fail with port_occupied inside the runtime and the process
                # exits; the next loop then adopts the healthy one again.
                self._health_failures = 0
                if self.proc is not None and self.proc.returncode is None:
                    continue  # owned proc still alive; health may recover
                if await self._spawn():
                    log.info("live browser controller restarted by core")
            except asyncio.CancelledError:
                break
            except Exception as exc:  # monitor must never kill the panel
                log.warning("live browser supervisor monitor: %s", exc)
                await asyncio.sleep(HEALTH_INTERVAL_S)

    async def _restart_owned(self) -> None:
        delay = self._backoff
        self._backoff = min(self._backoff * 2, RESTART_BACKOFF_CAP_S)
        await asyncio.sleep(delay)
        if self._stopping:
            return
        if await self._health() is not None:
            # Something else (e.g. the systemd unit) already owns it.
            self.owned = False
            self.proc = None
            return
        await self._spawn()

    # -- wake -----------------------------------------------------------
    async def ensure_browser(self, deadline_s: float = 20.0) -> bool:
        """Wake the worker (if idle-stopped) and wait until CDP answers.

        Goes through the 39234 proxy, which is the runtime's own wake path:
        connecting there calls ``ensure_worker``; ``/health`` alone does not
        wake. Returns True once ``/json/version`` answers, False on timeout.
        """
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            return False
        deadline = time.monotonic() + max(1.0, deadline_s)
        while time.monotonic() < deadline:
            if self._stopping:
                return False
            try:
                async with ClientSession(
                    timeout=ClientTimeout(total=5.0)
                ) as session:
                    async with session.get(CDP_PROXY_HTTP) as resp:
                        if resp.status == 200:
                            return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        self.last_error = "wake_timeout"
        return False

    # -- stop -----------------------------------------------------------
    async def stop(self) -> None:
        self._stopping = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        proc, self.proc = self.proc, None
        if proc is None or not self.owned:
            # Adopted (systemd-owned) controller: leave it running.
            return
        if proc.returncode is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=STOP_TIMEOUT_S)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        log.info("live browser controller stopped")
