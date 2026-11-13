"""Clawd-native panel update: check GitHub releases, pull, build, restart.

Replaces the upstream Hermes sidecar update path, which compared the local
checkout against NousResearch/hermes-agent main and ran ``hermes update``.
Both are wrong for clawd: the check 404s on fork commits, and there is no
system ``hermes`` binary on this host at all.

Installed by clawd_server.admin_native onto the sidecar module:
get_update_info / start_update / get_update_plan / get_update_status
become clawd-native implementations. The panel frontend already polls
update/status and renders an update run; this module gives it a real
backend.

Release channels (the panel offers a channel switch, default stable):
- stable: plain semver tags (e.g. 0.0.2), GitHub release prerelease=false.
- beta: C-prefixed tags (e.g. C0.0.1, C = test build), GitHub release
  prerelease=true. A beta is never offered to the stable channel.

Auth: J606y/clawd is private. The release check and the git fetch need a
GitHub token with read access to this repo: env GITHUB_TOKEN, or the file
<home>/panel/github-token (0600, first line). Without one the check
reports github_auth_required and start refuses. The token is never logged;
for git fetch it goes through a 0600 credential-store file inside the run
dir, deleted right after the fetch.

Safety: an update only runs when the checkout is on the clawd branch with
a clean tree and the target tag is a descendant of HEAD (fast-forward
only). The runner (clawd_server/update_runner.sh) resets to the tag,
builds the panel into a staging dir, swaps dist, restarts the panel
service and health-checks it; any failure rolls code + dist back and
restarts again. Never automatic: only runs when the user clicks update
in the panel.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CLAWD_REPO = "J606y/clawd"
CLAWD_BRANCH = "clawd"
CHANNELS = ("stable", "beta")
DEFAULT_CHANNEL = "stable"
UPDATE_TTL_S = 1800.0
UPDATE_MIN_INTERVAL_S = 60.0
UPDATE_MIN_FREE = 2 * 1024 ** 3
UPDATE_UNIT = "clawd-panel-update"
UPDATE_KEEP_RUNS = 10
UPDATE_LOG_TAIL_LINES = 120
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}$")
_STABLE_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_BETA_TAG_RE = re.compile(r"^C(\d+)\.(\d+)\.(\d+)$")
_VERSION_RE = re.compile(r"CLAWD_VERSION\s*=\s*\"([^\"]+)\"")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# channel -> {"at": float, "data": dict}
_CHECK_CACHE: dict[str, dict[str, Any]] = {}
_LAST_CLEARED_RUN: str | None = None

UPDATE_SH = '"$1" "$2" "$3" "$4"; echo $? > "$2/exit-code"'

# ---------------------------------------------------------------- pure bits


def parse_version(tag: str) -> tuple[str, tuple[int, int, int]] | None:
    """Parse a release tag into (channel, semver). None when not a release."""
    if not isinstance(tag, str):
        return None
    m = _BETA_TAG_RE.match(tag.strip())
    if m:
        return ("beta", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
    m = _STABLE_TAG_RE.match(tag.strip())
    if m:
        return ("stable", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return None


def select_release(releases: list[dict[str, Any]], channel: str) -> dict[str, Any] | None:
    """Newest release of a channel. Stable skips prereleases and C tags;
    beta only takes prereleases with C-prefixed tags."""
    best: dict[str, Any] | None = None
    best_ver: tuple[int, int, int] | None = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        parsed = parse_version(str(rel.get("tag_name") or ""))
        if parsed is None:
            continue
        rel_channel, ver = parsed
        if rel_channel != channel:
            continue
        if channel == "stable" and rel.get("prerelease"):
            continue
        if channel == "beta" and not rel.get("prerelease"):
            continue
        if best_ver is None or ver > best_ver:
            best, best_ver = rel, ver
    return best


def _channel_file(home: Path) -> Path:
    return home / "panel" / "update-channel.json"


def read_channel(home: Path) -> str:
    """Stored update channel; default stable on missing/invalid data."""
    try:
        data = json.loads(_channel_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_CHANNEL
    channel = data.get("channel") if isinstance(data, dict) else None
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


def write_channel(home: Path, channel: str) -> str:
    """Persist the update channel. Returns the stored value."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel!r}")
    path = _channel_file(home)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"channel": channel}, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return channel


