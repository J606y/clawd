#!/usr/bin/env python3
"""配套测试。编写时未运行，也不连接网络。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate


MIT_LICENSE = "MIT License\n\nCopyright (c) 2025 Nous Research\n\nPermission is hereby granted.\n"
BSD_LICENSE = "BSD License\n\nCopyright (c) Example\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class InventoryTests(unittest.TestCase):
    def test_default_inputs_are_explicit_files(self) -> None:
        joined = "/".join(generate.DEFAULT_RELATIVE_INPUTS)
        self.assertNotIn("*", joined)
        self.assertNotIn("node_modules", joined)
        self.assertNotIn("venv", joined)
        self.assertIn("LICENSE", generate.DEFAULT_RELATIVE_INPUTS)
        self.assertIn("NOTICE", generate.DEFAULT_RELATIVE_INPUTS)

    def test_missing_field_is_not_filled_with_mit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = _write(
                root / "package-lock.json",
                json.dumps(
                    {
                        "name": "demo",
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "demo"},
                            "node_modules/has-license": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/has-license/-/has-license-1.0.0.tgz",
                                "integrity": "sha512-present",
                                "license": "MIT",
                            },
                            "node_modules/no-license": {
                                "version": "2.0.0",
                                "resolved": "https://registry.npmjs.org/no-license/-/no-license-2.0.0.tgz",
                                "integrity": "sha512-absent",
                            },
                        },
                    }
                ),
            )
            doc = generate.build_inventory([lock])
            rows = {row["name"]: row for row in doc["dependencies"]}
            self.assertEqual(rows["no-license"]["license"], "unknown")
            self.assertEqual(rows["no-license"]["declared_license"], "unknown")
            self.assertNotEqual(rows["no-license"]["declared_license"], "MIT")
            self.assertEqual(rows["has-license"]["license"], "unknown")
            self.assertEqual(rows["has-license"]["declared_license"], "MIT")
            self.assertEqual(rows["has-license"]["integrity"], "sha512-present")

    def test_manifest_license_field_alone_is_not_determination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "package.json", '{"name":"hermes-agent","license":"MIT"}')
            doc = generate.build_inventory([path])
            self.assertEqual(doc["components"][0]["manifest_license"], "MIT")
            self.assertEqual(doc["components"][0]["license"], "unknown")

    def test_sibling_notice_does_not_cover_other_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "LICENSE", MIT_LICENSE)
            _write(root / "pyproject.toml", '[project]\nname = "hermes-agent"\nlicense = "MIT"\ndependencies = ["rich==1"]\n')
            _write(root / "package.json", '{"name":"hermes-agent","license":"MIT"}')
            _write(root / "apps" / "panel" / "package.json", '{"name":"hermes-panel","version":"0.1.0"}')
            doc = generate.build_inventory(
                [
                    root / "LICENSE",
                    root / "pyproject.toml",
                    root / "package.json",
                    root / "apps" / "panel" / "package.json",
                ]
            )
            by_path = {Path(item["path"]).name + ":" + str(Path(item["path"]).parent.name): item for item in doc["components"]}
            self.assertEqual(by_path["pyproject.toml:" + root.name]["license"], "MIT")
            self.assertEqual(by_path["package.json:" + root.name]["license"], "MIT")
            self.assertEqual(by_path["package.json:panel"]["license"], "unknown")
            rich = next(row for row in doc["dependencies"] if row["name"] == "rich")
            self.assertEqual(rich["license"], "unknown")
            self.assertEqual(rich["packaged"], "unknown")

    def test_non_mit_heading_is_not_relabeled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "LICENSE", BSD_LICENSE)
            _write(root / "pyproject.toml", '[project]\nname = "demo"\nlicense = "MIT"\n')
            doc = generate.build_inventory([root / "LICENSE", root / "pyproject.toml"])
            self.assertEqual(doc["notices"][0]["heading"], "BSD License")
            self.assertEqual(doc["components"][0]["license"], "unknown")
            self.assertEqual(doc["components"][0]["manifest_license"], "MIT")

    def test_directory_is_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "pkg"
            base.mkdir()
            (base / "secret.txt").write_text("secret-token", encoding="utf-8")
            doc = generate.build_inventory([base])
            self.assertEqual(doc["inputs"][0]["status"], "directory_refused")
            self.assertNotIn("secret-token", json.dumps(doc))
            self.assertEqual(doc["dependencies"], [])
            self.assertTrue(doc["errors"])

    def test_node_modules_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_modules" / "leftpad" / "package.json"
            doc = generate.build_inventory([path])
            self.assertEqual(doc["inputs"][0]["status"], "forbidden")
            self.assertFalse(path.exists())
            self.assertEqual(doc["dependencies"], [])

    def test_python_relation_scope_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "pyproject.toml",
                """
