from __future__ import annotations

from pathlib import Path

import typer

import hc.repl as repl


def _typer_group() -> typer.Typer:
    app = typer.Typer()

    @app.command("noop")
    def noop() -> None:
        return

    @app.command("ping")
    def ping() -> None:
        return

    return app


def test_run_repl_help_prints_command_list(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    prompts = iter(["help", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)

    hist = tmp_path / "history.txt"
    monkeypatch.setattr(repl, "HISTORY_PATH", hist)

    repl.run_repl(_typer_group())
    out = capsys.readouterr().out
    assert "Команды:" in out
    assert "status" in out  # из базового списка команд REPL


def test_run_repl_use_sets_context(monkeypatch, tmp_path: Path, isolated_home) -> None:
    prompts = iter(["use core", "exit"])
    last_session: dict[str, object] = {}

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""
            last_session["obj"] = self

        def prompt(self) -> str:
            # Важно: prompt в REPL становится `hc core> ` (с пробелом), а не `core>`,
            # поэтому нельзя ориентироваться на суффикс — иначе получаем бесконечный цикл.
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    app = _typer_group()

    repl.run_repl(app)
    sess = last_session.get("obj")
    assert isinstance(sess, _FakePromptSession)
    # После `use core` REPL переключает prompt на контекст группы.
    assert str(getattr(sess, "message", "")).startswith("hc core>")


def test_run_repl_history_tail(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    hist = tmp_path / "h.txt"
    hist.write_text("a\nb\nc\n", encoding="utf-8")

    monkeypatch.setattr(repl, "HISTORY_PATH", hist)

    prompts = iter(["history 2", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)

    repl.run_repl(_typer_group())
    out = capsys.readouterr().out
    assert "b" in out
    assert "c" in out
    assert out.rstrip("\n").endswith("b\nc")


def test_run_repl_batch_and_stops_on_failed_and(monkeypatch, tmp_path: Path, isolated_home) -> None:
    calls: list[str] = []

    app = typer.Typer()

    @app.command("ok")
    def ok() -> None:
        calls.append("ok")

    @app.command("bad")
    def bad() -> None:
        raise typer.BadParameter("nope")

    @app.command("noop")
    def noop() -> None:
        calls.append("noop")

    prompts = iter(["ok && bad && noop", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    repl.run_repl(app)
    assert calls == ["ok"]