def read_github_token(home: Path) -> str | None:
    """GitHub token for the private repo. Env wins, then a 0600 file.
    Never log the returned value."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        lines = (home / "panel" / "github-token").read_text(encoding="utf-8").strip().splitlines()
        token = lines[0].strip() if lines else ""
    except OSError:
        return None
    return token or None


def current_version(repo: Path) -> str:
    """The CLAWD_VERSION string from the panel source (single source)."""
    try:
        text = (repo / "apps" / "panel" / "src" / "react" / "lib" / "version.ts").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else ""

# ---------------------------------------------------------------- git + github


def _git(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
        stdin=subprocess.DEVNULL,
    )


def _local_git_info(mod: Any) -> dict[str, Any]:
    repo = Path(mod.HERMES_INSTALL)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = [
        ln[3:] for ln in _git(repo, "status", "--porcelain").stdout.splitlines() if len(ln) > 3
    ]
    return {
        "commit": head[:10] if _SHA_RE.match(head) else "",
        "head": head,
        "branch": branch,
        "localChanges": [mod.redact(x)[:200] for x in dirty[:30]],
    }


def _github_json(mod: Any, path: str, token: str | None) -> Any:
    """One GitHub API call. Maps failures onto ApiError with clear codes."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "clawd-panel-update",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read(6 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403) and token:
            raise mod.ApiError(502, "github_auth_failed", "GitHub 凭证无效或无权访问该仓库，请检查凭证") from exc
        if exc.code in (401, 403, 404) and not token:
            raise mod.ApiError(
                502, "github_auth_required",
                "GitHub 仓库是私有的，需要配置访问凭证（或把仓库设为公开）",
            ) from exc
        if exc.code in (403, 429):
            raise mod.ApiError(502, "github_rate_limited", "GitHub 请求过于频繁，稍后再试") from exc
        if exc.code == 404:
            raise mod.ApiError(502, "github_not_found", "GitHub 上找不到该仓库或发布") from exc
        raise mod.ApiError(502, "github_error", f"GitHub 返回 {exc.code}") from exc
    except ValueError as exc:
        raise mod.ApiError(502, "github_bad_response", "GitHub 返回了无法解析的数据") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise mod.ApiError(502, "github_unreachable", "无法连接 GitHub") from exc


def _fetch_releases(mod: Any, home: Path) -> list[dict[str, Any]]:
    token = read_github_token(home)
    data = _github_json(mod, f"/repos/{CLAWD_REPO}/releases?per_page=100", token)
    if not isinstance(data, list):
        raise mod.ApiError(502, "github_bad_response", "GitHub 返回了意料之外的数据")
    return [item for item in data if isinstance(item, dict)]


def _check_compute(mod: Any, channel: str) -> dict[str, Any]:
    home = Path(mod.HERMES_HOME)
    repo = Path(mod.HERMES_INSTALL)
    local = _local_git_info(mod)
    current = current_version(repo)
    parsed_current = parse_version(current)
    releases = _fetch_releases(mod, home)
    latest = select_release(releases, channel)
    info: dict[str, Any] = {
        "version": current,
        "releaseDate": "",
        "commit": local["commit"],
        "branch": local["branch"],
        "installDir": str(repo),
        "localChanges": local["localChanges"],
        "behindBy": None,
        "channel": channel,
        "checkedAt": time.time(),
    }
    if latest is None:
        info["error"] = "该通道暂无已发布的版本" if channel == "stable" else "测试版通道暂无已发布的版本"
        return info
    tag = str(latest.get("tag_name") or "")
    parsed_latest = parse_version(tag)
    published = str(latest.get("published_at") or "")
    info.update(
        latestVersion=tag,
        latestTag=tag,
        releaseUrl=str(latest.get("html_url") or ""),
        releaseNotes=str(latest.get("body") or "")[:800],
        releaseDate=published[:10],
    )
    if parsed_current is None or parsed_latest is None:
        info["behindBy"] = None
        info["error"] = "无法比较当前版本号"
    elif parsed_latest[1] > parsed_current[1]:
        info["behindBy"] = 1
    else:
        info["behindBy"] = 0
    return info