[project]
name = "hermes-agent"
dependencies = [
  "prompt_toolkit==3.0.53",
  "tzdata==2025.3; sys_platform == 'win32'",
]
[dependency-groups]
dev = ["pytest==9.1.1"]
[build-system]
requires = ["setuptools==83.0.0"]
""".strip(),
            )
            _write(
                root / "uv.lock",
                """
[[package]]
name = "hermes-agent"
version = "0.0.0"
source = { editable = "." }

[[package]]
name = "prompt-toolkit"
version = "3.0.53"
source = { registry = "https://pypi.org/simple" }
dependencies = [
  { name = "wcwidth" },
]
sdist = { url = "https://example.invalid/prompt.tgz", hash = "sha256:aaa" }
wheels = [
  { url = "https://example.invalid/a.whl", hash = "sha256:bbb" },
  { url = "https://example.invalid/b.whl", hash = "sha256:ccc" },
]

[[package]]
name = "wcwidth"
version = "0.2.13"
source = { registry = "https://pypi.org/simple" }
wheels = [
  { url = "https://example.invalid/w.whl", hash = "sha256:ddd" },
]

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
dependencies = [
  { name = "pluggy" },
]
wheels = [
  { url = "https://example.invalid/p.whl", hash = "sha256:eee" },
]

[[package]]
name = "pluggy"
version = "1.6.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
  { url = "https://example.invalid/pl.whl", hash = "sha256:fff" },
]

[[package]]
name = "tzdata"
version = "2025.3"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["sys_platform == 'win32'"]
wheels = [
  { url = "https://example.invalid/t.whl", hash = "sha256:111" },
]

[[package]]
name = "psutil"
version = "7.2.2"
source = { registry = "https://pypi.org/simple" }
wheels = [
  { url = "https://example.invalid/ps.whl", hash = "sha256:777" },
]

[[package]]
name = "psutil"
version = "8.0.0"
source = { git = "https://github.com/giampaolo/psutil.git?rev=abc" }

[[package]]
name = "kittentts"
version = "0.8.1"
source = { url = "https://example.invalid/kittentts.whl" }
wheels = [
  { url = "https://example.invalid/kittentts.whl", hash = "sha256:222" },
]

