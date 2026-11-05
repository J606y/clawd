"""Static inclusion contract for the vendored browser-harness tree.

Does not build, install, import ``browser_harness``, or open a clawd wheel
or sdist. ``CLAWD_DISTRIBUTION_WHEEL`` and ``CLAWD_DISTRIBUTION_SDIST`` are
not acceptance inputs here. A missing vendor tree is a failure, not a pass.
"""

from __future__ import annotations

import hashlib
import tarfile
from fnmatch import fnmatchcase
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VENDOR_REL = Path("tools/vendor/browser_harness_0_1_13")
VENDOR = REPO / VENDOR_REL
SDIST = REPO / "artifacts/browser-daemon-source/browser_harness-0.1.13.tar.gz"
SDIST_PREFIX = "browser_harness-0.1.13/"

SDIST_SHA256 = "284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3"
LICENSE_SHA256 = "5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631"
IPC_SHA256 = "b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3"
UNPATCHED_DAEMON_SHA256 = "7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97"
UNPATCHED_HELPERS_SHA256 = "5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3"

NESTED_PACKAGE = "tools.vendor.browser_harness_0_1_13.src.browser_harness"
PATCHED = {
    "src/browser_harness/daemon.py": UNPATCHED_DAEMON_SHA256,
    "src/browser_harness/helpers.py": UNPATCHED_HELPERS_SHA256,
}
UPSTREAM_SHA256 = {
    "src/browser_harness/SKILL.md": "2155ce650490e214734b2ea2f3b23f9dd12addedd0382e048a8e9d805ab507a7",
    "src/browser_harness/__init__.py": "b159e84a2701448a4905041f48b27bf6d26cc420b597b82e4fdeabbd7fe26c2f",
    "src/browser_harness/_ipc.py": IPC_SHA256,
    "src/browser_harness/admin.py": "d2d21c753f06e9275ce32291ca7e2c20acb83fab50486fffdbbfbb0fb1a7da94",
    "src/browser_harness/auth.py": "e71acf31c28524f4b26f87770333d154c2182b70a9412be5276d982fa6c2cf9d",
    "src/browser_harness/macos.py": "e7fb08ba92af6976a58c6abc91e36eb625b978102ba03fee8f9f5911c88f5a74",
    "src/browser_harness/mcp_cli.py": "11d66e3168436bf9bf49c2ddd301c19ac7bed9e717fc907fbb5ff61c7c160ece",
    "src/browser_harness/paths.py": "ba3c6e4c91a8e5f8add837eedd51a1aa391f47a7d11c79db47ac8b8146267ad7",
    "src/browser_harness/recorder.py": "ccf987870ab88cff7dbefe586165eaac122bba53dc73813e8202d65c840814e8",
    "src/browser_harness/run.py": "cf2f5e52596c45686f0b4d606b89bce10a153293d2205523bfb246d161dc07b7",
    "src/browser_harness/telemetry.py": "8d48b95afe4b149c2be115e0df18dd9f62496148a194277702d74829455cfa94",
    "src/browser_harness/video-template.html": "e237f67c2636e6946fd853c286fba0df0e7b10cfa2c95d4a9bb08fdd9d24e933",
    "src/browser_harness/video.py": "6112a7582cfc71f7a8db9d8c0f703cbbf6ec47b5c5464564725154ffc3e41142",
    "src/browser_harness/video_render.py": "96a2a69d3653d47f8a2de1b4b51d1b7deca4a12d163e03d7f4afcc262b2e4e04",
}
VENDOR_DATA = [
    "vendor/browser_harness_0_1_13/LICENSE",
    "vendor/browser_harness_0_1_13/NOTICE",
    "vendor/browser_harness_0_1_13/src/browser_harness/SKILL.md",
    "vendor/browser_harness_0_1_13/src/browser_harness/video-template.html",
]
TOOLS_PACKAGE_DATA = [
    "bot_desktop/launcher.sh",
    "bot_desktop/wallpaper.png",
    *VENDOR_DATA,
]
MANIFEST_LINES = [
    "include LICENSE",
    "include clawd_server/bundled/RESOURCE_CONTRACT.json",
    "recursive-include clawd_server/bundled/LICENSES *",
    "recursive-include clawd_server/bundled/apps/panel/dist *",
    "recursive-include clawd_server/bundled/apps/panel/sidecar *.py",
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
ALLOWED = {"LICENSE", "NOTICE", *UPSTREAM_SHA256, *PATCHED}
FORBIDDEN_REL = {
    "PKG-INFO",
    "README.md",
    "pyproject.toml",
    "setup.cfg",
    "src/mcp_server.py",
    "src/browser_harness.egg-info",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_locked_sdist() -> dict[str, bytes]:
    if not SDIST.is_file():
        raise AssertionError(f"locked sdist missing: {SDIST}")
    raw = SDIST.read_bytes()
    if _sha256(raw) != SDIST_SHA256:
        raise AssertionError("artifact sdist sha256 is not the locked 0.1.13 archive")
    members: dict[str, bytes] = {}
    with tarfile.open(SDIST, "r:gz") as archive:
        for info in archive.getmembers():
            name = info.name.replace("\\", "/")
            if info.issym() or info.islnk():
                raise AssertionError(f"refusing sdist symlink: {name}")
            if not info.isfile():
                continue
            if not name.startswith(SDIST_PREFIX):
                raise AssertionError(f"unexpected sdist member: {name}")
            extracted = archive.extractfile(info)
            if extracted is None:
                raise AssertionError(f"unreadable sdist member: {name}")
            members[name[len(SDIST_PREFIX) :]] = extracted.read()
    return members


def _vendor_files() -> dict[str, bytes]:
    if not VENDOR.is_dir():
        raise AssertionError(
            "vendor 树不存在。缺树不是已收录。禁止改 packages.find 来补这个缺口，"
            "也禁止把源码可导入当成 wheel/sdist 已包含。"
        )
    if VENDOR.is_symlink():
        raise AssertionError("refusing symlinked vendor root")
    found: dict[str, bytes] = {}
    for path in sorted(VENDOR.rglob("*")):
        relative = path.relative_to(VENDOR).as_posix()
        if path.is_symlink():
            raise AssertionError(f"refusing vendor symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AssertionError(f"refusing non-regular vendor path: {relative}")
        found[relative] = path.read_bytes()
    return found


def test_declarations_keep_distribution_identity_and_vendor_data_only():
    import tomllib

    parsed = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert parsed["project"]["name"] == "clawd"
    assert parsed["project"]["license-files"] == ["LICENSE"]
    assert "vendor/browser_harness_0_1_13/LICENSE" not in parsed["project"]["license-files"]
    pin = "browser-harness==0.1.13; python_version >= '3.14'"
    assert pin in parsed["project"]["dependencies"]
    find = parsed["tool"]["setuptools"]["packages"]["find"]
    assert "namespaces" not in find
    assert find.get("where", ["."]) in (["."], ".")
    assert "tools" in find["include"]
    assert "tools.*" in find["include"]
    assert "browser_harness" not in find["include"]
    assert not any("browser_harness_0_1_13" in item for item in find["include"])
    assert parsed["tool"]["setuptools"].get("include-package-data", True) is True
    package_data = parsed["tool"]["setuptools"]["package-data"]
    assert package_data["tools"] == TOOLS_PACKAGE_DATA
    assert all(not item.endswith(".py") for item in package_data["tools"])
    assert not any("browser_harness" in item for item in package_data["clawd_server"])
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert manifest == MANIFEST_LINES
    include_at = manifest.index("recursive-include tools/vendor/browser_harness_0_1_13 *")
    assert include_at < manifest.index("global-exclude *.pyc")
    assert not (REPO / "browser_harness").exists()
    assert not (REPO / "src" / "browser_harness").exists()
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    block = lock.split('[[package]]\nname = "browser-harness"\n', 1)[1].split("\n[[package]]", 1)[0]
    assert 'version = "0.1.13"' in block
    assert f"sha256:{SDIST_SHA256}" in block
    assert "browser_harness_0_1_13" not in lock
    for relative in ("nix/lib.nix", "nix/python.nix", "setup.py", "clawd_server/resources.py"):
        assert "browser_harness_0_1_13" not in (REPO / relative).read_text(encoding="utf-8")


def test_existing_tools_star_matches_nested_src_package_name():
    import inspect

    from setuptools.config.expand import find_packages

    assert inspect.signature(find_packages).parameters["namespaces"].default is True
    assert fnmatchcase(NESTED_PACKAGE, "tools.*")
    for part in Path(NESTED_PACKAGE.replace(".", "/")).parts:
        assert "." not in part
        assert part.isidentifier()
    # A dot in a directory name makes PackageFinder skip that directory and
    # not descend. The vendored package path has none, so the walk reaches it.
    assert "." in "browser_harness.egg-info"


def test_vendor_files_match_locked_sdist_except_two_patches():
    members = _read_locked_sdist()
    vendor = _vendor_files()
    problems: list[str] = []
    extra = sorted(set(vendor) - ALLOWED)
    missing = sorted(ALLOWED - set(vendor))
    if extra:
        problems.append("extra: " + ", ".join(extra))
    if missing:
        problems.append("missing: " + ", ".join(missing))
    if "src/mcp_server.py" in vendor or "src/mcp_server.py" in extra:
        problems.append(
            "src/mcp_server.py is not part of the vendored package; "
            "PEP 420 would ship it as tools.vendor.browser_harness_0_1_13.src.mcp_server; "
            "MANIFEST exclude does not stop that wheel module; "
            "a packages.find exclude is out of scope — do not copy this file"
        )
    for relative, expected in UPSTREAM_SHA256.items():
        original = members.get(relative)
        if original is None or _sha256(original) != expected:
            problems.append(f"locked sdist drift: {relative}")
            continue
        got = vendor.get(relative)
        if got is None:
            continue
        if _sha256(got) != expected:
            problems.append(f"vendor bytes differ from locked original: {relative}")
    for relative, unpatched in PATCHED.items():
        original = members.get(relative)
        if original is None or _sha256(original) != unpatched:
            problems.append(f"locked sdist patch baseline drift: {relative}")
            continue
        got = vendor.get(relative)
        if got is None:
            continue
        digest = _sha256(got)
        if digest == unpatched or got == original:
            problems.append(f"vendor file is still the unpatched original: {relative}")
        if not got.strip():
            problems.append(f"vendor patch is empty: {relative}")
    license_bytes = vendor.get("LICENSE")
    if license_bytes is not None:
        if _sha256(license_bytes) != LICENSE_SHA256:
            problems.append("vendor LICENSE hash is not the Browser Use MIT text")
        if b"Copyright (c) 2026 Browser Use" not in license_bytes:
            problems.append("vendor LICENSE lost the upstream copyright line")
        if license_bytes == (REPO / "LICENSE").read_bytes():
            problems.append("vendor LICENSE was replaced with the repo root license")
    notice = vendor.get("NOTICE")
    if notice is not None:
        text = notice.decode("utf-8")
        required = (
            "Browser Use",
            "MIT",
            SDIST_SHA256,
            "v0.1.13",
            "daemon.py",
            "helpers.py",
        )
        absent = [token for token in required if token not in text]
        if absent:
            problems.append("NOTICE missing: " + ", ".join(absent))
        if "Copyright" not in text and "版权" not in text:
            problems.append("NOTICE does not mention copyright")
        if not any(token in text for token in ("不移除", "保留", "not remove", "retain")):
            problems.append("NOTICE does not say the patch keeps upstream copyright")
        if notice == (REPO / "LICENSE").read_bytes():
            problems.append("NOTICE is the repo root license")
    if problems:
        raise AssertionError("vendor distribution bytes are not the locked contract:\n" + "\n".join(problems))


def test_real_tree_is_discovered_without_changing_packages_find():
    if not VENDOR.is_dir():
        raise AssertionError(
            "vendor 树不存在，无法核对嵌套 src 发现。不要改 packages.find 来掩盖缺树。"
        )
    from setuptools.config.expand import find_packages
    from setuptools.discovery import PackageFinder

    include = ["browser_harness_0_1_13", "browser_harness_0_1_13.*"]
    kwargs = {"where": ["."], "include": include, "root_dir": VENDOR.parent}
    found = find_packages(namespaces=True, **kwargs)
    nested = "browser_harness_0_1_13.src.browser_harness"
    if nested not in found:
        raise AssertionError(
            "setuptools 83 namespaces=True did not discover the nested src package. "
            "Changing packages.find is out of scope. Do not omit the package silently "
            "and do not treat a source-tree import as wheel inclusion."
        )
    traditional = PackageFinder.find(VENDOR.parent, include=include)
    if nested in traditional:
        raise AssertionError(
            "traditional __init__.py discovery saw the nested package; "
            "intermediate __init__.py files are not part of the vendor contract"
        )
