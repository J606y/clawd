from argparse import Namespace
import sys
import types

import pytest


class _NonInteractiveStdin:
    def isatty(self):
        return False


def _chat_args(**overrides):
    base = {
        "continue_last": None,
        "model": None,
        "provider": None,
        "resume": None,
        "no_restore_cwd": False,
        "toolsets": None,
        "skills": None,
        "tui": False,
        "tui_dev": False,
        "cli": True,
        "verbose": None,
        "quiet": True,
        "query": "hello",
        "image": None,
        "worktree": False,
        "checkpoints": False,
        "pass_session_id": False,
        "max_turns": None,
        "ignore_rules": False,
        "ignore_user_config": False,
        "safe_mode": False,
        "compact": False,
        "source": None,
        "yolo": False,
        "accept_hooks": False,
    }
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def main_mod(monkeypatch):
    import hermes_cli.main as mod

    monkeypatch.setattr(mod, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr(mod, "_sync_bundled_skills_for_startup", lambda: None)
    monkeypatch.setattr(mod, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(mod, "_resolve_session_by_name_or_id", lambda val: val)
    monkeypatch.setattr(mod, "_oneshot_cleanup_done", False)
    return mod


@pytest.fixture
def fake_cli(monkeypatch):
    captured = {}

    def fake_cli_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "cli", types.SimpleNamespace(main=fake_cli_main))
    return captured



def _set_startup_config(
    monkeypatch, *, provider="custom", default="safe-model", base_url="", ack=None
):
    model = {"provider": provider, "default": default}
    if base_url:
        model["base_url"] = base_url
    config = {"model": model}
    if ack is not None:
        config["security"] = {
            "allow_data_training_tiers_noninteractive": ack,
        }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)






def test_cmd_chat_allows_acknowledged_data_training_tier_noninteractively(
    main_mod, fake_cli, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch, ack=True)

    main_mod.cmd_chat(
        _chat_args(model="muse-spark-1.2-contributor", provider="custom")
    )

    assert fake_cli["model"] == "muse-spark-1.2-contributor"
    err = capsys.readouterr().err
    assert "TRAINS ON YOUR DATA" in err
    assert "security.allow_data_training_tiers_noninteractive" in err


def test_cmd_chat_rejects_unacknowledged_data_training_tier_with_opt_in_hint(
    main_mod, fake_cli, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not fake_cli
    err = capsys.readouterr().err
    assert "TRAINS ON YOUR DATA" in err
    assert "security.allow_data_training_tiers_noninteractive" in err




def test_data_training_acknowledgement_requires_literal_true(
    main_mod, fake_cli, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch, ack="true")

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not fake_cli


def test_data_training_acknowledgement_does_not_skip_interactive_confirmation(
    main_mod, fake_cli, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    _set_startup_config(monkeypatch, ack=True)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not fake_cli
    assert "Model override cancelled" in capsys.readouterr().err










def test_cmd_chat_allows_noninteractive_safe_codex_startup_override(
    main_mod, fake_cli, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"model": {"provider": "openai-codex", "default": "gpt-5.5"}},
    )

    main_mod.cmd_chat(_chat_args(model="gpt-5.5", provider="openai-codex"))

    assert fake_cli["model"] == "gpt-5.5"
    assert fake_cli["provider"] == "openai-codex"


