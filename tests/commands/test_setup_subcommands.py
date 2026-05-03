from __future__ import annotations

from typer.testing import CliRunner

from hc.main import app


def test_setup_logs_help_does_not_run_wizard() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "logs", "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Host" not in result.output


def test_setup_status_does_not_run_wizard() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "status"])
    assert result.exit_code == 0
    assert "Host" not in result.output