[[package]]
name = "orphan"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
""".strip(),
            )
            doc = generate.build_inventory([root / "pyproject.toml", root / "uv.lock"])
            locks = [row for row in doc["dependencies"] if row["record_kind"] == "lock"]
            by_key = {(row["name"], row["version"]): row for row in locks}
            prompt = by_key[("prompt-toolkit", "3.0.53")]
            self.assertEqual(prompt["relation"], "direct")
            self.assertEqual(prompt["install_class"], "runtime")
            self.assertEqual(prompt["packaged"], "unknown")
            self.assertEqual(prompt["license"], "unknown")
            self.assertEqual(prompt["integrity"], ["sha256:aaa", "sha256:bbb", "sha256:ccc"])
            self.assertEqual(by_key[("wcwidth", "0.2.13")]["relation"], "transitive")
            self.assertEqual(by_key[("wcwidth", "0.2.13")]["install_class"], "runtime")
            self.assertEqual(by_key[("pytest", "9.1.1")]["install_class"], "development")
            self.assertEqual(by_key[("pytest", "9.1.1")]["packaged"], "unknown")
            self.assertEqual(by_key[("pluggy", "1.6.0")]["relation"], "transitive")
            self.assertEqual(by_key[("pluggy", "1.6.0")]["install_class"], "development")
            tzdata = by_key[("tzdata", "2025.3")]
            self.assertEqual(tzdata["relation"], "direct")
            self.assertEqual(tzdata["install_class"], "unknown")
            self.assertIn("runtime", tzdata["possible_install_classes"])
            self.assertEqual(by_key[("psutil", "8.0.0")]["integrity"], "unknown")
            self.assertEqual(by_key[("psutil", "8.0.0")]["source_type"], "git")
            self.assertEqual(by_key[("psutil", "7.2.2")]["version"], "7.2.2")
            self.assertEqual(by_key[("kittentts", "0.8.1")]["source_type"], "url")
            self.assertEqual(by_key[("kittentts", "0.8.1")]["integrity"], ["sha256:222"])
            self.assertEqual(by_key[("orphan", "1.0.0")]["install_class"], "unknown")
            self.assertNotIn(("hermes-agent", "0.0.0"), by_key)

    def test_lock_without_manifest_is_unknown_not_transitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "uv.lock",
                '[[package]]\nname = "rich"\nversion = "1"\nsource = { registry = "https://pypi.org/simple" }\n',
            )
            doc = generate.build_inventory([path])
            self.assertEqual(doc["dependencies"][0]["relation"], "unknown")

    def test_npm_direct_dev_and_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "package.json",
                json.dumps({"name": "hermes-panel", "dependencies": {"react": "^19.3.1"}, "devDependencies": {"vite": "^8.3.3"}}),
            )
            _write(
                root / "package-lock.json",
                json.dumps(
                    {
                        "name": "hermes-panel",
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "hermes-panel"},
                            "ui-tui": {"name": "hermes-tui", "version": "0.0.1"},
                            "node_modules/react": {
                                "version": "19.3.1",
                                "resolved": "https://registry.npmjs.org/react/-/react-19.3.1.tgz",
                                "integrity": "sha512-react",
                            },
                            "node_modules/loose-envify": {
                                "version": "1.4.0",
                                "resolved": "https://registry.npmjs.org/loose-envify/-/loose-envify-1.4.0.tgz",
                                "integrity": "sha512-loose",
                                "license": "MIT",
                            },
                            "node_modules/vite": {
                                "version": "8.3.3",
                                "resolved": "https://registry.npmjs.org/vite/-/vite-8.3.3.tgz",
                                "integrity": "sha512-vite",
                                "dev": True,
                            },
                            "node_modules/@hermes/ink": {"resolved": "ui-tui/packages/hermes-ink", "link": True},
                        },
                    }
                ),
            )
            doc = generate.build_inventory([root / "package.json", root / "package-lock.json"])
            locks = {row["lock_path"]: row for row in doc["dependencies"] if row["record_kind"] == "lock"}
            self.assertNotIn("ui-tui", locks)
            self.assertEqual(locks["node_modules/react"]["relation"], "direct")
            self.assertEqual(locks["node_modules/react"]["install_class"], "runtime")
            self.assertEqual(locks["node_modules/react"]["declared_license"], "unknown")
            self.assertEqual(locks["node_modules/react"]["packaged"], "unknown")
            self.assertEqual(locks["node_modules/loose-envify"]["relation"], "transitive")
            self.assertEqual(locks["node_modules/loose-envify"]["license"], "unknown")
            self.assertEqual(locks["node_modules/vite"]["install_class"], "development")
            self.assertEqual(locks["node_modules/vite"]["lock_dev"], True)
            self.assertEqual(locks["node_modules/vite"]["packaged"], "unknown")
            ink = locks["node_modules/@hermes/ink"]
            self.assertEqual(ink["relation"], "first_party")
            self.assertEqual(ink["license"], "unknown")
            self.assertEqual(ink["integrity"], "unknown")
            self.assertEqual(ink["version"], "unknown")

    def test_requirements_hashes_and_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "requirements.txt",
                "# comment\nPyYAML==6.0.3 \\\n    --hash=sha256:abc \\\n    --hash=sha256:def\n",
            )
            doc = generate.build_inventory([path])
            row = doc["dependencies"][0]
            self.assertEqual(row["name"], "PyYAML")
            self.assertEqual(row["version"], "6.0.3")
            self.assertEqual(row["source"], "unknown")
            self.assertEqual(row["integrity"], ["sha256:abc", "sha256:def"])
            self.assertEqual(row["license"], "unknown")
            self.assertEqual(row["packaged"], "unknown")

    def test_parse_error_invents_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "package-lock.json", "{")
            doc = generate.build_inventory([path])
            self.assertEqual(doc["inputs"][0]["status"], "parse_error")
            self.assertEqual(doc["dependencies"], [])
            self.assertTrue(doc["errors"])
            self.assertNotIn("cve", doc)
            self.assertEqual(doc["advisory_status"], "not_scanned")
            self.assertEqual(doc["compliance_conclusion"], "not_determined")
            self.assertFalse(doc["security_scan"])

    def test_names_normalize(self) -> None:
        self.assertEqual(generate.normalize_python_name("prompt_toolkit"), "prompt-toolkit")
        self.assertEqual(generate.normalize_python_name("ruamel.yaml.clib"), "ruamel-yaml-clib")
        self.assertEqual(generate.normalize_python_name("PyJWT"), "pyjwt")

    def test_source_has_no_network_imports(self) -> None:
        text = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import urllib", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("import subprocess", text)
        self.assertNotIn("import socket", text)
        self.assertNotIn('or "MIT"', text)
        self.assertNotIn("or 'MIT'", text)
        self.assertIn("--offline", text)
        self.assertIn("--local-only", text)
        self.assertIn('"network_audit": false', text)

    def test_main_refuses_forbidden_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write(root / "package.json", '{"name":"demo"}')
            code = generate.main(["--input", str(manifest), "--output", str(root / "node_modules" / "out.json")])
            self.assertEqual(code, 2)
            self.assertFalse((root / "node_modules").exists())

    def test_pyproject_optional_reaches_lock_transitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "pyproject.toml",
                '[project]\nname = "demo"\ndependencies = ["rich"]\n\n[project.optional-dependencies]\nvoice = ["soundfile"]\n',
            )
            _write(
                root / "uv.lock",
                """
