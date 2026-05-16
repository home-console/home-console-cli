from __future__ import annotations

from typer.testing import CliRunner


def test_nav_root_shows_deploy_section() -> None:
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["nav"])
    assert r.exit_code == 0, r.output
    out = r.output.lower()
    assert "доступные разделы" in out
    assert "deploy" in out


def test_nav_deploy_shows_core_section() -> None:
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["nav", "deploy"])
    assert r.exit_code == 0, r.output
    out = r.output.lower()
    assert "core" in out
    assert "провалиться глубже" in out


def test_nav_env_shows_rebuild() -> None:
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["nav", "env"])
    assert r.exit_code == 0, r.output
    out = r.output.lower()
    assert "rebuild" in out
    assert "stats" in out


def test_nav_unknown_section_returns_2() -> None:
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["nav", "unknown"])
    assert r.exit_code == 2, r.output
    assert "неизвестный раздел" in r.output.lower()
