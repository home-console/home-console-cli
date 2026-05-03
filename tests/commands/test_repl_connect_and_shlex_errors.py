from __future__ import annotations

from pathlib import Path

import typer

import hc.repl as repl


def test_run_repl_shlex_error_prints_message(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    prompts = iter(['bad "', "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    app = typer.Typer()

    @app.command("noop")
    def noop() -> None:
        return

    @app.command("ping")
    def ping() -> None:
        return

    repl.run_repl(app)
    out = capsys.readouterr().out
    assert "Ошибка:" in out


def test_run_repl_connect_missing_host_shows_hint(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    prompts = iter(["connect", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    app = typer.Typer()

    @app.command("connect")
    def connect(host: str = typer.Argument(..., help="HOST")) -> None:
        _ = host

    @app.command("noop")
    def noop() -> None:
        return

    repl.run_repl(app)
    out = capsys.readouterr().out
    assert "не указан адрес" in out.lower()
