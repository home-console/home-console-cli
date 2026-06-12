"""
hc workspace — управление привязкой CLI к локальному монорепо разработчика.

Когда workspace задан, hc env up / hc core up / hc deploy и т.д. используют
исходники из <workspace>/core-runtime-service и <workspace>/platform-home-console
вместо managed-клонов в ~/.local/share/hc.

Это позволяет редактировать код в IDE и видеть изменения в контейнерах
напрямую (через live volume mount + watchfiles). Не нужен второй клон,
не нужен ручной sync.

Источники workspace (по приоритету):
  1. $HC_WORKSPACE
  2. cwd + parents (магия "cd в монорепо")
  3. workspace.path в ~/.config/hc/config.toml (то, что ставит `hc workspace set`)

Команды:
  hc workspace status      — какой workspace активен и почему
  hc workspace set [path]  — записать в конфиг (по умолчанию = текущий cwd)
  hc workspace unset       — убрать из конфига
  hc workspace use         — алиас для set (без аргумента берёт cwd)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from hc.config import Config
from hc.core_source import (
    _looks_like_monorepo,
    detect_workspace_root,
    resolve_workspace_root,
)


def _git_summary(repo: Path) -> str | None:
    """Краткая сводка состояния git-репо: branch, dirty, ahead/behind.

    Возвращает строку вида `master * (3↑1↓)` или None, если не git-репо
    или git недоступен.
    """
    if not (repo / ".git").exists():
        return None
    try:
        branch = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip() or "?"

        dirty = bool(
            subprocess.run(  # noqa: S603
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=3, check=False,
            ).stdout.strip()
        )

        # ahead/behind upstream
        ab = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
             "HEAD...@{u}"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        ahead = behind = 0
        if ab.returncode == 0 and ab.stdout.strip():
            parts = ab.stdout.strip().split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])

        flags = []
        if dirty:
            flags.append("*")
        if ahead or behind:
            counts = []
            if ahead:
                counts.append(f"{ahead}↑")
            if behind:
                counts.append(f"{behind}↓")
            flags.append(f"({''.join(counts)})")
        suffix = (" " + " ".join(flags)) if flags else ""
        return f"{branch}{suffix}"
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _describe_source(path: Path | None) -> tuple[str, str]:
    """Вернуть (источник, путь) для отображения."""
    if path is None:
        return ("не найден", "—")
    env = os.environ.get("HC_WORKSPACE", "").strip()
    if env and Path(env).expanduser().resolve() == path:
        return ("HC_WORKSPACE", str(path))
    cwd_root = _scan_from_cwd()
    if cwd_root and cwd_root == path:
        return ("cwd", str(path))
    cfg_path = (Config.load().workspace.path or "").strip()
    if cfg_path and Path(cfg_path).expanduser().resolve() == path:
        return ("config.toml", str(path))
    return ("auto", str(path))


def _scan_from_cwd() -> Path | None:
    """Тот же подъём от cwd, что в core_source, но без чтения env/config."""
    try:
        start = Path.cwd().resolve()
    except (OSError, RuntimeError):
        return None
    for p in [start, *start.parents]:
        if _looks_like_monorepo(p):
            return p
    return None


def register(app: typer.Typer) -> None:
    ws_app = typer.Typer(
        help="Привязка CLI к локальному монорепо разработчика",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @ws_app.command("status")
    def status_cmd() -> None:
        """Показать активный workspace и его источник."""
        console = Console()
        root = resolve_workspace_root()
        source, path = _describe_source(root)

        lines = [
            f"[bold]Активный workspace:[/bold] {path}",
            f"[dim]Источник:[/dim] {source}",
        ]
        if root:
            core = root / "core-runtime-service"
            platform = root / "platform-home-console"
            lines.append("")
            lines.append("Компоненты:")
            for label, p in (
                ("core-runtime-service", core),
                ("platform-home-console", platform),
            ):
                if not p.is_dir():
                    icon = "[red]✗[/red]" if label == "core-runtime-service" else "[yellow]—[/yellow]"
                    lines.append(f"  {icon} {label}: {p}")
                    continue
                git_info = _git_summary(p)
                git_suffix = f"  [dim][{git_info}][/dim]" if git_info else "  [dim][не git][/dim]"
                lines.append(f"  [green]✓[/green] {label}: {p}{git_suffix}")
            lines.append("")
            lines.append("[dim]Легенда git:[/dim] [dim]master * (3↑1↓) — branch, dirty, ahead/behind upstream[/dim]")
        else:
            lines.append("")
            lines.append("[yellow]Workspace не задан.[/yellow] CLI работает с")
            lines.append("managed-клоном в ~/.local/share/hc/core-runtime-service.")
            lines.append("")
            lines.append("Как привязать локальный монорепо:")
            lines.append("  1. cd в корень монорепо и запусти команду — авто-детект")
            lines.append("  2. либо `hc workspace set [path]` — запишет в конфиг")
            lines.append("  3. либо `export HC_WORKSPACE=/path/to/monorepo`")

        env = os.environ.get("HC_WORKSPACE", "").strip()
        cfg_path = (Config.load().workspace.path or "").strip()
        if env or cfg_path:
            lines.append("")
            lines.append("Известные привязки:")
            if env:
                lines.append(f"  HC_WORKSPACE={env}")
            if cfg_path:
                lines.append(f"  config.toml: workspace.path = {cfg_path}")

        console.print(Panel.fit("\n".join(lines), title="hc workspace"))

    @ws_app.command("set")
    def set_cmd(
        path: str = typer.Argument(
            None,
            help="Путь к корню монорепо (по умолчанию: текущий cwd)",
        ),
    ) -> None:
        """Записать workspace.path в ~/.config/hc/config.toml."""
        console = Console()
        target = Path(path).expanduser().resolve() if path else Path.cwd().resolve()

        if not target.is_dir():
            console.print(f"[red]Ошибка:[/red] не директория: {target}")
            raise typer.Exit(code=1)

        if not _looks_like_monorepo(target):
            console.print(
                f"[red]Ошибка:[/red] {target} не похоже на монорепо HomeConsole.\n"
                f"  Ожидается папка с core-runtime-service/ и хотя бы одним из:\n"
                f"  home-console-cli/, packages/, platform-home-console/"
            )
            raise typer.Exit(code=1)

        cfg = Config.load()
        cfg.workspace.path = str(target)
        cfg.save()
        console.print(f"[green]✓[/green] workspace.path → {target}")
        console.print(
            "[dim]Теперь hc env/core/deploy будут использовать исходники из этого пути.[/dim]"
        )

    ws_app.command("use")(set_cmd)  # алиас

    @ws_app.command("unset")
    def unset_cmd() -> None:
        """Убрать workspace.path из конфига (вернуться к managed-клону)."""
        console = Console()
        cfg = Config.load()
        if not cfg.workspace.path:
            console.print("[yellow]workspace.path уже не задан в config.toml.[/yellow]")
            return
        prev = cfg.workspace.path
        cfg.workspace.path = ""
        cfg.save()
        console.print(f"[green]✓[/green] workspace.path удалён (был: {prev})")

    app.add_typer(ws_app, name="workspace")
