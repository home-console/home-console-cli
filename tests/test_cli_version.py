from __future__ import annotations

from typer.testing import CliRunner


def test_version_prints_current(monkeypatch) -> None:
    from hc.main import app

    monkeypatch.setattr("hc.commands.cli_version.get_update_notification", lambda _c: None)
    runner = CliRunner()
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "homeconsole-cli" in r.output


def test_version_shows_banner_when_update_available(monkeypatch) -> None:
    from hc.main import app

    monkeypatch.setattr("hc.update_check.get_update_notification", lambda _c: "99.0.0")
    runner = CliRunner()
    r = runner.invoke(app, ["version"])
    assert "99.0.0" in r.output


def test_upgrade_check_exits_when_update_available(monkeypatch) -> None:
    from hc.main import app

    monkeypatch.setattr("hc.commands.cli_version.__version__", "0.0.1")
    monkeypatch.setattr("hc.commands.cli_version.get_update_notification", lambda _c: "99.0.0")
    runner = CliRunner()
    r = runner.invoke(app, ["upgrade", "--check"])
    assert r.exit_code == 1
    assert "99.0.0" in r.output


def test_upgrade_already_latest(monkeypatch) -> None:
    from hc import __version__
    from hc.main import app

    monkeypatch.setattr("hc.commands.cli_version.get_update_notification", lambda _c: None)
    monkeypatch.setattr("hc.commands.cli_version._fetch_latest", lambda: __version__)
    runner = CliRunner()
    r = runner.invoke(app, ["upgrade"])
    assert r.exit_code == 0
    assert "последняя" in r.output.lower()


def test_root_invokes_update_banner_on_status(monkeypatch) -> None:
    from hc.main import app

    calls: list[str] = []

    def _banner(console, current: str) -> bool:  # noqa: ANN001
        calls.append(current)
        return False

    monkeypatch.setattr("hc.main.print_update_banner", _banner)
    monkeypatch.setattr(
        "hc.commands.status.require_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no client")),
    )
    runner = CliRunner()
    runner.invoke(app, ["status"])
    assert calls, "print_update_banner should run before subcommands"
