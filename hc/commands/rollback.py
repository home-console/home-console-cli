from __future__ import annotations

import typer
from rich.console import Console

from hc.config import Config, normalize_deploy_core_mode
from hc.core_ops import require_docker
from hc.core_source import VALID_MODES
from hc.commands.deploy import _do_rollout


def register(app: typer.Typer) -> None:
    @app.command("rollback")
    def rollback(
        tag: str | None = typer.Argument(None, help="Тег для отката (по умолчанию: последний задеплоенный из config)"),
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev | dev-reload | dev-image | prod"),
        db: str | None = typer.Option(None, "--db", help="Vault DB backend: sqlite|postgres"),
        cache: str | None = typer.Option(None, "--cache", help="Cache backend: memory|redis"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
    ) -> None:
        """Откатить core-runtime на тег. Без тега — берёт последний задеплоенный из config."""
        console = Console()
        require_docker(console)
        cfg = Config.load()
        resolved_image = (image or cfg.deploy.core_image).strip()
        resolved_mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
        if resolved_mode not in VALID_MODES:
            console.print(
                f"[red]Ошибка:[/red] --mode {resolved_mode!r} недопустим. "
                f"Допустимые: {' | '.join(sorted(VALID_MODES))}"
            )
            raise typer.Exit(code=2)
        resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        resolved_path = path if path is not None else (cfg.deploy.path or None)
        resolved_tag = tag or cfg.deploy.last_tag
        if not resolved_tag:
            console.print("[red]Ошибка:[/red] тег не указан и нет сохранённого last_tag.")
            console.print("Укажи явно: [bold]hc rollback v0.1.0[/bold]")
            raise typer.Exit(code=1)
        console.print(f"[yellow]→[/yellow] Rollback: [bold]{resolved_image}:{resolved_tag}[/bold]")
        _do_rollout(
            console,
            image=resolved_image,
            tag=resolved_tag,
            ssh=resolved_ssh,
            path=resolved_path,
            mode=resolved_mode,
            db=db,
            cache=cache,
            wait=wait,
            timeout=timeout,
            interval=interval,
            health_url=health_url,
            pull=True,
            rollback_on_failure=False,
            save_on_success=True,
        )
        console.print(f"[green]✓[/green] Rollback → {resolved_image}:{resolved_tag} done")