def get_update_info(mod: Any, force: bool = False, channel: str | None = None) -> dict[str, Any]:
    """Local version + latest release of the selected channel.
    Release list cached 30 min; force refresh at most once a minute
    (anonymous GitHub quota is 60/hr, and the repo is private)."""
    home = Path(mod.HERMES_HOME)
    if channel is not None and channel in CHANNELS:
        write_channel(home, channel)
    channel = read_channel(home)
    now = time.time()
    cached = _CHECK_CACHE.get(channel)
    if cached is not None and (now - cached["at"] < UPDATE_MIN_INTERVAL_S or (now - cached["at"] < UPDATE_TTL_S and not force)):
        return dict(cached["data"])
    try:
        data = _check_compute(mod, channel)
    except mod.ApiError as exc:
        if cached is not None:
            return {**cached["data"], "error": exc.message, "stale": True}
        local = _local_git_info(mod)
        return {
            "version": current_version(Path(mod.HERMES_INSTALL)),
            "commit": local["commit"],
            "branch": local["branch"],
            "localChanges": local["localChanges"],
            "behindBy": None,
            "channel": channel,
            "error": exc.message,
        }
    _CHECK_CACHE[channel] = {"at": now, "data": data}
    return dict(data)

# ---------------------------------------------------------------- plan + status


def _run_root(home: Path) -> Path:
    return home / "panel-updates"


def _panel_unit(mod: Any) -> str:
    """The panel service to restart after an update: the clawd unit this
    process runs under, else the known SG unit name."""
    for name, _label in getattr(mod, "SERVICES", []) or []:
        if isinstance(name, str) and name.startswith("clawd"):
            return name
    return "clawd-panel-accept"


def get_update_plan(mod: Any) -> dict[str, Any]:
    """Read-only preview of what an update would do."""
    try:
        info = get_update_info(mod)
    except mod.ApiError as exc:
        return {"plan": f"无法检查更新：{exc.message}", "exitCode": 1}
    if info.get("behindBy") != 1:
        return {"plan": "已是该通道的最新版，无需更新。", "exitCode": 0}
    unit = _panel_unit(mod)
    plan = (
        f"更新到 {info.get('latestVersion', '')}（通道：{'测试版' if info.get('channel') == 'beta' else '正式版'}）\n"
        "步骤：备份当前代码与面板 dist → 从 GitHub 拉取该版本 → 构建面板 → 换上新 dist → "
        f"重启 {unit} → 检查面板恢复。\n"
        "失败时自动回滚代码与 dist 并再次重启。更新期间对话会短暂中断。"
    )
    return {"plan": plan, "exitCode": 0}


def _unit_active() -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", f"{UPDATE_UNIT}.service"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.strip() in ("active", "activating", "deactivating")


def _run_dirs(home: Path) -> list[Path]:
    try:
        return sorted(
            (d for d in _run_root(home).iterdir() if d.is_dir() and RUN_ID_RE.match(d.name)),
            key=lambda d: d.name,
        )
    except OSError:
        return []


def _read_small(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def _log_tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).rstrip() for ln in raw.splitlines()]
    return "\n".join(lines[-UPDATE_LOG_TAIL_LINES:])[-20000:]


