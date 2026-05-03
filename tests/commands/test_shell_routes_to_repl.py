from __future__ import annotations

import typer

import hc.shell as shell


def test_run_shell_calls_run_repl(monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_run_repl(app):  # noqa: ANN001
        called["app"] = app

    monkeypatch.setattr("hc.repl.run_repl", fake_run_repl)

    app = typer.Typer()

    shell.run_shell(app)

    assert called.get("app") is app
