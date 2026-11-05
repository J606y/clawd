"""Executor handoff from a fake package child to an isolated manifest pytest.

Not executed by the authoring turn. Spawn is replaced. These tests must not
import ``clawd_server.packaging_backend`` or build a wheel.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "clawd_acceptance"))

import execute
import stages
from classify import aggregate_exit
from isolation import Layout


REPO = Path(__file__).resolve().parents[2]


def _marker(wheel: Path, sdist: Path) -> str:
    payload = json.dumps({"wheel": str(wheel), "sdist": str(sdist)}, ensure_ascii=False)
    return f"{stages.DISTRIBUTION_MARKER} {payload}\n"


def _archives(isolation: Path, name: str) -> tuple[Path, Path]:
    out = isolation / name
    out.mkdir(mode=0o700)
    wheel = out / "clawd-0.0.0-py3-none-any.whl"
    sdist = out / "clawd-0.0.0.tar.gz"
    wheel.write_bytes(b"not-a-zip")
    sdist.write_bytes(b"sdist")
    return wheel, sdist


def _python(repo: Path) -> Path:
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    return python


def _stage(python: Path, repo: Path, layout: Layout) -> dict:
    return {
        "id": "package",
        "timeout_seconds": 600,
        "argv": stages.package_argv(python, repo, layout.wheel),
        "cwd": str(repo),
        "overlap_group": "package",
    }


def _status(checks: list[dict], cid: str) -> str | None:
    found = [item.get("status") for item in checks if item.get("id") == cid]
    if not found:
        return None
    return found[-1]


def _detail(checks: list[dict], cid: str) -> str:
    found = [item.get("detail") for item in checks if item.get("id") == cid]
    if not found:
        return ""
    return str(found[-1])


class ExecutePackageDistributionTests(unittest.TestCase):
    def test_stage_argv_is_used_and_parent_paths_are_not_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            python = _python(repo)
            wheel, sdist = _archives(layout.wheel, "accept-build-new")
            old_wheel = layout.wheel / "hermes_agent-0.0.0-py3-none-any.whl"
            old_sdist = layout.wheel / "hermes_agent-0.0.0.tar.gz"
            old_wheel.write_bytes(b"old-wheel")
            old_sdist.write_bytes(b"old-sdist")
            (layout.wheel / "clawd-old-py3-none-any.whl").write_bytes(b"old-clawd")
            stage = _stage(python, repo, layout)
            captured: list[dict] = []

            def fake_spawn(argv, env, cwd, log_path, pid_path, timeout):
                captured.append({"argv": argv, "env": dict(env), "cwd": cwd, "timeout": timeout})
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("ab") as handle:
                    handle.write(_marker(wheel, sdist).encode())
                return {"exit_code": 0, "timed_out": False, "pid": 7}

            previous = {
                stages.DISTRIBUTION_WHEEL_ENV: str(old_wheel),
                stages.DISTRIBUTION_SDIST_ENV: str(old_sdist),
            }
            os.environ.update(previous)
            try:
                with unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn):
                    result = execute.run_package(repo, layout, None, stage)
            finally:
                for key in previous:
                    os.environ.pop(key, None)
            self.assertEqual(captured[0]["argv"], stage["argv"])
            script = captured[0]["argv"][2]
            self.assertLess(script.index("HERMES_NIX_BUILD"), script.index("from stages import"))
            self.assertNotIn("setuptools.build_meta", script)
            self.assertEqual(captured[0]["env"]["HERMES_NIX_BUILD"], "1")
            self.assertNotIn(stages.DISTRIBUTION_WHEEL_ENV, captured[0]["env"])
            self.assertNotIn(stages.DISTRIBUTION_SDIST_ENV, captured[0]["env"])
            self.assertEqual(result["distribution_env"][stages.DISTRIBUTION_WHEEL_ENV], str(wheel))
            self.assertEqual(result["distribution_env"][stages.DISTRIBUTION_SDIST_ENV], str(sdist))
            self.assertNotEqual(result["distribution_env"][stages.DISTRIBUTION_WHEEL_ENV], str(old_wheel))
            self.assertEqual(_status(result["checks"], "package-manifest-compared"), "not_run")
            self.assertNotEqual(result["status"], "pass")
            self.assertEqual(result["exit_code"], 2)
            self.assertNotIn(".glob(", inspect_source(execute.run_package))

    def test_previous_log_marker_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            python = _python(repo)
            new_wheel, new_sdist = _archives(layout.wheel, "accept-build-new")
            old_wheel, old_sdist = _archives(layout.wheel, "accept-build-old")
            log_path = layout.logs / "package.log"
            log_path.write_text(_marker(old_wheel, old_sdist), encoding="utf-8")

            def fake_spawn(argv, env, cwd, log_path, pid_path, timeout):
                with log_path.open("ab") as handle:
                    handle.write(_marker(new_wheel, new_sdist).encode())
                return {"exit_code": 0, "timed_out": False, "pid": 8}

            with unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn):
                result = execute.run_package(repo, layout, None, _stage(python, repo, layout))
            self.assertEqual(result["distribution_env"][stages.DISTRIBUTION_WHEEL_ENV], str(new_wheel))
            self.assertNotIn(str(old_wheel), result["distribution_env"].values())

    def test_child_exit_1_is_fail_and_does_not_export(self) -> None:
        result, captured = _child(exit_code=1, text="build_wheel failed: RuntimeError: no\n")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["exit_code"], 1)
        self.assertIsNone(result["distribution_env"])
        self.assertEqual(_status(result["checks"], "package-build-wheel"), "fail")
        self.assertEqual(_status(result["checks"], "package-manifest-compared"), "not_run")
        self.assertEqual(len(captured), 1)

    def test_child_exit_2_and_timeout_are_blocked(self) -> None:
        blocked, _calls = _child(exit_code=2, text="isolation directory missing\n")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["exit_code"], 2)
        self.assertIsNone(blocked["distribution_env"])
        timed, _calls = _child(exit_code=124, text="", timed_out=True)
        self.assertEqual(timed["status"], "blocked")
        self.assertEqual(timed["exit_code"], 2)
        self.assertIsNone(timed["distribution_env"])
        self.assertIn("124", timed["detail"])
        self.assertNotEqual(timed["status"], "fail")

    def test_missing_marker_and_one_path_do_not_start_pytest(self) -> None:
        missing, _calls = _child(exit_code=0, text="build finished without a marker\n")
        self.assertIsNone(missing["distribution_env"])
        self.assertEqual(_status(missing["checks"], "package-distribution-env"), "blocked")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            _python(repo)
            wheel, _sdist = _archives(layout.wheel, "accept-build-one")
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                row = execute.compare_distribution_manifest(
                    repo,
                    layout,
                    {stages.DISTRIBUTION_WHEEL_ENV: str(wheel)},
                )
            self.assertIsNone(row)
            self.assertFalse(spawn.called)

    def test_historical_name_does_not_start_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            _python(repo)
            out = layout.wheel / "accept-build-old"
            out.mkdir()
            wheel = out / "hermes_agent-0.0.0-py3-none-any.whl"
            sdist = out / "hermes_agent-0.0.0.tar.gz"
            wheel.write_bytes(b"old")
            sdist.write_bytes(b"old")
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                row = execute.compare_distribution_manifest(
                    repo,
                    layout,
                    {
                        stages.DISTRIBUTION_WHEEL_ENV: str(wheel),
                        stages.DISTRIBUTION_SDIST_ENV: str(sdist),
                    },
                )
            self.assertEqual(row["status"], "blocked")
            self.assertFalse(spawn.called)

    def test_missing_pinned_python_and_foreign_argv_do_not_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                missing = execute.run_package(repo, layout, None, {"id": "package", "argv": ["/usr/bin/python3", "-c", "pass"]})
            self.assertEqual(missing["status"], "blocked")
            self.assertEqual(missing["exit_code"], 2)
            self.assertIsNone(missing["distribution_env"])
            self.assertFalse(spawn.called)
            python = _python(repo)
            foreign = {
                "id": "package",
                "argv": ["/usr/bin/python3", "-c", "pass"],
                "timeout_seconds": 600,
            }
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                refused = execute.run_package(repo, layout, None, foreign)
            self.assertEqual(refused["status"], "blocked")
            self.assertFalse(spawn.called)
            self.assertNotEqual(Path(foreign["argv"][0]), python)

    def test_setuptools_argv_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            python = _python(repo)
            stage = {
                "id": "package",
                "timeout_seconds": 600,
                "argv": [
                    str(python),
                    "-c",
                    "from setuptools.build_meta import build_wheel",
                ],
            }
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                result = execute.run_package(repo, layout, None, stage)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(spawn.called)
            self.assertIn("setuptools.build_meta", result["detail"])

    def test_dispatch_passes_stage(self) -> None:
        stage = {"id": "package", "argv": ["python"], "timeout_seconds": 600}
        layout = Layout(Path("/tmp/clawd-accept-dispatch"))
        with unittest.mock.patch.object(execute, "run_package", return_value={"id": "package"}) as run:
            execute._dispatch(stage, REPO, layout, {})
        self.assertIs(run.call_args.args[3], stage)
        self.assertIsNone(run.call_args.args[2])

    def test_run_all_starts_isolated_pytest_only_for_both_new_archives(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            python = _python(repo)
            wheel, sdist = _archives(layout.wheel, "accept-build-new")
            old = str(layout.wheel / "hermes_agent-0.0.0-py3-none-any.whl")
            marker = _marker(wheel, sdist)
            package_result = {
                "id": "package",
                "status": "blocked",
                "exit_code": 2,
                "log": str(layout.logs / "package.log"),
                "detail": "hooks returned",
                "checks": stages.package_build_checks(0, marker, layout.wheel) + [
                    execute.leaf("package-installed-panel", "blocked", "no isolated install prefix", "package"),
                    execute.leaf("package-wheel-members", "blocked", "marker file was not opened as zip", "package"),
                ],
                "distribution_env": {
                    stages.DISTRIBUTION_WHEEL_ENV: str(wheel),
                    stages.DISTRIBUTION_SDIST_ENV: str(sdist),
                },
            }
            captured: list[dict] = []

            def fake_spawn(argv, env, cwd, log_path, pid_path, timeout):
                captured.append({"argv": list(argv), "env": dict(env), "cwd": cwd, "timeout": timeout})
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("1 passed in 0.01s\n", encoding="utf-8")
                return {"exit_code": 0, "timed_out": False, "pid": 9}

            os.environ[stages.DISTRIBUTION_WHEEL_ENV] = old
            os.environ[stages.DISTRIBUTION_SDIST_ENV] = old
            try:
                code, checks, stages_out = _run_all(repo, layout, package_result, fake_spawn)
            finally:
                os.environ.pop(stages.DISTRIBUTION_WHEEL_ENV, None)
                os.environ.pop(stages.DISTRIBUTION_SDIST_ENV, None)
            self.assertEqual(len(captured), 1)
            self.assertEqual(
                captured[0]["argv"],
                [str(python), "-m", "pytest", "-q", execute.MANIFEST_PYTEST_REL],
            )
            self.assertNotIn("run_tests.sh", captured[0]["argv"])
            self.assertEqual(captured[0]["cwd"], str(repo))
            self.assertEqual(captured[0]["timeout"], 600)
            self.assertNotIn(35, {captured[0]["timeout"]})
            self.assertEqual(captured[0]["env"][stages.DISTRIBUTION_WHEEL_ENV], str(wheel))
            self.assertEqual(captured[0]["env"][stages.DISTRIBUTION_SDIST_ENV], str(sdist))
            self.assertNotEqual(captured[0]["env"][stages.DISTRIBUTION_WHEEL_ENV], old)
            self.assertEqual(_status(checks, "package-manifest-compared"), "pass")
            self.assertEqual(sum(item.get("id") == "package-manifest-compared" for item in checks), 1)
            self.assertNotEqual(code, 0)
            self.assertEqual(stages_out[0]["status"], "blocked")

    def test_pytest_fail_beats_blocked_and_empty_zero_is_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            _python(repo)
            wheel, sdist = _archives(layout.wheel, "accept-build-new")
            result = _package_result(layout, wheel, sdist)

            def fail_spawn(argv, env, cwd, log_path, pid_path, timeout):
                log_path.write_text("1 failed\n", encoding="utf-8")
                return {"exit_code": 1, "timed_out": False, "pid": 3}

            code, checks, _stages = _run_all(repo, layout, result, fail_spawn)
            self.assertEqual(_status(checks, "package-manifest-compared"), "fail")
            self.assertEqual(code, 1)

            def empty_spawn(argv, env, cwd, log_path, pid_path, timeout):
                log_path.write_text("", encoding="utf-8")
                return {"exit_code": 0, "timed_out": False, "pid": 4}

            code, checks, _stages = _run_all(repo, layout, result, empty_spawn)
            self.assertEqual(_status(checks, "package-manifest-compared"), "blocked")
            self.assertNotEqual(_status(checks, "package-manifest-compared"), "pass")
            self.assertEqual(code, 2)

            def timeout_spawn(argv, env, cwd, log_path, pid_path, timeout):
                return {"exit_code": 124, "timed_out": True, "pid": 5}

            code, checks, _stages = _run_all(repo, layout, result, timeout_spawn)
            self.assertEqual(_status(checks, "package-manifest-compared"), "blocked")
            self.assertEqual(code, 2)
            self.assertNotEqual(code, 1)

    def test_missing_distribution_env_does_not_spawn_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            result = {
                "id": "package",
                "status": "blocked",
                "exit_code": 2,
                "log": "",
                "detail": "no marker",
                "checks": stages.package_build_checks(2, "blocked\n", layout.wheel),
                "distribution_env": None,
            }
            with unittest.mock.patch.object(execute, "spawn") as spawn:
                code, checks, _stages = _run_all(repo, layout, result, spawn)
            self.assertFalse(spawn.called)
            self.assertEqual(_status(checks, "package-manifest-compared"), "not_run")
            self.assertEqual(code, 2)

    def test_license_exit_0_unknown_is_blocked_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "license-inventory.json"
            path.write_text(json.dumps(_inventory(), ensure_ascii=False), encoding="utf-8")
            classified = execute.classify_license_inventory(path)
            self.assertEqual(classified["status"], "blocked")
            self.assertEqual(classified["exit_code"], 2)
            self.assertNotEqual(classified["status"], "pass")
            self.assertEqual(classified["legal_review"], "blocked")
            self.assertEqual(classified["extractor_parse"], "pass")
            self.assertEqual(_status(classified["checks"], "license-inventory"), "pass")
            self.assertIn("不是法律合规通过", classified["detail"])
            self.assertNotIn("必须书面授权", classified["detail"])
            self.assertNotIn("四包书面授权", classified["detail"])
            self.assertEqual(_status(classified["checks"], "license-scope-unknown"), "blocked")
            self.assertEqual(_status(classified["checks"], "license-packaged-unknown"), "blocked")
            self.assertGreaterEqual(len(classified["unknown_scopes"]), 4)
            self.assertTrue(all(item["status"] == "blocked" for item in classified["unknown_scopes"]))
            self.assertIn("当前未知单独 blocked", _detail(classified["checks"], "license-scope-unknown"))
            self.assertIn("不外溢", classified["detail"])

            spilled = _inventory()
            spilled["components"][0]["license"] = "MIT"
            path.write_text(json.dumps(spilled, ensure_ascii=False), encoding="utf-8")
            recorded = execute.classify_license_inventory(path)
            self.assertEqual(recorded["status"], "blocked")
            self.assertEqual(recorded["exit_code"], 2)
            self.assertNotEqual(recorded["status"], "pass")
            self.assertNotEqual(recorded["status"], "fail")
            self.assertEqual(recorded["legal_review"], "blocked")
            self.assertEqual(_status(recorded["checks"], "license-scope-unknown"), "blocked")
            self.assertEqual(_status(recorded["checks"], "license-packaged-unknown"), "blocked")
            scope = _detail(recorded["checks"], "license-scope-unknown")
            self.assertIn("不是法律授权", scope)
            self.assertIn("不外溢", scope)
            self.assertNotIn("必须书面授权", scope)
            self.assertNotIn("四包书面授权", recorded["detail"])
            self.assertTrue(recorded["recorded_license_fields"])
            self.assertTrue(all(item["status"] == "blocked" for item in recorded["recorded_license_fields"]))

            filled = _inventory()
            for component in filled["components"]:
                if str(component["path"]).endswith("package.json"):
                    component["license"] = "MIT"
                    component["license_basis"] = "sibling_notice_file"
            path.write_text(json.dumps(filled, ensure_ascii=False), encoding="utf-8")
            filled_result = execute.classify_license_inventory(path)
            self.assertEqual(filled_result["status"], "blocked")
            self.assertNotEqual(filled_result["status"], "pass")
            self.assertNotEqual(filled_result["status"], "fail")
            self.assertEqual(filled_result["legal_review"], "blocked")
            self.assertEqual(filled_result["unknown_scopes"], [])
            self.assertEqual(len(filled_result["recorded_license_fields"]), 4)
            self.assertIn("不是法律授权", _detail(filled_result["checks"], "license-scope-unknown"))
            self.assertIn("不外溢", _detail(filled_result["checks"], "license-scope-unknown"))

            membership = _inventory(packaged_membership="included")
            path.write_text(json.dumps(membership, ensure_ascii=False), encoding="utf-8")
            membership_result = execute.classify_license_inventory(path)
            self.assertEqual(membership_result["status"], "blocked")
            self.assertNotEqual(membership_result["status"], "fail")
            self.assertNotEqual(membership_result["status"], "pass")
            self.assertEqual(_status(membership_result["checks"], "license-packaged-unknown"), "blocked")
            self.assertIn("不是入包证明", _detail(membership_result["checks"], "license-packaged-unknown"))

            deps = _inventory()
            deps["dependencies"] = [{"name": "example", "license": "MIT", "packaged": "yes"}]
            path.write_text(json.dumps(deps, ensure_ascii=False), encoding="utf-8")
            deps_result = execute.classify_license_inventory(path)
            self.assertEqual(deps_result["status"], "blocked")
            self.assertNotEqual(deps_result["status"], "fail")
            self.assertNotEqual(deps_result["status"], "pass")
            self.assertIn("不是裁定", deps_result["detail"])

            claimed = _inventory(packaged_membership="included", compliance_conclusion="compliant")
            path.write_text(json.dumps(claimed, ensure_ascii=False), encoding="utf-8")
            claimed_result = execute.classify_license_inventory(path)
            self.assertEqual(claimed_result["status"], "fail")
            self.assertEqual(claimed_result["exit_code"], 1)
            self.assertNotEqual(claimed_result["status"], "pass")
            self.assertEqual(claimed_result["legal_review"], "blocked")
            self.assertIn("compliance_conclusion", claimed_result["detail"])
            self.assertIn("不是法律审查通过", claimed_result["detail"])
            self.assertNotIn("必须书面授权", claimed_result["detail"])
            self.assertEqual(_status(claimed_result["checks"], "license-scope-unknown"), "blocked")
            self.assertGreaterEqual(len(claimed_result["unknown_scopes"]), 4)

            offline = _inventory(network_audit=True)
            path.write_text(json.dumps(offline, ensure_ascii=False), encoding="utf-8")
            offline_result = execute.classify_license_inventory(path)
            self.assertEqual(offline_result["status"], "fail")
            self.assertNotEqual(offline_result["status"], "pass")
            self.assertEqual(offline_result["legal_review"], "blocked")
            self.assertIn("network_audit", offline_result["detail"])

            missing_conclusion = _inventory()
            missing_conclusion.pop("compliance_conclusion")
            path.write_text(json.dumps(missing_conclusion, ensure_ascii=False), encoding="utf-8")
            missing_result = execute.classify_license_inventory(path)
            self.assertNotEqual(missing_result["status"], "pass")
            self.assertNotEqual(missing_result["extractor_parse"], "pass")
            self.assertEqual(missing_result["legal_review"], "blocked")

    def test_license_timeout_and_exit_0_without_json_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            layout = Layout(root / "accept")
            layout.ensure()
            _python(repo)

            def timeout_spawn(argv, env, cwd, log_path, pid_path, timeout):
                return {"exit_code": 124, "timed_out": True, "pid": 11}

            with (
                unittest.mock.patch.object(execute, "inventory_ready", return_value={"ok": True, "detail": "ok"}),
                unittest.mock.patch.object(execute, "license_signature_ok", return_value=(True, "ok")),
                unittest.mock.patch.object(execute, "spawn", side_effect=timeout_spawn),
            ):
                timed = execute.run_license(repo, layout)
            self.assertEqual(timed["status"], "blocked")
            self.assertEqual(timed["exit_code"], 2)
            self.assertNotEqual(timed["status"], "fail")

            def quiet_spawn(argv, env, cwd, log_path, pid_path, timeout):
                return {"exit_code": 0, "timed_out": False, "pid": 12}

            with (
                unittest.mock.patch.object(execute, "inventory_ready", return_value={"ok": True, "detail": "ok"}),
                unittest.mock.patch.object(execute, "license_signature_ok", return_value=(True, "ok")),
                unittest.mock.patch.object(execute, "spawn", side_effect=quiet_spawn),
            ):
                empty = execute.run_license(repo, layout)
            self.assertEqual(empty["status"], "blocked")
            self.assertNotEqual(empty["status"], "pass")
            self.assertEqual(_status(empty["checks"], "license-scope-unknown"), "blocked")
            self.assertEqual(_status(empty["checks"], "license-packaged-unknown"), "blocked")

    def test_source_does_not_call_the_build_backend_or_glob_wheels(self) -> None:
        text = (REPO / "scripts" / "clawd_acceptance" / "execute.py").read_text(encoding="utf-8")
        self.assertNotIn("from setuptools.build_meta import build_wheel", text)
        self.assertNotIn("run_package_build", text)
        self.assertNotIn("CLAWD_DISTRIBUTION_WHEEL", text)
        self.assertNotIn("CLAWD_DISTRIBUTION_SDIST", text)
        self.assertIn("run_package(repo, layout, prefix, stage)", text)
        self.assertIn("compare_distribution_manifest", text)
        self.assertNotIn("run_tests.sh", inspect_source(execute.run_all))
        self.assertNotIn(".glob(", inspect_source(execute.run_package))


def inspect_source(fn) -> str:
    import inspect

    return inspect.getsource(fn)


def _child(*, exit_code: int, text: str, timed_out: bool = False) -> tuple[dict, list]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        repo = root / "checkout"
        layout = Layout(root / "accept")
        layout.ensure()
        python = _python(repo)
        captured: list[dict] = []

        def fake_spawn(argv, env, cwd, log_path, pid_path, timeout):
            captured.append(argv)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as handle:
                handle.write(text.encode())
            return {"exit_code": exit_code, "timed_out": timed_out, "pid": 1}

        with unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn):
            result = execute.run_package(repo, layout, None, _stage(python, repo, layout))
        return result, captured


def _package_result(layout: Layout, wheel: Path, sdist: Path) -> dict:
    return {
        "id": "package",
        "status": "blocked",
        "exit_code": 2,
        "log": str(layout.logs / "package.log"),
        "detail": "hooks returned",
        "checks": stages.package_build_checks(0, _marker(wheel, sdist), layout.wheel) + [
            execute.leaf("package-installed-panel", "blocked", "no isolated install prefix", "package"),
        ],
        "distribution_env": {
            stages.DISTRIBUTION_WHEEL_ENV: str(wheel),
            stages.DISTRIBUTION_SDIST_ENV: str(sdist),
        },
    }


def _run_all(repo: Path, layout: Layout, package_result: dict, spawn) -> tuple[int, list[dict], list[dict]]:
    captured: dict = {}

    def fake_assemble(**kwargs):
        captured["checks"] = kwargs["checks"]
        captured["stages"] = kwargs["stages"]
        return {"summary": {"exit_code": aggregate_exit(kwargs["checks"])}, "checks": kwargs["checks"]}

    with (
        unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True}),
        unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": "package", "timeout_seconds": 600}]),
        unittest.mock.patch.object(execute, "execute_stage", return_value=package_result),
        unittest.mock.patch.object(execute, "spawn", side_effect=spawn),
        unittest.mock.patch.object(execute, "assemble", side_effect=fake_assemble),
        unittest.mock.patch.object(execute, "coverage_checks", return_value=[
            {
                "id": "core",
                "status": "not_run",
                "required": True,
                "counts_as_test": False,
                "overlap_group": "core-pytest",
                "detail": "not executed",
            }
        ]),
    ):
        code = execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
    return code, captured["checks"], captured["stages"]


def _inventory(**overrides: object) -> dict:
    components = []
    for suffix in execute._INTERNAL_LICENSE_SUFFIXES:
        components.append(
            {
                "path": f"/root/hermes-workspace/clawd/{suffix}",
                "license": "unknown",
                "manifest_license": "unknown",
            }
        )
    components.append(
        {
            "path": "/root/hermes-workspace/clawd/pyproject.toml",
            "license": "MIT",
            "license_basis": "sibling_notice_file",
            "manifest_license": "MIT",
        }
    )
    document = {
        "network_audit": False,
        "compliance_conclusion": "not_determined",
        "packaged_membership": "unknown",
        "errors": False,
        "components": components,
        "dependencies": [{"name": "example", "license": "unknown", "packaged": "unknown"}],
    }
    document.update(overrides)
    return document
