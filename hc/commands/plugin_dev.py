"""
hc plugin dev — dev-режим плагина с watch + auto-reload.

Следит за изменениями файлов в <path> и при изменении:
  1. Копирует изменённые файлы в Core (docker volume или native filesystem)
  2. Вызывает POST /api/v1/admin/plugins/<name>/reload

Режимы синхронизации:
  native — прямой shutil.copy2 в plugins/<name>/ рядом с core-runtime-service
  docker  — docker compose cp в контейнер (именованный том /app/plugins)
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from pathlib import Path

import anyio
import typer
from rich.console import Console


# ---------------------------------------------------------------------------
# File watching (polling — без watchdog, без доп. зависимостей)
# ---------------------------------------------------------------------------

def _collect_mtimes(src: Path) -> dict[Path, float]:
    """Рекурсивно собрать mtime всех файлов в src (кроме __pycache__)."""
    result: dict[Path, float] = {}
    for p in src.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"):
            try:
                result[p] = p.stat().st_mtime
            except OSError:
                pass
    return result


def _changed_files(old: dict[Path, float], new: dict[Path, float]) -> list[Path]:
    changed = []
    for p, mtime in new.items():
        if old.get(p) != mtime:
            changed.append(p)
    # Удалённые файлы тоже считаем изменением (нужен полный ресинк)
    deleted = set(old) - set(new)
    changed.extend(deleted)
    return changed


# ---------------------------------------------------------------------------
# Sync strategies
# ---------------------------------------------------------------------------

def _sync_native(
    src: Path,
    plugin_name: str,
    core_root: Path,
    changed: list[Path],
    *,
    console: Console,
    full: bool = False,
) -> None:
    """Скопировать файлы в plugins/<name>/ рядом с core-runtime-service."""
    dest_dir = core_root / "plugins" / plugin_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    files = list(src.rglob("*")) if full else changed
    copied = 0
    for f in files:
        if not f.is_file():
            continue
        if "__pycache__" in f.parts or f.name.endswith(".pyc"):
            continue
        rel = f.relative_to(src)
        dest = dest_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        if not full:
            console.print(f"  [cyan]↑[/cyan] {rel}")
        copied += 1
    if full:
        console.print(f"[dim]native sync: {copied} файлов → {dest_dir}[/dim]")


def _sync_docker(
    src: Path,
    plugin_name: str,
    core_root: Path,
    compose_file: str,
    changed: list[Path],
    *,
    console: Console,
    full: bool = False,
) -> None:
    """Копировать файлы в Docker volume через docker compose cp."""
    compose_part = f"docker compose -f {shlex.quote(compose_file)}"
    container_dir = f"/app/plugins/{plugin_name}"

    if full:
        # При первом запуске — полный cp всей директории
        cmd = f"{compose_part} cp {shlex.quote(str(src) + '/.')} core-runtime:{container_dir}/"
        p = subprocess.run(["sh", "-lc", cmd], cwd=str(core_root), check=False)  # noqa: S603
        if p.returncode != 0:
            console.print("[red]Ошибка: docker compose cp не удался.[/red]")
            return
        # Исправляем права
        chown = f"{compose_part} exec -T -u 0 core-runtime sh -lc 'chown -R nobody:nogroup {container_dir} || true'"
        subprocess.run(["sh", "-lc", chown], cwd=str(core_root), check=False)  # noqa: S603
        console.print(f"[dim]docker sync (full): {plugin_name} → {container_dir}[/dim]")
        return

    for f in changed:
        if not f.is_file():
            continue
        if "__pycache__" in f.parts or f.name.endswith(".pyc"):
            continue
        rel = f.relative_to(src)
        remote_path = f"{container_dir}/{rel}"
        # Создать папку если нужна
        if str(rel.parent) != ".":
            mkdir_cmd = f"{compose_part} exec -T -u 0 core-runtime sh -lc {shlex.quote('mkdir -p ' + str(Path(container_dir) / rel.parent))}"
            subprocess.run(["sh", "-lc", mkdir_cmd], cwd=str(core_root), check=False)  # noqa: S603
        cp_cmd = f"{compose_part} cp {shlex.quote(str(f))} core-runtime:{remote_path}"
        p = subprocess.run(["sh", "-lc", cp_cmd], cwd=str(core_root), check=False)  # noqa: S603
        if p.returncode == 0:
            console.print(f"  [cyan]↑[/cyan] {rel}")
        else:
            console.print(f"  [red]✗[/red] {rel} (ошибка cp)")


# ---------------------------------------------------------------------------
# Reload via API
# ---------------------------------------------------------------------------

def _api_reload(console: Console, plugin_name: str) -> bool:
    from hc.commands._client_helpers import require_client
    try:
        client = require_client(console, silent=True)
        result = anyio.run(client.reload_plugin, plugin_name)
        return result is not None
    except Exception as e:
        console.print(f"[red]reload API error: {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def register_dev(plugin_app: typer.Typer) -> None:

    @plugin_app.command("dev")
    def dev(
        src: Path = typer.Argument(
            ...,
            help="Путь к директории плагина (должна содержать plugin.json)",
        ),
        name: str | None = typer.Option(
            None, "--name", "-n",
            help="Имя плагина (дефолт: читается из plugin.json)",
        ),
        mode: str = typer.Option(
            "auto", "--mode",
            help="native | docker | auto (auto определяет по наличию Docker-контейнера)",
        ),
        compose: str = typer.Option(
            "deploy/dev/docker-compose.yml",
            "--compose",
            help="Compose-файл относительно core-runtime-service",
        ),
        interval: float = typer.Option(
            1.0, "--interval", "-i",
            help="Интервал проверки изменений в секундах",
        ),
        no_reload: bool = typer.Option(
            False, "--no-reload",
            help="Только синхронизировать файлы, не вызывать API reload",
        ),
    ) -> None:
        """Dev-режим плагина: следит за изменениями и автоматически перезагружает.

        При изменении любого .py файла:
          1. Копирует изменённые файлы в Core (native или docker)
          2. Вызывает hc plugin reload <name>

        Примеры:
          hc plugin dev ./plugins/my_sensor
          hc plugin dev ./plugins/my_sensor --mode native
          hc plugin dev ./plugins/my_sensor --no-reload  # только sync
        """
        console = Console()
        src = src.resolve()

        if not src.is_dir():
            console.print(f"[red]Ошибка:[/red] не директория: {src}")
            raise typer.Exit(code=1)

        # --- Имя плагина ---
        plugin_name = name
        if not plugin_name:
            manifest_path = src / "plugin.json"
            if manifest_path.is_file():
                import json
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    plugin_name = str(data.get("name", ""))
                except (json.JSONDecodeError, OSError):
                    pass
        if not plugin_name:
            plugin_name = src.name
        plugin_name = plugin_name.strip()

        # --- Core root ---
        from hc.commands.plugin import _resolve_core_root
        core_root = _resolve_core_root(console)

        # --- Определить mode ---
        m = mode.strip().lower()
        if m == "auto":
            # Есть запущенный контейнер core-runtime?
            check = subprocess.run(  # noqa: S603
                ["docker", "ps", "--filter", "name=core-runtime", "--format", "{{.Names}}"],
                capture_output=True, text=True, check=False,
            )
            has_docker = bool(check.stdout.strip())
            m = "docker" if has_docker else "native"
            console.print(f"[dim]auto mode → [bold]{m}[/bold][/dim]")

        compose_file = compose
        if m == "docker":
            compose_path = core_root / compose_file
            if not compose_path.is_file():
                # Попробуем prod compose
                alt = "deploy/prod/docker-compose.image.yml"
                if (core_root / alt).is_file():
                    compose_file = alt
                    console.print(f"[dim]compose: {compose_file}[/dim]")
                else:
                    console.print(
                        f"[red]Ошибка:[/red] compose-файл не найден: {compose_path}\n"
                        "Укажи явно: [bold]--compose deploy/dev/docker-compose.yml[/bold]"
                    )
                    raise typer.Exit(code=1)

        # --- Initial sync ---
        console.print(
            f"\n[bold]Plugin dev[/bold]: [cyan]{plugin_name}[/cyan]  "
            f"src=[dim]{src}[/dim]  mode=[bold]{m}[/bold]\n"
        )

        if m == "native":
            _sync_native(src, plugin_name, core_root, [], console=console, full=True)
        else:
            _sync_docker(src, plugin_name, core_root, compose_file, [], console=console, full=True)

        if not no_reload:
            if _api_reload(console, plugin_name):
                console.print(f"[green]✓[/green] {plugin_name} загружен")
            else:
                console.print(
                    f"[yellow]⚠[/yellow] reload не удался — Core может быть не запущен. "
                    "Продолжаю следить за файлами."
                )

        # --- Watch loop ---
        console.print(f"\n[dim]Слежу за {src} (интервал {interval}s). Ctrl+C для остановки…[/dim]\n")
        mtimes = _collect_mtimes(src)

        try:
            while True:
                time.sleep(interval)
                new_mtimes = _collect_mtimes(src)
                changed = _changed_files(mtimes, new_mtimes)
                if not changed:
                    continue

                mtimes = new_mtimes
                ts = time.strftime("%H:%M:%S")
                console.print(f"[dim]{ts}[/dim] [yellow]изменено {len(changed)} файл(ов)[/yellow]")

                if m == "native":
                    _sync_native(src, plugin_name, core_root, changed, console=console)
                else:
                    _sync_docker(src, plugin_name, core_root, compose_file, changed, console=console)

                if no_reload:
                    continue

                if _api_reload(console, plugin_name):
                    console.print(f"  [green]✓[/green] reloaded")
                else:
                    console.print(f"  [red]✗[/red] reload failed")

        except KeyboardInterrupt:
            console.print("\n[dim]Остановлено.[/dim]")
