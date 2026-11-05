"""Public acceptance entry reports. Not executed by the authoring turn.

Calls prepare, run_all, report_only, and accept.main with temp fixtures and
mocked stage execution. Does not import the product, build a wheel, or start
a model. Do not point these fixtures at artifacts/final-acceptance.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "clawd_acceptance"))

import accept
import execute
import report_build
import stages
from isolation import Layout


REPO = Path(__file__).resolve().parents[2]
EXECUTE = REPO / "scripts" / "clawd_acceptance" / "execute.py"
OLD_WHEEL = b"old-wheel-not-this-run"
STALE_WHEEL = b"stale-clawd-wheel-not-named"
SCHEMA_KEYS = (
    "schema_version",
    "kind",
    "producer",
    "executed",
    "never_executed",
    "phase",
    "plan_only",
    "acceptance_green",
    "final_acceptance",
    "covers_cutover",
    "generated_at",
    "identity",
    "source_identity",
    "artifacts",
    "blockers",
    "gate",
    "isolation",
    "toolchain",
    "stages",
    "checks",
    "summary",
)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _check(cid: str, status: str, detail: str = "fixture") -> dict:
    return execute.leaf(cid, status, detail, cid)


def _old_green() -> dict:
    return {
        "schema_version": 1,
        "acceptance_green": True,
        "final_acceptance": True,
        "executed": True,
        "phase": "run",
        "summary": {"exit_code": 0, "status": "pass"},
    }


@contextmanager
def _world():
    with tempfile.TemporaryDirectory(prefix="clawd-entry-") as raw:
        root = Path(raw)
        repo = root / "checkout"
        repo.mkdir()
        layout = Layout(repo / "artifacts" / "entry-fixture")
        layout.wheel.mkdir(parents=True)
        old = layout.wheel / "hermes_agent-0.0.0-py3-none-any.whl"
        old.write_bytes(OLD_WHEEL)
        stale_dir = layout.wheel / "accept-build-stale"
        stale_dir.mkdir()
        stale = stale_dir / "clawd-0.0.0-py3-none-any.whl"
        stale.write_bytes(STALE_WHEEL)
        yield repo, layout, old, stale


def _plant_old_report(layout: Layout) -> dict:
    layout.root.mkdir(parents=True, exist_ok=True)
    payload = _old_green()
    (layout.root / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _plant_state(layout: Layout, sid: str, result: dict) -> None:
    layout.logs.mkdir(parents=True, exist_ok=True)
    execute.write_json(layout.logs / f"{sid}.state.json", {"state": result.get("status"), "pid": None, "result": result})


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema(test: unittest.TestCase, report: dict, *, phase: str, executed: bool) -> None:
    for key in SCHEMA_KEYS:
        test.assertIn(key, report)
    test.assertEqual(report["schema_version"], 1)
    test.assertEqual(report["kind"], "clawd-final-acceptance")
    test.assertEqual(report["producer"], "scripts/clawd_acceptance")
    test.assertEqual(report["phase"], phase)
    test.assertIs(report["executed"], executed)
    test.assertEqual(report["never_executed"], not executed)
    test.assertEqual(report["plan_only"], phase == "prepare" or not executed)
    test.assertIs(report["final_acceptance"], report["acceptance_green"])
    test.assertIn(report["summary"]["exit_code"], (0, 1, 2))
    test.assertEqual(
        report["summary"]["status"],
        {0: "pass", 1: "fail", 2: "blocked"}[report["summary"]["exit_code"]],
    )
    if not report["acceptance_green"]:
        test.assertNotEqual(report["summary"]["exit_code"], 0)
        test.assertFalse(report["covers_cutover"])
    blob = json.dumps(report)
    test.assertNotIn(_digest(OLD_WHEEL), blob)
    test.assertNotIn(_digest(STALE_WHEEL), blob)
    test.assertNotIn("hermes_agent-0.0.0-py3-none-any.whl", blob)


def _assert_handoff_unnamed(test: unittest.TestCase, wheel_dir: object) -> None:
    test.assertIsInstance(wheel_dir, execute.TrustedArchiveHandoff)
    test.assertNotIsInstance(wheel_dir, Path)
    test.assertIsNone(wheel_dir.wheel)
    test.assertIsNone(wheel_dir.sdist)


@contextmanager
def _spy_assemble():
    captured: list[dict] = []
    real = execute.assemble

    def spy(**kwargs):
        captured.append(dict(kwargs))
        return real(**kwargs)

    with unittest.mock.patch.object(execute, "assemble", side_effect=spy):
        yield captured


@contextmanager
def _forbid_stages():
    def boom(*_args, **_kwargs):
        raise AssertionError("stage execution is not allowed in this fixture")

    with (
        unittest.mock.patch.object(execute, "execute_stage", side_effect=boom),
        unittest.mock.patch.object(execute, "spawn", side_effect=boom),
    ):
        yield


@contextmanager
def _cli(repo: Path, layout: Layout):
    with (
        unittest.mock.patch.object(accept, "REPO", repo),
        unittest.mock.patch.object(accept, "Layout", lambda _root: layout),
        redirect_stdout(io.StringIO()),
    ):
        yield


class PublicEntryReportPathTests(unittest.TestCase):
    def test_prepare_writes_plan_without_executing_or_reading_old_artifacts(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            previous = _plant_old_report(layout)
            _plant_state(layout, "core", {"id": "core", "status": "pass", "exit_code": 0, "checks": [_check("core", "pass")]})
            with _forbid_stages(), _spy_assemble() as captured:
                report = execute.prepare(repo, layout)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["phase"], "prepare")
            self.assertIs(captured[0]["executed"], False)
            _assert_handoff_unnamed(self, captured[0]["wheel_dir"])
            _assert_schema(self, report, phase="prepare", executed=False)
            self.assertFalse(report["acceptance_green"])
            self.assertEqual(report["summary"]["exit_code"], 2)
            plan = _load(layout.root / "prepare-plan.json")
            written = _load(layout.root / "report.json")
            self.assertEqual(plan["acceptance_exit"], 2)
            self.assertEqual(plan["report"]["phase"], "prepare")
            self.assertIs(plan["report"]["executed"], False)
            self.assertFalse(plan["report"]["acceptance_green"])
            self.assertEqual(written["phase"], "prepare")
            self.assertNotEqual(written, previous)
            self.assertTrue((layout.logs / "core.state.json").is_file())

    def test_gate_rejected_run_exits_2_and_does_not_execute(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            previous = _plant_old_report(layout)
            _plant_state(layout, "core", {"id": "core", "status": "pass", "exit_code": 0, "checks": [_check("core", "pass")]})
            with _forbid_stages(), _spy_assemble() as captured:
                code = execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
            self.assertEqual(code, 2)
            self.assertEqual(captured[0]["phase"], "run")
            self.assertIs(captured[0]["executed"], False)
            _assert_handoff_unnamed(self, captured[0]["wheel_dir"])
            written = _load(layout.root / "report.json")
            _assert_schema(self, written, phase="run", executed=False)
            self.assertFalse(written["acceptance_green"])
            self.assertEqual(written["summary"]["exit_code"], 2)
            self.assertNotEqual(written, previous)
            self.assertFalse(written["gate"]["run_allowed"])

    def test_flag_refusal_exits_2_without_reading_or_replacing_old_report(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            previous = _plant_old_report(layout)
            with (
                unittest.mock.patch.object(execute, "assemble", side_effect=AssertionError("assemble")),
                unittest.mock.patch.object(execute, "gate_view", side_effect=AssertionError("gate")),
                unittest.mock.patch.object(execute, "execute_stage", side_effect=AssertionError("stage")),
            ):
                missing_run = execute.run_all(repo, layout, {"implementation_complete": True})
                missing_flag = execute.run_all(repo, layout, {"run": True})
                empty = execute.run_all(repo, layout, {})
            self.assertEqual((missing_run, missing_flag, empty), (2, 2, 2))
            self.assertEqual(_load(layout.root / "report.json"), previous)

    def test_report_only_replays_stored_status_and_ignores_unnamed_wheels(self) -> None:
        with _world() as (repo, layout, old, stale):
            layout.ensure()
            new_wheel = layout.wheel / "accept-build-new" / "clawd-0.0.0-py3-none-any.whl"
            new_sdist = layout.wheel / "accept-build-new" / "clawd-0.0.0.tar.gz"
            new_wheel.parent.mkdir()
            new_wheel.write_bytes(b"this-run-wheel")
            new_sdist.write_bytes(b"this-run-sdist")
            _plant_state(
                layout,
                "package",
                {
                    "id": "package",
                    "status": "blocked",
                    "exit_code": 2,
                    "log": "",
                    "detail": "stored package",
                    "checks": [_check("package-build-wheel", "blocked")],
                    "distribution_env": {
                        stages.DISTRIBUTION_WHEEL_ENV: str(new_wheel),
                        stages.DISTRIBUTION_SDIST_ENV: str(new_sdist),
                    },
                },
            )
            _plant_old_report(layout)
            with _forbid_stages(), _spy_assemble() as captured:
                code = execute.report_only(repo, layout)
            self.assertNotEqual(code, 0)
            self.assertEqual(captured[0]["phase"], "report")
            self.assertIs(captured[0]["executed"], True)
            self.assertEqual(captured[0]["wheel_dir"].wheel, str(new_wheel))
            self.assertEqual(captured[0]["wheel_dir"].sdist, str(new_sdist))
            self.assertNotIn(str(old), (captured[0]["wheel_dir"].wheel, captured[0]["wheel_dir"].sdist))
            self.assertNotIn(str(stale), (captured[0]["wheel_dir"].wheel, captured[0]["wheel_dir"].sdist))
            written = _load(layout.root / "report.json")
            _assert_schema(self, written, phase="report", executed=True)
            self.assertFalse(written["acceptance_green"])

    def test_report_only_does_not_turn_empty_fail_blocked_or_pass_into_green(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            layout.ensure()
            _plant_state(layout, "core", {"id": "core", "status": "fail", "exit_code": 1, "detail": "assertion", "checks": []})
            with _forbid_stages():
                failed = execute.report_only(repo, layout)
            failed_report = _load(layout.root / "report.json")
            self.assertEqual(failed, 1)
            self.assertTrue(any(item.get("status") == "fail" for item in failed_report["checks"]))
            self.assertFalse(failed_report["acceptance_green"])

            _plant_state(layout, "core", {"id": "core", "status": "blocked", "exit_code": 2, "detail": "waiting", "checks": []})
            with _forbid_stages():
                blocked = execute.report_only(repo, layout)
            self.assertEqual(blocked, 2)
            self.assertFalse(_load(layout.root / "report.json")["acceptance_green"])

            _plant_state(layout, "core", {"id": "core", "status": "pass", "exit_code": 0, "checks": []})
            with _forbid_stages():
                empty_pass = execute.report_only(repo, layout)
            empty_report = _load(layout.root / "report.json")
            self.assertEqual(empty_pass, 2)
            self.assertFalse(empty_report["acceptance_green"])
            self.assertFalse(any(item.get("status") == "pass" and item.get("required", True) for item in empty_report["checks"]))

            _plant_state(layout, "core", {"id": "core", "status": "pass", "exit_code": 0, "checks": [_check("core", "pass")]})
            with _forbid_stages():
                replayed_pass = execute.report_only(repo, layout)
            replayed = _load(layout.root / "report.json")
            self.assertEqual(replayed_pass, 2)
            self.assertEqual(replayed["phase"], "report")
            self.assertIs(replayed["executed"], True)
            self.assertFalse(replayed["acceptance_green"])

    def test_report_only_keeps_unreadable_state_and_refuses_symlinks(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            layout.ensure()
            (layout.logs / "core.state.json").write_text("{not-json", encoding="utf-8")
            with _forbid_stages():
                code = execute.report_only(repo, layout)
            report = _load(layout.root / "report.json")
            self.assertEqual(code, 2)
            self.assertFalse(report["acceptance_green"])
            self.assertIs(report["executed"], False)
            self.assertTrue(any(item.get("id") == "core" and item.get("status") == "blocked" for item in report["checks"]))

        with _world() as (repo, layout, _old, _stale):
            other = layout.root.parent / "other-logs"
            other.mkdir()
            execute.write_json(
                other / "core.state.json",
                {"state": "pass", "result": {"id": "core", "status": "pass", "exit_code": 0, "checks": [_check("core", "pass")]}},
            )
            layout.logs.symlink_to(other, target_is_directory=True)
            with _forbid_stages(), _spy_assemble() as captured:
                code = execute.report_only(repo, layout)
            self.assertEqual(code, 2)
            self.assertIs(captured[0]["executed"], False)
            self.assertTrue(any(item.get("id") == "stored-logs" for item in captured[0]["checks"]))
            _assert_handoff_unnamed(self, captured[0]["wheel_dir"])

        with tempfile.TemporaryDirectory(prefix="clawd-entry-link-") as raw:
            root = Path(raw)
            repo = root / "checkout"
            repo.mkdir()
            target = root / "old-accept"
            target.mkdir()
            previous = _old_green()
            (target / "report.json").write_text(json.dumps(previous), encoding="utf-8")
            (target / "logs").mkdir()
            execute.write_json(
                target / "logs" / "core.state.json",
                {"state": "pass", "result": {"id": "core", "status": "pass", "exit_code": 0, "checks": [_check("core", "pass")]}},
            )
            link = root / "linked-accept"
            link.symlink_to(target, target_is_directory=True)
            layout = Layout(link)
            with _forbid_stages():
                prepared = execute.prepare(repo, layout)
                rejected = execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
                replayed = execute.report_only(repo, layout)
            self.assertIs(prepared["executed"], False)
            self.assertEqual(prepared["phase"], "prepare")
            self.assertFalse(prepared["acceptance_green"])
            self.assertEqual(rejected, 2)
            self.assertNotEqual(replayed, 0)
            self.assertEqual(_load(target / "report.json"), previous)
            self.assertFalse((target / "prepare-plan.json").exists())

    def test_report_only_without_records_is_not_a_pass_and_skips_missing_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clawd-entry-missing-") as raw:
            root = Path(raw)
            repo = root / "checkout"
            repo.mkdir()
            layout = Layout(root / "missing-accept")
            with _forbid_stages(), _spy_assemble() as captured:
                code = execute.report_only(repo, layout)
            self.assertEqual(code, 2)
            self.assertIs(captured[0]["executed"], False)
            self.assertEqual(captured[0]["phase"], "report")
            _assert_handoff_unnamed(self, captured[0]["wheel_dir"])
            self.assertFalse(layout.root.exists())
            self.assertFalse((layout.root / "report.json").exists())
        with _world() as (repo, layout, _old, _stale):
            layout.ensure()
            with _forbid_stages():
                written_code = execute.report_only(repo, layout)
            written = _load(layout.root / "report.json")
            self.assertEqual(written_code, 2)
            _assert_schema(self, written, phase="report", executed=False)
            self.assertFalse(written["acceptance_green"])

    def test_full_run_close_keeps_fail_blocked_and_empty(self) -> None:
        with _world() as (repo, layout, old, _stale):
            new_wheel = layout.wheel / "accept-build-new" / "clawd-9-py3-none-any.whl"
            new_sdist = layout.wheel / "accept-build-new" / "clawd-9.tar.gz"
            new_wheel.parent.mkdir(parents=True)
            new_wheel.write_bytes(b"named-wheel")
            new_sdist.write_bytes(b"named-sdist")

            def run_case(stage_result: dict) -> tuple[int, dict]:
                def fake_spawn(*_args, **_kwargs):
                    return {"exit_code": 2, "timed_out": False, "pid": 4}

                with (
                    unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True, "cases": {"checks": []}, "inventory": {"ok": False}}),
                    unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": stage_result["id"], "timeout_seconds": 1}]),
                    unittest.mock.patch.object(execute, "execute_stage", return_value=stage_result),
                    unittest.mock.patch.object(execute, "coverage_checks", return_value=[]),
                    unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn),
                    _spy_assemble() as captured,
                ):
                    code = execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
                self.assertEqual(len(captured), 1)
                return code, captured[0]

            fail_code, fail_kwargs = run_case(
                {"id": "core", "status": "fail", "exit_code": 1, "log": "", "detail": "assertion", "checks": []},
            )
            self.assertEqual(fail_code, 1)
            self.assertEqual(fail_kwargs["phase"], "run")
            self.assertIs(fail_kwargs["executed"], True)
            fail_report = _load(layout.root / "report.json")
            self.assertEqual(fail_report["summary"]["exit_code"], 1)
            self.assertFalse(fail_report["acceptance_green"])
            self.assertTrue(any(item.get("status") == "fail" for item in fail_report["checks"]))

            blocked_code, _blocked_kwargs = run_case(
                {"id": "core", "status": "blocked", "exit_code": 2, "log": "", "detail": "waiting", "checks": []},
            )
            self.assertEqual(blocked_code, 2)
            self.assertFalse(_load(layout.root / "report.json")["acceptance_green"])

            empty_code, empty_kwargs = run_case(
                {"id": "core", "status": "pass", "exit_code": 0, "log": "", "detail": "no rows", "checks": []},
            )
            self.assertEqual(empty_code, 2)
            self.assertFalse(_load(layout.root / "report.json")["acceptance_green"])
            _assert_handoff_unnamed(self, empty_kwargs["wheel_dir"])

            named = {
                "id": "package",
                "status": "blocked",
                "exit_code": 2,
                "log": "",
                "detail": "named paths only",
                "checks": [_check("package-build-wheel", "blocked")],
                "distribution_env": {
                    stages.DISTRIBUTION_WHEEL_ENV: str(new_wheel),
                    stages.DISTRIBUTION_SDIST_ENV: str(new_sdist),
                },
            }
            named_code, named_kwargs = run_case(named)
            self.assertNotEqual(named_code, 0)
            self.assertEqual(named_kwargs["wheel_dir"].wheel, str(new_wheel))
            self.assertEqual(named_kwargs["wheel_dir"].sdist, str(new_sdist))
            self.assertNotEqual(named_kwargs["wheel_dir"].wheel, str(old))
            blob = json.dumps(_load(layout.root / "report.json"))
            self.assertNotIn(_digest(OLD_WHEEL), blob)
            self.assertNotIn(_digest(STALE_WHEEL), blob)

    def test_full_run_is_green_only_when_assemble_source_and_checks_are_complete(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            passed = {"id": "core", "status": "pass", "exit_code": 0, "log": "", "detail": "fixture pass", "checks": [_check("core", "pass")]}

            def close(*, complete: bool) -> int:
                source = {"complete": complete, "fingerprint": "abc" if complete else None, "captured_at": "t"}
                artifacts = {"complete": complete, "items": [{"sha256": "1", "content_hashed": True}], "blockers": [] if complete else ["missing"]}
                with (
                    unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True, "cases": {"checks": []}, "inventory": {"ok": True}}),
                    unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": "core", "timeout_seconds": 1}]),
                    unittest.mock.patch.object(execute, "execute_stage", return_value=passed),
                    unittest.mock.patch.object(execute, "coverage_checks", return_value=[]),
                    unittest.mock.patch.object(execute, "spawn", side_effect=AssertionError("spawn")),
                    unittest.mock.patch.object(report_build, "source_identity", return_value=source),
                    unittest.mock.patch.object(report_build, "artifact_report", return_value=artifacts),
                    _spy_assemble() as captured,
                ):
                    code = execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
                self.assertEqual(captured[0]["phase"], "run")
                self.assertIs(captured[0]["executed"], True)
                _assert_handoff_unnamed(self, captured[0]["wheel_dir"])
                return code

            incomplete = close(complete=False)
            incomplete_report = _load(layout.root / "report.json")
            self.assertEqual(incomplete, 2)
            self.assertFalse(incomplete_report["acceptance_green"])
            self.assertNotIn(_digest(OLD_WHEEL), json.dumps(incomplete_report))

            complete = close(complete=True)
            complete_report = _load(layout.root / "report.json")
            self.assertEqual(complete, 0)
            self.assertTrue(complete_report["acceptance_green"])
            self.assertEqual(complete_report["phase"], "run")
            self.assertIs(complete_report["executed"], True)
            self.assertNotIn(_digest(OLD_WHEEL), json.dumps(complete_report))

    def test_assemble_errors_are_not_rewritten_as_a_pass(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            previous = _plant_old_report(layout)
            with unittest.mock.patch.object(execute, "assemble", side_effect=RuntimeError("boom")), _forbid_stages():
                with self.assertRaises(RuntimeError):
                    execute.prepare(repo, layout)
                with self.assertRaises(RuntimeError):
                    execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
                with self.assertRaises(RuntimeError):
                    execute.report_only(repo, layout)
            self.assertEqual(_load(layout.root / "report.json"), previous)

    def test_cli_main_boundaries_do_not_start_stages(self) -> None:
        with _world() as (repo, layout, _old, _stale):
            with _cli(repo, layout), _forbid_stages():
                self.assertEqual(accept.main([]), 2)
                self.assertFalse((layout.root / "report.json").exists())
                self.assertEqual(accept.main(["run"]), 2)
                self.assertEqual(accept.main(["run", "--run"]), 2)
                self.assertEqual(accept.main(["run", "--implementation-complete"]), 2)
                self.assertFalse((layout.root / "report.json").exists())

                prepared = accept.main(["prepare"])
                self.assertEqual(prepared, 0)
                plan = _load(layout.root / "prepare-plan.json")
                report = _load(layout.root / "report.json")
                self.assertEqual(plan["acceptance_exit"], 2)
                self.assertEqual(report["phase"], "prepare")
                self.assertIs(report["executed"], False)
                self.assertFalse(report["acceptance_green"])
                self.assertNotEqual(prepared, report["summary"]["exit_code"])

                rejected = accept.main(["run", "--run", "--implementation-complete"])
                self.assertEqual(rejected, 2)
                rejected_report = _load(layout.root / "report.json")
                self.assertEqual(rejected_report["phase"], "run")
                self.assertIs(rejected_report["executed"], False)
                self.assertFalse(rejected_report["acceptance_green"])

                unseen = accept.main(["report"])
                self.assertEqual(unseen, 2)
                self.assertEqual(_load(layout.root / "report.json")["phase"], "report")
                self.assertIs(_load(layout.root / "report.json")["executed"], False)

            fail_stage = {"id": "core", "status": "fail", "exit_code": 1, "log": "", "detail": "assertion", "checks": [_check("core", "fail")]}
            with (
                _cli(repo, layout),
                unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True, "cases": {"checks": []}, "inventory": {"ok": False}}),
                unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": "core", "timeout_seconds": 1}]),
                unittest.mock.patch.object(execute, "execute_stage", return_value=fail_stage),
                unittest.mock.patch.object(execute, "coverage_checks", return_value=[]),
                unittest.mock.patch.object(execute, "spawn", side_effect=AssertionError("spawn")),
            ):
                code = accept.main(["run", "--run", "--implementation-complete"])
            self.assertEqual(code, 1)
            closed = _load(layout.root / "report.json")
            self.assertEqual(closed["phase"], "run")
            self.assertIs(closed["executed"], True)
            self.assertFalse(closed["acceptance_green"])
            self.assertEqual(closed["summary"]["exit_code"], 1)

    def test_source_sets_phase_once_and_keeps_archive_handoff(self) -> None:
        text = EXECUTE.read_text(encoding="utf-8")
        self.assertNotIn("**_context(", text)
        self.assertNotIn("wheel_dir=layout.wheel", text)
        self.assertIn("assemble(**_report_kwargs(", text)
        self.assertIn('phase="prepare"', text)
        self.assertIn('phase="run"', text)
        self.assertIn('phase="report"', text)
        self.assertIn("wheel_dir=_trusted_archive_handoff(layout, None)", text)
        self.assertIn("wheel_dir=_trusted_archive_handoff(layout, package_result)", text)
        self.assertNotIn("run_tests.sh", inspect_run_all())


def inspect_run_all() -> str:
    import inspect

    return inspect.getsource(execute.run_all)
