from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console

from hc.core_ops import require_docker
from hc.commands._compose_helpers import read_env_kv
from hc.commands.recovery import RecoveryContext


def build_app(ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Операции над CoreRuntime через docker compose",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("status")
    def status() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["ps", "core-runtime"])

    @app.command("up")
    def up(
        no_ui: bool = typer.Option(True, "--no-ui/--with-ui", help="Поднимать без UI (по умолчанию)"),
        build: bool = typer.Option(False, "--build", help="Пересобрать образ core-runtime перед запуском"),
    ) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        env = read_env_kv(src.path / ".env")
        vault_type = (env.get("RUNTIME_VAULT_STORAGE_TYPE") or "").strip().lower()
        wants_pg = vault_type in {"postgres", "postgresql"}

        build_arg = ["--build"] if build else []
        if no_ui:
            services = ["postgres", "core-runtime"] if wants_pg else ["core-runtime"]
            ctx.run_compose(console, src, ["up", *build_arg, "-d", *services])
            console.print("[green]✓[/green] CoreRuntime поднят (без UI).")
        else:
            ctx.run_compose(console, src, ["up", *build_arg, "-d"])
            console.print("[green]✓[/green] CoreRuntime + UI подняты.")

    @app.command("down")
    def down(
        volumes: bool = typer.Option(False, "-v", "--volumes"),
        yes: bool = typer.Option(False, "--yes", help="Не спрашивать подтверждение"),
    ) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        if volumes and not yes:
            if not typer.confirm(
                "Это удалит volumes (данные) для CoreRuntime. Точно сделать `down -v`?",
                default=False,
            ):
                raise typer.Exit(code=0)
        args = ["down"]
        if volumes:
            args.append("-v")
        ctx.run_compose(console, src, args)
        console.print("[green]✓[/green] CoreRuntime остановлен.")

    @app.command("logs")
    def logs(
        follow: bool = typer.Option(False, "-f", "--follow"),
        tail: int = typer.Option(200, "--tail"),
    ) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("-f")
        args.append("core-runtime")
        ctx.run_compose(console, src, args)

    @app.command("repair")
    def repair(
        with_ui: bool = typer.Option(False, "--with-ui", help="Поднять вместе с UI (caddy)"),
        backup_dir: Path = typer.Option(Path("./backups"), "--backup-dir", help="Куда складывать бэкапы"),
        build: bool = typer.Option(True, "--build/--no-build", help="Пересобрать образ core-runtime (по умолчанию да)"),
    ) -> None:
        """Авто-repair: backup → down -v → up → health."""
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)

        snap = ctx.recovery_backup(console, src, backup_dir)
        console.print(f"[green]✓[/green] Backup: {snap}")

        env = read_env_kv(src.path / ".env")
        vault_type = (env.get("RUNTIME_VAULT_STORAGE_TYPE") or "").strip().lower()
        wants_pg = vault_type in {"postgres", "postgresql"}

        ctx.run_compose(console, src, ["down", "-v"])

        if wants_pg:
            # Поднимаем postgres и ждём readiness
            ctx.run_compose(console, src, ["up", "-d", "postgres"])
            for _ in range(60):
                try:
                    ctx.run_compose(console, src, ["exec", "-T", "postgres", "pg_isready", "-U", "postgres"])
                    break
                except typer.Exit:
                    subprocess.run(["sleep", "1"], check=False)  # noqa: S603

        build_arg = ["--build"] if build else []
        if with_ui:
            ctx.run_compose(console, src, ["up", *build_arg, "-d"])
        else:
            services = ["postgres", "core-runtime"] if wants_pg else ["core-runtime"]
            ctx.run_compose(console, src, ["up", *build_arg, "-d", *services])

        ok = False
        for _ in range(120):
            try:
                running = ctx.compose_capture(console, src, ["ps", "--status", "running", "core-runtime"]).strip()
            except typer.Exit:
                running = ""
            if not running or len([ln for ln in running.splitlines() if ln.strip()]) < 2:
                subprocess.run(["sleep", "1"], check=False)  # noqa: S603
                continue
            try:
                res = ctx.compose_capture(
                    console,
                    src,
                    [
                        "exec",
                        "-T",
                        "core-runtime",
                        "sh",
                        "-lc",
                        "curl -fsS http://localhost:8000/monitor/health >/dev/null && echo ok || echo no",
                    ],
                ).strip()
                if res == "ok":
                    ok = True
                    break
            except typer.Exit:
                pass
            subprocess.run(["sleep", "1"], check=False)  # noqa: S603
        if not ok:
            console.print("[red]Ошибка:[/red] core не вышел в healthy за ~120s.")
            console.print("Посмотри логи: `hc recovery core logs -f`")
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] core repair ok (healthcheck passed)")

    return app

