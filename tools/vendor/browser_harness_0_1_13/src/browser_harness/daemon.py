"""CDP WS holder + IPC relay (Unix socket on POSIX, TCP loopback on Windows). One daemon per BU_NAME."""
import asyncio, hmac, json, os, platform, secrets, socket, sys, time, urllib.error, urllib.request
from urllib.parse import urlparse
from collections import deque
from pathlib import Path

from . import _ipc as ipc
from . import auth
from . import paths
from cdp_use.client import CDPClient


def _load_env():
    repo_root = Path(__file__).resolve().parents[2]
    workspace = paths.workspace_dir()
    for p in (repo_root / ".env", workspace / ".env"):
        if not p.exists():
            continue
        _load_env_file(p)


def _load_env_file(p):
    for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NAME = os.environ.get("BU_NAME", "default")
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))
BUF = 500
_REFUSED_CLOSE_METHODS = frozenset({
    "Browser.close",
    "Target.disposeBrowserContext",
    "Page.close",
})
# Reviewed session domains. Not an allowlist: any non-Target method is delivered
# on a session, including domains absent from this tuple. Listed so a prefix
# gate cannot drop DOM, Network, Fetch, CSS, Debugger, Emulation, and the rest.
_SESSION_SCOPED_DOMAINS = frozenset({
    "Accessibility", "Animation", "Audits", "Autofill", "BackgroundService",
    "BluetoothEmulation", "Browser", "CSS", "CacheStorage", "Cast", "Console",
    "DOM", "DOMDebugger", "DOMSnapshot", "DOMStorage", "Debugger",
    "DeviceOrientation", "Emulation", "EventBreakpoints", "FedCm", "Fetch",
    "FileSystem", "HeadlessExperimental", "HeapProfiler", "IO", "IndexedDB",
    "Input", "Inspector", "LayerTree", "Log", "Media", "Memory", "Network",
    "Overlay", "Page", "Performance", "PerformanceTimeline", "Preload",
    "Profiler", "Runtime", "Security", "ServiceWorker", "Storage", "Tracing",
    "WebAudio", "WebAuthn",
})
# Methods whose parameters name a target or session even when Target.* strips
# the IPC session. Param scanning is not limited to this set.
_KNOWN_TARGET_PARAM_METHODS = frozenset({
    "Target.activateTarget",
    "Target.attachToTarget",
    "Target.autoAttachRelated",
    "Target.closeTarget",
    "Target.detachFromTarget",
    "Target.exposeDevToolsProtocol",
    "Target.getTargetInfo",
    "Target.sendMessageToTarget",
    "Browser.getWindowForTarget",
})
_TARGET_PARAM_KEYS = ("targetId", "target_id")
_SESSION_PARAM_KEYS = ("sessionId", "session_id")
TASK_GRANT_VERSION = 1


def command_address_classes(method):
    """How a CDP method can name a task target. Not an allowlist.

    ``session`` — every non-Target method, sent on the IPC session or the
    default session. Covers Page, Runtime, Input, DOM, Network, Fetch, and
    unknown domains the same way.
    ``target_param`` / ``session_param`` — params targetId or sessionId,
    including Target.sendMessageToTarget and Browser.getWindowForTarget.
    ``ipc_session`` — Target.* does not forward session_id, but naming a task
    session on the request is still a task call.
    ``refused_close`` — Browser.close, Target.disposeBrowserContext, Page.close.
    """
    method = method if isinstance(method, str) else ""
    if method in _REFUSED_CLOSE_METHODS:
        return ("refused_close",)
    domain = method.split(".", 1)[0] if "." in method else ""
    if method.startswith("Target."):
        return ("target_param", "session_param", "ipc_session")
    # Touch the reviewed set so it cannot be trimmed without this check noticing.
    # Permission does not consult it: an unlisted domain stays session-scoped.
    _ = domain in _SESSION_SCOPED_DOMAINS or method in _KNOWN_TARGET_PARAM_METHODS
    return ("session", "target_param", "session_param")
_MAC_PROFILES = (
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Google/Chrome Canary",
    "Library/Application Support/Comet",
    "Library/Application Support/Arc/User Data",
    "Library/Application Support/Dia/User Data",
    "Library/Application Support/Microsoft Edge",
    "Library/Application Support/Microsoft Edge Beta",
    "Library/Application Support/Microsoft Edge Dev",
    "Library/Application Support/Microsoft Edge Canary",
    "Library/Application Support/BraveSoftware/Brave-Browser",
    "Library/Application Support/BraveSoftware/Brave-Origin",
)
_LINUX_PROFILES = (
    ".config/google-chrome",
    ".config/chromium",
    ".config/chromium-browser",
    ".config/microsoft-edge",
    ".config/microsoft-edge-beta",
    ".config/microsoft-edge-dev",
    ".var/app/org.chromium.Chromium/config/chromium",
    ".var/app/com.google.Chrome/config/google-chrome",
    ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    ".var/app/com.microsoft.Edge/config/microsoft-edge",
)
_WINDOWS_PROFILES = (  # relative to %LOCALAPPDATA%; SxS = Canary channel
    "Google/Chrome/User Data",
    "Google/Chrome SxS/User Data",
    "Google/Chrome Beta/User Data",
    "Google/Chrome Dev/User Data",
    "Chromium/User Data",
    "Microsoft/Edge/User Data",
    "Microsoft/Edge Beta/User Data",
    "Microsoft/Edge Dev/User Data",
    "Microsoft/Edge SxS/User Data",
    "BraveSoftware/Brave-Browser/User Data",
)


