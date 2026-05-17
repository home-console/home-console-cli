from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel

from hc.config import Config, normalize_deploy_core_mode
from hc.constants import CONFIG_PATH
from hc.core_source import VALID_MODES


def _mask_secret(value: str) -> str:
    return "***" if value.strip() else "(empty)"


def _format_config(cfg: Config) -> str:
    return (
        f"[core]\n"
        f"  host          = {cfg.core.host}\n"
        f"  port          = {cfg.core.port}\n"
        f"  token         = {_mask_secret(cfg.core.token)}\n"
        f"  refresh_token = {_mask_secret(cfg.core.refresh_token)}\n"
        f"  auth          = {cfg.core.auth}\n"
        f"  verify_ssl    = {cfg.core.verify_ssl}\n"
        f"\n[display]\n"
        f"  color         = {cfg.display.color}\n"
        f"  emoji         = {cfg.display.emoji}\n"
        f"\n[recovery]\n"
        f"  mode          = {cfg.recovery.mode}\n"
        f"\n[deploy]\n"
        f"  core_image    = {cfg.deploy.core_image}\n"
        f"  core_mode     = {cfg.deploy.core_mode}\n"
        f"  ssh           = {cfg.deploy.ssh or '(empty)'}\n"
        f"  path          = {cfg.deploy.path or '(empty)'}\n"
        f"\nфайл: {CONFIG_PATH}\n"
    )


_CONFIG_SETTERS: dict[str, tuple[str, Callable[[Config, Any], None], type]] = {
    "core.host": ("core", lambda c, v: setattr(c.core, "host", str(v)), str),
    "core.port": ("core", lambda c, v: setattr(c.core, "port", int(v)), int),
    "core.token": ("core", lambda c, v: setattr(c.core, "token", str(v)), str),
    "core.refresh_token": (
        "core",
        lambda c, v: setattr(c.core, "refresh_token", str(v)),
        str,
    ),
    "core.auth": ("core", lambda c, v: setattr(c.core, "auth", str(v)), str),
    "core.verify_ssl": (
        "core",
        lambda c, v: setattr(c.core, "verify_ssl", _parse_bool(v)),
        bool,
    ),
    "display.color": ("display", lambda c, v: setattr(c.display, "color", _parse_bool(v)), bool),
    "display.emoji": ("display", lambda c, v: setattr(c.display, "emoji", _parse_bool(v)), bool),
    "recovery.mode": ("recovery", lambda c, v: setattr(c.recovery, "mode", str(v)), str),
    "deploy.core_image": (
        "deploy",
        lambda c, v: setattr(c.deploy, "core_image", str(v)),
        str,
    ),
    "deploy.core_mode": (
        "deploy",
        lambda c, v: setattr(c.deploy, "core_mode", normalize_deploy_core_mode(str(v))),
        str,
    ),
    "deploy.ssh": ("deploy", lambda c, v: setattr(c.deploy, "ssh", str(v)), str),
    "deploy.path": ("deploy", lambda c, v: setattr(c.deploy, "path", str(v)), str),
}


def _parse_bool(raw: object) -> bool:
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"ожидалось true/false, получено {raw!r}")


def _validate_after_set(cfg: Config, key: str) -> None:
    if key == "core.auth":
        auth = cfg.core.auth.strip().lower()
        if auth not in {"auto", "bearer", "api-key"}:
            raise ValueError("core.auth: допустимо auto | bearer | api-key")
    if key == "deploy.core_mode":
        if cfg.deploy.core_mode not in VALID_MODES:
            valid = " | ".join(sorted(VALID_MODES))
            raise ValueError(f"deploy.core_mode: допустимо {valid}")


def apply_config_set(cfg: Config, key: str, value: str) -> None:
    key = key.strip().lower()
    if key not in _CONFIG_SETTERS:
        keys = ", ".join(sorted(_CONFIG_SETTERS))
        raise ValueError(f"неизвестный ключ {key!r}. Допустимые: {keys}")
    _section, setter, typ = _CONFIG_SETTERS[key]
    try:
        if typ is bool:
            parsed: Any = _parse_bool(value)
        elif typ is int:
            parsed = int(value.strip())
        else:
            parsed = value
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key}: {e}") from e
    setter(cfg, parsed)
    _validate_after_set(cfg, key)


def open_config_editor(console: Console) -> None:
    if not CONFIG_PATH.exists():
        Config.load().save()
    editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
    if not editor:
        for cand in ("nvim", "vim", "nano", "micro"):
            if shutil.which(cand):
                editor = cand
                break
    if not editor:
        console.print("[red]Ошибка:[/red] не задан редактор. Укажи EDITOR или VISUAL.")
        console.print(f"[dim]Файл:[/dim] {CONFIG_PATH}")
        raise typer.Exit(code=2)
    cmd = [*shlex.split(editor), str(CONFIG_PATH)]
    proc = subprocess.run(cmd, check=False)  # noqa: S603
    if proc.returncode != 0:
        console.print(f"[yellow]Редактор завершился с кодом {proc.returncode}[/yellow]")
        raise typer.Exit(code=proc.returncode)
    console.print("[green]✓[/green] редактор закрыт")


def register(app: typer.Typer) -> None:
    cfg_app = typer.Typer(
        help="Конфигурация ~/.config/hc/config.toml",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @cfg_app.command("show")
    def config_show() -> None:
        """Показать текущий конфиг (секреты скрыты)."""
        console = Console()
        cfg = Config.load()
        console.print(Panel.fit(_format_config(cfg), title="hc config"))

    @cfg_app.command("set")
    def config_set(
        key: str = typer.Argument(..., help="Ключ, например core.port"),
        value: str = typer.Argument(..., help="Новое значение"),
    ) -> None:
        """Установить параметр конфига."""
        console = Console()
        cfg = Config.load()
        try:
            apply_config_set(cfg, key, value)
        except ValueError as e:
            console.print(f"[red]Ошибка:[/red] {e}")
            raise typer.Exit(code=2)
        cfg.save()
        console.print(f"[green]✓[/green] {key} = {value}")

    @cfg_app.command("edit")
    def config_edit() -> None:
        """Открыть config.toml в $EDITOR / $VISUAL. После сохранения — валидация."""
        console = Console()
        open_config_editor(console)
        # Валидируем что файл не сломан после редактирования
        try:
            cfg = Config.load()
            console.print(f"[green]✓[/green] Конфиг валиден: {cfg.core.host}:{cfg.core.port}")
        except Exception as e:
            console.print(f"[red]Ошибка: конфиг повреждён после редактирования:[/red] {e}")
            console.print(f"[dim]Исправь файл: {CONFIG_PATH}[/dim]")
            raise typer.Exit(code=1)

    app.add_typer(cfg_app, name="config")
