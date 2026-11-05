"""Argument forwarding and TUI path boundaries. Not executed by the authoring turn.

Does not run pytest, npm, or the product CLI. The runner import loads the
splitter only; tests must not call main().
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "clawd_acceptance"))

from isolation import Layout, core_argv, tui_build_locations  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "clawd_run_tests_parallel_forwarding",
    REPO / "scripts" / "run_tests_parallel.py",
)
assert _SPEC and _SPEC.loader
_RUNNER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)


class RunnerArgumentForwardingTests(unittest.TestCase):
    def test_space_basetemp_is_joined_and_tests_stays_positional(self) -> None:
        our, bare, explicit = _RUNNER.split_forwarded_argv(
            ["-j", "1", "--file-timeout", "300", "--basetemp", "/owned/pytest-basetemp", "tests"],
            {"-j", "--jobs", "--file-timeout", "--paths"},
        )
        self.assertEqual(our, ["-j", "1", "--file-timeout", "300", "tests"])
        self.assertEqual(bare, ["--basetemp=/owned/pytest-basetemp"])
        self.assertEqual(explicit, [])
        self.assertNotIn("/owned/pytest-basetemp", our)
        self.assertEqual(our.count("tests"), 1)

    def test_equals_basetemp_is_kept_and_does_not_eat_tests(self) -> None:
        our, bare, _explicit = _RUNNER.split_forwarded_argv(
            ["--basetemp=/owned/pytest-basetemp", "tests"],
            set(),
        )
        self.assertEqual(our, ["tests"])
        self.assertEqual(bare, ["--basetemp=/owned/pytest-basetemp"])

    def test_missing_path_does_not_consume_the_suite_root(self) -> None:
        our, bare, _explicit = _RUNNER.split_forwarded_argv(["--basetemp", "tests"], set())
        self.assertEqual(our, ["tests"])
        self.assertEqual(bare, ["--basetemp"])
        self.assertIsNone(_RUNNER.attach_pytest_path_value("--basetemp", "tests"))
        self.assertIsNone(_RUNNER.attach_pytest_path_value("--basetemp", "tests/"))

    def test_short_value_flag_still_consumes_its_value_only(self) -> None:
        our, bare, _explicit = _RUNNER.split_forwarded_argv(
            ["-k", "expr", "tests", "-q"],
            set(),
        )
        self.assertEqual(bare[:2], ["-k", "expr"])
        self.assertEqual(our, ["tests"])
        self.assertIn("-q", bare)

    def test_contract_window_does_not_make_basetemp_a_runner_flag(self) -> None:
        text = (REPO / "scripts" / "run_tests_parallel.py").read_text(encoding="utf-8")
        window = text.split("OUR_FLAGS", 1)[-1][:800]
        self.assertNotIn('"--basetemp"', window)
        shell = (REPO / "scripts" / "run_tests.sh").read_text(encoding="utf-8")
        self.assertIn('FORWARD_ARGS+=("$1=$2")', shell)
        self.assertIn('"$2" != "tests"', shell)
        self.assertIn('"${FORWARD_ARGS[@]}"', shell)
        self.assertNotIn('run_tests_parallel.py" "$@"', shell)

    def test_acceptance_core_argv_keeps_one_tests_root(self) -> None:
        argv = core_argv(REPO, Layout(Path("/root/hermes-workspace/not-a-created-accept-root")))
        self.assertEqual(argv.count("tests"), 1)
        self.assertEqual(argv[argv.index("--basetemp") + 1].endswith("pytest-basetemp"), True)
        self.assertNotEqual(argv[argv.index("--basetemp") + 1], "tests")
        self.assertIn("-j", argv)
        self.assertEqual(argv[argv.index("-j") + 1], "1")


class TuiBuildPathBoundaryTests(unittest.TestCase):
    def test_unique_token_is_outside_the_checkout_and_not_system_tmp(self) -> None:
        plan = tui_build_locations(REPO, "run-owned-1")
        self.assertTrue(plan["ok"], plan["detail"])
        tmp = plan["tmpdir"]
        out = plan["out"]
        self.assertEqual(tmp.parents[2], Path("/root/hermes-workspace"))
        self.assertFalse(str(tmp).startswith(str(REPO)))
        self.assertFalse(str(out).startswith(str(REPO)))
        self.assertFalse(str(tmp).startswith("/tmp"))
        self.assertFalse(str(out).startswith("/tmp"))
        self.assertNotEqual(tmp, out)
        self.assertFalse(tmp.exists())
        self.assertFalse(out.exists())

    def test_token_and_overlap_are_refused(self) -> None:
        self.assertFalse(tui_build_locations(REPO, "../escape")["ok"])
        self.assertFalse(tui_build_locations(REPO, "a/b")["ok"])
        inside = tui_build_locations(Path("/root/hermes-workspace"), "run-owned-2")
        self.assertFalse(inside["ok"])
        self.assertIn("overlap", inside["detail"])