def _publish_own_pid(path=PID, pid=None):
    """Atomically retain or publish this daemon's PID record.

    The parent may already have published a process-start fingerprint while
    holding the spawn lock. Never truncate that record: another cold caller
    must not observe an empty PID file and start a sibling daemon.
    """
    pid = pid or os.getpid()
    target = Path(path)
    try:
        published = target.read_text()
        try:
            published_pid = int(json.loads(published)["pid"])
        except (json.JSONDecodeError, TypeError, KeyError):
            published_pid = int(published.split()[0])
    except (FileNotFoundError, OSError, ValueError, IndexError):
        published = str(pid)
        published_pid = pid
    if published_pid != pid:
        published = str(pid)
    tmp = target.with_name(f"{target.name}.{pid}.tmp")
    tmp.write_text(published)
    os.replace(tmp, target)


def profile_dirs(system=None):
    system = system or platform.system()
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return [local / p for p in _WINDOWS_PROFILES]
    if system == "Darwin":
        return [Path.home() / p for p in _MAC_PROFILES]
    return [Path.home() / p for p in _LINUX_PROFILES]


PROFILES = profile_dirs()
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")
BU_API = "https://api.browser-use.com/api/v3"
REMOTE_ID = os.environ.get("BU_BROWSER_ID")
_REMOTE_STOPPED = False
BROWSER_KIND = "cloud" if REMOTE_ID else ("cdp" if (os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL")) else "local")
# Chrome 144+ shows a per-connection popup, and the connection that raised it is
# the only thing keeping it on screen. There is deliberately no approval
# deadline: expiry would drop the sheet and make a later attempt create another
# connection and another prompt. Chrome/process death and explicit cancellation
# still terminate the pending connection.
LOCAL_HANDSHAKE_TIMEOUT = None
# How long get_ws_url() keeps waiting for DevToolsActivePort before giving up
NO_TOGGLE_GRACE = 3
TOGGLE_BOOT_GRACE = 12
# Cancellation should make an in-flight CDP call finish immediately. Keep the
# drain bounded anyway so shutdown fails closed if a client ignores cancellation.
RECOVERY_CANCEL_DRAIN_TIMEOUT = 2
TAB_MARKER_JS = "if(!document.title.startsWith('\U0001F434'))document.title='\U0001F434 '+document.title"


def tab_marker_enabled():
    """Whether the cosmetic controlled-tab title marker should be added."""
    return os.environ.get("BH_TAB_MARKER", "").strip().lower() not in {"0", "false", "no", "off"}


def _devtools_port_live(base):
    """True when something is listening on the profile's DevToolsActivePort port.

    A stale file left behind by a closed browser must not count as a running
    instance — it would route recovery to "click Allow" on a popup that can't
    exist."""
    try:
        port = int((base / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return False
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def remote_debugging_user_enabled():
    """chrome://inspect's "Allow remote debugging" toggle

    True only when a toggle-on profile also has a live DevTools port.
    False if a profile records it off, None when no profile records it."""
    seen = None
    for base in PROFILES:
        try:
            state = json.loads((base / "Local State").read_text(encoding="utf-8", errors="replace"))
            enabled = ((state.get("devtools") or {}).get("remote_debugging") or {}).get("user-enabled")
        except (OSError, ValueError, AttributeError):
            continue
        if enabled is True and _devtools_port_live(base):
            return True
        if enabled is False:
            seen = False
    return seen


def remote_debugging_toggle_profiles():
    """Profile dirs whose chrome://inspect toggle is recorded on in Local State"""
    out = []
    for base in PROFILES:
        try:
            state = json.loads((base / "Local State").read_text(encoding="utf-8", errors="replace"))
            if ((state.get("devtools") or {}).get("remote_debugging") or {}).get("user-enabled") is True:
                out.append(base)
        except (OSError, ValueError, AttributeError):
            continue
    return out


def browser_running_for_profile(base):
    """True when a running browser instance holds this user-data-dir (POSIX)"""
    try:
        target = os.readlink(str(base / "SingletonLock"))
    except OSError:
        return False
    try:
        pid = int(target.rsplit("-", 1)[-1])
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # pid exists but belongs to another user


def supported_browser_running():
    """Is any browser whose profile we scan actually running?"""
    if platform.system() == "Windows":
        # Chromium on Windows uses a named mutex instead of SingletonLock —
        import subprocess
        try:
            out = subprocess.check_output(["tasklist"], text=True, errors="replace", timeout=5).lower()
        except Exception:
            return True  # can't tell — assume running so recovery stays on the popup/toggle path
        return any(n in out for n in ("chrome.exe", "msedge.exe", "chromium.exe", "brave.exe", "helium.exe"))
    return any(browser_running_for_profile(base) for base in PROFILES)


def log(msg):
    open(LOG, "a", encoding="utf-8", errors="replace").write(f"{msg}\n")


def _safe_connection_label(url):
    """Log only endpoint topology, never CDP credentials or provider session paths."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return "<redacted-cdp-endpoint>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except (TypeError, ValueError):
        return "<redacted-cdp-endpoint>"


async def _silent(coro):
    try:
        await coro
    except Exception:
        pass


def _ws_from_devtools_active_port(http_url: str) -> str | None:
    """When /json/version returns 404 (Chrome 147+ default profile), match DevToolsActivePort by port."""
    p = urlparse(http_url)
    want_port = str(p.port) if p.port else ""
    if not want_port:
        return None
    host = p.hostname or "127.0.0.1"
    if ":" in host:  # urlparse strips IPv6 brackets; restore them for the ws:// URL
        host = f"[{host}]"
    for base in PROFILES:
        try:
            active = (base / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()
        except (FileNotFoundError, NotADirectoryError):
            continue
        port = active[0].strip() if active else ""
        ws_path = active[1].strip() if len(active) > 1 else ""
        if port == want_port and ws_path:
            return f"ws://{host}:{port}{ws_path}"
    return None


def get_ws_url():
    if url := os.environ.get("BU_CDP_WS"):
        return url
    if url := os.environ.get("BU_CDP_URL"):
        # HTTP DevTools endpoint (e.g. http://127.0.0.1:9333) — resolve to ws via /json/version.
        # Use this for a dedicated automation Chrome on a non-default profile, which avoids the
        # M144 "Allow remote debugging" dialog and the M136 default-profile lockdown.
        deadline = time.time() + 30
        last_err = None
        base_url = url.rstrip("/")
        while time.time() < deadline:
            try:
                return json.loads(urllib.request.urlopen(f"{base_url}/json/version", timeout=5).read())["webSocketDebuggerUrl"]
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise RuntimeError("permission-blocked: Chrome is reachable, but the per-session Allow remote debugging popup has not been accepted")
                if e.code == 404 and (ws := _ws_from_devtools_active_port(url)):
                    return ws
                time.sleep(1)
            except Exception as e:
                last_err = e
                time.sleep(1)
        hint = "is the dedicated automation Chrome running? Launch it with --remote-debugging-port=<port> --user-data-dir=<dedicated dir>"
        if platform.system() == "Windows":
            hint += "; on Windows also check that a firewall/antivirus isn't blocking localhost connections"
        raise RuntimeError(f"BU_CDP_URL={url} unreachable after 30s: {last_err} -- {hint}")
    deadline = time.time() + 30
    next_liveness_check = 0.0
    while time.time() < deadline:
        for base in PROFILES:
            try:
                active = (base / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()
            except (FileNotFoundError, NotADirectoryError):
                continue
            port = active[0].strip() if active else ""
            ws_path = active[1].strip() if len(active) > 1 else ""
            if not port:
                continue
            # Resolve the live WS URL via /json/version instead of trusting the path stored
            # alongside the port in DevToolsActivePort: if Chrome was previously launched
            # with a different --user-data-dir on the same port, that file is left behind
            # with a stale browser UUID and the WS upgrade returns 404.
            try:
                return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read())["webSocketDebuggerUrl"]
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise RuntimeError("permission-blocked: Chrome is reachable, but the per-session Allow remote debugging popup has not been accepted")
                # Chrome 147+ disables /json/* HTTP discovery on the default user-data-dir;
                # the ws path Chrome wrote to DevToolsActivePort still works.
                if e.code == 404 and ws_path:
                    return f"ws://127.0.0.1:{port}{ws_path}"
            except (OSError, KeyError, ValueError):
                pass
        # Closed browser leaves stale DevToolsActivePort files
        now = time.time()
        if now >= next_liveness_check:
            if not supported_browser_running():
                raise RuntimeError(
                    "chrome-not-running: no supported Chromium-family browser is running -- start Chrome, then retry"
                )
            next_liveness_check = now + 2
        # The browser is running but the port isn't up; waiting 30s
        grace = TOGGLE_BOOT_GRACE if remote_debugging_toggle_profiles() else NO_TOGGLE_GRACE
        if now > deadline - 30 + grace:
            break
        time.sleep(0.2)
    for probe_port in (9222, 9223):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{probe_port}/json/version", timeout=1) as r:
                return json.loads(r.read())["webSocketDebuggerUrl"]
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise RuntimeError("permission-blocked: Chrome is reachable, but the per-session Allow remote debugging popup has not been accepted")
        except (OSError, KeyError, ValueError):
            continue
    if remote_debugging_user_enabled() is False:
        raise RuntimeError('remote debugging is turned off for this browser instance — enable chrome://inspect/#remote-debugging (tick "Allow remote debugging for this browser instance")')
    raise RuntimeError(f"DevToolsActivePort not found in {[str(p) for p in PROFILES]} — enable chrome://inspect/#remote-debugging, or set BU_CDP_WS for a remote browser")


def stop_remote(strict=False):
    global _REMOTE_STOPPED
    if not REMOTE_ID:
        return True
    if _REMOTE_STOPPED:
        return True
    last_error = None
    for attempt in range(3):
        try:
            key = auth.get_browser_use_api_key()
            req = urllib.request.Request(
                f"{BU_API}/browsers/{REMOTE_ID}",
                data=json.dumps({"action": "stop"}).encode(),
                method="PATCH",
                headers={"X-Browser-Use-API-Key": key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=15).read()
            _REMOTE_STOPPED = True
            log(f"stopped remote browser {REMOTE_ID}")
            return True
        except Exception as e:
            last_error = e
            log(f"stop_remote attempt {attempt + 1}/3 failed ({REMOTE_ID}): {e}")
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    if strict:
        raise RuntimeError(f"failed to stop remote browser {REMOTE_ID}: {last_error}")
    return False


def is_real_page(t):
    return t["type"] == "page" and not t.get("url", "").startswith(INTERNAL)


def is_reusable_blank_page(t):
    """A plain about:blank tab that is safe to attach to and navigate"""
    url = t.get("url", "")
    return (
        t["type"] == "page"
        and (url == "about:blank" or url.startswith("about:blank#"))
        and not t.get("title", "").startswith("Starting agent ")
    )


def is_inspect_tab(t):
    """A chrome://inspect tab — normally the one the permission flow opened"""
    return t["type"] == "page" and t.get("url", "").startswith("chrome://inspect")


def harness_opened_inspect():
    """True when admin's recovery flow opened a chrome://inspect tab that is
    still awaiting cleanup (the marker survives until the next connect)."""
    try:
        return paths.inspect_marker().exists()
    except OSError:
        return False


def is_reusable_new_tab_page(t):
    """The browser's own New Tab Page, ex: from a fresh launch"""
    return t["type"] == "page" and t.get("url", "").startswith(
        ("chrome://newtab", "chrome://new-tab-page", "edge://newtab", "about:newtab")
    )


class _PatientCDPClient(CDPClient):
    """CDPClient whose local Chrome approval handshake has no deadline."""

    async def start(self):
        import websockets
        if self.ws is not None:
            raise RuntimeError("Client is already started")
        connect_kwargs = {"max_size": self.max_ws_frame_size, "open_timeout": LOCAL_HANDSHAKE_TIMEOUT}
        if self.additional_headers:
            connect_kwargs["additional_headers"] = self.additional_headers
        self.ws = await websockets.connect(self.url, **connect_kwargs)
        self._message_handler_task = asyncio.create_task(self._handle_messages())


class Daemon:
    def __init__(self):
        self.cdp = None
        self.session = None
        self.target_id = None
        self.dedicated_target_id = None
        self._dedicated_target_lock = asyncio.Lock()
        self._session_state_lock = asyncio.Lock()
        self._active_recoveries = 0
        self._recovery_tasks = set()
        self._recoveries_idle = asyncio.Event()
        self._recoveries_idle.set()
        self._shutting_down = False
        self._session_replacements = {}
        self.events = deque(maxlen=BUF)
        self.dialog = None
        self.stop = None  # asyncio.Event, set inside start()
        # Issued only by IPC Target.createTarget in handle. Not attach_first_page.
        self._task_grants = {}
        self._session_targets = {}
        self._issued_targets = set()
        self._page_enabled_sessions = set()

    async def attach_first_page(self, replaces_session=None, enable_domains=True):
        """Attach to a real page (or any page). Sets self.session. Returns attached target or None."""
        targets = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
        # Named daemons (BU_NAME != "default") share one browser with other
        # daemons — attaching to the first page makes parallel daemons fight
        # over a single tab (navigations clobber each other). Give each named
        # daemon its own dedicated tab instead. REMOTE_ID (cloud) browsers are
        # already exclusive to this daemon, so first-page attach stays.
        if NAME != "default" and not REMOTE_ID:
            # The permission recovery flow can leave chrome://inspect open.
            # Clean it up before returning from this early path as well.
            if BROWSER_KIND == "local":
                await self._close_inspect_tabs(targets)
            pages_by_id = {t["targetId"]: t for t in targets if t["type"] == "page"}
            # A stale CDP session does not necessarily mean its tab disappeared.
            # Reattach to the current tab first, then the daemon's dedicated tab.
            page = pages_by_id.get(self.target_id) or pages_by_id.get(self.dedicated_target_id)
            if page is None:
                # Two stale IPC requests can recover concurrently. Recheck
                # inside a narrow lock so they share one replacement tab.
                async with self._dedicated_target_lock:
                    refreshed = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
                    pages_by_id = {t["targetId"]: t for t in refreshed if t["type"] == "page"}
                    page = pages_by_id.get(self.target_id) or pages_by_id.get(self.dedicated_target_id)
                    if page is None:
                        tid = (await self.cdp.send_raw(
                            "Target.createTarget", {"url": "about:blank", "background": True}
                        ))["targetId"]
                        self.dedicated_target_id = tid
                        log(f"named daemon {NAME}: created dedicated tab ({tid})")
                        page = {"targetId": tid, "url": "about:blank", "type": "page"}
            tid = page["targetId"]
            self.session = (await self.cdp.send_raw(
                "Target.attachToTarget", {"targetId": tid, "flatten": True}
            ))["sessionId"]
            self._record_session_replacement(replaces_session, self.session)
            self.target_id = tid
            self._remember_session(self.session, tid)
            log(f"attached {tid} ({page.get('url','')[:80]}) session={self.session}")
            if enable_domains:
                await self._enable_default_domains(self.session)
            return page

        pages = [t for t in targets if is_real_page(t)]
        if not pages:
            # Fresh browser (ex: BU cloud) starts w about:blank; reuse it
            pages = [t for t in targets if is_reusable_blank_page(t)]
        if not pages:
            # Freshly launched browser (ex: harness relaunching closed Chrome)
            # starts with just the New Tab Page. Reuse it — creating about:blank
            pages = [t for t in targets if is_reusable_new_tab_page(t)]
        take_over = None
        if not pages and harness_opened_inspect():
            # After perms granted, only tab is often chrome://inspect
            # Attach to it instead of creating a new about:blank
            inspect_tabs = [t for t in targets if is_inspect_tab(t)]
            if inspect_tabs:
                pages = [inspect_tabs[0]]
                take_over = inspect_tabs[0]["targetId"]
        if not pages:
            # No usable pages - create one instead of attaching to omnibox popup.
            tid = (await self.cdp.send_raw(
                "Target.createTarget", {"url": "about:blank", "background": True}
            ))["targetId"]
            log(f"no real pages found, created about:blank ({tid})")
            pages = [{"targetId": tid, "url": "about:blank", "type": "page"}]
        self.session = (await self.cdp.send_raw(
            "Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True}
        ))["sessionId"]
        self._record_session_replacement(replaces_session, self.session)
        self.target_id = pages[0]["targetId"]
        self._remember_session(self.session, pages[0]["targetId"])
        log(f"attached {pages[0]['targetId']} ({pages[0].get('url','')[:80]}) session={self.session}")
        if take_over:
            try:
                await self.cdp.send_raw("Page.navigate", {"url": "about:blank"}, session_id=self.session)
                log(f"took over inspect tab {take_over} -> about:blank")
            except Exception as e:
                log(f"take over inspect tab {take_over}: {e}")
        if BROWSER_KIND == "local":
            await self._close_inspect_tabs(targets)
        if enable_domains:
            await self._enable_default_domains(self.session)
        return pages[0]

    async def _close_inspect_tabs(self, targets):
        """Close chrome://inspect tabs left open by the permission recovery flow"""
        if not harness_opened_inspect():
            return
        for t in targets:
            if t["targetId"] != self.target_id and is_inspect_tab(t):
                try:
                    await self.cdp.send_raw("Target.closeTarget", {"targetId": t["targetId"]})
                    log(f"closed leftover chrome://inspect tab {t['targetId']}")
                except Exception as e:
                    log(f"close inspect tab {t['targetId']}: {e}")
        try:
            paths.inspect_marker().unlink()
        except OSError:
            pass

    async def _enable_default_domains(self, session_id):
        """Enable Page/DOM/Runtime/Network on a CDP session.

        Used by both initial attach and set_session (called after switch_tab/
        new_tab). Without this, helpers that depend on Network.* events —
        notably wait_for_network_idle() — silently stop receiving events
        after a tab switch, because each fresh CDP session starts with all
        domains disabled.

        Runs the four enables in parallel via gather so the worst-case time is
        bounded by a single CDP round trip rather than four sequential ones —
        important on the set_session path, where the helper's IPC socket has
        a 5s read timeout.
        """
        async def enable_one(d):
            try:
                await asyncio.wait_for(
                    self.cdp.send_raw(f"{d}.enable", session_id=session_id),
                    timeout=4,
                )
            except Exception as e:
                log(f"enable {d} on {session_id}: {e}")
        await asyncio.gather(*(enable_one(d) for d in ("Page", "DOM", "Runtime", "Network")))

    def _record_session_replacement(self, stale_session, replacement_session):
        """Remember which recovered session still controls the same tab."""
        if not stale_session or not replacement_session or stale_session == replacement_session:
            return
        # Preserve chains so requests delayed across multiple recoveries still
        # land on their original tab, never whichever tab is current now.
        for source, replacement in list(self._session_replacements.items()):
            if replacement == stale_session:
                self._session_replacements[source] = replacement_session
        self._session_replacements[stale_session] = replacement_session
        while len(self._session_replacements) > 32:
            self._session_replacements.pop(next(iter(self._session_replacements)))

    def _begin_recovery(self):
        """Register the current IPC handler unless shutdown has started."""
        if self._shutting_down:
            return None
        task = asyncio.current_task()
        if task is None:
            return None
        self._recovery_tasks.add(task)
        self._active_recoveries += 1
        self._recoveries_idle.clear()
        return task

    def _finish_recovery(self, task):
        self._recovery_tasks.discard(task)
        self._active_recoveries -= 1
        if self._active_recoveries == 0:
            self._recoveries_idle.set()

    async def _cancel_and_drain_recoveries(self):
        """Cancel active stale-session handlers and wait a bounded time."""
        current = asyncio.current_task()
        tasks = [
            task for task in self._recovery_tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(
                tasks, timeout=RECOVERY_CANCEL_DRAIN_TIMEOUT
            )
            if pending:
                return False
        return self._recoveries_idle.is_set()

    def _schedule_tab_marker(self, session_id):
        """Mark the controlled tab without extending the synchronous IPC path."""
        if not tab_marker_enabled():
            return None
        return asyncio.create_task(_silent(asyncio.wait_for(
            self.cdp.send_raw(
                "Runtime.evaluate",
                {"expression": TAB_MARKER_JS},
                session_id=session_id,
            ),
            timeout=2,
        )))

    def _record_event(self, method, params, session_id=None):
        self.events.append({"method": method, "params": params, "session_id": session_id})
        if method == "Target.attachedToTarget":
            self._note_attached_event(params, session_id)
        if method == "Page.javascriptDialogOpening":
            self.dialog = self._dialog_from_open(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            self._clear_dialog_if_same_session(session_id)
        elif method in ("Page.loadEventFired", "Page.domContentEventFired"):
            self._schedule_tab_marker(self.session)

    def _remember_session(self, session_id, target_id):
        """Record this process's session→target map. Return True if new."""
        if not isinstance(session_id, str) or not session_id:
            return False
        if not isinstance(target_id, str) or not target_id:
            return False
        first = session_id not in self._session_targets
        self._session_targets[session_id] = target_id
        return first

    def _grant_matches(self, presented, expected):
        if not isinstance(presented, str) or not isinstance(expected, str) or not expected:
            return False
        return hmac.compare_digest(presented, expected)

    def _grant_error(self, req, target_ids):
        needed = []
        for tid in target_ids:
            if isinstance(tid, str) and tid in self._task_grants and tid not in needed:
                needed.append(tid)
        if not needed:
            return None
        if not isinstance(req, dict) or "task_grant" not in req or req.get("task_grant") is None:
            return {"error": "task_grant_required"}
        presented = req.get("task_grant")
        matched = True
        for tid in needed:
            if not self._grant_matches(presented, self._task_grants[tid]):
                matched = False
        if not matched:
            return {"error": "task_grant_mismatch"}
        return None

    def _add_task_target(self, found, tid):
        if isinstance(tid, str) and tid in self._task_grants and tid not in found:
            found.append(tid)

    def _add_task_session(self, found, sid):
        if isinstance(sid, str) and sid:
            self._add_task_target(found, self._session_targets.get(sid))

    def _param_task_targets(self, params):
        found = []
        if not isinstance(params, dict):
            return found
        for key in _TARGET_PARAM_KEYS:
            self._add_task_target(found, params.get(key))
        for key in _SESSION_PARAM_KEYS:
            self._add_task_session(found, params.get(key))
        return found

    def _task_targets_for_call(self, req, method, params, classes=None):
        """Task targets this call would reach. Empty means not a task call."""
        if classes is None:
            classes = command_address_classes(method)
        found = []
        if "session" in classes:
            explicit = req.get("session_id") if isinstance(req, dict) else None
            if explicit:
                self._add_task_session(found, explicit)
            else:
                self._add_task_session(found, self.session)
                self._add_task_target(found, self.target_id)
        if "ipc_session" in classes:
            self._add_task_session(found, req.get("session_id") if isinstance(req, dict) else None)
        if "target_param" in classes or "session_param" in classes:
            for tid in self._param_task_targets(params):
                if tid not in found:
                    found.append(tid)
        return found

    def _task_targets_for_set_session(self, session_id, target_id):
        found = []
        self._add_task_target(found, target_id)
        self._add_task_session(found, session_id)
        return found

    def _session_routed_off_named_task(self, method, params, sid):
        """A session-routed call named a task target it will not run on.

        Target.* is addressed by its params, not by the default page session.
        A Page/DOM/Network call that names a task target must use that target's
        session. Otherwise the grant check can pass and the command still lands
        on the user's current page.
        """
        if not isinstance(method, str) or method.startswith("Target."):
            return False
        named = self._param_task_targets(params)
        if not named:
            return False
        routed = self._session_targets.get(sid) if isinstance(sid, str) else None
        if routed in named:
            return False
        if (
            isinstance(sid, str) and sid and sid == self.session
            and self.target_id in named and self.target_id in self._task_grants
        ):
            return False
        return True

    def _note_attached_event(self, params, session_id):
        """Map an auto-attached session only when the target was already issued."""
        if not isinstance(params, dict):
            return
        info = params.get("targetInfo") if isinstance(params.get("targetInfo"), dict) else {}
        tid = info.get("targetId") if isinstance(info.get("targetId"), str) else None
        if not tid or tid not in self._task_grants:
            return
        sid = params.get("sessionId") if isinstance(params.get("sessionId"), str) else None
        if not sid and isinstance(session_id, str):
            sid = session_id
        if sid and self._remember_session(sid, tid):
            self._schedule_task_page_enable(sid)

    def _schedule_task_page_enable(self, session_id):
        if not session_id or session_id in self._page_enabled_sessions or not self.cdp:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._enable_task_page(session_id))

    async def _enable_task_page(self, session_id):
        """Page.enable once for a task session. Not the four-domain set_session path."""
        if not session_id or session_id in self._page_enabled_sessions:
            return
        self._page_enabled_sessions.add(session_id)
        if not self.cdp:
            self._page_enabled_sessions.discard(session_id)
            return
        try:
            await asyncio.wait_for(
                self.cdp.send_raw("Page.enable", session_id=session_id),
                timeout=4,
            )
        except Exception:
            self._page_enabled_sessions.discard(session_id)
            log("enable Page on task session failed")

    def _dialog_from_open(self, params, session_id):
        src = params if isinstance(params, dict) else {}
        dialog = dict(src)
        if session_id:
            dialog["sessionId"] = session_id
        tid = self._session_targets.get(session_id) if session_id else None
        if tid:
            dialog["targetId"] = tid
        dialog["dialogId"] = secrets.token_urlsafe(16)
        return dialog

    def _clear_dialog_if_same_session(self, session_id):
        current = self.dialog if isinstance(self.dialog, dict) else None
        if current is None:
            return
        current_sid = current.get("sessionId")
        if session_id == current_sid or (not session_id and not current_sid):
            self.dialog = None

    async def _finish_cdp(self, method, params, result):
        """Issue a grant only after IPC createTarget. Do not touch the default page."""
        resp = {"result": result}
        if method == "Target.createTarget" and isinstance(result, dict):
            tid = result.get("targetId")
            if isinstance(tid, str) and tid:
                grant = secrets.token_urlsafe(32)
                self._task_grants[tid] = grant
                self._issued_targets.add(tid)
                resp["task_grant"] = grant
        if method == "Target.attachToTarget" and isinstance(result, dict) and isinstance(params, dict):
            sid = result.get("sessionId")
            tid = params.get("targetId")
            if isinstance(sid, str) and sid and isinstance(tid, str) and tid:
                first = self._remember_session(sid, tid)
                if first and tid in self._task_grants:
                    await self._enable_task_page(sid)
        return resp

    async def _close_issued_targets(self):
        """Close only target ids issued by this process's handle. No target list."""
        if not self.cdp:
            return
        for tid in list(self._issued_targets):
            if not isinstance(tid, str) or not tid:
                self._issued_targets.discard(tid)
                continue
            try:
                await self.cdp.send_raw("Target.closeTarget", {"targetId": tid})
            except Exception as e:
                log(f"close issued task tab on shutdown: {e}")
            else:
                self._issued_targets.discard(tid)

    async def start(self):
        self.stop = asyncio.Event()
        url = get_ws_url()
        log(f"connecting to {_safe_connection_label(url)}")
        self.cdp = _PatientCDPClient(url) if BROWSER_KIND == "local" else CDPClient(url)
        if BROWSER_KIND == "local":
            # Allow while this handshake is still parked on the popup
            log("handshake-wait: if Chrome shows an 'Allow remote debugging?' popup, click Allow")
        try:
            await self.cdp.start()
        except Exception as e:
            if os.environ.get("BU_CDP_WS"):
                raise RuntimeError(
                    f"CDP WS handshake failed: {e} -- remote browser WebSocket connection failed. "
                    "This can happen when network policy blocks the connection, the WS URL is wrong or expired, or the remote endpoint is down. "
                    "If you use Browser Use cloud, verify auth and get a fresh URL via start_remote_daemon()."
                )
            if BROWSER_KIND == "local" and ("timed out" in str(e).lower() or "403" in str(e)) and remote_debugging_user_enabled():
                raise RuntimeError(
                    "permission-blocked: Chrome did not approve the remote debugging connection; "
                    "browser-harness did not retry or create another connection"
                )
            raise RuntimeError(f"CDP WS handshake failed: {e} -- click Allow in Chrome if prompted, then retry")
        await self.attach_first_page()
        orig = self.cdp._event_registry.handle_event
        async def tap(method, params, session_id=None):
            self._record_event(method, params, session_id)
            return await orig(method, params, session_id)
        self.cdp._event_registry.handle_event = tap

    async def handle(self, req):
        # Token guard for Windows TCP loopback: any local process can otherwise
        # connect and issue CDP commands. expected_token() is None on POSIX so
        # this check is a no-op there (AF_UNIX + chmod 600 is the boundary).
        expected = ipc.expected_token()
        if expected is not None and req.get("token") != expected:
            return {"error": "unauthorized"}
        meta = req.get("meta")
        # Liveness probe — lets clients confirm the listener is actually this
        # daemon and not an unrelated process that reused our port post-crash.
        # `pid` lets restart_daemon() verify the live daemon's identity before
        # signaling — protects against SIGTERM-by-stale-pid-file after PID reuse.
        if meta == "ping":        return {"pong": True, "pid": os.getpid(), "browser_kind": BROWSER_KIND}
        if meta == "capabilities":
            # No CDP, no token, no task grant. Clients use this to refuse an
            # unpatched daemon before creating a task page.
            return {
                "task_grants": True,
                "dialog_identity": True,
                "task_grant_version": TASK_GRANT_VERSION,
            }
        if meta == "drain_events":
            out = list(self.events); self.events.clear()
            return {"events": out}
        if meta == "session":     return {"session_id": self.session}
        if meta == "current_tab":
            # Resolve the attached page's target info server-side. Helpers can't
            # send Target.getTargetInfo themselves: daemon strips session_id for
            # any Target.* method (browser-level call), and without a targetId
            # Chrome silently returns the *browser* target.
            if not self.target_id:
                return {"error": "not_attached"}
            grant_error = self._grant_error(
                req, [self.target_id] if self.target_id in self._task_grants else []
            )
            if grant_error:
                return grant_error
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
            except Exception:
                return {"error": "cdp_disconnected"}
            return {"targetId": info.get("targetId"), "url": info.get("url", ""), "title": info.get("title", "")}
        if meta == "connection_status":
            if not self.target_id:
                return {"error": "not_attached"}
            grant_error = self._grant_error(
                req, [self.target_id] if self.target_id in self._task_grants else []
            )
            if grant_error:
                return grant_error
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
            except Exception:
                return {"error": "cdp_disconnected"}
            page = None
            if is_real_page(info):
                page = {
                    "targetId": info.get("targetId"),
                    "title": info.get("title") or "(untitled)",
                    "url": info.get("url") or "",
                }
            return {"target_id": self.target_id, "session_id": self.session, "page": page}
        if meta == "set_session":
            async with self._session_state_lock:
                new_session = req.get("session_id")
                new_target = req.get("target_id") or self.target_id
                grant_error = self._grant_error(
                    req, self._task_targets_for_set_session(new_session, new_target)
                )
                if grant_error:
                    return grant_error
                old_session = self.session
                self.session = new_session
                self.target_id = new_target
                if new_session and new_target:
                    self._remember_session(new_session, new_target)
            # Run the old-session Network.disable (defense in depth — keeps
            # background-tab traffic out of the global event buffer; the
            # consumer-side filter in wait_for_network_idle is the actual
            # correctness gate) in parallel with the four enables on the new
            # session. Different sessions, independent CDP requests. Keeps
            # the synchronous reply under the helper's 5s IPC read timeout
            # even on a remote daemon — sequentially these would have stacked
            # to ~22s worst case.
            tasks = []
            if old_session and old_session != new_session:
                async def disable_old():
                    try:
                        await asyncio.wait_for(
                            self.cdp.send_raw("Network.disable", session_id=old_session),
                            timeout=2,
                        )
                    except Exception: pass
                tasks.append(disable_old())
            tasks.append(self._enable_default_domains(new_session))
            await asyncio.gather(*tasks)
            # 🐴 tab-marker title prefix is purely cosmetic — fire-and-forget so
            # it doesn't add to the synchronous IPC budget.
            self._schedule_tab_marker(new_session)
            return {"session_id": new_session}
        if meta == "pending_dialog": return {"dialog": self.dialog}
        if meta == "shutdown":
            # Flip the barrier synchronously with recovery registration, then
            # cancel/drain existing handlers. In particular, a CDP replay that
            # never answers must not prevent Cloud cleanup from being attempted.
            if self._shutting_down:
                return {"error": "shutdown already in progress"}
            self._shutting_down = True
            if not await self._cancel_and_drain_recoveries():
                # Preserve the daemon as a retryable cleanup authority. The
                # strict caller will leave its endpoint and PID file intact.
                self._shutting_down = False
                return {"error": "stale-session recovery did not stop"}
            try:
                stop_remote(strict=True)
            except Exception as e:
                # A failed Cloud stop must leave the daemon usable so a later
                # shutdown request can retry the billable-browser cleanup.
                async with self._session_state_lock:
                    self._shutting_down = False
                return {"error": str(e)}
            self.stop.set()
            return {"ok": True}

        method = req["method"]
        params = req.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        classes = command_address_classes(method)
        if "refused_close" in classes:
            return {"error": "browser_close_refused"}
        grant_error = self._grant_error(req, self._task_targets_for_call(req, method, params, classes))
        if grant_error:
            return grant_error
        # Browser-level Target.* calls must not use a session (stale or otherwise).
        # For everything else, explicit session in req wins; else default.
        sid = None if method.startswith("Target.") else (req.get("session_id") or self.session)
        if self._session_routed_off_named_task(method, params, sid):
            # Do not satisfy the grant check and then execute on the default
            # user page. Mismatch is not retried without the grant.
            return {"error": "task_grant_mismatch"}
        try:
            result = await self.cdp.send_raw(method, params, session_id=sid)
        except Exception as e:
            msg = str(e)
            if "Session with given id not found" in msg and sid:
                # Explicit session callers asked for that exact session; do not
                # silently redirect them to the daemon's current tab.
                # A presented task_grant, or a session mapped to a task target,
                # must not fall back to attach_first_page. That recovery attaches
                # the user's current page.
                if (
                    req.get("session_id")
                    or "task_grant" in req
                    or self._session_targets.get(sid) in self._task_grants
                ):
                    return {"error": msg}
                recovery_task = self._begin_recovery()
                if recovery_task is None:
                    return {"error": "daemon is shutting down"}
                try:
                    recovered_here = False
                    async with self._session_state_lock:
                        if self._shutting_down:
                            return {"error": "daemon is shutting down"}
                        replacement_session = self._session_replacements.get(sid)
                        if replacement_session is None and sid == self.session:
                            log(f"stale session {sid}, re-attaching")
                            if not await self.attach_first_page(
                                replaces_session=sid, enable_domains=False
                            ):
                                return {"error": msg}
                            replacement_session = self._session_replacements.get(sid)
                            recovered_here = replacement_session is not None
                    if recovered_here:
                        await self._enable_default_domains(replacement_session)
                    # Retry only on a session known to replace this exact stale
                    # session. self.session may instead have changed because the
                    # user deliberately switched tabs while this request waited.
                    if replacement_session:
                        try:
                            return {"result": await self.cdp.send_raw(
                                method, params, session_id=replacement_session
                            )}
                        except Exception as retry_error:
                            return {"error": str(retry_error)}
                finally:
                    self._finish_recovery(recovery_task)
            return {"error": msg}
        return await self._finish_cdp(method, params, result)


async def serve(d):
    async def handler(reader, writer):
        try:
            line = await reader.readline()
            if not line: return
            resp = await d.handle(json.loads(line))
            writer.write((json.dumps(resp, default=str) + "\n").encode())
            await writer.drain()
        except Exception as e:
            log(f"conn: {e}")
            try:
                writer.write((json.dumps({"error": str(e)}) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()

    serve_task = asyncio.create_task(ipc.serve(NAME, handler))
    stop_task = asyncio.create_task(d.stop.wait())
    await asyncio.sleep(0.05)  # let serve() bind so sock_addr() resolves to the live endpoint
    log(f"listening on {ipc.sock_addr(NAME)} (name={NAME}, remote={REMOTE_ID or 'local'})")
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if serve_task.done(): await serve_task  # surfaces a serve crash
    finally:
        for t in (serve_task, stop_task):
            t.cancel()
            try: await t
            except (asyncio.CancelledError, Exception): pass
        # A server crash/cancellation does not pass through meta=shutdown.
        # Establish the same recovery barrier before touching owned targets.
        d._shutting_down = True
        recoveries_drained = await d._cancel_and_drain_recoveries()
        # Named non-cloud daemons create one dedicated background tab. Shutdown
        # has blocked new recovery and drained active recovery before setting
        # d.stop (or finalization established the barrier after a server crash).
        # Take the same locks, in the same order as recovery, so cleanup closes
        # exactly the final daemon-owned target and never races creation.
        if recoveries_drained:
            async with d._session_state_lock:
                async with d._dedicated_target_lock:
                    if d.dedicated_target_id and d.cdp:
                        try:
                            await d.cdp.send_raw(
                                "Target.closeTarget", {"targetId": d.dedicated_target_id}
                            )
                            d.dedicated_target_id = None
                        except Exception as e:
                            log(f"close dedicated tab on shutdown: {e}")
                    await d._close_issued_targets()
        else:
            log("skip dedicated-tab cleanup: stale-session recovery did not stop")
        ipc.cleanup_endpoint(NAME)


async def main():
    d = Daemon()
    await d.start()
    await serve(d)


def already_running():
    # Ping handshake (not a bare connect) so a stale .port file + port reuse
    # after a daemon crash doesn't make us mistake an unrelated listener for ours.
    return ipc.ping(NAME, timeout=1.0)


if __name__ == "__main__":
    if already_running():
        print(f"daemon already running on {SOCK}", file=sys.stderr)
        sys.exit(0)
    open(LOG, "w").close()
    _publish_own_pid()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"fatal: {e}")
        sys.exit(1)
    finally:
        stop_remote()
        try: os.unlink(PID)
        except FileNotFoundError: pass
