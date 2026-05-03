from __future__ import annotations

from pathlib import Path

import typer

import hc.repl as repl


def test_prompt_with_and_without_group() -> None:
    assert repl._prompt(None) == "hc> "
    assert repl._prompt("hc core") == "hc core> "


def test_run_repl_use_unknown_group(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    prompts = iter(["use not-a-real-group", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    repl.run_repl(typer.Typer())
    out = capsys.readouterr().out
    assert "неизвестный контекст" in out
    assert "not-a-real-group" in out


def test_run_repl_strips_leading_hc_prefix(monkeypatch, tmp_path: Path, isolated_home) -> None:
    seen: list[list[str]] = []

    app = typer.Typer()

    @app.command("alpha")
    def alpha() -> None:
        return

    real_call = typer.Typer.__call__

    def _wrap(self, *a, **k):  # noqa: ANN001
        if self is app and "args" in k:
            seen.append(list(k["args"]))
        return real_call(self, *a, **k)

    monkeypatch.setattr(typer.Typer, "__call__", _wrap)

    prompts = iter(["hc alpha", "exit"])

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    repl.run_repl(app)
    assert ["alpha"] in seen


def test_run_repl_back_clears_group_context(monkeypatch, tmp_path: Path, isolated_home) -> None:
    prompts = iter(["use core", "back", "exit"])
    last: dict[str, object] = {}

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""
            last["session"] = self

        def prompt(self) -> str:
            return next(prompts)

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    app = typer.Typer()

    @app.command("noop")
    def noop() -> None:
        return

    repl.run_repl(app)
    sess = last.get("session")
    assert sess is not None
    assert getattr(sess, "message", "") == "hc> "


def test_run_repl_eof_on_prompt_exits(monkeypatch, tmp_path: Path, capsys, isolated_home) -> None:
    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            raise EOFError

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)
    monkeypatch.setattr(repl, "HISTORY_PATH", tmp_path / "history.txt")

    repl.run_repl(typer.Typer())
    out = capsys.readouterr().out
    assert "Type 'help'" in out