[[package]]
name = "soundfile"
version = "0.14.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
  { name = "numpy" },
]
wheels = [
  { url = "https://example.invalid/soundfile.whl", hash = "sha256:sss" },
]

[[package]]
name = "numpy"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
""".strip(),
            )
            doc = generate.build_inventory([root / "pyproject.toml", root / "uv.lock"])
            locks = {(row["name"], row["version"]): row for row in doc["dependencies"] if row["record_kind"] == "lock"}
            sound = locks[("soundfile", "0.14.0")]
            self.assertEqual(sound["relation"], "direct")
            self.assertEqual(sound["install_class"], "optional")
            self.assertEqual(sound["integrity"], ["sha256:sss"])
            self.assertEqual(sound["license"], "unknown")
            self.assertEqual(sound["packaged"], "unknown")
            numpy = locks[("numpy", "2.0.0")]
            self.assertEqual(numpy["relation"], "transitive")
            self.assertEqual(numpy["install_class"], "optional")
            self.assertEqual(numpy["integrity"], "unknown")
            self.assertEqual(numpy["license"], "unknown")

    def test_python_lock_optional_dev_git_url_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "LICENSE", MIT_LICENSE)
            _write(root / "pyproject.toml", '[project]\nname = "demo"\nlicense = "MIT"\ndependencies = ["rich"]\n')
            _write(
                root / "uv.lock",
                """
[[package]]
name = "demo"
version = "0.0.0"
source = { editable = "." }
dependencies = [
  { name = "rich" },
  { name = "misaki", extra = ["en"] },
]

