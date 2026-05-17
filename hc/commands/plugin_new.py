"""
hc plugin new — scaffolding нового плагина.

Генерирует минимальную рабочую структуру:
  plugins/<name>/
    __init__.py
    plugin.py      ← класс <Name>Plugin(BasePlugin)
    plugin.json    ← манифест
    requirements.txt
"""
from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax


def _to_class_name(snake: str) -> str:
    """network_scanner → NetworkScannerPlugin"""
    return "".join(w.capitalize() for w in snake.split("_")) + "Plugin"


def _validate_name(name: str) -> str | None:
    """None = ok, str = сообщение об ошибке."""
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return "Имя должно быть snake_case: только a-z, 0-9, _ и начинаться с буквы."
    if len(name) < 2:
        return "Имя слишком короткое."
    return None


def _render_plugin_py(name: str, class_name: str, description: str, caps: list[str]) -> str:
    caps_repr = repr(caps)
    return f'''\
"""
{description}

Services:
  {name}.<your_service>

Events published:
  (none)
"""
from __future__ import annotations

from sdk.plugin_ext import BasePlugin, PluginMetadata


class {class_name}(BasePlugin):

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="{name}",
            version="0.1.0",
            description="{description}",
            capabilities_required={caps_repr},
        )

    async def on_load(self) -> None:
        """Регистрация сервисов и подписки на события."""
        await super().on_load()

        async def _example_handler(**kwargs):
            return {{"ok": True, "plugin": "{name}"}}

        await self.register_service("{name}.example", _example_handler)

    async def on_start(self) -> None:
        """Запуск фоновых задач."""
        await super().on_start()

    async def on_stop(self) -> None:
        """Остановка."""
        await super().on_stop()

    async def on_unload(self) -> None:
        """Cleanup: отменить задачи, снять регистрации."""
        await super().on_unload()
        await self.unregister_service("{name}.example")
'''


def _render_init_py(name: str, class_name: str, description: str) -> str:
    return f'''\
"""
{description}
"""
from .plugin import {class_name}

__all__ = ["{class_name}"]
'''


def _render_plugin_json(name: str, class_name: str, description: str, author: str) -> str:
    import json
    manifest = {
        "class_path": f"plugins.{name}.plugin.{class_name}",
        "name": name,
        "version": "0.1.0",
        "description": description,
        "author": author,
        "dependencies": [],
        "provides_events": [],
        "is_integration": False,
    }
    return json.dumps(manifest, ensure_ascii=False, indent=4) + "\n"


def _find_plugins_dir(console: Console) -> Path | None:
    """Найти plugins/ рядом с core-runtime-service."""
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        candidate = p / "plugins"
        core = p / "core-runtime-service"
        if candidate.is_dir() and core.is_dir():
            return candidate
    return None


def register_new(plugin_app: typer.Typer) -> None:
    """Регистрирует команду `new` в переданном plugin_app."""

    @plugin_app.command("new")
    def new(
        name: str | None = typer.Argument(None, help="Имя плагина (snake_case)"),
        description: str = typer.Option("", "--description", "-d", help="Краткое описание"),
        author: str = typer.Option("", "--author", "-a", help="Автор"),
        caps: list[str] = typer.Option(
            [], "--capability", "-c",
            help="Capabilities required (можно повторять: -c oauth:yandex -c yandex:session_cookies)",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o",
            help="Директория для создания плагина (дефолт: plugins/ в монорепо)",
        ),
        force: bool = typer.Option(False, "--force", help="Перезаписать если директория существует"),
    ) -> None:
        """Создать новый плагин из шаблона.

        Генерирует plugin.py, __init__.py, plugin.json, requirements.txt.

        Примеры:
          hc plugin new my_sensor
          hc plugin new my_sensor -d "Датчик температуры" -a "Me" -c oauth:yandex
        """
        console = Console()

        # --- Имя ---
        if name is None:
            name = typer.prompt("Имя плагина (snake_case)").strip()

        err = _validate_name(name)
        if err:
            console.print(f"[red]Ошибка:[/red] {err}")
            raise typer.Exit(code=1)

        class_name = _to_class_name(name)

        # --- Description ---
        if not description:
            description = typer.prompt(
                "Описание", default=f"Плагин {name}"
            ).strip()

        # --- Author ---
        if not author:
            author = typer.prompt("Автор", default="Home Console").strip()

        # --- Output dir ---
        if output is None:
            plugins_dir = _find_plugins_dir(console)
            if plugins_dir is None:
                console.print(
                    "[yellow]Не найдена plugins/ рядом с core-runtime-service.[/yellow]\n"
                    "Укажи путь явно: [bold]hc plugin new --output ./plugins[/bold]"
                )
                raise typer.Exit(code=1)
            output = plugins_dir

        plugin_dir = output / name

        if plugin_dir.exists() and not force:
            console.print(
                f"[red]Ошибка:[/red] директория уже существует: {plugin_dir}\n"
                "Используй [bold]--force[/bold] чтобы перезаписать."
            )
            raise typer.Exit(code=1)

        # --- Генерация ---
        plugin_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "plugin.py":        _render_plugin_py(name, class_name, description, list(caps)),
            "__init__.py":      _render_init_py(name, class_name, description),
            "plugin.json":      _render_plugin_json(name, class_name, description, author),
            "requirements.txt": "",
        }

        for filename, content in files.items():
            (plugin_dir / filename).write_text(content, encoding="utf-8")

        # --- Вывод ---
        console.print(
            Panel(
                "\n".join(f"  [green]✓[/green] {plugin_dir / f}" for f in files),
                title=f"[bold]Плагин [cyan]{name}[/cyan] создан[/bold]",
                expand=False,
            )
        )

        console.print(Syntax(
            _render_plugin_py(name, class_name, description, list(caps)),
            "python", theme="monokai", line_numbers=False,
        ))

        console.print(
            f"\n[dim]Следующие шаги:[/dim]\n"
            f"  1. Отредактируй [bold]{plugin_dir}/plugin.py[/bold]\n"
            f"  2. Добавь зависимости в [bold]requirements.txt[/bold]\n"
            f"  3. Задеплой: [bold]hc plugin sync[/bold]\n"
            f"  4. Подключи: [bold]hc plugin start {name}[/bold]\n"
        )
