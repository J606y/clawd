"""Packaging declaration contract for bundled panel resources.

Does not build, install, or invent a project wheel. Archive comparison reads
only a path the caller supplies. Missing archives fail closed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from clawd_server.resources import distribution_path_is_pruned, distribution_relatives

REPO = Path(__file__).resolve().parents[2]
BUNDLE_MARKER = "clawd_server/bundled/"
CONTRACT_NAME = "RESOURCE_CONTRACT.json"

PACKAGE_DATA = [
    "bundled/RESOURCE_CONTRACT.json",
    "bundled/LICENSES/*",
    "bundled/apps/panel/dist/*",
    "bundled/apps/panel/dist/*/*",
    "bundled/apps/panel/dist/*/*/*",
    "bundled/apps/panel/plugins/*/*",
]

# sdist depth authority, including the browser vendor include/exclude/prune
# lines. Those lines are exact. They do not replace the panel depth rules,
# and they do not relax the archive size, sha256, or forbidden-member checks
# below. A vendor include is not a pass for a hash mismatch.
MANIFEST_LINES = [
    "include LICENSE",
    "include clawd_server/bundled/RESOURCE_CONTRACT.json",
    "recursive-include clawd_server/bundled/LICENSES *",
    "recursive-include clawd_server/bundled/apps/panel/dist *",
    "recursive-include clawd_server/bundled/apps/panel/plugins *",
    "recursive-include tools/vendor/browser_harness_0_1_13 *",
    "global-exclude */node_modules/*",
    "global-exclude */.venv/*",
    "global-exclude */venv/*",
    "global-exclude *.pyc",
    "exclude tools/vendor/browser_harness_0_1_13/PKG-INFO",
    "exclude tools/vendor/browser_harness_0_1_13/README.md",
    "exclude tools/vendor/browser_harness_0_1_13/pyproject.toml",
    "exclude tools/vendor/browser_harness_0_1_13/setup.cfg",
    "exclude tools/vendor/browser_harness_0_1_13/src/mcp_server.py",
    "recursive-exclude tools/vendor/browser_harness_0_1_13 *.egg-info/*",
    "recursive-exclude tools/vendor/browser_harness_0_1_13 __pycache__/*",
    "prune apps/desktop",
    "prune web",
    "prune apps/panel/node_modules",
    "prune hermes_cli/web_dist",
    "prune tools/vendor/browser_harness_0_1_13/src/browser_harness.egg-info",
]

REQUIRED_RELATIVE = (
    "LICENSES/LICENSE",
    "apps/panel/dist/index.html",
    "apps/panel/plugins/browser-window-guard/__init__.py",
    "apps/panel/plugins/browser-window-guard/live_task_store.py",
    "apps/panel/plugins/browser-window-guard/plugin.yaml",
    "apps/panel/plugins/browser-window-guard/README.md",
)

FORBIDDEN_PARTS = frozenset({"node_modules", "venv", ".venv", "__pycache__", ".git"})
# Real bundled search providers. Not an allowlist for other web paths.
REQUIRED_SEARCH = (
    "plugins/web/brave_free/__init__.py",
    "plugins/web/brave_free/provider.py",
    "plugins/web/brave_free/plugin.yaml",
    "plugins/web/ddgs/__init__.py",
    "plugins/web/ddgs/provider.py",
    "plugins/web/ddgs/plugin.yaml",
    "plugins/web/ddgs/_search_worker.py",
)


class ArchiveMismatch(AssertionError):
    """Real archive members do not match the bundled contract."""


def _posix(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def bundled_relative(member: str) -> str | None:
    norm = _posix(member)
    index = norm.find(BUNDLE_MARKER)
    if index < 0:
        return None
    relative = norm[index + len(BUNDLE_MARKER) :]
    if not relative or relative.endswith("/"):
        return None
    return relative


def _parts(path: str) -> list[str]:
    resolved: list[str] = []
    for part in _posix(path).split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(part)
    return resolved


def forbidden_reason(member: str) -> str | None:
    norm = _posix(member)
    parts = _parts(norm)
    if any(part in FORBIDDEN_PARTS for part in parts):
        return f"forbidden path segment: {norm}"
    if norm.endswith(".pyc") or norm.endswith("/.DS_Store") or norm.endswith(".DS_Store"):
        return f"forbidden cache file: {norm}"
    # Official UI only, after archive-root and resource-root normalization.
    # An inner /web/ is not the dashboard. Search paths are not an allowlist.
    if distribution_path_is_pruned(norm):
        return f"forbidden pruned tree: {norm}"
    return None


def _asset_js(relative: str) -> bool:
    prefix = "apps/panel/dist/assets/"
    if not relative.startswith(prefix) or not relative.endswith(".js"):
        return False
    rest = relative[len(prefix) :]
    return bool(rest) and "/" not in rest and rest not in (".", "..")


def _read_archive(path: Path) -> dict[str, bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.name.lower()
    members: dict[str, bytes] = {}
    if suffix.endswith(".whl") or suffix.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                members[_posix(info.filename)] = archive.read(info)
        return members
    if suffix.endswith(".tar.gz") or suffix.endswith(".tgz") or suffix.endswith(".tar"):
        with tarfile.open(path, "r:*") as archive:
            for info in archive.getmembers():
                name = _posix(info.name)
                if info.issym() or info.islnk():
                    raise ArchiveMismatch(f"refusing archive symlink: {name}")
                if not info.isfile():
                    continue
                extracted = archive.extractfile(info)
                if extracted is None:
                    raise ArchiveMismatch(f"unreadable archive member: {name}")
                members[name] = extracted.read()
        return members
    raise ArchiveMismatch(f"unsupported archive type: {path}")


def compare_distribution_archive(path: Path) -> dict[str, object]:
    """Compare one real wheel or sdist to the contract stored inside it.

    Raises FileNotFoundError when the archive is absent. Does not synthesize
    members or treat a missing build as success.
    """
    path = Path(path)
    members = _read_archive(path)
    bundled = {relative: name for name, raw in members.items() if (relative := bundled_relative(name))}
    if CONTRACT_NAME not in bundled:
        raise ArchiveMismatch(f"{path} has no {BUNDLE_MARKER}{CONTRACT_NAME}")
    contract_bytes = members[bundled[CONTRACT_NAME]]
    try:
        contract = json.loads(contract_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveMismatch(f"{path} contract is not utf-8 json") from exc
    if not isinstance(contract, dict) or contract.get("version") != 2:
        raise ArchiveMismatch(f"{path} contract version is not 2")
    entries = contract.get("files")
    if not isinstance(entries, list) or not entries:
        raise ArchiveMismatch(f"{path} contract files list is empty")

    listed: dict[str, dict] = {}
    missing: list[str] = []
    mismatched: list[str] = []
    for item in entries:
        if not isinstance(item, dict) or item.get("type") != "file":
            raise ArchiveMismatch(f"{path} contract entry is not a file: {item!r}")
        relative = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(relative, str) or not isinstance(digest, str) or type(size) is not int:
            raise ArchiveMismatch(f"{path} contract entry is incomplete: {item!r}")
        if forbidden_reason(BUNDLE_MARKER + relative):
            raise ArchiveMismatch(f"{path} contract lists a forbidden path: {relative}")
        listed[relative] = item
        member = bundled.get(relative)
        if member is None:
            missing.append(relative)
            continue
        data = members[member]
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            mismatched.append(relative)
    extra = sorted(set(bundled) - set(listed) - {CONTRACT_NAME})
    absent_required = [item for item in REQUIRED_RELATIVE if item not in listed]
    if not any(_asset_js(item) for item in listed):
        absent_required.append("apps/panel/dist/assets/*.js")
    problems: list[str] = []
    if missing:
        problems.append("missing: " + ", ".join(sorted(missing)))
    if mismatched:
        problems.append("hash/size mismatch: " + ", ".join(sorted(mismatched)))
    if extra:
        problems.append("residual: " + ", ".join(extra))
    if absent_required:
        problems.append("contract omits required: " + ", ".join(absent_required))
    for name in members:
        reason = forbidden_reason(name)
        if reason:
            problems.append(reason)
    name = path.name.lower()
    if name.endswith(".whl") or name.endswith(".zip"):
        if not any(item.endswith(".dist-info/licenses/LICENSE") for item in members):
            problems.append("wheel missing *.dist-info/licenses/LICENSE")
    else:
        if not any(_posix(item).endswith("/LICENSE") and bundled_relative(item) is None for item in members):
            problems.append("sdist missing root LICENSE")
    search_present = sorted(
        item for item in REQUIRED_SEARCH if item in _member_relatives(members)
    )
    if _is_clawd_distribution(members):
        missing_search = [item for item in REQUIRED_SEARCH if item not in search_present]
        if missing_search:
            problems.append("missing search provider: " + ", ".join(missing_search))
    if problems:
        raise ArchiveMismatch(f"{path} does not match contract v2:\n" + "\n".join(problems))
    return {
        "archive": str(path),
        "bundled_files": sorted(bundled),
        "contract_files": sorted(listed),
        "search_members": search_present,
    }


def _contract_entry(relative: str, data: bytes) -> dict[str, object]:
    return {
        "path": relative,
        "type": "file",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _fixture_tree() -> dict[str, bytes]:
    files = {
        "LICENSES/LICENSE": b"MIT\n",
        "apps/panel/dist/index.html": b"<html></html>\n",
        "apps/panel/dist/assets/app.js": b"console.log(1)\n",
        "apps/panel/dist/a/b/c/deep.txt": b"deep\n",
        "apps/panel/dist/assets/.keep": b"dot\n",
        "apps/panel/plugins/browser-window-guard/__init__.py": b"plugin\n",
        "apps/panel/plugins/browser-window-guard/live_task_store.py": b"plugin-store\n",
        "apps/panel/plugins/browser-window-guard/plugin.yaml": b"name: browser-window-guard\n",
        "apps/panel/plugins/browser-window-guard/README.md": b"notes\n",
    }
    contract = {
        "version": 2,
        "files": [_contract_entry(path, data) for path, data in sorted(files.items())],
    }
    files[CONTRACT_NAME] = json.dumps(contract, sort_keys=True).encode("utf-8")
    return files


def _zip_bytes(files: dict[str, bytes], *, prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for relative, data in files.items():
            archive.writestr(prefix + BUNDLE_MARKER + relative, data)
        archive.writestr(prefix + "hermes_agent-0.0.0.dist-info/licenses/LICENSE", b"MIT\n")
    return buffer.getvalue()


def test_declarations_keep_manifest_as_depth_authority():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    import tomllib

    parsed = tomllib.loads(text)
    setuptools = parsed["tool"]["setuptools"]
    assert setuptools.get("include-package-data", True) is True
    assert parsed["tool"]["setuptools"]["package-data"]["clawd_server"] == PACKAGE_DATA
    assert parsed["project"]["license-files"] == ["LICENSE"]
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert manifest == MANIFEST_LINES
    vendor_include = manifest.index("recursive-include tools/vendor/browser_harness_0_1_13 *")
    assert vendor_include < manifest.index("global-exclude *.pyc")
    assert "exclude tools/vendor/browser_harness_0_1_13/src/mcp_server.py" in manifest
    assert "prune tools/vendor/browser_harness_0_1_13/src/browser_harness.egg-info" in manifest
    setup = (REPO / "setup.py").read_text(encoding="utf-8")
    for klass in ("_GuardedSdist", "_GuardedBdistWheel"):
        body = setup.split(f"class {klass}", 1)[1].split("\nclass ", 1)[0]
        assert body.index("stage_distribution") < body.index("super().run(")


def test_setuptools_recursive_include_covers_depth_package_data_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from glob import glob
    from setuptools.command.egg_info import FileList

    monkeypatch.chdir(tmp_path)
    deep = tmp_path / "clawd_server/bundled/apps/panel/dist/a/b/c/deep.txt"
    dot = tmp_path / "clawd_server/bundled/apps/panel/dist/assets/.keep"
    nested = tmp_path / "apps/panel/node_modules/pkg/index.js"
    direct = tmp_path / "apps/panel/node_modules/left.js"
    for path, text in ((deep, "deep"), (dot, "dot"), (nested, "nope"), (direct, "nope")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    shallow = set(glob("clawd_server/bundled/apps/panel/dist/*/*/*", recursive=True))
    assert "clawd_server/bundled/apps/panel/dist/a/b/c/deep.txt" not in shallow
    assert all(".keep" not in item for item in glob("clawd_server/bundled/apps/panel/dist/**", recursive=True))

    found = FileList()
    assert found.recursive_include("clawd_server/bundled/apps/panel/dist", "*")
    included = {_posix(item) for item in found.files}
    assert "clawd_server/bundled/apps/panel/dist/a/b/c/deep.txt" in included
    assert "clawd_server/bundled/apps/panel/dist/assets/.keep" in included

    excluded = FileList()
    excluded.files = [
        "apps/panel/node_modules/pkg/index.js",
        "apps/panel/node_modules/left.js",
        "clawd_server/bundled/apps/panel/dist/a/b/c/deep.txt",
    ]
    excluded.global_exclude("*/node_modules/*")
    assert "apps/panel/node_modules/left.js" not in excluded.files
    assert "apps/panel/node_modules/pkg/index.js" in excluded.files
    excluded.prune("apps/panel/node_modules")
    assert "apps/panel/node_modules/pkg/index.js" not in excluded.files
    assert "clawd_server/bundled/apps/panel/dist/a/b/c/deep.txt" in excluded.files


def test_archive_checker_matches_contract_item_by_item(tmp_path: Path):
    wheel = tmp_path / "fixture-not-a-project-wheel.whl"
    wheel.write_bytes(_zip_bytes(_fixture_tree()))
    report = compare_distribution_archive(wheel)
    assert "apps/panel/dist/a/b/c/deep.txt" in report["contract_files"]
    assert "apps/panel/dist/assets/.keep" in report["contract_files"]

    broken = _fixture_tree()
    del_target = "apps/panel/dist/a/b/c/deep.txt"
    payload = json.loads(broken[CONTRACT_NAME].decode("utf-8"))
    payload["files"] = [item for item in payload["files"] if item["path"] != del_target]
    broken[CONTRACT_NAME] = json.dumps(payload, sort_keys=True).encode("utf-8")
    residual = tmp_path / "residual.whl"
    residual.write_bytes(_zip_bytes(broken))
    with pytest.raises(ArchiveMismatch, match="residual"):
        compare_distribution_archive(residual)

    forbidden = _fixture_tree()
    forbidden["apps/panel/dist/node_modules/pkg/index.js"] = b"nope"
    payload = json.loads(forbidden[CONTRACT_NAME].decode("utf-8"))
    payload["files"].append(_contract_entry("apps/panel/dist/node_modules/pkg/index.js", b"nope"))
    forbidden[CONTRACT_NAME] = json.dumps(payload, sort_keys=True).encode("utf-8")
    dirty = tmp_path / "dirty.whl"
    dirty.write_bytes(_zip_bytes(forbidden))
    with pytest.raises(ArchiveMismatch, match="node_modules"):
        compare_distribution_archive(dirty)


def test_supplied_archives_match_contract_and_missing_archives_fail_closed(tmp_path: Path):
    wheel_env = os.environ.get("CLAWD_DISTRIBUTION_WHEEL")
    sdist_env = os.environ.get("CLAWD_DISTRIBUTION_SDIST")
    if wheel_env is None and sdist_env is None:
        missing = tmp_path / "not-built.whl"
        with pytest.raises(FileNotFoundError):
            compare_distribution_archive(missing)
        return
    if not wheel_env or not sdist_env:
        raise AssertionError("验收必须同时给出 CLAWD_DISTRIBUTION_WHEEL 与 CLAWD_DISTRIBUTION_SDIST")
    compare_distribution_archive(Path(wheel_env))
    compare_distribution_archive(Path(sdist_env))


def _member_relatives(members: dict[str, bytes]) -> set[str]:
    found: set[str] = set()
    for name in members:
        found.update(distribution_relatives(name))
    return found


def _is_clawd_distribution(members: dict[str, bytes]) -> bool:
    """True for a clawd wheel or sdist, not a panel-only fixture archive."""
    for name in members:
        norm = _posix(name)
        if norm.startswith("clawd-") and ".dist-info/METADATA" in norm:
            return True
        first, _, rest = norm.partition("/")
        if rest == "PKG-INFO" and first.startswith("clawd-"):
            return True
    return False


def _tar_bytes(files: dict[str, bytes], prefix: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _project_wheel_bytes(
    *,
    include_search: bool = True,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for relative, data in _fixture_tree().items():
            archive.writestr(BUNDLE_MARKER + relative, data)
        archive.writestr(
            "clawd-0.0.0.dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: clawd\nVersion: 0.0.0\n",
        )
        archive.writestr("clawd-0.0.0.dist-info/licenses/LICENSE", b"MIT\n")
        if include_search:
            for relative in REQUIRED_SEARCH:
                archive.writestr(relative, (REPO / relative).read_bytes())
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _project_sdist_bytes(
    *,
    include_search: bool = True,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    files = {BUNDLE_MARKER + name: data for name, data in _fixture_tree().items()}
    files["LICENSE"] = b"MIT\n"
    files["PKG-INFO"] = b"Metadata-Version: 2.1\nName: clawd\nVersion: 0.0.0\n"
    if include_search:
        for relative in REQUIRED_SEARCH:
            files[relative] = (REPO / relative).read_bytes()
    if extra:
        files.update(extra)
    return _tar_bytes(files, "clawd-0.0.0/")


def test_search_providers_must_ship_and_official_ui_must_not(tmp_path: Path):
    for relative in REQUIRED_SEARCH:
        assert (REPO / relative).is_file(), relative
        assert forbidden_reason(relative) is None
        assert forbidden_reason(f"clawd-0.0.0/{relative}") is None
        assert forbidden_reason(f"probe-0.0.0/{relative}") is None
    kept = (
        "hermes_cli/web_routers/app.py",
        "tools/bot_desktop/launcher.sh",
        "tools/vendor/browser_harness_0_1_13/LICENSE",
        "skills/web/SKILL.md",
        "website/docs/user-guide/skills/bundled/web/SKILL.md",
        "docs/guide/web/page.md",
        "clawd_server/bundled/apps/panel/dist/index.html",
        "/tmp/not-a-repo/web/index.html",
        "clawd-0.0.0.data/purelib/plugins/web/ddgs/provider.py",
        "clawd-0.0.0.data/purelib/hermes_cli/web_routers/app.py",
        "clawd-0.0.0.data/data/docs/guide/web/page.md",
    )
    for item in kept:
        assert forbidden_reason(item) is None, item
    rejected = (
        "web/index.html",
        "web/package.json",
        "apps/desktop/package.json",
        "apps/desktop/dist-webapp/index.html",
        "hermes_cli/web_dist/index.html",
        "clawd-0.0.0/web/index.html",
        "probe-0.0.0/apps/desktop/src/main.ts",
        "boundaryprobe-0.0.0/hermes_cli/web_dist/assets/app.js",
        "clawd_server/bundled/web/index.html",
        "clawd_server/bundled/apps/desktop/secret.txt",
        "clawd_server/bundled/hermes_cli/web_dist/index.html",
        "clawd-0.0.0/clawd_server/bundled/web/index.html",
        "probe-0.0.0/clawd_server/bundled/apps/desktop/package.json",
        "clawd-0.0.0.data/data/web/index.html",
        "clawd-0.0.0.data/purelib/hermes_cli/web_dist/index.html",
        "clawd-0.0.0.data/data/apps/desktop/package.json",
        "plugins/web/../../web/index.html",
        "/tmp/checkout/clawd_server/bundled/web/index.html",
        "/tmp/checkout/hermes_cli/web_dist/index.html",
        "/tmp/checkout/apps/desktop/secret.txt",
    )
    for item in rejected:
        reason = forbidden_reason(item)
        assert reason and reason.startswith("forbidden pruned tree:"), item
    assert "node_modules" in (forbidden_reason("plugins/web/brave_free/node_modules/pkg/index.js") or "")
    assert "cache" in (forbidden_reason("plugins/web/ddgs/provider.pyc") or "")
    assert forbidden_reason("plugins/web/ddgs/__pycache__/x.pyc")
    assert forbidden_reason("plugins/web/brave_free/.DS_Store")

    wheel = tmp_path / "with-search.whl"
    wheel.write_bytes(_project_wheel_bytes())
    report = compare_distribution_archive(wheel)
    assert list(REQUIRED_SEARCH) == report["search_members"] or set(REQUIRED_SEARCH) == set(report["search_members"])

    missing = tmp_path / "missing-search.whl"
    missing.write_bytes(_project_wheel_bytes(include_search=False))
    with pytest.raises(ArchiveMismatch, match="missing search provider"):
        compare_distribution_archive(missing)

    leaked = tmp_path / "official-ui.whl"
    leaked.write_bytes(
        _project_wheel_bytes(
            extra={
                "web/index.html": b"ui\n",
                "clawd_server/bundled/apps/desktop/secret.txt": b"no\n",
                "hermes_cli/web_dist/index.html": b"ui\n",
            }
        )
    )
    with pytest.raises(ArchiveMismatch, match="forbidden pruned tree"):
        compare_distribution_archive(leaked)

    sdist = tmp_path / "with-search.tar.gz"
    sdist.write_bytes(_project_sdist_bytes())
    sdist_report = compare_distribution_archive(sdist)
    assert set(REQUIRED_SEARCH) <= set(sdist_report["search_members"])

    dirty_sdist = tmp_path / "nested-ui.tar.gz"
    dirty_sdist.write_bytes(
        _project_sdist_bytes(extra={"clawd_server/bundled/web/index.html": b"ui\n", "web/package.json": b"{}\n"})
    )
    with pytest.raises(ArchiveMismatch, match="forbidden pruned tree"):
        compare_distribution_archive(dirty_sdist)

    omitted = tmp_path / "missing-ddgs.tar.gz"
    omitted.write_bytes(_project_sdist_bytes(include_search=False))
    with pytest.raises(ArchiveMismatch, match="plugins/web/ddgs"):
        compare_distribution_archive(omitted)
