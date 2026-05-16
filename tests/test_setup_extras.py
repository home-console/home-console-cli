from __future__ import annotations

from hc.hints import SETUP_ENV_HINT


def test_setup_env_hint_text() -> None:
    assert "hc env up" in SETUP_ENV_HINT
    assert "hc core up" in SETUP_ENV_HINT


def test_setup_wizard_imports_extras() -> None:
    from hc.commands import setup_wizard

    assert callable(setup_wizard._offer_shell_completion)
    assert callable(setup_wizard._maybe_upgrade_cli)
