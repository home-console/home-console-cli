from __future__ import annotations

import typer

import hc.repl as repl


def test_run_repl_invokes_app_for_simple_command(monkeypatch, isolated_home) -> None:
    calls: list[list[str]] = []

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            # one command then exit
            if not hasattr(self, "_n"):
                self._n = 0
            self._n += 1
            if self._n == 1:
                return "status"
            raise EOFError

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)

    class _FakeFileHistory:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            pass

    monkeypatch.setattr(repl, "FileHistory", _FakeFileHistory)

    app = typer.Typer()

    @app.command("status")
    def status() -> None:
        calls.append(["status"])

    @app.command("noop")
    def noop() -> None:
        return

    repl.run_repl(app)
    assert calls == [["status"]]


def test_run_repl_strips_leading_hc_prefix(monkeypatch, isolated_home) -> None:
    calls: list[list[str]] = []

    class _FakePromptSession:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            self.message = ""

        def prompt(self) -> str:
            if not hasattr(self, "_n"):
                self._n = 0
            self._n += 1
            if self._n == 1:
                return "hc ping --host localhost --port 1"
            raise EOFError

    monkeypatch.setattr(repl, "PromptSession", _FakePromptSession)
    monkeypatch.setattr(repl, "FileHistory", lambda *a, **k: None)

    app = typer.Typer()

    @app.command("ping")
    def ping(host: str = typer.Option("x"), port: int = typer.Option(1)) -> None:
        calls.append([host, str(port)])

    @app.command("noop")
    def noop() -> None:
        return

    repl.run_repl(app)
    assert calls == [["localhost", "1"]]
