from __future__ import annotations

from typer.testing import CliRunner

from hc.commands.config_cmd import apply_config_set
from hc.config import Config


def test_apply_config_set_core_port() -> None:
    cfg = Config()
    apply_config_set(cfg, "core.port", "9090")
    assert cfg.core.port == 9090


def test_apply_config_set_unknown_key() -> None:
    cfg = Config()
    try:
        apply_config_set(cfg, "nope.key", "x")
    except ValueError as e:
        assert "неизвестный ключ" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_config_show_and_set_cli(isolated_home) -> None:
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["config", "set", "core.port", "9091"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["config", "show"])
    assert r.exit_code == 0
    assert "9091" in r.output
    assert "***" not in r.output or "token" in r.output.lower()