def get_update_status(mod: Any) -> dict[str, Any]:
    """Latest update run from its run dir on disk (survives restarts)."""
    global _LAST_CLEARED_RUN
    home = Path(mod.HERMES_HOME)
    running = _unit_active()
    out: dict[str, Any] = {"running": running, "state": "idle", "runId": None, "logTail": ""}
    dirs = _run_dirs(home)
    if not dirs:
        return out
    d = dirs[-1]
    code_s = _read_small(d / "exit-code")
    if running:
        state = "running"
    elif code_s == "0":
        state = "done"
    elif code_s.isdigit():
        state = "failed"
    else:
        state = "interrupted"
    old = _read_small(d / "old-head")
    new = _git(Path(mod.HERMES_INSTALL), "rev-parse", "HEAD").stdout.strip()
    out.update(
        state=state, runId=d.name, exitCode=int(code_s) if code_s.isdigit() else None,
        oldHead=old[:10], newHead=new[:10],
        changed=bool(old) and old != new,
        backupDir=str(d),
        patchBytes=(d / "local-changes.patch").stat().st_size if (d / "local-changes.patch").exists() else 0,
        hasUntracked=(d / "untracked.tar.gz").exists(),
        logTail=_log_tail(d / "update.log"),
    )
    if not running and state != "idle" and _LAST_CLEARED_RUN != d.name:
        _CHECK_CACHE.clear()
        _LAST_CLEARED_RUN = d.name
    return out

# ---------------------------------------------------------------- execute


