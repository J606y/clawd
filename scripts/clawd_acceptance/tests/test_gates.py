"""Orchestrator unit tests. Not run by the acceptance authoring turn."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import accept
import classify
import gate
import isolation
import resume
from constants import FORBIDDEN_KILL_SECONDS, REQUIRED_CASE_SUITES
from stages import stage_catalog


class GateTests(unittest.TestCase):
    def test_missing_manifest_blocks_run(self) -> None:
        loaded = gate.load_manifest(Path("/tmp/does-not-exist-clawd-manifest.json"))
        decision = gate.manifest_allows_run(loaded, True)
        self.assertFalse(decision["run_allowed"])
        self.assertFalse(decision["tests_passed"])

    def test_implemented_without_flag_is_not_a_test_pass(self) -> None:
        loaded = {
            "ok": True,
            "reason": "ok",
            "items": [
                {
                    "id": "core",
                    "scope": "core",
                    "owner": "x",
                    "status": "implemented",
                    "evidence": ["diff"],
                    "blocking": True,
                }
            ],
        }
        decision = gate.manifest_allows_run(loaded, False)
        self.assertFalse(decision["run_allowed"])
        self.assertEqual(decision["implemented_means"], "code delivered, not tests passed")

    def test_flag_does_not_override_pending(self) -> None:
        loaded = {
            "ok": True,
            "items": [
                {
                    "id": "panel",
                    "scope": "panel",
                    "owner": "x",
                    "status": "pending",
                    "evidence": [],
                    "blocking": True,
                }
            ],
        }
        decision = gate.manifest_allows_run(loaded, True)
        self.assertFalse(decision["run_allowed"])
        self.assertEqual(decision["blocking_not_implemented"], ["panel"])

    def test_missing_case_dir_is_blocked_and_not_created(self) -> None:
        root = Path(self.id().replace(".", "_"))
        spec = REQUIRED_CASE_SUITES[0]
        status = gate.case_suite_status(root, spec)
        self.assertEqual(status["status"], "blocked")
        self.assertFalse((root / spec["rel_dir"]).exists())

    def test_production_path_refused(self) -> None:
        self.assertTrue(gate.forbidden_path(Path("/root/.hermes/config.yaml")))
        self.assertTrue(gate.forbidden_path(Path("/root/panel-deploy.sh")))

    def test_inventory_missing_does_not_network(self) -> None:
        root = Path(self.id().replace(".", "_"))
        ready = gate.inventory_ready(root)
        self.assertFalse(ready["ok"])
        self.assertFalse(ready["network"])


class IsolationTests(unittest.TestCase):
    def test_env_i_does_not_pretend_clawd_home_reaches_children(self) -> None:
        layout = isolation.Layout(Path("/tmp/clawd-accept-plan"))
        plan = isolation.run_tests_plan(Path("/root/hermes-workspace/clawd"), layout)
        self.assertIn("HOME", plan["forwarded"])
        self.assertIn("CLAWD_HOME", plan["stripped"])
        self.assertEqual(plan["parent_env"]["HOME"], str(layout.account_home))
        self.assertEqual(plan["child_clawd_home"], str(layout.home))
        self.assertTrue(plan["matches_core_resolution"])
        self.assertFalse(plan["production_credentials_read"])
        self.assertEqual(plan["argv"].count("tests"), 1)
        for spec in REQUIRED_CASE_SUITES:
            self.assertNotIn(spec["rel_dir"], plan["argv"])
        for rel in ("tests/clawd_auth_native", "tests/clawd_memory_native", "tests/clawd_event_native"):
            self.assertNotIn(rel, plan["argv"])

    def test_catalog_uses_one_core_discovery_root(self) -> None:
        layout = isolation.Layout(Path("/tmp/clawd-accept-plan"))
        core = next(item for item in stage_catalog(Path("/root/hermes-workspace/clawd"), layout) if item["id"] == "core")
        self.assertEqual(core["argv"].count("tests"), 1)
        self.assertNotIn("35", core["argv"])
        self.assertNotIn("240", core["argv"])


class ClassifyTests(unittest.TestCase):
    def test_empty_is_exit_2(self) -> None:
        self.assertEqual(classify.aggregate_exit([]), 2)

    def test_fail_beats_blocked(self) -> None:
        checks = [
            {"id": "a", "status": "blocked", "required": True},
            {"id": "b", "status": "fail", "required": True},
        ]
        self.assertEqual(classify.aggregate_exit(checks), 1)

    def test_blocked_is_not_pass(self) -> None:
        checks = [{"id": "mcp", "status": "blocked", "required": True}]
        self.assertEqual(classify.aggregate_exit(checks), 2)
        self.assertNotEqual(checks[0]["status"], "pass")

    def test_overlap_is_not_summed(self) -> None:
        checks = [
            {"id": "core", "status": "pass", "required": True, "passed_count": 251},
            {"id": "core", "status": "pass", "required": True, "passed_count": 25},
            {"id": "extra", "status": "pass", "required": True, "passed_count": 395},
        ]
        summary = classify.summary_counts(checks)
        self.assertEqual(summary["unique"]["pass"], 2)
        self.assertEqual(summary["overlap_excluded"], 1)
        self.assertNotEqual(summary["unique"]["pass"], 251 + 25 + 395)

    def test_forbidden_kill_timeouts(self) -> None:
        for seconds in FORBIDDEN_KILL_SECONDS:
            self.assertIsNotNone(classify.timeout_rejected(seconds, node_wall=True))
        self.assertIsNotNone(classify.timeout_rejected(395, node_wall=True))
        self.assertIsNone(classify.timeout_rejected(900, node_wall=True))
        self.assertEqual(
            classify.classify_timeout(configured=35, killed=True, assertion_failure=True, node_wall=True),
            "blocked",
        )

    def test_requested_model_echo_is_not_identity(self) -> None:
        payload = {"status": "completed", "model": "grok-4.7", "runtime": {"model": "hermes-agent"}}
        result = classify.classify_run_payload(payload)
        self.assertTrue(result["identity"]["mismatch"])
        self.assertEqual(result["status"], "fail")

    def test_test_executor_cannot_pass(self) -> None:
        payload = {
            "status": "completed",
            "runtime": {"model": "grok-4.7"},
            "output": "test-executor-not-llm",
        }
        result = classify.classify_run_payload(payload)
        self.assertFalse(result["identity"]["can_pass"])
        self.assertNotEqual(result["status"], "pass")

    def test_missing_runtime_is_not_a_parent_mismatch(self) -> None:
        payload = {"status": "completed", "model": "grok-4.7"}
        result = classify.classify_run_payload(payload)
        self.assertIsNone(result["identity"]["mismatch"])
        self.assertNotEqual(result["status"], "fail")
        self.assertNotEqual(result["status"], "pass")
        self.assertFalse(result["identity"]["parent_agent_checked"])

    def test_external_case_requires_request_stamp(self) -> None:
        payload = {"status": "completed", "delegation": True}
        result = classify.classify_external_case(payload, requested_model="grok-4.7")
        self.assertEqual(result["status"], "blocked")
        stamped = {**payload, "_requested_model": "grok-4.7"}
        passed = classify.classify_external_case(stamped, requested_model="grok-4.7")
        self.assertEqual(passed["status"], "pass")

    def test_wheel_members_do_not_prove_installed_panel(self) -> None:
        from constants import WHEEL_MEMBERS

        inspected = classify.inspect_wheel_names(list(WHEEL_MEMBERS))
        self.assertEqual(inspected["members_status"], "pass")
        self.assertEqual(inspected["installed_panel_status"], "blocked")


class ResumeTests(unittest.TestCase):
    def test_live_pid_is_not_restarted(self) -> None:
        self.assertEqual(resume.decide(state="running", pid=1, resume_interrupted=True, rerun=True), "attach")

    def test_dead_run_is_not_blindly_restarted(self) -> None:
        self.assertEqual(resume.decide(state="running", pid=None, resume_interrupted=False, rerun=False), "refuse")


class CliTests(unittest.TestCase):
    def test_default_does_not_execute(self) -> None:
        self.assertEqual(accept.main([]), 2)


if __name__ == "__main__":
    unittest.main()
