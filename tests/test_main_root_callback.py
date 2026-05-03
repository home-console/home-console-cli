from __future__ import annotations

from typer.testing import CliRunner


def test_root_without_subcommand_non_tty_exits_with_message(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, [])
    assert r.exit_code == 1
    out = r.output.lower()
    assert "команда" in out or "укажи" in out