[package.optional-dependencies]
voice = [
  { name = "soundfile", marker = "sys_platform != 'win32'" },
]

[package.dev-dependencies]
dev = [
  { name = "pytest" },
]

[[package]]
name = "rich"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
  { name = "mdit" },
]
wheels = [
  { url = "https://example.invalid/rich.whl", hash = "sha256:aaa" },
]

[[package]]
name = "mdit"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
  { url = "https://example.invalid/mdit.whl", hash = "sha256:bbb" },
]

[[package]]
name = "misaki"
version = "0.9.4"
source = { git = "https://github.com/NousResearch/misaki.git?rev=abc123#abc123" }
dependencies = [
  { name = "addict" },
]
license = "Apache-2.0"

[package.optional-dependencies]
en = [
  { name = "num2words" },
]

[[package]]
name = "addict"
version = "2.4.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
  { url = "https://example.invalid/addict.whl", hash = "sha256:ccc" },
]

[[package]]
name = "num2words"
version = "0.5.14"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "soundfile"
version = "0.14.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
dependencies = [
  { name = "pluggy" },
]
wheels = [
  { url = "https://example.invalid/pytest.whl", hash = "sha256:ddd" },
]

[[package]]
name = "pluggy"
version = "1.6.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "kittentts"
version = "0.8.1"
source = { url = "https://example.invalid/kittentts.whl" }
wheels = [
  { url = "https://example.invalid/kittentts.whl", hash = "sha256:eee" },
]

