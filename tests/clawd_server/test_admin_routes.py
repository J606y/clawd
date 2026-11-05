"""Static checks for admin route wiring. Not executed in this change."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def test_routes_wrap_route_after_dispatch_and_leave_name_delete_alone():
    from clawd_server.admin_routes import install

    calls = {}

    class Handler:
        def _dispatch(self, method: str) -> None:
            calls["dispatch"] = method

        def _route(self, method: str, path: str, query: dict[str, str]):
            calls["original"] = (method, path)
            return {"original": True}

        def _json_body(self):
            return {"name": "demo", "confirm": "demo", "paths": ["/tmp/demo"], "token": "x" * 32, "fingerprint": "a" * 64}

    mod = SimpleNamespace(
        ApiError=ApiError,
        Handler=Handler,
        preview_delete_profile=lambda name: {"ok": False, "deleted": False, "name": name},
        confirm_delete_profile=lambda name, confirm, paths, token=None, fingerprint=None: {
            "confirm": confirm,
            "paths": paths,
            "token": token,
            "fingerprint": fingerprint,
        },
        delete_profile=lambda name: calls.setdefault("name_delete", name),
    )
    assert install(mod) is True
    handler = Handler()
    preview = handler._route("POST", "/profiles/delete-preview", {})
    assert preview["name"] == "demo"
    assert "dispatch" not in calls
    assert "name_delete" not in calls
    confirmed = handler._route("POST", "/profiles/delete-confirm", {})
    assert confirmed["token"] == "x" * 32
    assert confirmed["fingerprint"] == "a" * 64
    assert handler._route("POST", "/profiles/delete", {}) == {"original": True}
    assert calls["original"] == ("POST", "/profiles/delete")
    assert Handler._dispatch is not Handler._route
    install(mod)
    assert handler._route("GET", "/profiles", {}) == {"original": True}


def test_missing_token_does_not_read_a_profile():
    from clawd_server.admin_profiles import confirm_delete_profile

    mod = SimpleNamespace(ApiError=ApiError)
    try:
        confirm_delete_profile(mod, "demo", "demo", ["/tmp/demo"])
    except ApiError as exc:
        assert exc.status == 409
        assert exc.code == "profile_delete_confirmation_required"
    else:
        raise AssertionError("missing token was treated as confirmation")


def test_ticket_is_exact_expiring_and_one_time():
    from clawd_server.admin_profiles import _consume_ticket, _issue_ticket, _paths_exact

    issued = _issue_ticket("/tmp/clawd-home", "demo", ["/a", "/b"], "c" * 64)
    assert _paths_exact(["/b", "/a"], ["/a", "/b"]) is False
    assert _paths_exact(["/a", "/b"], ["/a", "/b"]) is True
    ticket, reason = _consume_ticket(issued["token"], "/tmp/clawd-home")
    assert reason == "ok"
    assert ticket is not None
    assert ticket["paths"] == ["/a", "/b"]
    again, second = _consume_ticket(issued["token"], "/tmp/clawd-home")
    assert again is None
    assert second == "missing"
    other = _issue_ticket("/tmp/clawd-home", "demo", ["/a"], "d" * 64)
    wrong_home, home_reason = _consume_ticket(other["token"], "/tmp/other")
    assert wrong_home is None
    assert home_reason == "home"


def test_sources_keep_auth_boundary_and_delivery_failure():
    native = (ROOT / "clawd_server" / "native.py").read_text(encoding="utf-8")
    wrapper = native.index('mod._clawd_config_set_owner = "wrapper"')
    browser = native.index("install_browser(mod)")
    runtime = native.index("install_runtime(mod)")
    admin = native.index("install_admin(mod)")
    assert wrapper < browser < runtime < admin
    assert native.index('mod._clawd_browser_installed = True') < runtime
    admin_native = (ROOT / "clawd_server" / "admin_native.py").read_text(encoding="utf-8")
    assert "install_browser" not in admin_native
    assert "install_runtime" not in admin_native
    routes = (ROOT / "clawd_server" / "admin_routes.py").read_text(encoding="utf-8")
    assert "Handler._dispatch =" not in routes
    assert "getattr(mod, \"delete_profile\"" not in routes
    assert "body.get(\"name\"))" not in routes or "preview_delete_profile" in routes
    sidecar = (ROOT / "apps/panel/sidecar/hermes_panel_sidecar.py").read_text(encoding="utf-8")
    assert '"/profiles/delete": lambda: delete_profile(body.get("name"))' in sidecar
    assert "delete-preview" not in sidecar
    webhooks = (ROOT / "clawd_server" / "admin_webhooks.py").read_text(encoding="utf-8")
    assert "webhook_deliveries_unavailable" in webhooks
    assert '{"data": []}' not in webhooks
    profiles = (ROOT / "clawd_server" / "admin_profiles.py").read_text(encoding="utf-8")
    assert "return confirm_delete_profile" not in profiles
    assert "profiles.delete_profile" not in profiles
    panel = (ROOT / "apps/panel/src/react/pages/settings/plugins-tab.tsx").read_text(encoding="utf-8")
    assert "confirmDeleteProfile" in panel
    assert "previewDeleteProfile" in panel
    assert "deleteProfile" not in panel
    assert "已将「" not in panel
    config = (ROOT / "apps/panel/src/react/pages/settings/config-tab.tsx").read_text(encoding="utf-8")
    assert "已保存。新对话生效" not in config
    assert "savedNotice" in config
