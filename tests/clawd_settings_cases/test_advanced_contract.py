"""Static contract for advanced config objects and the 43 Chinese labels.

Not a runtime probe. Importing this module does not read a profile. The merge
cases use a fake policy object and do not open config.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/clawd/settings-core-matrix.json"
SUPPLEMENT = ROOT / "apps/panel/src/react/lib/settings-zh-supplement.json"
SETTINGS_ZH = ROOT / "apps/panel/src/react/lib/settings-zh.json"
CONFIG_TAB = ROOT / "apps/panel/src/react/pages/settings/config-tab.tsx"
ADMIN_SETTINGS = ROOT / "clawd_server/admin_settings.py"
DENIED = (
    "providers",
    "model_catalog.providers",
    "model_overrides",
    "monitoring.export.otlp.headers_env",
    "onboarding.seen",
    "secrets.onepassword.env",
)


class _Policy:
    def _is_secret_name(self, name: str) -> bool:
        return name.lower() in {"api_key", "token", "password"}

    def _is_secret_key(self, key: str) -> bool:
        leaf = key.rsplit(".", 1)[-1]
        return self._is_secret_name(leaf)


def _matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def test_allowlist_matches_matrix_order_and_keeps_other_plans_out():
    from clawd_server.admin_settings import ADVANCED_ALLOWLIST, MASKED_ADVANCED_KEYS, advanced_plan

    expected = [
        item["key"]
        for item in _matrix()["openContainers"]
        if item["plan"] in {"advanced-object", "masked-advanced"}
    ]
    assert list(ADVANCED_ALLOWLIST) == expected
    assert len(ADVANCED_ALLOWLIST) == 38
    for item in _matrix()["openContainers"]:
        if item["plan"] in {"advanced-object", "masked-advanced"}:
            assert advanced_plan(item["key"]) == item["plan"]
        else:
            assert advanced_plan(item["key"]) is None
    assert MASKED_ADVANCED_KEYS == {item["key"] for item in _matrix()["openContainers"] if item["plan"] == "masked-advanced"}
    for key in DENIED:
        assert advanced_plan(key) is None


def test_supplement_is_exactly_the_43_missing_labels():
    missing = _matrix()["counts"]["zhMissing"]
    supplement = json.loads(SUPPLEMENT.read_text(encoding="utf-8"))
    covered = json.loads(SETTINGS_ZH.read_text(encoding="utf-8"))
    assert list(supplement) == missing
    assert len(supplement) == 43
    assert all(isinstance(value, str) and value.strip() for value in supplement.values())
    assert set(supplement).isdisjoint(covered)


def test_config_tab_wires_supplement_groups_and_keeps_existing_rows():
    text = CONFIG_TAB.read_text(encoding="utf-8")
    assert 'from "@/lib/settings-zh-supplement.json"' in text
    assert "Object.prototype.hasOwnProperty.call(ZH, key)" in text
    assert "Object.prototype.hasOwnProperty.call(ZH_SUPPLEMENT, key)" in text
    assert "ToolsSection" in text
    assert "MemorySection" in text
    assert "ConnectionsSection" in text
    assert "getAdvanced" in text
    assert "setAdvanced" in text
    assert 'from "@/lib/admin-api"' in text
    assert "savedNotice" in text
    assert "已保存。新对话生效" not in text
    assert 'role="tablist" aria-label="设置方式"' in text
    for key in json.loads(MATRIX.read_text(encoding="utf-8"))["openContainers"]:
        if key["plan"] in {"advanced-object", "masked-advanced"}:
            assert key["key"] in text


def test_mask_does_not_replace_secret_and_response_hides_it():
    from clawd_server.admin_settings import mask_advanced, merge_advanced

    mod = _Policy()
    existing = {
        "model": "a",
        "api_key": "real-secret",
        "label": "keep-stars",
        "nested": {"token": "tok", "n": 1},
        "items": [{"api_key": "list-secret", "name": "one"}],
    }
    incoming = {
        "model": "b",
        "api_key": "***",
        "label": "***",
        "nested": {"token": ""},
        "items": [{"api_key": "***", "name": "two"}],
    }
    merged = merge_advanced(mod, existing, incoming, force_mask=False)
    assert merged["api_key"] == "real-secret"
    assert merged["nested"]["token"] == "tok"
    assert "n" not in merged["nested"]
    assert merged["model"] == "b"
    assert merged["label"] == "***"
    assert merged["items"][0]["api_key"] == "list-secret"
    assert merged["items"][0]["name"] == "two"
    assert "real-secret" != "***"
    shown, leaves = mask_advanced(mod, merged, force_mask=False)
    rendered = json.dumps(shown, ensure_ascii=False)
    assert "real-secret" not in rendered
    assert "tok" not in rendered
    assert "list-secret" not in rendered
    assert shown["api_key"] == "***"
    assert "api_key" in leaves
    assert shown["label"] == "***"


def test_omitted_or_blank_secret_is_kept_and_null_clears():
    from clawd_server.admin_settings import merge_advanced

    mod = _Policy()
    existing = {"api_key": "real-secret", "model": "a", "token": "keep"}
    kept = merge_advanced(mod, existing, {"model": "b"}, force_mask=False)
    assert kept["api_key"] == "real-secret"
    assert kept["token"] == "keep"
    blank = merge_advanced(mod, existing, {"api_key": "", "model": "a", "token": "已设置"}, force_mask=False)
    assert blank["api_key"] == "real-secret"
    assert blank["token"] == "keep"
    cleared = merge_advanced(mod, existing, {"api_key": None, "model": "a", "token": "keep"}, force_mask=False)
    assert "api_key" not in cleared
    assert cleared["token"] == "keep"


def test_masked_container_preserves_values_when_mask_is_sent_back():
    from clawd_server.admin_settings import mask_advanced, merge_advanced

    mod = _Policy()
    existing = {"FOO": "secret-value", "BAR": "other-value", "nested": {"token": "nested-secret"}}
    merged = merge_advanced(mod, existing, {"FOO": "***", "nested": {"token": "***"}}, force_mask=True)
    assert merged["FOO"] == "secret-value"
    assert merged["BAR"] == "other-value"
    assert merged["nested"]["token"] == "nested-secret"
    replaced = merge_advanced(mod, existing, {"FOO": "new-value"}, force_mask=True)
    assert replaced["FOO"] == "new-value"
    assert replaced["BAR"] == "other-value"
    shown, _leaves = mask_advanced(mod, replaced, force_mask=True)
    rendered = json.dumps(shown, ensure_ascii=False)
    assert "secret-value" not in rendered
    assert "other-value" not in rendered
    assert "new-value" not in rendered
    assert shown["FOO"] == "***"


def test_missing_secret_policy_refuses_without_a_local_list():
    from clawd_server.admin_common import ConfigError
    from clawd_server.admin_settings import merge_advanced

    try:
        merge_advanced(SimpleNamespace(), {}, {"plain": "x"}, force_mask=False)
    except ConfigError as exc:
        assert exc.status == 503
        assert exc.code == "secret_policy_unavailable"
    else:
        raise AssertionError("missing secret policy was allowed")


def test_write_contract_does_not_claim_hot_load_or_touch_env():
    text = ADMIN_SETTINGS.read_text(encoding="utf-8")
    assert "def get_advanced(" in text
    assert "def set_advanced(" in text
    assert 'fields["applied"] = False' in text
    assert 'fields["processEnvChanged"] = False' in text
    assert 'fields["hotLoaded"] = False' in text
    assert 'fields["effectiveNow"] = False' in text
    assert 'fields["processSwitched"] = False' in text
    assert "os.environ" not in text
    assert "advanced_key_denied" in text
    assert "advanced_invalid" in text
    assert "advanced_unverified" in text
    assert "profile_scope_unsafe" in text
    assert "不猜测 default" in text
