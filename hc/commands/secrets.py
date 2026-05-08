"""hc secrets — управление SecretStore и bootstrap-секретами Core Runtime.

Все операции выполняются ВНУТРИ контейнера (docker compose exec)
или на удалённом хосте (SSH), чтобы у CLI не было прямого доступа к БД.

Модель:
  local  — docker compose exec -T core-runtime python /app/scripts/secrets_tool.py ...
  remote — ssh user@host "cd /path && docker compose -f <compose> exec -T core-runtime ..."

Команды:
  hc secrets probe   [--ssh ...] [--path ...]   — проверить БД + SecretStore
  hc secrets init    [--ssh ...] [--path ...]   — bootstrap секреты из env → store
  hc secrets list    [--ssh ...] [--path ...]   — список ключей в store
  hc secrets get KEY [--ssh ...] [--path ...]   — прочитать секрет (только dev)
  hc secrets set KEY [--ssh ...] [--path ...]   — записать секрет (значение из stdin или --value)
  hc secrets delete KEY [--ssh ...] [--path ...]— удалить секрет
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from hc.config import Config
from hc.core_source import get_core_source_from_repo, get_core_source_local
from hc.errors import HcCliError, json_error_payload


# ── helpers ───────────────────────────────────────────────────────────────────

_TOOL = "/app/scripts/secrets_tool.py"
_SERVICE = "core-runtime"

# Mapping mode → compose file path relative to core-runtime-service root
_COMPOSE_BY_MODE: dict[str, str] = {
    "dev":   "deploy/dev/docker-compose.yml",
    "image": "deploy/dev/docker-compose.image.yml",
    "prod":  "deploy/prod/docker-compose.image.yml",
}


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            return p
    return None


def _resolve_compose(mode: str) -> str:
    rel = _COMPOSE_BY_MODE.get(mode.lower())
    if rel is None:
        raise HcCliError(
            message=f"Неизвестный mode: {mode!r}. Допустимые: dev, image, prod.",
            exit_code=2,
        )
    return rel


def _resolve_core_path() -> Path | None:
    repo = _find_repo_root()
    if repo:
        src = get_core_source_from_repo(repo)
        if src:
            return src.path
    src = get_core_source_local()
    return src.path if src else None


def _run_tool_local(
    args: list[str],
    *,
    compose_file: Path,
    stdin_data: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Запустить secrets_tool.py через docker compose exec локально."""
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in (extra_env or {}).items())
    tool_cmd = f"python {_TOOL} " + " ".join(shlex.quote(a) for a in args)
    full_cmd = f"{env_prefix} {tool_cmd}".strip() if env_prefix else tool_cmd

    cmd = [
        "docker", "compose", "-f", str(compose_file),
        "exec", "-T", _SERVICE,
        "sh", "-c", full_cmd,
    ]
    p = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(compose_file.parent),
        text=True,
        capture_output=True,
        input=stdin_data,
    )
    return _parse_output(p)


