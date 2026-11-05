"""Package-stage call contract. Not executed by the authoring turn.

Injected hooks stand in for ``clawd_server.packaging_backend``. These tests
must not import that backend or build a wheel.
"""

from __future__ import annotations

import io
import inspect
import os
import stat
import sys
import tempfile
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "clawd_acceptance"))

import stages
import execute as acceptance_execute
from isolation import Layout


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "clawd_server" / "packaging_backend.py"
RESOURCES = REPO / "clawd_server" / "resources.py"
SETUP = REPO / "setup.py"
EXECUTE = REPO / "scripts" / "clawd_acceptance" / "execute.py"


class PackageStageExecutionTests(unittest.TestCase):
    def test_catalog_command_calls_both_hooks_and_embeds_no_archive_path(self) -> None:
        layout = Layout(Path("/tmp/clawd-accept-plan"))
        package = _package_stage(layout)
        argv = package["argv"]
        script = argv[2]
        self.assertEqual(argv[0], str(REPO / ".venv" / "bin" / "python"))
        self.assertEqual(argv[1], "-c")
        self.assertLess(script.index("HERMES_NIX_BUILD"), script.index("from stages import run_package_build"))
        self.assertNotIn("setuptools.build_meta", script)
        self.assertNotIn(".whl", script)
        self.assertNotIn(".tar.gz", script)
        self.assertNotIn("CLAWD_DISTRIBUTION_WHEEL=", script)
        self.assertNotIn("shutil", script)
        self.assertNotIn("unlink", script)
        self.assertNotIn("rmtree", script)
        self.assertEqual(argv[5], str(layout.wheel))
        self.assertIsNone(package["distribution_env"])
        self.assertEqual(
            package["distribution_env_keys"],
            ["CLAWD_DISTRIBUTION_WHEEL", "CLAWD_DISTRIBUTION_SDIST"],
        )
        ids = [item["id"] for item in stages.stage_catalog(REPO, layout)]
        self.assertEqual(ids.count("package"), 1)

    def test_real_hook_signatures_and_executor_handoff(self) -> None:
        backend = BACKEND.read_text(encoding="utf-8")
        self.assertIn(
            "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):",
            backend,
        )
        self.assertIn("def build_sdist(sdist_directory, config_settings=None):", backend)
        resources = RESOURCES.read_text(encoding="utf-8")
        self.assertIn('".stage-"', resources)
        self.assertIn('".rejected-"', resources)
        self.assertIn(".stage.lock", resources)
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn('os.environ.get("HERMES_NIX_BUILD") == "1"', setup)
        executor_text = EXECUTE.read_text(encoding="utf-8")
        self.assertNotIn("from setuptools.build_meta import build_wheel", executor_text)
        self.assertIn("setuptools.build_meta", executor_text)
        self.assertIn("拒绝私有 setuptools.build_meta 包命令", executor_text)
        self.assertNotIn("run_package_build", executor_text)
        self.assertNotIn("CLAWD_DISTRIBUTION_WHEEL", executor_text)
        self.assertNotIn("CLAWD_DISTRIBUTION_SDIST", executor_text)
        self.assertIn('stage.get("argv")', executor_text)
        self.assertIn("run_package(repo, layout, prefix, stage)", executor_text)
        self.assertIn("compare_distribution_manifest", executor_text)
        self.assertIn("_trusted_archive_handoff", executor_text)
        package_body = inspect.getsource(acceptance_execute.run_package)
        self.assertIn("_package_stage_argv", package_body)
        self.assertIn("package_child_env", package_body)
        self.assertNotIn(".glob(", package_body)
        self.assertNotIn("rglob", package_body)
        manifest_body = inspect.getsource(acceptance_execute.compare_distribution_manifest)
        self.assertIn("env.pop(DISTRIBUTION_WHEEL_ENV, None)", manifest_body)
        self.assertIn("env.pop(DISTRIBUTION_SDIST_ENV, None)", manifest_body)
        self.assertIn('env[DISTRIBUTION_WHEEL_ENV] = handoff["env"][DISTRIBUTION_WHEEL_ENV]', manifest_body)
        self.assertIn('env[DISTRIBUTION_SDIST_ENV] = handoff["env"][DISTRIBUTION_SDIST_ENV]', manifest_body)
        self.assertIn("MANIFEST_PYTEST_REL", manifest_body)
        self.assertNotIn("run_tests.sh", manifest_body)
        self.assertNotIn("os.environ[", manifest_body)
        note = _package_stage(Layout(Path("/tmp/clawd-accept-plan")))["note"]
        self.assertNotIn("does not read this argv", note)
        self.assertNotIn("does not export CLAWD_DISTRIBUTION_WHEEL", note)
        self.assertIn("execute.run_package reads this argv", note)
        self.assertIn("independent venv pytest", note)
        loader = inspect.getsource(stages._load_builders)
        body = inspect.getsource(stages.run_package_build)
        self.assertIn("build_wheel", loader)
        self.assertIn("build_sdist", loader)
        self.assertNotIn("resource_root", loader)
        self.assertIn("build_wheel(str(outdir))", body)
        self.assertIn("build_sdist(str(outdir))", body)
        self.assertNotIn("unlink", body)
        self.assertNotIn("rmtree", body)

    def test_child_env_strips_stale_distribution_paths(self) -> None:
        env = stages.package_child_env(
            {
                "PATH": "/usr/bin",
                "HERMES_NIX_BUILD": "0",
                "CLAWD_DISTRIBUTION_WHEEL": "/tmp/old.whl",
                "CLAWD_DISTRIBUTION_SDIST": "/tmp/old.tar.gz",
            }
        )
        self.assertEqual(env["HERMES_NIX_BUILD"], "1")
        self.assertNotIn("CLAWD_DISTRIBUTION_WHEEL", env)
        self.assertNotIn("CLAWD_DISTRIBUTION_SDIST", env)

    def test_injected_hooks_are_called_and_do_not_make_contract_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            isolation, checkout, kept = _layout(Path(raw))
            calls: list[tuple[str, str]] = []

            def build_wheel(directory: str) -> str:
                calls.append(("wheel", directory))
                path = Path(directory) / "clawd-0.0.0-py3-none-any.whl"
                path.write_bytes(b"wheel-bytes")
                return path.name

            def build_sdist(directory: str) -> str:
                calls.append(("sdist", directory))
                path = Path(directory) / "clawd-0.0.0.tar.gz"
                path.write_bytes(b"sdist-bytes")
                return path.name

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=build_sdist,
                stdout=out,
            )
            self.assertEqual(code, 0)
            self.assertEqual([item[0] for item in calls], ["wheel", "sdist"])
            self.assertEqual(calls[0][1], calls[1][1])
            outdir = Path(calls[0][1])
            self.assertTrue(outdir.name.startswith("accept-build-"))
            self.assertNotIn(".stage-", outdir.name)
            self.assertNotIn(".rejected-", outdir.name)
            self.assertNotIn(".partial", outdir.name)
            self.assertEqual(stat.S_IMODE(outdir.stat().st_mode), 0o700)
            text = out.getvalue()
            self.assertIn("CLAWD_ACCEPTANCE_DISTRIBUTION ", text)
            handoff = stages.parse_distribution_marker(text, isolation)
            self.assertTrue(handoff["may_start_manifest_test"])
            self.assertFalse(handoff["contract_compared"])
            self.assertNotEqual(handoff["status"], "pass")
            self.assertEqual(
                set(handoff["env"]),
                {"CLAWD_DISTRIBUTION_WHEEL", "CLAWD_DISTRIBUTION_SDIST"},
            )
            checks = stages.package_build_checks(code, text, isolation)
            self.assertEqual(stages.package_result_exit(checks), 2)
            self.assertEqual(_status(checks, "package-build-wheel"), "pass")
            self.assertEqual(_status(checks, "package-build-sdist"), "pass")
            self.assertEqual(_status(checks, "package-distribution-env"), "pass")
            self.assertEqual(_status(checks, "package-manifest-compared"), "not_run")
            compared = [
                {**row, "status": "pass"} if row["id"] == "package-manifest-compared" else row
                for row in checks
            ]
            self.assertEqual(stages.package_result_exit(compared), 0)
            self.assertEqual((isolation / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertTrue((isolation / "bundled.partial").is_file())
            self.assertTrue((isolation / ".bundled.stage.lock").is_file())
            self.assertEqual(kept, ["keep"])

    def test_wheel_failure_does_not_call_sdist_or_export(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            isolation, checkout, _kept = _layout(Path(raw))
            calls: list[str] = []

            def build_wheel(directory: str) -> str:
                calls.append("wheel")
                raise RuntimeError("stage refused")

            def build_sdist(directory: str) -> str:
                calls.append("sdist")
                return "clawd-0.0.0.tar.gz"

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=build_sdist,
                stdout=out,
            )
            self.assertEqual(code, 1)
            self.assertEqual(calls, ["wheel"])
            self.assertNotIn("CLAWD_ACCEPTANCE_DISTRIBUTION", out.getvalue())
            checks = stages.package_build_checks(code, out.getvalue(), isolation)
            self.assertEqual(stages.package_result_exit(checks), 1)
            self.assertEqual(_status(checks, "package-build-wheel"), "fail")
            self.assertEqual(_status(checks, "package-build-sdist"), "not_run")
            self.assertNotEqual(_status(checks, "package-distribution-env"), "pass")
            self.assertEqual((isolation / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_sdist_failure_keeps_wheel_and_does_not_export_one_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            isolation, checkout, _kept = _layout(Path(raw))

            def build_wheel(directory: str) -> str:
                path = Path(directory) / "clawd-0.0.0-py3-none-any.whl"
                path.write_bytes(b"wheel-bytes")
                return path.name

            def build_sdist(directory: str) -> str:
                raise RuntimeError("sdist guard")

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=build_sdist,
                stdout=out,
            )
            self.assertEqual(code, 1)
            self.assertNotIn("CLAWD_ACCEPTANCE_DISTRIBUTION", out.getvalue())
            wheels = list(isolation.rglob("*.whl"))
            self.assertEqual(len(wheels), 1)
            checks = stages.package_build_checks(code, out.getvalue(), isolation)
            self.assertEqual(stages.package_result_exit(checks), 1)
            self.assertEqual(_status(checks, "package-build-sdist"), "fail")
            self.assertEqual(_status(checks, "package-distribution-env"), "blocked")

    def test_missing_files_historical_names_and_symlinks_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            isolation, checkout, _kept = _layout(Path(raw))

            def missing_wheel(directory: str) -> str:
                return "clawd-0.0.0-py3-none-any.whl"

            def missing_sdist(directory: str) -> str:
                return "clawd-0.0.0.tar.gz"

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=missing_wheel,
                build_sdist=missing_sdist,
                stdout=out,
            )
            self.assertEqual(code, 2)
            self.assertNotIn("CLAWD_ACCEPTANCE_DISTRIBUTION", out.getvalue())
            self.assertEqual(stages.package_result_exit(stages.package_build_checks(code, out.getvalue(), isolation)), 2)

            def historical_wheel(directory: str) -> str:
                path = Path(directory) / "hermes_agent-0.0.0-py3-none-any.whl"
                path.write_bytes(b"old")
                return path.name

            def historical_sdist(directory: str) -> str:
                path = Path(directory) / "clawd-0.0.0.tar.gz"
                path.write_bytes(b"sdist")
                return path.name

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=historical_wheel,
                build_sdist=historical_sdist,
                stdout=out,
            )
            self.assertEqual(code, 2)
            self.assertTrue(list(isolation.rglob("hermes_agent-0.0.0-py3-none-any.whl")))

            def link_wheel(directory: str) -> str:
                target = Path(directory) / "clawd-0.0.0-py3-none-any.whl"
                target.write_bytes(b"wheel")
                link = Path(directory) / "clawd-link.whl"
                link.symlink_to(target.name)
                return link.name

            def link_sdist(directory: str) -> str:
                path = Path(directory) / "clawd-0.0.0.tar.gz"
                path.write_bytes(b"sdist")
                return path.name

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=link_wheel,
                build_sdist=link_sdist,
                stdout=out,
            )
            self.assertEqual(code, 2)
            links = [path for path in isolation.rglob("clawd-link.whl") if path.is_symlink()]
            self.assertEqual(len(links), 1)

    def test_bad_hook_return_and_outside_marker_do_not_export(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            isolation, checkout, _kept = _layout(Path(raw))

            def slash_wheel(directory: str) -> str:
                return "../clawd-0.0.0-py3-none-any.whl"

            def slash_sdist(directory: str) -> str:
                return "clawd-0.0.0.tar.gz"

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=slash_wheel,
                build_sdist=slash_sdist,
                stdout=out,
            )
            self.assertEqual(code, 2)
            outside = isolation.parent / "clawd-0.0.0-py3-none-any.whl"
            outside.write_bytes(b"nope")
            marker = (
                "CLAWD_ACCEPTANCE_DISTRIBUTION "
                + '{"wheel": "'
                + str(outside)
                + '", "sdist": "'
                + str(outside)
                + '"}'
            )
            parsed = stages.parse_distribution_marker(marker, isolation)
            self.assertFalse(parsed["may_start_manifest_test"])
            self.assertIsNone(parsed["env"])
            self.assertNotEqual(parsed["status"], "pass")
            doubled = marker + "\n" + marker
            ambiguous = stages.parse_distribution_marker(doubled, isolation)
            self.assertFalse(ambiguous["may_start_manifest_test"])
            self.assertIsNone(ambiguous["env"])

    def test_missing_dependency_and_bad_isolation_do_not_call_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            isolation, checkout, _kept = _layout(root)
            calls: list[str] = []

            def build_wheel(directory: str) -> str:
                calls.append("wheel")
                return "clawd-0.0.0-py3-none-any.whl"

            out = io.StringIO()
            code = stages.run_package_build(
                str(isolation),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=None,
                stdout=out,
            )
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

            missing = root / "missing-isolation"
            code = stages.run_package_build(
                str(missing),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=build_wheel,
                stdout=out,
            )
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

            link = root / "linked-isolation"
            link.symlink_to(isolation, target_is_directory=True)
            code = stages.run_package_build(
                str(link),
                str(checkout),
                build_wheel=build_wheel,
                build_sdist=build_wheel,
                stdout=out,
            )
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

            with unittest.mock.patch.object(stages, "forbidden_path", return_value=True):
                code = stages.run_package_build(
                    str(isolation),
                    str(checkout),
                    build_wheel=build_wheel,
                    build_sdist=build_wheel,
                    stdout=out,
                )
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

            previous = os.environ.get("HERMES_NIX_BUILD")
            os.environ.pop("HERMES_NIX_BUILD", None)
            try:
                with unittest.mock.patch.object(stages, "_load_builders", side_effect=AssertionError("imported")):
                    code = stages.run_package_build(str(isolation), str(checkout), stdout=out)
            finally:
                if previous is None:
                    os.environ.pop("HERMES_NIX_BUILD", None)
                else:
                    os.environ["HERMES_NIX_BUILD"] = previous
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])

    def test_orchestration_exit_codes_do_not_promote_gaps(self) -> None:
        self.assertEqual(stages.package_result_exit([]), 2)
        self.assertEqual(stages.package_result_exit(None), 2)
        self.assertEqual(
            stages.package_result_exit([{"id": "only", "status": "pass", "required": False}]),
            2,
        )
        self.assertEqual(
            stages.package_result_exit(
                [
                    {"id": "build", "status": "fail", "required": True},
                    {"id": "env", "status": "blocked", "required": True},
                ]
            ),
            1,
        )
        for status in ("blocked", "unknown", "not_run"):
            self.assertEqual(
                stages.package_result_exit([{"id": status, "status": status, "required": True}]),
                2,
            )
            self.assertNotEqual(
                stages.package_result_exit([{"id": status, "status": status, "required": True}]),
                0,
            )
        both_missing = stages.classify_distribution_handoff(None, None, Path("/tmp/unused"))
        one_missing = stages.classify_distribution_handoff("/tmp/a.whl", None, Path("/tmp/unused"))
        self.assertEqual(both_missing["exit_code"], 2)
        self.assertFalse(both_missing["may_start_manifest_test"])
        self.assertNotEqual(both_missing["status"], "pass")
        self.assertEqual(one_missing["exit_code"], 2)
        self.assertNotEqual(one_missing["status"], "fail")
        self.assertIsNone(one_missing["env"])
        with tempfile.TemporaryDirectory() as raw:
            isolation = Path(raw)
            timed_out = stages.package_build_checks(1, "build_wheel failed", isolation, timed_out=True)
            self.assertEqual(stages.package_result_exit(timed_out), 2)
            self.assertTrue(all(row["status"] != "fail" for row in timed_out))
            not_run = stages.package_build_checks(None, "", isolation)
            self.assertEqual(stages.package_result_exit(not_run), 2)
            unknown = stages.package_build_checks(7, "", isolation)
            self.assertEqual(_status(unknown, "package-build-wheel"), "unknown")
            self.assertEqual(stages.package_result_exit(unknown), 2)
            empty_output = stages.package_build_checks(0, "", isolation)
            self.assertEqual(_status(empty_output, "package-distribution-env"), "blocked")
            self.assertEqual(stages.package_result_exit(empty_output), 2)


def _package_stage(layout: Layout) -> dict:
    return next(item for item in stages.stage_catalog(REPO, layout) if item["id"] == "package")


def _layout(root: Path) -> tuple[Path, Path, list[str]]:
    checkout = root / "checkout"
    checkout.mkdir()
    isolation = root / "wheel"
    isolation.mkdir()
    (isolation / "keep.txt").write_text("keep", encoding="utf-8")
    (isolation / "bundled.partial").write_text("partial", encoding="utf-8")
    (isolation / ".bundled.stage.lock").write_text("lock", encoding="utf-8")
    return isolation, checkout, ["keep"]


def _status(checks: list[dict], cid: str) -> str:
    return next(row["status"] for row in checks if row["id"] == cid)


if __name__ == "__main__":
    unittest.main()
