"""Acceptance cannot be green while any required check is blocked."""
import pytest

from scripts.verify_clawd import verification_exit_code


@pytest.mark.parametrize(("statuses", "expected"), [
    (["pass"], 0), (["pass", "blocked"], 2), (["fail", "blocked"], 1),
    ([], 2), (["unknown"], 2),
])
def test_acceptance_exit_reports_incomplete_checks(statuses, expected):
    results = {str(i): {"status": status} for i, status in enumerate(statuses)}
    assert verification_exit_code(results) == expected