def _run_tool_remote(
    args: list[str],
    *,
    ssh: str,
    remote_path: str,
    compose_rel: str,
    stdin_data: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Запустить secrets_tool.py через SSH + docker compose exec."""
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in (extra_env or {}).items())
    tool_cmd = f"python {_TOOL} " + " ".join(shlex.quote(a) for a in args)
    full_cmd = f"{env_prefix} {tool_cmd}".strip() if env_prefix else tool_cmd

    # Pipe stdin через heredoc если нужно
    if stdin_data is not None:
        inner = (
            f"cd {shlex.quote(remote_path)} && "
            f"echo {shlex.quote(stdin_data)} | "
            f"docker compose -f {shlex.quote(compose_rel)} exec -T {_SERVICE} sh -c {shlex.quote(full_cmd)}"
        )
    else:
        inner = (
            f"cd {shlex.quote(remote_path)} && "
            f"docker compose -f {shlex.quote(compose_rel)} exec -T {_SERVICE} sh -c {shlex.quote(full_cmd)}"
        )

    p = subprocess.run(  # noqa: S603
        ["ssh", ssh, inner],
        text=True,
        capture_output=True,
    )
    return _parse_output(p)


def _parse_output(p: "subprocess.CompletedProcess[str]") -> dict:
    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()

    if p.returncode != 0:
        # Попробуем распарсить JSON ошибки из stdout
        try:
            data = json.loads(stdout)
            if not data.get("ok", True):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        msg = stderr or stdout or f"exit code {p.returncode}"
        return {"ok": False, "error": msg}

    if not stdout:
        return {"ok": False, "error": "no output from secrets_tool"}

    # Последняя строка — JSON (инструмент может писать что-то в stderr)
    last_line = stdout.splitlines()[-1]
    try:
        return json.loads(last_line)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": f"unexpected output: {last_line!r}"}


def _resolve_targets(
    ssh: str | None,
    path: str | None,
    mode: str,
    compose_override: str | None = None,
) -> tuple[str | None, str | None, str, Path | None]:
    """
    Вернуть (ssh, remote_path, compose_rel, local_compose_file).

    compose_override позволяет передать произвольный путь к compose-файлу
    вместо автоопределения по mode.
    """
    cfg = Config.load()
    resolved_ssh = ssh or cfg.deploy.ssh or None
    resolved_path = path or cfg.deploy.path or None

    compose_rel = compose_override or _resolve_compose(mode)

    if resolved_ssh:
        return resolved_ssh, resolved_path, compose_rel, None

    # local
    core_path = _resolve_core_path()
    if core_path is None:
        raise HcCliError(
            message="Исходники Core не найдены локально.",
            exit_code=1,
            hint="Запусти из монорепы HomeConsole или сделай `hc core init`.",
        )
    compose_file = core_path / compose_rel
    if not compose_file.exists():
        raise HcCliError(
            message=f"Compose-файл не найден: {compose_file}",
            exit_code=1,
            hint=f"Проверь --mode ({mode!r}) или укажи --compose явно.",
        )
    return None, None, "", compose_file


def _run(
    args: list[str],
    *,
    ssh: str | None,
    remote_path: str | None,
    compose_rel: str,
    local_compose: Path | None,
    stdin_data: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    if ssh:
        if not remote_path:
            raise HcCliError(message="для --ssh нужен --path", exit_code=2,
                             hint="Пример: `hc secrets probe --ssh user@host --path /srv/core-runtime-service`")
        return _run_tool_remote(args, ssh=ssh, remote_path=remote_path,
                                compose_rel=compose_rel, stdin_data=stdin_data,
                                extra_env=extra_env)
    if local_compose is None:
        raise HcCliError(message="не удалось определить compose файл", exit_code=1)
    return _run_tool_local(args, compose_file=local_compose,
                           stdin_data=stdin_data, extra_env=extra_env)


def _print_result(result: dict, console: Console, json_out: bool) -> None:
    if json_out:
        print(json.dumps(result, ensure_ascii=False))
        return
    if not result.get("ok"):
        console.print(f"[red]Ошибка:[/red] {result.get('error', 'unknown error')}")
        raise typer.Exit(code=1)


# ── register ──────────────────────────────────────────────────────────────────

def register(app: typer.Typer) -> None:
    secrets_app = typer.Typer(
        help="Управление SecretStore и bootstrap-секретами Core Runtime.",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    _ssh_opt     = typer.Option(None,   "--ssh",     help="user@host для удалённого доступа")
    _path_opt    = typer.Option(None,   "--path",    help="Путь к core-runtime-service на сервере")
    _mode_opt    = typer.Option("prod", "--mode",    help="Compose mode: dev | image | prod (default)")
    _compose_opt = typer.Option(None,   "--compose", help="Явный путь к compose-файлу (override --mode)")
    _json_opt    = typer.Option(False,  "--json",    help="Вывод в JSON")

    @secrets_app.command("probe")
    def probe(
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
    ) -> None:
        """Проверить доступность БД и SecretStore (read-only)."""
        console = Console()
        try:
            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run(["probe"], ssh=resolved_ssh, remote_path=resolved_path,
                          compose_rel=compose_rel, local_compose=local_compose)
            _print_result(result, console, json_out)
            if not json_out:
                count = result.get("secret_count", "?")
                console.print(f"[green]✓[/green] SecretStore accessible ({count} keys)")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @secrets_app.command("init")
    def init(
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
        source: str = typer.Option(
            "store+env",
            "--source",
            help="Режим источника: store+env (default) | store | env",
        ),
    ) -> None:
        """
        Bootstrap секреты: загрузить из env в SecretStore.

        После успешного init можно убрать CSRF_SECRET и OAUTH_ENCRYPTION_KEY
        из .env — при следующем старте core возьмёт их из SecretStore.
        """
        console = Console()
        try:
            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run(
                ["init"],
                ssh=resolved_ssh, remote_path=resolved_path,
                compose_rel=compose_rel, local_compose=local_compose,
                extra_env={"RUNTIME_SECRETS_SOURCE": source},
            )
            _print_result(result, console, json_out)
            if not json_out:
                imported = result.get("imported_from_env", [])
                generated = result.get("generated", [])
                missing = result.get("missing_required", [])
                if imported:
                    console.print(f"[green]↑[/green] Импортировано из env: {', '.join(imported)}")
                if generated:
                    console.print(f"[green]+[/green] Сгенерировано: {', '.join(generated)}")
                if missing:
                    console.print(f"[yellow]![/yellow] Не хватает: {', '.join(missing)}")
                if not missing:
                    console.print("[green]✓[/green] Secrets init OK")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @secrets_app.command("list")
    def list_keys(
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
    ) -> None:
        """Список ключей в SecretStore."""
        console = Console()
        try:
            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run(["list"], ssh=resolved_ssh, remote_path=resolved_path,
                          compose_rel=compose_rel, local_compose=local_compose)
            _print_result(result, console, json_out)
            if not json_out:
                keys = result.get("keys", [])
                if not keys:
                    console.print("[dim]SecretStore пустой[/dim]")
                    return
                table = Table(show_header=True, header_style="bold")
                table.add_column("Key", style="cyan")
                for k in keys:
                    table.add_row(k)
                console.print(table)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @secrets_app.command("get")
    def get_secret(
        key: str = typer.Argument(..., help="Ключ секрета"),
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
    ) -> None:
        """
        Прочитать значение секрета.

        Работает ТОЛЬКО когда RUNTIME_ENV=development на сервере.
        В продакшене — отказ.
        """
        console = Console()
        try:
            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run([f"get", key], ssh=resolved_ssh, remote_path=resolved_path,
                          compose_rel=compose_rel, local_compose=local_compose)
            _print_result(result, console, json_out)
            if not json_out:
                console.print(f"[bold]{result['key']}[/bold] = {result['value']}")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @secrets_app.command("set")
    def set_secret(
        key: str = typer.Argument(..., help="Ключ секрета"),
        value: Optional[str] = typer.Option(
            None, "--value", "-v",
            help="Значение. Если не указано — читается из stdin.",
        ),
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
    ) -> None:
        """
        Записать секрет в SecretStore.

        Значение передаётся через --value или stdin:
          echo 'mysecret' | hc secrets set my.key
          hc secrets set my.key --value mysecret
        """
        console = Console()
        try:
            stdin_data = value
            if stdin_data is None:
                if sys.stdin.isatty():
                    stdin_data = typer.prompt("Значение секрета", hide_input=True)
                else:
                    stdin_data = sys.stdin.read().strip()
            if not stdin_data:
                console.print("[red]Ошибка:[/red] значение не может быть пустым")
                raise typer.Exit(code=1)

            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run(["set", key], ssh=resolved_ssh, remote_path=resolved_path,
                          compose_rel=compose_rel, local_compose=local_compose,
                          stdin_data=stdin_data)
            _print_result(result, console, json_out)
            if not json_out:
                console.print(f"[green]✓[/green] Секрет [bold]{key}[/bold] сохранён")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @secrets_app.command("delete")
    def delete_secret(
        key: str = typer.Argument(..., help="Ключ секрета"),
        ssh: Optional[str] = _ssh_opt,
        path: Optional[str] = _path_opt,
        mode: str = _mode_opt,
        compose_override: Optional[str] = _compose_opt,
        json_out: bool = _json_opt,
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
    ) -> None:
        """Удалить секрет из SecretStore."""
        console = Console()
        try:
            if not yes:
                typer.confirm(f"Удалить секрет {key!r}?", abort=True)
            resolved_ssh, resolved_path, compose_rel, local_compose = _resolve_targets(
                ssh, path, mode, compose_override
            )
            result = _run(["delete", key], ssh=resolved_ssh, remote_path=resolved_path,
                          compose_rel=compose_rel, local_compose=local_compose)
            _print_result(result, console, json_out)
            if not json_out:
                deleted = result.get("deleted", False)
                if deleted:
                    console.print(f"[green]✓[/green] Секрет [bold]{key}[/bold] удалён")
                else:
                    console.print(f"[yellow]![/yellow] Секрет [bold]{key}[/bold] не найден")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            raise typer.Exit(code=int(e.exit_code or 1))

    app.add_typer(secrets_app, name="secrets")
