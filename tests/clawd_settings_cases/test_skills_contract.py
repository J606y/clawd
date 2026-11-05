"""Static contract for the skill enable switch. Not executed in this change."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "clawd_server" / "admin_skills.py"
TAB = ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "skills-tab.tsx"


def test_skill_export_writes_disabled_without_cli_or_installer():
    text = SKILLS.read_text(encoding="utf-8")
    assert "def set_skill_enabled(mod: Any, name: Any, enabled: Any)" in text
    assert '__all__ = ["set_skill_enabled"]' in text
    assert 'commit_set(mod, _KEY, json.dumps(sorted(updated), ensure_ascii=False))' in text
    assert "commit_unset(mod, _KEY, missing_ok=True)" in text
    assert '_KEY = "skills.disabled"' in text
    assert '"applied": False' in text
    assert '"effectiveNow": False' in text
    assert '"hotLoaded": False' in text
    assert '"loadedNow": False' in text
    assert '"processEnvChanged": False' in text
    assert '"restartRequired": False' in text
    assert "skill_not_found" in text
    assert "skill_toggle_unverified" in text
    assert "profile_scope_unsafe" in text
    assert "def install" not in text
    assert "subprocess" not in text
    assert "_hermes" not in text
    assert "os.environ[" not in text
    assert "save_disabled_skills" not in text
    assert "get_active_profile" not in text
    assert "501" not in text


def test_skill_tab_keeps_server_state_and_shows_server_error():
    text = TAB.read_text(encoding="utf-8")
    assert "setQueryData" not in text
    assert "useQueryClient" not in text
    assert "errText(error, \"切换失败\")" in text
    assert "await skills.refetch()" in text
    assert "bad={noticeBad}" in text
    assert "本进程未热加载" in text
    assert "className=\"skills-root\"" in text
    assert "className=\"card list\"" in text
    assert "className=\"editor\"" in text
    assert "安装" not in text


def test_skills_disabled_is_an_exact_runtime_key_not_a_skills_prefix():
    text = (ROOT / "hermes_cli" / "config_runtime_keys.py").read_text(encoding="utf-8")
    assert "dotted in CORE_RUNTIME_EXACT_KEYS" in text
    prefix_dict = text.split("CORE_RUNTIME_KEYS: dict", 1)[1].split("CORE_RUNTIME_EXACT_KEYS: dict", 1)[0]
    assert '"skills' not in prefix_dict
    exact_dict = text.split("CORE_RUNTIME_EXACT_KEYS: dict", 1)[1].split("\n}\n", 1)[0]
    assert '"skills.disabled"' in exact_dict
    assert "platform_disabled" not in exact_dict
    native = (ROOT / "clawd_server" / "admin_native.py").read_text(encoding="utf-8")
    routes = (ROOT / "clawd_server" / "admin_routes.py").read_text(encoding="utf-8")
    bind = native.split("def _bind_skill_export", 1)[1].split("def _mark_journal_unavailable", 1)[0]
    assert 'load_export("admin_skills", "set_skill_enabled")' in bind
    assert "没有改用命令行或旧写入" in bind
    assert "save_disabled_skills" not in bind
    assert "_hermes" not in bind
    assert '_need(mod, "admin_skills", "set_skill_enabled"' in routes
    skill_branch = routes.split('path == "/skills/enabled"', 1)[1].split('path == "/mcp/servers"', 1)[0]
    assert "_FALLTHROUGH" not in skill_branch
    assert "_hermes" not in skill_branch