[[package]]
name = "orphan"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "bare"
""".strip(),
            )
            doc = generate.build_inventory([root / "LICENSE", root / "pyproject.toml", root / "uv.lock"])
            self.assertEqual(doc["components"][0]["license"], "MIT")
            locks = {(row["name"], row["version"]): row for row in doc["dependencies"] if row["record_kind"] == "lock"}
            self.assertNotIn(("demo", "0.0.0"), locks)
            rich = locks[("rich", "1.0.0")]
            self.assertEqual(rich["relation"], "direct")
            self.assertEqual(rich["install_class"], "runtime")
            self.assertEqual(rich["integrity"], ["sha256:aaa"])
            self.assertEqual(rich["license"], "unknown")
            self.assertEqual(locks[("mdit", "2.0.0")]["relation"], "transitive")
            self.assertEqual(locks[("mdit", "2.0.0")]["install_class"], "runtime")
            misaki = locks[("misaki", "0.9.4")]
            self.assertEqual(misaki["source_type"], "git")
            self.assertEqual(misaki["git_rev"], "abc123")
            self.assertEqual(misaki["integrity"], "unknown")
            self.assertEqual(misaki["license"], "unknown")
            self.assertEqual(misaki["declared_license"], "Apache-2.0")
            self.assertNotEqual(misaki["license"], "MIT")
            self.assertEqual(locks[("addict", "2.4.0")]["relation"], "transitive")
            self.assertEqual(locks[("num2words", "0.5.14")]["relation"], "transitive")
            self.assertEqual(locks[("num2words", "0.5.14")]["install_class"], "runtime")
            self.assertEqual(locks[("num2words", "0.5.14")]["integrity"], "unknown")
            sound = locks[("soundfile", "0.14.0")]
            self.assertEqual(sound["relation"], "direct")
            self.assertEqual(sound["install_class"], "unknown")
            self.assertIn("optional", sound["possible_install_classes"])
            self.assertEqual(sound["scope_certainty"], "marker_dependent")
            self.assertEqual(sound["lock_extras"], ["voice"])
            self.assertEqual(locks[("pytest", "9.1.1")]["relation"], "direct")
            self.assertEqual(locks[("pytest", "9.1.1")]["install_class"], "development")
            self.assertEqual(locks[("pluggy", "1.6.0")]["relation"], "transitive")
            self.assertEqual(locks[("pluggy", "1.6.0")]["install_class"], "development")
            kitten = locks[("kittentts", "0.8.1")]
            self.assertEqual(kitten["relation"], "unknown")
            self.assertEqual(kitten["source_type"], "url")
            self.assertEqual(kitten["integrity"], ["sha256:eee"])
            self.assertEqual(kitten["artifacts"][0]["url"], "https://example.invalid/kittentts.whl")
            self.assertEqual(kitten["license"], "unknown")
            self.assertEqual(locks[("orphan", "1.0.0")]["relation"], "unknown")
            self.assertEqual(locks[("orphan", "1.0.0")]["install_class"], "unknown")
            bare = locks[("bare", "unknown")]
            self.assertEqual(bare["source"], "unknown")
            self.assertEqual(bare["source_type"], "unknown")
            self.assertEqual(bare["integrity"], "unknown")
            self.assertEqual(bare["license"], "unknown")
            self.assertEqual(bare["git_rev"], "unknown")
            self.assertTrue(all(row["license"] == "unknown" and row["packaged"] == "unknown" for row in doc["dependencies"]))

    def test_npm_optional_dev_git_url_file_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = _write(
                Path(tmp) / "package-lock.json",
                json.dumps(
                    {
                        "name": "demo",
                        "lockfileVersion": 3,
                        "packages": {
                            "": {
                                "name": "demo",
                                "dependencies": {"leftpad": "1.0.0"},
                                "devDependencies": {"vite": "1.0.0"},
                            },
                            "node_modules/leftpad": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/leftpad/-/leftpad-1.0.0.tgz",
                                "integrity": "sha512-left",
                            },
                            "node_modules/vite": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/vite/-/vite-1.0.0.tgz",
                                "integrity": "sha512-vite",
                                "dev": True,
                            },
                            "node_modules/loose": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/loose/-/loose-1.0.0.tgz",
                                "integrity": "sha512-loose",
                            },
                            "node_modules/opt-only": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/opt-only/-/opt-only-1.0.0.tgz",
                                "integrity": "sha512-opt",
                                "optional": True,
                            },
                            "node_modules/@esbuild/aix-ppc64": {
                                "version": "0.28.2",
                                "resolved": "https://registry.npmjs.org/@esbuild/aix-ppc64/-/aix-ppc64-0.28.2.tgz",
                                "dev": True,
                                "optional": True,
                            },
                            "node_modules/from-git": {
                                "version": "1.2.3",
                                "resolved": "git+https://github.com/example/from-git.git#abc123",
                                "license": "ISC",
                            },
                            "node_modules/from-url": {
                                "version": "4.0.0",
                                "resolved": "https://example.invalid/from-url-4.0.0.tgz",
                                "integrity": "sha512-url",
                            },
                            "node_modules/local-file": {
                                "version": "0.0.1",
                                "resolved": "file:../local-file",
                            },
                            "node_modules/no-integrity": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/no-integrity/-/no-integrity-1.0.0.tgz",
                            },
                        },
                    }
                ),
            )
            doc = generate.build_inventory([lock])
            rows = {row["lock_path"]: row for row in doc["dependencies"] if row["record_kind"] == "lock"}
            self.assertEqual(rows["node_modules/leftpad"]["relation"], "direct")
            self.assertEqual(rows["node_modules/leftpad"]["install_class"], "runtime")
            self.assertEqual(rows["node_modules/leftpad"]["source_type"], "registry")
            self.assertEqual(rows["node_modules/vite"]["relation"], "direct")
            self.assertEqual(rows["node_modules/vite"]["install_class"], "development")
            self.assertEqual(rows["node_modules/vite"]["lock_dev"], True)
            self.assertEqual(rows["node_modules/loose"]["relation"], "transitive")
            self.assertEqual(rows["node_modules/loose"]["install_class"], "unknown")
            optional = rows["node_modules/opt-only"]
            self.assertEqual(optional["install_class"], "optional")
            self.assertEqual(optional["lock_optional"], True)
            self.assertEqual(optional["scope_certainty"], "lock_flag")
            self.assertEqual(optional["declared_license"], "unknown")
            self.assertEqual(optional["license"], "unknown")
            both = rows["node_modules/@esbuild/aix-ppc64"]
            self.assertEqual(both["install_class"], "mixed")
            self.assertEqual(both["possible_install_classes"], ["development", "optional"])
            self.assertEqual(both["integrity"], "unknown")
            self.assertEqual(both["license"], "unknown")
            self.assertEqual(both["relation"], "transitive")
            git = rows["node_modules/from-git"]
            self.assertEqual(git["source_type"], "git")
            self.assertNotEqual(git["relation"], "first_party")
            self.assertEqual(git["git_rev"], "abc123")
            self.assertEqual(git["integrity"], "unknown")
            self.assertEqual(git["version"], "1.2.3")
            self.assertEqual(git["declared_license"], "ISC")
            self.assertEqual(git["license"], "unknown")
            url = rows["node_modules/from-url"]
            self.assertEqual(url["source_type"], "url")
            self.assertEqual(url["integrity"], "sha512-url")
            self.assertEqual(url["license"], "unknown")
            local = rows["node_modules/local-file"]
            self.assertEqual(local["source_type"], "file")
            self.assertNotEqual(local["relation"], "first_party")
            self.assertEqual(local["integrity"], "unknown")
            missing = rows["node_modules/no-integrity"]
            self.assertEqual(missing["integrity"], "unknown")
            self.assertEqual(missing["source_type"], "registry")
            self.assertEqual(missing["license"], "unknown")
            self.assertNotEqual(missing["license"], "MIT")

    def test_npm_locks_do_not_share_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "package.json", json.dumps({"name": "root", "dependencies": {"leftpad": "1.0.0"}}))
            _write(
                root / "package-lock.json",
                json.dumps(
                    {
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "root"},
                            "node_modules/leftpad": {
                                "version": "1.0.0",
                                "resolved": "https://registry.npmjs.org/leftpad/-/leftpad-1.0.0.tgz",
                                "integrity": "sha512-left",
                            },
                            "node_modules/react": {
                                "version": "19.0.0",
                                "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz",
                                "integrity": "sha512-react",
                            },
                        },
                    }
                ),
            )
            panel = root / "apps" / "panel"
            _write(panel / "package.json", json.dumps({"name": "hermes-panel", "dependencies": {"react": "^19"}}))
            _write(
                panel / "package-lock.json",
                json.dumps(
                    {
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "hermes-panel"},
                            "node_modules/react": {
                                "version": "19.3.1",
                                "resolved": "https://registry.npmjs.org/react/-/react-19.3.1.tgz",
                                "integrity": "sha512-panel",
                            },
                        },
                    }
                ),
            )
            doc = generate.build_inventory(
                [
                    root / "package.json",
                    root / "package-lock.json",
                    panel / "package.json",
                    panel / "package-lock.json",
                ]
            )
            reacts = [row for row in doc["dependencies"] if row["record_kind"] == "lock" and row["name"] == "react"]
            by_parent = {Path(row["lock_file"]).parent.name: row for row in reacts}
            self.assertEqual(by_parent[root.name]["relation"], "transitive")
            self.assertEqual(by_parent["panel"]["relation"], "direct")
            self.assertEqual(by_parent["panel"]["install_class"], "runtime")
            self.assertTrue(all(row["license"] == "unknown" and row["packaged"] == "unknown" for row in reacts))

    def test_offline_input_does_not_scan_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _write(Path(tmp) / "package.json", '{"name":"demo"}')
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = generate.main(["--offline", "--input", str(manifest)])
            self.assertEqual(code, 0)
            doc = json.loads(captured.getvalue())
            self.assertFalse(doc["network_audit"])
            self.assertTrue(doc["offline"])
            self.assertEqual(doc["packaged_membership"], "unknown")
            self.assertEqual(doc["compliance_conclusion"], "not_determined")
            self.assertNotIn("prompt-toolkit", captured.getvalue())
            self.assertNotIn("node_modules", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
