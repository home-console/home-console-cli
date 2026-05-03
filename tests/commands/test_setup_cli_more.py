from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_setup_logs_missing_file_exits_1(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing-setup.log"
    monkeypatch.setattr("hc.commands.setup.SETUP_LOG_PATH", missing)

    from hc.main import app

    r = runner.invoke(app, ["setup", "logs"])
    assert r.exit_code == 1
    assert "лог" in r.output.lower()


def test_setup_logs_prints_tail(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "setup.log"
    log.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    monkeypatch.setattr("hc.commands.setup.SETUP_LOG_PATH", log)

    from hc.main import app

    r = runner.invoke(app, ["setup", "logs", "--lines", "2"])
    assert r.exit_code == 0
    assert "beta" in r.output
    assert "gamma" in r.output
    assert "alpha" not in r.output


def test_setup_status_no_background_process(runner: CliRunner, isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("hc.commands.setup.SetupProcess.load", lambda: None)

    from hc.main import app

    r = runner.invoke(app, ["setup", "status"])
    assert r.exit_code == 0
    assert "не запущен" in r.output.lower() or "фон" in r.output.lower()


def test_setup_status_running_shows_pid(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    class _SP:
        pid = 777888
        log_path = tmp_path / "bg.log"

        def is_running(self) -> bool:
            return True

    monkeypatch.setattr("hc.commands.setup.SetupProcess.load", lambda: _SP())

    from hc.main import app

    r = runner.invoke(app, ["setup", "status"])
    assert r.exit_code == 0
    assert "777888" in r.output
    assert "работает" in r.output.lower() or "pid" in r.output.lower()


def test_setup_status_finished_shows_message(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    class _SP:
        pid = 555
        log_path = tmp_path / "bg.log"

        def is_running(self) -> bool:
            return False

    monkeypatch.setattr("hc.commands.setup.SetupProcess.load", lambda: _SP())

    from hc.main import app

    r = runner.invoke(app, ["setup", "status"])
    assert r.exit_code == 0
    assert "555" in r.output
    assert "заверш" in r.output.lower() or "pid" in r.output.lower()
