"""定时任务字段与日志边界的准备用例。本阶段不执行。

只读源码文本，或在显式替身 home 上调用 admin_logs。不读生产配置，
不调用 journalctl，不把空列表当成 journal 成功。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
JOBS = ROOT / "apps/panel/src/react/pages/settings/jobs-tab.tsx"
UPDATE = ROOT / "apps/panel/src/react/pages/settings/update-tab.tsx"
LOGS = ROOT / "apps/panel/src/react/pages/settings/logs-tab.tsx"
ADMIN = ROOT / "clawd_server/admin_logs.py"
GATEWAY = ROOT / "gateway/platforms/api_server.py"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _mod(**extra: Any) -> Any:
    base = {
        "ApiError": ApiError,
        "redact": lambda text: text.replace("secret-value", "***"),
    }
    base.update(extra)
    return SimpleNamespace(**base)


def test_gateway_job_domain_is_unchanged():
    text = GATEWAY.read_text(encoding="utf-8")
    assert '_UPDATE_ALLOWED_FIELDS = {"name", "schedule", "prompt", "deliver", "skills", "skill", "repeat", "enabled"}' in text
    assert 'if repeat is not None and (not isinstance(repeat, int) or repeat < 1):' in text
    assert '"deliver": body.get("deliver", "local")' in text
    assert 'if skills:' in text
    assert 'if repeat is not None:' in text


def test_jobs_form_sends_deliver_skills_repeat_on_create_and_edit():
    text = JOBS.read_text(encoding="utf-8")
    assert "createJob.mutateAsync(asJobInput(body))" in text
    assert "updateJob.mutateAsync({ id: draft.id, patch: asJobInput(patch) })" in text
    assert "if (deliver.value) body.deliver = deliver.value" in text
    assert "if (skills.value.length) body.skills = skills.value" in text
    assert "if (repeat.value != null) body.repeat = repeat.value" in text
    assert 'deliver: deliver.value || "local"' in text
    assert "skills: skills.value" in text
    assert "repeat: repeat.value" in text
    assert "Repeat must be a positive integer" not in text
    assert "重复次数须为 1 及以上的整数" in text


def test_update_tab_has_no_clickable_start():
    text = UPDATE.read_text(encoding="utf-8")
    assert "useUpdateStart" not in text
    assert "useUpdatePlan" not in text
    assert "update/start" not in text
    assert "UpdateConfirm" not in text
    assert "立即更新…" not in text
    assert "确认更新" not in text
    assert "mutateAsync" not in text
    assert "没有安全的进程内更新接口，不能从这里立即更新" in text
    assert "useUpdateInfo" in text
    assert "useUpdateRun" in text


def test_logs_tab_does_not_offer_journal_as_a_source():
    text = LOGS.read_text(encoding="utf-8")
    assert "journal_host_boundary" in text
    assert 'kind === "journal"' in text
    assert 'kind === "file"' in text
    assert 'label="系统服务"' not in text
    assert "没有匹配的日志" in text
    assert "不读取 systemd，也不以空列表表示成功" in text


def test_admin_logs_source_does_not_call_systemd():
    text = ADMIN.read_text(encoding="utf-8")
    code: list[str] = []
    in_doc = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            marker = '"""' if stripped.startswith('"""') else "'''"
            if stripped.count(marker) == 1:
                in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        code.append(line)
    body = "\n".join(code)
    assert "subprocess" not in body
    assert "journalctl" not in body
    assert "journal_host_boundary" in text
    assert "def get_logs(" in text


def test_journal_is_501_and_does_not_touch_home(monkeypatch):
    from clawd_server.admin_logs import LOG_JOURNAL, get_logs

    def explode(*_args, **_kwargs):
        raise AssertionError("journal read touched the log home")

    monkeypatch.setattr("clawd_server.admin_logs.panel_home", explode)
    monkeypatch.setattr("clawd_server.admin_logs._log_dir", explode)
    monkeypatch.setattr("clawd_server.admin_logs._tail_text", explode)
    for source in LOG_JOURNAL:
        try:
            get_logs(_mod(), source, 50, "", "", "")
        except ApiError as exc:
            assert exc.status == 501
            assert exc.code == "journal_host_boundary"
            assert exc.message
        else:
            raise AssertionError(f"{source} returned a body")


def test_unknown_source_is_404_not_an_empty_list(monkeypatch):
    from clawd_server.admin_logs import get_logs

    monkeypatch.setattr("clawd_server.admin_logs.panel_home", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("home")))
    try:
        get_logs(_mod(), "systemd", 50, "", "", "")
    except ApiError as exc:
        assert exc.status == 404
        assert exc.code == "unknown_source"
    else:
        raise AssertionError("unknown source returned a body")


def test_file_source_keeps_lines_inside_temp_home(tmp_path, monkeypatch):
    import hermes_constants
    from clawd_server.admin_logs import get_logs

    monkeypatch.setattr(hermes_constants, "get_process_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, "get_hermes_home_override", lambda: None)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "agent.log").write_text(
        "2026-11-05 00:00:00 INFO agent: hello secret-value\n",
        encoding="utf-8",
    )
    result = get_logs(_mod(HERMES_HOME=tmp_path), "agent", 100, "", "", "")
    assert result["source"] == "agent"
    assert result["lines"] == ["2026-11-05 00:00:00 INFO agent: hello ***"]
    assert "secret-value" not in "\n".join(result["lines"])


def test_missing_file_is_empty_but_journal_is_not(tmp_path, monkeypatch):
    import hermes_constants
    from clawd_server.admin_logs import get_logs

    monkeypatch.setattr(hermes_constants, "get_process_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, "get_hermes_home_override", lambda: None)
    (tmp_path / "logs").mkdir()
    missing = get_logs(_mod(HERMES_HOME=tmp_path), "gateway", 100, "", "", "")
    assert missing["source"] == "gateway"
    assert missing["lines"] == []
    try:
        get_logs(_mod(HERMES_HOME=tmp_path), "j-gateway", 100, "", "", "")
    except ApiError as exc:
        assert exc.status == 501
        assert exc.code == "journal_host_boundary"
    else:
        raise AssertionError("missing journal was treated as an empty file")
