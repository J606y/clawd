"""Pins and policy for the final clawd acceptance. Not an execution entry."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO = PACKAGE_DIR.parents[1]
ACCEPT_ROOT = REPO / "artifacts" / "final-acceptance"
MANIFEST_PATH = REPO / "docs" / "clawd" / "implementation-completion-manifest.json"
RACE_DIR = REPO / "tests" / "clawd_race_cases"
RACE_DOC = REPO / "docs" / "clawd" / "race-case-preparation.md"
# Other lanes own these trees and docs. Acceptance only consumes them.
REQUIRED_CASE_SUITES = (
    {
        "id": "core-race-cases",
        "rel_dir": "tests/clawd_race_cases",
        "rel_doc": "docs/clawd/race-case-preparation.md",
        "themes": ("审批代次", "重复唤醒", "取消人工输入", "重启恢复"),
    },
    {
        "id": "core-file-auth-cases",
        "rel_dir": "tests/clawd_file_auth_cases",
        "rel_doc": "docs/clawd/file-auth-case-preparation.md",
        "themes": (),
    },
    {
        "id": "core-memory-scope-cases",
        "rel_dir": "tests/clawd_memory_scope_cases",
        "rel_doc": "docs/clawd/memory-scope-case-preparation.md",
        "themes": (),
    },
    {
        "id": "core-event-recovery-cases",
        "rel_dir": "tests/clawd_event_recovery_cases",
        "rel_doc": "docs/clawd/event-recovery-case-preparation.md",
        "themes": (),
    },
)
LICENSE_INVENTORY = REPO / "scripts" / "clawd_inventory"

PYTHON = REPO / ".venv" / "bin" / "python"
NODE_BIN = REPO / ".dev-home" / "tools" / "node-26.12.1-linux-x64" / "bin"
NODE = NODE_BIN / "node"
NPM = NODE_BIN / "npm"
RUNTIME_DIR = REPO / ".dev-home" / "tools"
EXPECTED_PYTHON = "3.14.9"
EXPECTED_NODE = "v26.12.1"

# Panel Node full suite has been observed near 395s. 35s and 240s kills are
# harness errors, not product failures. The wall clock must sit above that.
NODE_FULL_KNOWN_SECONDS = 395
NODE_FULL_TIMEOUT_SECONDS = 900
FORBIDDEN_KILL_SECONDS = (35, 240)
PYTEST_FILE_TIMEOUT_SECONDS = 300
CORE_WALL_SECONDS = 21600

REQUESTED_MODEL = "grok-4.7"
EXPECTED_IDENTITY = REQUESTED_MODEL
NOT_IDENTITY_MARKERS = (
    "hermes-agent",
    "test-not-llm",
    "test-executor-not-llm",
)
PRODUCER = "scripts/clawd_acceptance"
# Created by other lanes. Covered by the single tests discovery when present.
NATIVE_CASE_DIRS = (
    "tests/clawd_auth_native",
    "tests/clawd_memory_native",
    "tests/clawd_event_native",
)

MANIFEST_STATUSES = ("implemented", "unverified", "pending", "blocked")
CHECK_STATUSES = (
    "pass",
    "fail",
    "blocked",
    "not_run",
    "unknown",
    "interrupted",
    "in_progress",
)

# run_tests.sh drops these in `env -i`. Parent exports are not child isolation.
ENV_I_FORWARDED = (
    "PATH",
    "HOME",
    "HERMES_GATEWAY_LOCK_DIR",
    "TZ",
    "LANG",
    "LC_ALL",
    "PYTHONHASHSEED",
    "PYTHONUTF8",
)
ENV_I_STRIPPED = (
    "CLAWD_HOME",
    "HERMES_HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "HERMES_RUNTIME_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "PYTHONPATH",
)

FORBIDDEN_PREFIXES = (
    "/root/.hermes",
    "/usr/local/lib/hermes-agent",
    "/usr/local/bin/hermes",
)
PRODUCTION_ROLLBACK = Path("/root/panel-deploy.sh")

WHEEL_MEMBERS = (
    "clawd_server/__init__.py",
    "clawd_server/__main__.py",
    "clawd_projects/__init__.py",
    "clawd_projects/service.py",
    "clawd_cli/__init__.py",
    "clawd_cli/main.py",
)

RACE_THEMES = ("审批代次", "重复唤醒", "取消人工输入", "重启恢复")

# Direct subprocesses we spawn can carry these. run_tests.sh children cannot.
DIRECT_ISOLATION_KEYS = ("HOME", "CLAWD_HOME", "HERMES_HOME", "TMPDIR", "TEMP", "TMP")
