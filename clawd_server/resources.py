"""Panel and sidecar resource locations for checkout and installed layouts.

Checkout (and editable installs, whose ``__file__`` still lives in the tree)
read ``apps/panel`` next to ``pyproject.toml``. A wheel install reads the copy
staged under ``clawd_server/bundled/`` by ``scripts/build_clawd_resources.py``.

``code_root()`` is the directory that contains importable ``hermes_cli`` /
``clawd_server``. The sidecar settings helper runs ``python -I`` and inserts
that path; it must not be replaced with the panel resource root.

This module never downloads, never builds the frontend, and never falls back
to ``~/.hermes`` or ``/usr/local/lib/hermes-agent``. It does not delete a
pre-existing fixed ``.partial`` directory, and it does not delete unknown
data at a caller-supplied destination.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import stat
import uuid
from pathlib import Path

LAYOUT_CHECKOUT = "checkout"
LAYOUT_INSTALLED = "installed"
CONTRACT_VERSION = 2
CONTRACT_NAME = "RESOURCE_CONTRACT.json"
PANEL_RESOURCE_ROOT_ENV = "CLAWD_PANEL_RESOURCE_ROOT"

SYSTEM_SRC = Path("/usr/local/lib/hermes-agent")
SYSTEM_HERMES = Path("/usr/local/bin/hermes")
PRODUCTION_HOMES = (Path("/root/.hermes"), Path.home() / ".hermes")

DIST_SKIP_PARTS = frozenset({
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".git",
})
# Repo-relative prefixes of the official dashboard and Desktop trees.
# ``web`` is the top-level dashboard source root, not a path component:
# ``plugins/web`` is bundled search and must stay. ``web_routers`` and
# ``tools/bot_desktop`` are different names and stay.
SOURCE_PREFIXES = ("apps/desktop", "web", "hermes_cli/web_dist")
# Python packages the wheel must not discover. Search providers under
# ``plugins.web`` are not in this list. ``namespaces`` stays at the default.
PRUNED_PACKAGE_ROOTS = ("hermes_cli.web_dist",)
RESOURCE_ARCHIVE_ROOT = "clawd_server/bundled"
_WHEEL_DATA_SCHEMES = frozenset({"purelib", "platlib", "data", "scripts", "headers"})
DIST_BUILD_BASE_ENV = "CLAWD_DIST_BUILD_BASE"
_STAGE_PREFIX = ".clawd-dist.stage-"
SIDECAR_FILES = (
    "hermes_panel_sidecar.py",
    "image_studio.py",
    "live_task_store.py",
)
PLUGIN_NAME = "browser-window-guard"
PLUGIN_FILES = (
    "__init__.py",
    "live_task_store.py",
    "plugin.yaml",
    "README.md",
)
OMITTED_SIDECAR = (
    "test_*.py",
    "*.service",
    "requirements.txt",
)
_OWNED_MARKERS = (".stage-", ".rejected-")


class ResourceError(RuntimeError):
    """Missing, unsafe, or incomplete distribution resource."""


def _pkg(package_dir: Path | None = None) -> Path:
    if package_dir is None:
        return Path(__file__).resolve().parent
    return package_dir.resolve()


def _logical(path: Path) -> Path:
    """Absolute path without following the final symlink."""
    return Path(os.path.abspath(os.path.expanduser(path)))


def _resolved(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path


def under_production(path: Path) -> bool:
    """True for the official Hermes tree or a production data home."""
    resolved = _resolved(path)
    if resolved == SYSTEM_SRC or resolved in {_resolved(item) for item in PRODUCTION_HOMES}:
        return True
    try:
        resolved.relative_to(_resolved(SYSTEM_SRC))
        return True
    except ValueError:
        return False


def _contained(path: Path, root: Path) -> bool:
    try:
        _logical(path).relative_to(_logical(root))
        return True
    except ValueError:
        return False


def _symlink_component(path: Path) -> Path | None:
    logical = _logical(path)
    acc = Path(logical.anchor)
    for part in logical.parts[1:]:
        acc = acc / part
        if acc.is_symlink():
            return acc
    return None


def _refuse_symlink(path: Path) -> None:
    found = _symlink_component(path)
    if found is not None:
        raise ResourceError(f"refusing symlink: {found}")


def detect_layout(package_dir: Path | None = None) -> str:
    """Return ``checkout`` or ``installed``. Never selects a production tree."""
    package_dir = _pkg(package_dir)
    parent = package_dir.parent
    marker = parent / "apps" / "panel" / "sidecar" / "hermes_panel_sidecar.py"
    project = parent / "pyproject.toml"
    if (
        project.is_file()
        and not project.is_symlink()
        and marker.is_file()
        and not marker.is_symlink()
        and not under_production(parent)
    ):
        return LAYOUT_CHECKOUT
    return LAYOUT_INSTALLED


def layout(package_dir: Path | None = None) -> str:
    return detect_layout(package_dir)


def code_root(package_dir: Path | None = None) -> Path:
    """Import root for ``hermes_cli`` / ``clawd_server``. Not the panel tree."""
    package_dir = _pkg(package_dir)
    path = package_dir.parent
    if detect_layout(package_dir) != LAYOUT_CHECKOUT and under_production(path):
        raise ResourceError(f"refusing production install as clawd code root: {path}")
    if under_production(path) and detect_layout(package_dir) == LAYOUT_CHECKOUT:
        raise ResourceError(f"refusing production tree as clawd checkout: {path}")
    return path


def bundled_root(package_dir: Path | None = None) -> Path:
    path = _pkg(package_dir) / "bundled"
    if under_production(path):
        raise ResourceError(f"refusing production path as bundled resources: {path}")
    return path


def panel_root(package_dir: Path | None = None) -> Path:
    """Directory that contains ``dist/``, ``sidecar/``, and ``plugins/``."""
    package_dir = _pkg(package_dir)
    if detect_layout(package_dir) == LAYOUT_CHECKOUT:
        path = package_dir.parent / "apps" / "panel"
    else:
        path = bundled_root(package_dir) / "apps" / "panel"
    if under_production(path):
        raise ResourceError(f"refusing production path as panel root: {path}")
    return path


def _installed_incomplete_message() -> str:
    return (
        "安装包资源未通过完整性检查（路径、文件类型、版本、必要文件或散列不符）。"
        "不会把残留目录当作完整面板，也不会在安装环境下载或构建前端。"
    )


def panel_static_dir(package_dir: Path | None = None, *, require_built: bool | None = None) -> Path:
    """Built panel ``dist``. Checkout may be missing (server returns 503).

    Installed layout raises instead of pretending the frontend can be built
    in the install environment. A residual bundle with only ``index.html``
    is not treated as complete.
    """
    package_dir = _pkg(package_dir)
    path = panel_root(package_dir) / "dist"
    installed = detect_layout(package_dir) == LAYOUT_INSTALLED
    must = installed if require_built is None else require_built
    if must and not (path / "index.html").is_file():
        raise ResourceError(
            "安装包缺少已构建的面板静态资源（index.html）。"
            "在源码树先构建面板，再运行 scripts/build_clawd_resources.py 后重新打包。"
            "不会在安装环境下载或构建前端。"
        )
    if must and not bundled_is_complete(bundled_root(package_dir)):
        raise ResourceError(_installed_incomplete_message())
    return path


def sidecar_dir(package_dir: Path | None = None) -> Path:
    package_dir = _pkg(package_dir)
    if detect_layout(package_dir) == LAYOUT_INSTALLED and not bundled_is_complete(bundled_root(package_dir)):
        raise ResourceError(_installed_incomplete_message())
    path = panel_root(package_dir) / "sidecar"
    marker = path / "hermes_panel_sidecar.py"
    if not marker.is_file() or marker.is_symlink():
        raise ResourceError(
            f"sidecar Python 资源缺失: {marker}。"
            "检出需要 apps/panel/sidecar；安装包需要先暂存再打包。"
        )
    return path


def plugin_dir(name: str = PLUGIN_NAME, package_dir: Path | None = None) -> Path:
    package_dir = _pkg(package_dir)
    if detect_layout(package_dir) == LAYOUT_INSTALLED and not bundled_is_complete(bundled_root(package_dir)):
        raise ResourceError(_installed_incomplete_message())
    path = panel_root(package_dir) / "plugins" / name
    marker = path / "plugin.yaml"
    if not marker.is_file() or marker.is_symlink():
        raise ResourceError(f"面板插件资源缺失: {marker}")
    return path


def license_file(package_dir: Path | None = None) -> Path:
    package_dir = _pkg(package_dir)
    if detect_layout(package_dir) == LAYOUT_CHECKOUT:
        path = package_dir.parent / "LICENSE"
    else:
        if not bundled_is_complete(bundled_root(package_dir)):
            raise ResourceError(_installed_incomplete_message())
        path = bundled_root(package_dir) / "LICENSES" / "LICENSE"
    if not path.is_file() or path.is_symlink():
        raise ResourceError(f"许可证文件缺失: {path}")
    return path


def python_path_entries(package_dir: Path | None = None) -> tuple[Path, ...]:
    """Paths to prepend so ``import sidecar...`` works. Code root only in checkout."""
    package_dir = _pkg(package_dir)
    entries: list[Path] = []
    if detect_layout(package_dir) == LAYOUT_CHECKOUT:
        entries.append(code_root(package_dir))
    entries.append(panel_root(package_dir))
    return tuple(entries)


def export_resource_env(package_dir: Path | None = None, *, require_panel: bool = False) -> dict[str, str]:
    """Publish layout paths. Does not set ``HERMES_SRC`` (that stays ``code_root()``)."""
    package_dir = _pkg(package_dir)
    values = {
        "CLAWD_RESOURCE_LAYOUT": detect_layout(package_dir),
        "CLAWD_CODE_ROOT": str(code_root(package_dir)),
        "CLAWD_PANEL_ROOT": str(panel_root(package_dir)),
    }
    if require_panel or detect_layout(package_dir) == LAYOUT_INSTALLED:
        values["CLAWD_PANEL_STATIC"] = str(panel_static_dir(package_dir))
    else:
        values["CLAWD_PANEL_STATIC"] = str(panel_root(package_dir) / "dist")
    store = panel_root(package_dir) / "plugins" / PLUGIN_NAME / "live_task_store.py"
    if store.is_file() and not store.is_symlink():
        values["PANEL_LIVE_STORE_MODULE"] = str(store)
    for key, value in values.items():
        os.environ[key] = value
    return values


def _rel_forbidden(relative: str) -> bool:
    if not relative or relative.startswith("/") or "\\" in relative:
        return True
    parts = [part for part in relative.split("/") if part not in ("", ".")]
    if any(part in ("..",) or part in DIST_SKIP_PARTS for part in parts):
        return True
    if relative == "web" or relative.startswith("web/") or relative.startswith("apps/desktop"):
        return True
    if relative.startswith("hermes_cli/web_dist"):
        return True
    return False


def _panel_tree_problems(panel: Path) -> list[str]:
    problems: list[str] = []
    if panel.is_symlink() or not panel.is_dir():
        problems.append(f"面板资源根不是真实目录: {panel}")
        return problems
    dist = panel / "dist"
    if dist.is_symlink() or not (dist / "index.html").is_file() or (dist / "index.html").is_symlink():
        problems.append(
            "面板静态资源未构建：缺少 apps/panel/dist/index.html。"
            "请先在源码树构建面板。本脚本不会下载或构建前端。"
        )
    assets = dist / "assets"
    js_files = [
        path for path in assets.glob("*.js")
        if path.is_file() and not path.is_symlink()
    ] if assets.is_dir() and not assets.is_symlink() else []
    if not js_files:
        problems.append("面板静态资源不完整：apps/panel/dist/assets 下没有 *.js。不会自动构建。")
    sidecar = panel / "sidecar"
    for name in SIDECAR_FILES:
        path = sidecar / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"sidecar 模块缺失: apps/panel/sidecar/{name}")
    plugin = panel / "plugins" / PLUGIN_NAME
    for name in PLUGIN_FILES:
        path = plugin / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"插件资源缺失: apps/panel/plugins/{PLUGIN_NAME}/{name}")
    return problems


def source_problems(checkout: Path) -> list[str]:
    """Problems that must fail packaging. Does not build or download."""
    checkout = _logical(checkout)
    problems: list[str] = []
    if under_production(checkout):
        problems.append(f"refusing to stage from production tree: {checkout}")
        return problems
    problems.extend(_panel_tree_problems(checkout / "apps" / "panel"))
    license_path = checkout / "LICENSE"
    if not license_path.is_file() or license_path.is_symlink():
        problems.append("仓库 LICENSE 缺失，拒绝打包无许可证的资源副本。")
    return problems


def resource_root_problems(root: Path) -> list[str]:
    """Problems in an explicit panel resource root. Does not build or download."""
    root = _logical(root)
    problems: list[str] = []
    if under_production(root):
        problems.append(f"refusing production path as panel resource root: {root}")
        return problems
    if _symlink_component(root) is not None:
        problems.append(f"refusing symlink: {root}")
        return problems
    panel = root
    nested = root / "apps" / "panel"
    if nested.is_dir() and not nested.is_symlink() and (nested / "dist" / "index.html").is_file():
        panel = nested
    problems.extend(_panel_tree_problems(panel))
    return problems


def _assert_dest_safe(checkout: Path, dest: Path) -> None:
    _refuse_symlink(dest.parent)
    if dest.exists() or dest.is_symlink():
        _refuse_symlink(dest)
    if under_production(dest):
        raise ResourceError(f"refusing to stage into a production path: {dest}")
    panel = checkout / "apps" / "panel"
    if panel.exists() and _contained(dest, panel):
        raise ResourceError("refusing to stage resources inside apps/panel")
    try:
        _logical(dest).relative_to(_logical(checkout))
    except ValueError:
        return
    relative = _logical(dest).relative_to(_logical(checkout)).as_posix()
    if _rel_forbidden(relative) or relative.startswith("apps/desktop"):
        raise ResourceError(f"refusing to stage into excluded path: {relative}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular(path: Path, root: Path) -> bytes:
    if path.is_symlink():
        raise ResourceError(f"refusing symlink: {path}")
    _refuse_symlink(path)
    if not _contained(path, root):
        raise ResourceError(f"refusing path outside root: {path}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ResourceError(f"refusing non-file resource: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    return b"".join(chunks)


def _write_regular(path: Path, data: bytes, root: Path) -> None:
    if not _contained(path, root):
        raise ResourceError(f"refusing to write outside staging root: {path}")
    _refuse_symlink(path.parent)
    if path.exists() or path.is_symlink():
        raise ResourceError(f"refusing to overwrite an existing staging path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_file(src: Path, dst: Path, src_root: Path, dst_root: Path) -> dict[str, object]:
    data = _read_regular(src, src_root)
    _write_regular(dst, data, dst_root)
    relative = _logical(dst).relative_to(_logical(dst_root)).as_posix()
    if _rel_forbidden(relative):
        raise ResourceError(f"refusing to copy excluded path: {relative}")
    return {
        "path": relative,
        "type": "file",
        "sha256": _sha256(data),
        "size": len(data),
    }


def _iter_dist_files(dist: Path) -> list[Path]:
    if dist.is_symlink():
        raise ResourceError(f"refusing symlink: {dist}")
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(dist, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            raise ResourceError(f"refusing symlink: {current}")
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if name in DIST_SKIP_PARTS or name.startswith("."):
                continue
            if child.is_symlink():
                raise ResourceError(f"refusing symlink: {child}")
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name.endswith(".pyc") or name == ".DS_Store":
                continue
            path = current / name
            if path.is_symlink():
                raise ResourceError(f"refusing symlink: {path}")
            files.append(path)
    return files


def _required_bundle_paths() -> tuple[str, ...]:
    names = ["LICENSES/LICENSE", "apps/panel/dist/index.html"]
    names.extend(f"apps/panel/sidecar/{name}" for name in SIDECAR_FILES)
    names.extend(f"apps/panel/plugins/{PLUGIN_NAME}/{name}" for name in PLUGIN_FILES)
    return tuple(names)


def _asset_js(relative: str) -> bool:
    prefix = "apps/panel/dist/assets/"
    if not relative.startswith(prefix) or not relative.endswith(".js"):
        return False
    rest = relative[len(prefix):]
    return bool(rest) and "/" not in rest and rest not in (".", "..")


def _load_contract(dest: Path) -> dict | None:
    path = dest / CONTRACT_NAME
    if not path.is_file() or path.is_symlink() or not _contained(path, dest):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def bundled_is_complete(dest: Path) -> bool:
    """True only for a verified v2 bundle. Residual or hash-mismatched trees fail."""
    if dest.is_symlink() or not dest.is_dir():
        return False
    if _symlink_component(dest) is not None:
        return False
    contract = _load_contract(dest)
    if contract is None or contract.get("version") != CONTRACT_VERSION:
        return False
    if contract.get("license") != "LICENSES/LICENSE":
        return False
    entries = contract.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    listed: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            return False
        relative = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        if item.get("type") != "file":
            return False
        if not isinstance(relative, str) or _rel_forbidden(relative):
            return False
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            return False
        if type(size) is not int or size < 0:
            return False
        if relative in listed:
            return False
        target = dest / relative
        if not _contained(target, dest) or target.is_symlink() or not target.is_file():
            return False
        try:
            data = _read_regular(target, dest)
        except (OSError, ResourceError):
            return False
        if len(data) != size or _sha256(data) != digest:
            return False
        listed[relative] = item
    if any(relative not in listed for relative in _required_bundle_paths()):
        return False
    if not any(_asset_js(relative) for relative in listed):
        return False
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            return False
        for name in list(dirnames):
            child = current / name
            if child.is_symlink():
                return False
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                return False
            relative = path.relative_to(dest).as_posix()
            if relative == CONTRACT_NAME:
                continue
            if relative not in listed:
                return False
    return True


def _canonical_dest(checkout: Path) -> Path:
    return _logical(checkout / "clawd_server" / "bundled")


def _select_panel_tree(checkout: Path, resource_root: Path | None) -> Path:
    panel = checkout / "apps" / "panel"
    if panel.is_symlink() or (panel.exists() and not panel.is_dir()):
        raise ResourceError(f"refusing apps/panel that is not a real directory: {panel}")
    if panel.is_dir():
        problems = source_problems(checkout)
        if problems:
            raise ResourceError("\n".join(problems))
        return panel
    chosen = resource_root
    if chosen is None:
        env = os.environ.get(PANEL_RESOURCE_ROOT_ENV)
        if env:
            chosen = Path(env)
    if chosen is None:
        raise ResourceError(
            "找不到 apps/panel，且未设置 CLAWD_PANEL_RESOURCE_ROOT。"
            "不会使用忽略目录里预置的 bundled。不能发布缺少面板资源的包。"
        )
    problems = resource_root_problems(chosen)
    if problems:
        raise ResourceError("\n".join(problems))
    nested = _logical(chosen) / "apps" / "panel"
    if nested.is_dir() and not nested.is_symlink() and (nested / "dist" / "index.html").is_file():
        return nested
    return _logical(chosen)


def _acquire_lock(dest: Path) -> int:
    parent = dest.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ResourceError(f"refusing to stage when parent is missing or a symlink: {parent}")
    lock_path = parent / f".{dest.name}.stage.lock"
    if lock_path.is_symlink():
        raise ResourceError(f"refusing symlink: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ResourceError(f"refusing stage lock {lock_path}: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ResourceError(
            f"另一路暂存正在占用 {lock_path}，拒绝并发写入。不删除该锁，也不等待。"
        ) from exc
    return fd


def _unique_sibling(parent: Path, dest_name: str, kind: str) -> Path:
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    path = parent / f".{dest_name}.{kind}-{token}"
    if path.exists() or path.is_symlink():
        raise ResourceError(f"refusing occupied stage path: {path}")
    return path


def _make_owned_stage_dir(parent: Path, dest_name: str) -> Path:
    path = _unique_sibling(parent, dest_name, "stage")
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def _remove_tree_we_created(path: Path) -> None:
    """Remove a directory this call created. Refuses every other path."""
    if not any(marker in path.name for marker in _OWNED_MARKERS):
        raise ResourceError(f"refusing to remove a path this stage did not create: {path}")
    if path.is_symlink():
        raise ResourceError(f"refusing symlink: {path}")
    if not path.is_dir():
        raise ResourceError(f"refusing to remove a non-directory stage path: {path}")

    def _walk(current: Path) -> None:
        for child in list(current.iterdir()):
            if not _contained(child, path):
                raise ResourceError(f"refusing to remove path outside owned stage dir: {child}")
            if child.is_symlink():
                child.unlink()
                continue
            if child.is_dir():
                _walk(child)
                child.rmdir()
                continue
            if child.is_file():
                child.unlink()
                continue
            raise ResourceError(f"refusing unexpected node in owned stage dir: {child}")

    _walk(path)
    path.rmdir()


def _fill_staging(checkout: Path, panel: Path, staging: Path) -> list[dict[str, object]]:
    copied: list[dict[str, object]] = []
    dist = panel / "dist"
    for src in _iter_dist_files(dist):
        relative = Path("apps") / "panel" / "dist" / src.relative_to(dist)
        entry = _copy_file(src, staging / relative, panel, staging)
        copied.append(entry)
    for name in SIDECAR_FILES:
        rel = Path("apps") / "panel" / "sidecar" / name
        copied.append(_copy_file(panel / "sidecar" / name, staging / rel, panel, staging))
    for name in PLUGIN_FILES:
        rel = Path("apps") / "panel" / "plugins" / PLUGIN_NAME / name
        copied.append(_copy_file(panel / "plugins" / PLUGIN_NAME / name, staging / rel, panel, staging))
    license_rel = Path("LICENSES") / "LICENSE"
    copied.append(_copy_file(checkout / "LICENSE", staging / license_rel, checkout, staging))
    if not any(_asset_js(str(item["path"])) for item in copied):
        raise ResourceError("暂存结果没有 apps/panel/dist/assets/*.js，拒绝发布不完整包。")
    return copied


def _write_contract(staging: Path, copied: list[dict[str, object]]) -> None:
    files = sorted(copied, key=lambda item: str(item["path"]))
    contract = {
        "version": CONTRACT_VERSION,
        "layout": LAYOUT_INSTALLED,
        "panel_static": "apps/panel/dist",
        "sidecar": "apps/panel/sidecar",
        "plugins": [PLUGIN_NAME],
        "license": "LICENSES/LICENSE",
        "hash": "sha256",
        "omitted_sidecar": list(OMITTED_SIDECAR),
        "excluded_parts": sorted(DIST_SKIP_PARTS),
        "excluded_prefixes": list(SOURCE_PREFIXES),
        "files": files,
    }
    payload = (json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_regular(staging / CONTRACT_NAME, payload, staging)


def _restore_previous(dest: Path, backup: Path | None, moved: bool) -> None:
    if not moved or backup is None:
        return
    if dest.is_symlink() or backup.is_symlink():
        raise ResourceError(
            f"发布失败，且路径含 symlink。旧包仍在 {backup}，拒绝继续移动。"
        )
    if dest.exists():
        rejected = _unique_sibling(dest.parent, dest.name, "rejected")
        os.rename(dest, rejected)
    if not dest.exists() and backup.exists():
        os.rename(backup, dest)


def stage_distribution(
    checkout: Path,
    dest: Path | None = None,
    *,
    replace: bool = False,
    resource_root: Path | None = None,
    _before_publish=None,
) -> Path:
    """Copy built panel assets and sidecar/plugin modules into ``dest``.

    The canonical slot ``checkout/clawd_server/bundled`` may replace a previous
    bundle that already passes this module's integrity check. That replacement
    renames the old tree aside and leaves it there. Any other existing
    destination is refused unless ``replace`` is true, and even then only a
    verified previous bundle is swapped. Unknown contents are never removed.

    A missing ``apps/panel`` does not mean a pre-placed bundle is complete.
    Set ``resource_root`` or ``CLAWD_PANEL_RESOURCE_ROOT`` to the built panel
    root (Nix ``panelResources``). This function does not build or download.
    """
    checkout = _logical(checkout)
    if checkout.is_symlink() or not checkout.is_dir():
        raise ResourceError(f"refusing checkout that is not a real directory: {checkout}")
    if under_production(checkout):
        raise ResourceError(f"refusing to stage from production tree: {checkout}")
    explicit = dest is not None
    canonical = _canonical_dest(checkout)
    dest = _logical(dest if dest is not None else canonical)
    _assert_dest_safe(checkout, dest)
    parent = dest.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ResourceError(f"refusing to stage when parent is missing or a symlink: {parent}")
    panel = _select_panel_tree(checkout, resource_root)
    if not (checkout / "LICENSE").is_file():
        raise ResourceError("仓库 LICENSE 缺失，拒绝打包无许可证的资源副本。")

    is_canonical = dest == canonical
    allow_replace = (is_canonical and not explicit) or replace
    if dest.is_symlink():
        raise ResourceError(f"refusing symlink: {dest}")
    if dest.exists() and not dest.is_dir():
        raise ResourceError(f"refusing non-directory bundle destination: {dest}")
    if dest.exists() and not allow_replace:
        raise ResourceError(
            "已有目标目录。默认拒绝覆盖未声明的路径。"
            "只有 checkout/clawd_server/bundled 是打包计划内的替换位；"
            "其它路径需要 replace=True，且只能交换已校验的旧包。"
        )
    if dest.exists() and not bundled_is_complete(dest):
        raise ResourceError(
            "目标已有内容但不是已校验的资源包。拒绝删除未知数据。"
            "请先把该路径移走。不会清理固定 .partial。"
        )

    lock_fd: int | None = None
    staging: Path | None = None
    backup: Path | None = None
    moved = False
    try:
        lock_fd = _acquire_lock(dest)
        if dest.exists() and not bundled_is_complete(dest):
            raise ResourceError("目标在加锁后不再是已校验资源包，拒绝替换。")
        staging = _make_owned_stage_dir(parent, dest.name)
        copied = _fill_staging(checkout, panel, staging)
        _write_contract(staging, copied)
        if not bundled_is_complete(staging):
            raise ResourceError("暂存结果未通过完整性检查，未替换现有资源。")
        if dest.exists():
            backup = _unique_sibling(parent, dest.name, "kept")
            os.rename(dest, backup)
            moved = True
        if _before_publish is not None:
            _before_publish(staging, dest, backup)
        os.rename(staging, dest)
        staging = None
        if not bundled_is_complete(dest):
            raise ResourceError("发布后完整性检查失败，将恢复旧包。")
    except Exception:
        try:
            _restore_previous(dest, backup, moved)
        except Exception as restore_exc:
            raise ResourceError(
                f"发布失败且旧包未能复原。旧包仍在 {backup}，不要删除。原因: {restore_exc}"
            ) from restore_exc
        raise
    finally:
        pending = sys.exception()
        if lock_fd is not None:
            os.close(lock_fd)
        if staging is not None and (staging.exists() or staging.is_symlink()):
            try:
                _remove_tree_we_created(staging)
            except Exception:
                # Keep the original failure. Do not delete any other path to compensate.
                if pending is None:
                    raise
    return dest


def wheel_resource_prefix() -> str:
    return "clawd_server/bundled/"


def pruned_package_excludes() -> tuple[str, ...]:
    """setuptools ``packages.find`` exclude globs. Does not drop other plugins."""
    names: list[str] = []
    for root in PRUNED_PACKAGE_ROOTS:
        names.append(root)
        names.append(root + ".*")
    return tuple(names)


def package_is_pruned(name: str) -> bool:
    if not name:
        return False
    for root in PRUNED_PACKAGE_ROOTS:
        if name == root or name.startswith(root + "."):
            return True
    return False


def _posix_parts(path: str) -> list[str]:
    """Split a path and resolve ``.`` / ``..`` so traversal cannot hide a prefix."""
    resolved: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(part)
    return resolved


def _is_sdist_archive_root(part: str) -> bool:
    """True for a setuptools sdist top directory ``name-version``.

    Not ``.dist-info`` and not ``.data``. A bare ``web`` or ``plugins`` does not match.
    """
    if part.endswith(".dist-info") or part.endswith(".data"):
        return False
    if "-" not in part:
        return False
    name, version = part.rsplit("-", 1)
    return bool(name) and bool(version) and version[0].isdigit()


def _is_wheel_data_root(part: str) -> bool:
    if not part.endswith(".data"):
        return False
    return _is_sdist_archive_root(part[: -len(".data")])


def distribution_relatives(path: str) -> tuple[str, ...]:
    """Repo-relative views after known archive and resource roots are stripped.

    Known archive roots are one leading sdist ``name-version`` directory, or a
    wheel ``name-version.data/<scheme>/`` nest. The known resource root is
    ``clawd_server/bundled``. An inner ``web`` directory is not a root.
    """
    parts = _posix_parts(path)
    seeds = [parts]
    if parts and _is_sdist_archive_root(parts[0]):
        seeds.append(parts[1:])
    if len(parts) >= 2 and _is_wheel_data_root(parts[0]) and parts[1] in _WHEEL_DATA_SCHEMES:
        seeds.append(parts[2:])
    relatives: list[str] = []
    seen: set[str] = set()

    def _emit(view: list[str]) -> None:
        if not view:
            return
        joined = "/".join(view)
        if joined in seen:
            return
        seen.add(joined)
        relatives.append(joined)

    resource = RESOURCE_ARCHIVE_ROOT.split("/")
    for seed in seeds:
        _emit(seed)
        if len(seed) >= len(resource) and seed[: len(resource)] == resource:
            _emit(seed[len(resource) :])
        limit = len(seed) - len(resource) + 1
        for index in range(limit):
            if seed[index : index + len(resource)] == resource:
                _emit(seed[index + len(resource) :])
    return tuple(relatives)


def _starts_official(relative: str) -> bool:
    for prefix in SOURCE_PREFIXES:
        if relative == prefix or relative.startswith(prefix + "/"):
            return True
    return False


def distribution_path_is_pruned(path: str) -> bool:
    """True only for the official dashboard or Desktop trees.

    ``web`` matches only as a prefix after known archive and resource roots are
    removed. ``plugins/web``, ``hermes_cli/web_routers``, and any other inner
    ``web`` directory are not the official UI. ``apps/desktop`` and
    ``hermes_cli/web_dist`` match as those exact component sequences, including
    under the resource root or an absolute checkout path. ``bot_desktop`` does not.
    """
    if not path:
        return False
    parts = _posix_parts(path)
    for index in range(len(parts) - 1):
        if parts[index] == "apps" and parts[index + 1] == "desktop":
            return True
        if parts[index] == "hermes_cli" and parts[index + 1] == "web_dist":
            return True
    return any(_starts_official(relative) for relative in distribution_relatives(path))


def filter_pruned_packages(packages: list[str] | tuple[str, ...] | None):
    if not packages:
        return packages
    return [name for name in packages if not package_is_pruned(name)]


def merge_pruned_exclude_package_data(existing: dict | None) -> dict[str, list[str]]:
    """Drop official dashboard data a parent glob would still copy.

    Does not add ``plugins = ["web/*"]``. That fnmatch crosses separators and
    would drop search provider manifests.
    """
    merged: dict[str, list[str]] = {}
    if existing:
        for key, patterns in existing.items():
            merged[str(key)] = [str(item) for item in patterns]
    extra = {"hermes_cli": ("web_dist/*",)}
    for key, patterns in extra.items():
        current = list(merged.get(key, []))
        for pattern in patterns:
            if pattern not in current:
                current.append(pattern)
        merged[key] = current
    return merged


def _is_owned_stage(path: Path) -> bool:
    return any(".stage-" in part for part in Path(path).parts)


def _refuse_repository_cache(path: Path) -> None:
    logical = _logical(path)
    if logical.is_symlink():
        raise ResourceError(f"refusing symlink: {logical}")
    if logical.name in {"build", "lib", "SOURCES.txt"} or logical.name.endswith(".egg-info"):
        raise ResourceError(f"refusing repository cache path: {logical}")
    for part in logical.parts:
        if part == "build" or part.endswith(".egg-info"):
            raise ResourceError(f"refusing to stage inside repository cache: {logical}")


def _make_external_stage_dir() -> Path:
    import tempfile

    created = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX))
    if ".stage-" not in created.name or created.is_symlink() or not created.is_dir():
        raise ResourceError(f"refusing stage path: {created}")
    _refuse_repository_cache(created)
    return created


def _remove_owned_build_stage(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if ".stage-" not in path.name:
        raise ResourceError(f"refusing to remove a path this stage did not create: {path}")
    _refuse_repository_cache(path)
    _remove_tree_we_created(path)


def _assert_cache_not_finalized(distribution) -> None:
    commands = getattr(distribution, "command_obj", None) or {}
    build = commands.get("build")
    if build is not None and getattr(build, "finalized", False):
        current = Path(os.path.abspath(getattr(build, "build_base", "build")))
        if not _is_owned_stage(current):
            raise ResourceError(
                "refusing to reuse an already finalized build directory "
                f"({current}). Old build/lib is not an input."
            )
    egg = commands.get("egg_info")
    if egg is not None and getattr(egg, "finalized", False):
        current = Path(os.path.abspath(getattr(egg, "egg_base", None) or os.curdir))
        if not _is_owned_stage(current):
            raise ResourceError(
                f"refusing to reuse in-tree egg-info SOURCES.txt ({current})."
            )


def _boundary_root(distribution) -> Path:
    existing = getattr(distribution, "_clawd_boundary_root", None)
    if existing is not None:
        root = Path(existing)
        if root.is_symlink() or not root.is_dir():
            raise ResourceError(f"refusing distribution build base: {root}")
        _refuse_repository_cache(root)
        return root
    env = os.environ.get(DIST_BUILD_BASE_ENV)
    if env:
        root = Path(env)
        if root.is_symlink() or not root.is_dir():
            raise ResourceError(f"refusing distribution build base: {root}")
        _refuse_repository_cache(root)
        distribution._clawd_boundary_root = root
        distribution._clawd_boundary_owned = False
        return root
    root = _make_external_stage_dir()
    distribution._clawd_boundary_root = root
    distribution._clawd_boundary_owned = True
    return root


def _pin_commands(distribution, root: Path) -> None:
    build_base = root / "build"
    egg_base = root / "egg"
    build_base.mkdir(mode=0o700, exist_ok=True)
    egg_base.mkdir(mode=0o700, exist_ok=True)
    build = distribution.get_command_obj("build")
    if not getattr(build, "finalized", False):
        build.build_base = str(build_base)
    elif not _is_owned_stage(Path(os.path.abspath(build.build_base))):
        raise ResourceError(f"refusing finalized build directory: {build.build_base}")
    egg = distribution.get_command_obj("egg_info")
    if not getattr(egg, "finalized", False):
        egg.egg_base = str(egg_base)
    elif not _is_owned_stage(Path(os.path.abspath(egg.egg_base or os.curdir))):
        raise ResourceError(f"refusing finalized egg-info directory: {egg.egg_base}")


def apply_distribution_boundary(distribution):
    """Drop pruned packages and point this build at a fresh stage directory.

    Does not delete ``build/``, ``*.egg-info``, or ``SOURCES.txt``. A command
    that already finalized those default paths is refused instead of reused.
    """
    _assert_cache_not_finalized(distribution)
    packages = getattr(distribution, "packages", None)
    if packages:
        distribution.packages = filter_pruned_packages(list(packages))
    distribution.exclude_package_data = merge_pruned_exclude_package_data(
        getattr(distribution, "exclude_package_data", None)
    )
    root = _boundary_root(distribution)
    try:
        _pin_commands(distribution, root)
    except Exception:
        if getattr(distribution, "_clawd_boundary_owned", False):
            release_distribution_boundary(distribution)
        raise
    return root


def release_distribution_boundary(distribution) -> None:
    """Remove only a stage directory this command created."""
    if not getattr(distribution, "_clawd_boundary_owned", False):
        return
    root = getattr(distribution, "_clawd_boundary_root", None)
    distribution._clawd_boundary_root = None
    distribution._clawd_boundary_owned = False
    if root is None:
        return
    _remove_owned_build_stage(Path(root))


def begin_distribution_boundary_env():
    """Create the stage directory PEP 517 will hand to ``setup.py``."""
    root = _make_external_stage_dir()
    previous = os.environ.get(DIST_BUILD_BASE_ENV)
    os.environ[DIST_BUILD_BASE_ENV] = str(root)
    return previous, root


def end_distribution_boundary_env(token) -> None:
    previous, root = token
    current = os.environ.get(DIST_BUILD_BASE_ENV)
    if current == str(root):
        if previous is None:
            os.environ.pop(DIST_BUILD_BASE_ENV, None)
        else:
            os.environ[DIST_BUILD_BASE_ENV] = previous
    _remove_owned_build_stage(Path(root))


def make_pruned_build_py():
    """build_py that does not copy pruned modules or parent-package data."""
    from setuptools.command.build_py import build_py as _build_py

    class _PrunedBuildPy(_build_py):
        def finalize_options(self):
            apply_distribution_boundary(self.distribution)
            super().finalize_options()
            if self.packages:
                self.packages = filter_pruned_packages(list(self.packages))

        def find_all_modules(self):
            found = super().find_all_modules()
            return [item for item in found if not package_is_pruned(item[0])]

        def find_data_files(self, package, src_dir):
            files = super().find_data_files(package, src_dir)
            return [item for item in files if not distribution_path_is_pruned(item)]

    return _PrunedBuildPy
