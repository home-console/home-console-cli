from __future__ import annotations

import typer

def run_shell(app: typer.Typer) -> None:
    from hc.repl import run_repl

    run_repl(app)

