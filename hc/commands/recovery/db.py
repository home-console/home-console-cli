from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hc.core_ops import require_docker
from hc.commands._compose_helpers import (
    container_env_script,
    read_env_kv,
    remove_env_keys,
    upsert_env,
)
from hc.commands.recovery import RecoveryContext


def build_app(ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Операции над БД/хранилищем Core",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    def _mask_dsn(dsn: str) -> str:
        try:
            if "://" not in dsn or "@" not in dsn:
                return dsn
            scheme, rest = dsn.split("://", 1)
            creds, tail = rest.split("@", 1)
            if ":" in creds:
                user, _pw = creds.split(":", 1)
                return f"{scheme}://{user}:***@{tail}"
            return dsn
        except Exception:
            return dsn

    @app.command("status")
    def status() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        env_file = src.path / ".env"
        env = read_env_kv(env_file)

        # effective env inside container
        keys = [
            "RUNTIME_STORAGE_MODE",
            "RUNTIME_VAULT_STORAGE_TYPE",
            "RUNTIME_VAULT_PG_DSN",
            "RUNTIME_DB_PATH",
            "RUNTIME_VAULT_DB_PATH",
            "RUNTIME_VAULT_SECRET_DB_PATH",
        ]
        effective: dict[str, str] = {}
        raw = ctx.compose_capture(console, src, ["exec", "-T", "core-runtime", "sh", "-lc", container_env_script(keys)])
        for ln in raw.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                effective[k.strip()] = v.strip()

        storage_mode = (effective.get("RUNTIME_STORAGE_MODE") or env.get("RUNTIME_STORAGE_MODE") or "single").lower()
        vault_type = (effective.get("RUNTIME_VAULT_STORAGE_TYPE") or env.get("RUNTIME_VAULT_STORAGE_TYPE") or "sqlite").lower()
        vault_pg_dsn = (effective.get("RUNTIME_VAULT_PG_DSN") or env.get("RUNTIME_VAULT_PG_DSN") or "").strip()

        table = Table(title="recovery storage status (effective in container)")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("storage_mode", storage_mode)
        table.add_row("vault_storage_type", vault_type or "(unset)")
        if vault_type in {"postgresql", "postgres"}:
            table.add_row("vault_pg_dsn", _mask_dsn(vault_pg_dsn) if vault_pg_dsn else "[yellow](unset)[/yellow]")
        else:
            for k in ["RUNTIME_VAULT_DB_PATH", "RUNTIME_VAULT_SECRET_DB_PATH"]:
                v = effective.get(k) or env.get(k)
                if v:
                    table.add_row(k, v)
        v = effective.get("RUNTIME_DB_PATH") or env.get("RUNTIME_DB_PATH")
        if v:
            table.add_row("RUNTIME_DB_PATH", v)
        console.print(table)

        console.print(Panel.fit("Проверка файлов SQLite в core-runtime (/data)", title="checks"))
        file_tbl = Table(title="sqlite files on volume (/data)")
        file_tbl.add_column("path", style="bold")
        file_tbl.add_column("exists")
        file_tbl.add_column("size")
        for path in ["/data/runtime.db", "/data/vault.db", "/data/vault_secret.db"]:
            out = ctx.compose_capture(
                console,
                src,
                ["exec", "-T", "core-runtime", "sh", "-lc", f"ls -lah {shlex.quote(path)} 2>/dev/null || true"],
            ).strip()
            if not out:
                file_tbl.add_row(path, "[red]no[/red]", "")
                continue
            parts = out.split()
            size = parts[4] if len(parts) >= 5 else ""
            file_tbl.add_row(path, "[green]yes[/green]", size)
        console.print(file_tbl)

    @app.command("shell")
    def shell(cmd: str = typer.Option("sh", "--cmd")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["exec", "core-runtime", *shlex.split(cmd)])

    @app.command("backup")
    def backup(out_dir: Path = typer.Option(Path("."), "--out-dir"), prefix: str = typer.Option("hc-sqlite", "--prefix")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        out_dir.mkdir(parents=True, exist_ok=True)
        for container_path, dst in [
            ("/data/runtime.db", out_dir / f"{prefix}-runtime.db"),
            ("/data/vault.db", out_dir / f"{prefix}-vault.db"),
            ("/data/vault_secret.db", out_dir / f"{prefix}-vault_secret.db"),
        ]:
            ctx.run_compose(console, src, ["cp", f"core-runtime:{container_path}", str(dst.resolve())])
        console.print(f"[green]✓[/green] Бэкап сохранён в: {out_dir}")

    @app.command("restore")
    def restore(in_dir: Path = typer.Option(Path("."), "--in-dir"), prefix: str = typer.Option("hc-sqlite", "--prefix")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        for src_file, container_path in [
            (in_dir / f"{prefix}-runtime.db", "/data/runtime.db"),
            (in_dir / f"{prefix}-vault.db", "/data/vault.db"),
            (in_dir / f"{prefix}-vault_secret.db", "/data/vault_secret.db"),
        ]:
            if not src_file.exists():
                console.print(f"[yellow]Пропускаю (нет файла): {src_file}[/yellow]")
                continue
            ctx.run_compose(console, src, ["cp", str(src_file.resolve()), f"core-runtime:{container_path}"])
        console.print("[green]✓[/green] Restore выполнен. Перезапусти Core: `hc recovery core up`")

    @app.command("switch")
    def switch(mode: str = typer.Argument("volume", help="volume | relative | vault-postgres | vault-sqlite")) -> None:
        console = Console()
        src = ctx.resolve_source(console)
        env_file = src.path / ".env"
        if mode not in {"volume", "relative", "vault-postgres", "vault-sqlite"}:
            console.print("[red]Ошибка: mode должен быть volume, relative, vault-postgres или vault-sqlite[/red]")
            raise typer.Exit(code=2)
        if mode == "vault-postgres":
            upsert_env(
                env_file,
                {
                    "RUNTIME_STORAGE_MODE": "dual",
                    "RUNTIME_VAULT_STORAGE_TYPE": "postgresql",
                    "RUNTIME_VAULT_PG_DSN": "postgresql://postgres:postgres@postgres:5432/core",
                },
            )
            console.print(f"[green]✓[/green] Обновил {env_file} (vault → postgres)")
            raise typer.Exit(code=0)
        if mode == "vault-sqlite":
            env = read_env_kv(env_file)
            upsert_env(
                env_file,
                {
                    "RUNTIME_STORAGE_MODE": "dual",
                    "RUNTIME_VAULT_STORAGE_TYPE": "sqlite",
                    "RUNTIME_VAULT_DB_PATH": env.get("RUNTIME_VAULT_DB_PATH") or "/data/vault.db",
                    "RUNTIME_VAULT_SECRET_DB_PATH": env.get("RUNTIME_VAULT_SECRET_DB_PATH") or "/data/vault_secret.db",
                },
            )
            remove_env_keys(env_file, {"RUNTIME_VAULT_PG_DSN"})
            console.print(f"[green]✓[/green] Обновил {env_file} (vault → sqlite)")
            raise typer.Exit(code=0)
        if mode == "volume":
            upsert_env(
                env_file,
                {
                    "RUNTIME_DB_PATH": "/data/runtime.db",
                    "RUNTIME_VAULT_DB_PATH": "/data/vault.db",
                    "RUNTIME_VAULT_SECRET_DB_PATH": "/data/vault_secret.db",
                },
            )
        else:
            upsert_env(
                env_file,
                {
                    "RUNTIME_DB_PATH": "data/runtime.db",
                    "RUNTIME_VAULT_DB_PATH": "data/vault.db",
                    "RUNTIME_VAULT_SECRET_DB_PATH": "data/vault_secret.db",
                },
            )
        remove_env_keys(env_file, {"RUNTIME_VAULT_PG_DSN"})
        console.print(f"[green]✓[/green] Обновил {env_file}")

    @app.command("migrate")
    def migrate(mode: str = typer.Argument("to-dual", help="status | to-dual")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        if mode not in {"status", "to-dual"}:
            console.print("[red]Ошибка: mode должен быть status или to-dual[/red]")
            raise typer.Exit(code=2)
        if mode == "status":
            code = (
                "import asyncio\n"
                "from core.runtime.config import Config\n"
                "from modules.storage.migrate import check_migration_status\n"
                "cfg = Config.from_env()\n"
                "core_cnt, vault_cnt = asyncio.run(check_migration_status(cfg))\n"
                "print(f'core={core_cnt} vault={vault_cnt}')\n"
            )
            out = ctx.compose_capture(console, src, ["exec", "-T", "core-runtime", "python", "-c", code])
            console.print(out)
            raise typer.Exit(code=0)
        ctx.run_compose(console, src, ["exec", "-T", "core-runtime", "python", "-m", "modules.storage.migrate"])
        console.print("[green]✓[/green] migrate to-dual done")

    return app