def _write_git_credentials(run: Path, token: str) -> Path:
    """0600 credential-store file for one fetch. Deleted right after."""
    path = run / ".git-credentials"
    path.write_text(f"https://x-access-token:{token}@github.com\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _fetch_tag(mod: Any, repo: Path, run: Path, tag: str, token: str | None) -> str:
    """Fetch one tag from origin; return its commit. Raises ApiError."""
    creds = _write_git_credentials(run, token) if token else None
    try:
        cmd = ["git", "-c", "safe.directory=*", "-C", str(repo)]
        if creds is not None:
            cmd += ["-c", f"credential.helper=store --file={creds}"]
        cmd += ["fetch", "origin", "tag", tag]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            detail = " ".join(detail[-2:])[:300] if detail else ""
            raise mod.ApiError(502, "github_fetch_failed", f"从 GitHub 拉取 {tag} 失败：{detail or '请检查网络与凭证'}")
        out = _git(repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
        if not _SHA_RE.match(out):
            raise mod.ApiError(502, "github_fetch_failed", f"拉取后找不到 {tag} 对应的提交")
        return out
    finally:
        if creds is not None:
            try:
                os.remove(creds)
            except OSError:
                pass


def start_update(mod: Any, confirm: Any, tag: str | None = None, channel: str | None = None) -> dict[str, Any]:
    """User-triggered update only. Validates everything synchronously,
    then hands the mutate-and-restart work to a detached systemd unit
    (so restarting the panel cannot kill the update)."""
    if confirm != "update":
        raise mod.ApiError(400, "not_confirmed", "需要确认更新")
    home = Path(mod.HERMES_HOME)
    repo = Path(mod.HERMES_INSTALL)
    if channel is not None and channel in CHANNELS:
        write_channel(home, channel)
    channel = read_channel(home)
    if _unit_active():
        raise mod.ApiError(409, "update_running", "已有更新正在进行")
    if shutil.disk_usage("/").free < UPDATE_MIN_FREE:
        raise mod.ApiError(400, "low_disk", "磁盘剩余不足 2 GB，先清理再更新")
    if not (repo / ".git").exists():
        raise mod.ApiError(400, "not_git_install", "安装目录不是 git 安装，不支持面板内更新")
    local = _local_git_info(mod)
    if local["branch"] != CLAWD_BRANCH:
        raise mod.ApiError(400, "wrong_branch", f"当前分支是 {local['branch'] or '未知'}，请先切回 clawd 分支再更新")
    if local["localChanges"]:
        names = "、".join(local["localChanges"][:5])
        raise mod.ApiError(400, "local_changes", f"安装目录有 {len(local['localChanges'])} 处本地改动（{names}），请先提交或清理再更新")
    releases = _fetch_releases(mod, home)
    if tag is None:
        latest = select_release(releases, channel)
        if latest is None:
            raise mod.ApiError(404, "no_release", "该通道暂无已发布的版本")
        tag = str(latest.get("tag_name") or "")
    target = next((r for r in releases if str(r.get("tag_name") or "") == tag), None)
    if target is None:
        raise mod.ApiError(404, "unknown_release", f"GitHub 上找不到版本 {tag}")
    parsed_tag = parse_version(tag)
    if parsed_tag is None or parsed_tag[0] != channel:
        raise mod.ApiError(400, "wrong_channel", f"版本 {tag} 不属于{'测试版' if channel == 'beta' else '正式版'}通道")
    if channel == "stable" and target.get("prerelease"):
        raise mod.ApiError(400, "wrong_channel", f"版本 {tag} 是测试版，不会装到正式版通道")
    if channel == "beta" and not target.get("prerelease"):
        raise mod.ApiError(400, "wrong_channel", f"版本 {tag} 不是测试版")
    parsed_current = parse_version(current_version(repo))
    if parsed_current is not None and parsed_tag[1] <= parsed_current[1]:
        raise mod.ApiError(400, "already_latest", f"已是最新版（当前 {current_version(repo)}，目标 {tag}）")
    root = _run_root(home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = root / time.strftime("%Y%m%d-%H%M%S")
    run.mkdir(mode=0o700)
    try:
        backup = getattr(mod, "_backup_local_changes", None)
        if callable(backup):
            backup(run)
        else:
            (run / "old-head").write_text(local["head"] + "\n", encoding="ascii")
        dist = repo / "apps" / "panel" / "dist"
        if dist.is_dir():
            shutil.copytree(dist, run / "dist-backup")
        runner_src = repo / "clawd_server" / "update_runner.sh"
        runner = run / "runner.sh"
        shutil.copyfile(runner_src, runner)
        os.chmod(runner, 0o700)
        (run / "target.json").write_text(
            json.dumps({"tag": tag, "channel": channel, "oldHead": local["head"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run / "update.log").write_text(f"[panel] 更新开始 {time.strftime('%F %T')}，目标 {tag}\n", encoding="utf-8")
        token = read_github_token(home)
        tag_commit = _fetch_tag(mod, repo, run, tag, token)
        ancestor = _git(repo, "merge-base", "--is-ancestor", "HEAD", tag_commit)
        if ancestor.returncode != 0:
            raise mod.ApiError(400, "not_fast_forward", f"目标 {tag} 不是当前提交的后代（本地有未发布的提交），无法快进更新")
        subprocess.run(["systemctl", "reset-failed", f"{UPDATE_UNIT}.service"], capture_output=True, timeout=10, check=False)
        log_path = run / "update.log"
        unit = _panel_unit(mod)
        cmd = [
            "systemd-run", f"--unit={UPDATE_UNIT}", "--quiet", "--property=MemoryMax=2G",
            f"--working-directory={repo}",
            f"--property=StandardOutput=append:{log_path}", f"--property=StandardError=append:{log_path}",
            f"--setenv=HOME={os.environ.get('HOME', '/root')}", f"--setenv=HERMES_HOME={home}",
            f"--setenv=CLAWD_PANEL_UNIT={unit}", "--setenv=NO_COLOR=1", "--setenv=TERM=dumb",
            "--", "/bin/sh", "-c", UPDATE_SH, "clawd-update", str(runner), str(run), tag_commit, local["head"],
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise mod.ApiError(500, "start_failed", detail[-300:] if detail else "启动更新失败")
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise
    for old in _run_dirs(home)[:-UPDATE_KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "runId": run.name}


def install_update(mod: Any) -> None:
    """Bind clawd-native update functions onto the sidecar module."""
    mod.get_update_info = lambda force=False, channel=None: get_update_info(mod, force, channel)
    mod.start_update = lambda confirm, tag=None, channel=None: start_update(mod, confirm, tag, channel)
    mod.get_update_plan = lambda: get_update_plan(mod)
    mod.get_update_status = lambda: get_update_status(mod)
    mod._clawd_update_native = True
