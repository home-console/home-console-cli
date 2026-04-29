from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hc.constants import CONFIG_DIR, CONFIG_PATH, DATA_DIR, SETUP_LOG_PATH
from hc.core_ops import (
    compose_project_from_source,
    require_docker,
)
from hc.core_source import CoreSource, get_core_source_from_repo, get_core_source_local
from hc.env_bootstrap import core_env_path


def register(app: typer.Typer) -> None:
    recovery_app = typer.Typer(
        help="Recovery режим (локальный: файлы + docker)",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    def _find_repo_root() -> Path | None:
        here = Path(__file__).resolve()
        for p in [here, *here.parents]:
            if (p / "core-runtime-service").exists():
                return p
        return None

    def _resolve_source(console: Console) -> CoreSource:
        repo_root = _find_repo_root()
        if repo_root:
            src = get_core_source_from_repo(repo_root)
            if src:
                return src
        src = get_core_source_local()
        if src:
            return src
        console.print("[red]Ошибка: исходники Core не найдены локально.[/red]")
        console.print("Сделай: `hc core init` (скачает в ~/.local/share/hc) или запусти из монорепы.")
        raise typer.Exit(code=1)

    def _run_compose_interactive(console: Console, src: CoreSource, args: list[str]) -> None:
        """
        Запуск docker compose без capture_output (для exec/интерактива).
        """
        project = compose_project_from_source(console, src)
        cmd = ["docker", "compose", *_compose_file_args(project.cwd, project.compose_file), *args]
        try:
            p = subprocess.run(cmd, cwd=str(project.cwd), check=False)  # noqa: S603
        except FileNotFoundError:
            console.print("[red]Ошибка: docker не найден.[/red]")
            raise typer.Exit(code=1)
        if p.returncode != 0:
            raise typer.Exit(code=p.returncode)

    def _compose_capture(console: Console, src: CoreSource, args: list[str]) -> str:
        project = compose_project_from_source(console, src)
        cmd = ["docker", "compose", *_compose_file_args(project.cwd, project.compose_file), *args]
        try:
            p = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(project.cwd),
                check=False,
                text=True,
                capture_output=True,
            )
        except FileNotFoundError:
            console.print("[red]Ошибка: docker не найден.[/red]")
            raise typer.Exit(code=1)
        if p.returncode != 0:
            out = (p.stdout or "") + "\n" + (p.stderr or "")
            if out.strip():
                console.print(out.rstrip())
            raise typer.Exit(code=p.returncode)
        return (p.stdout or "").strip()

    def _compose_file_args(compose_dir: Path, base_compose: Path) -> list[str]:
        """
        Возвращает список аргументов `-f ...` для docker compose.
        Если рядом лежит `docker-compose.recovery.yml`, он подхватывается автоматически.
        """
        args = ["-f", str(base_compose)]
        override = compose_dir / "docker-compose.recovery.yml"
        if override.exists():
            args += ["-f", str(override)]
        return args

    compose_app = typer.Typer(
        help="Аналитика и управление docker compose (через override)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    def _override_path(console: Console, src: CoreSource) -> Path:
        project = compose_project_from_source(console, src)
        return project.cwd / "docker-compose.recovery.yml"

    def _read_text(p: Path) -> str:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    def _write_text(p: Path, text: str) -> None:
        p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    def _remove_managed_block(text: str, name: str) -> str:
        start = f"# BEGIN hc recovery: {name}"
        end = f"# END hc recovery: {name}"
        out_lines: list[str] = []
        skipping = False
        for ln in text.splitlines():
            if start in ln:
                skipping = True
                continue
            if skipping and end in ln:
                skipping = False
                continue
            if not skipping:
                out_lines.append(ln)
        return "\n".join(out_lines).rstrip() + "\n"

    def _ensure_sections(text: str) -> str:
        """
        Гарантирует наличие корневых секций services/volumes.
        """
        def _normalize_empty_root_maps(s: str) -> str:
            lines = s.splitlines()
            out: list[str] = []
            i = 0
            while i < len(lines):
                ln = lines[i]
                if ln in {"services:", "volumes:"}:
                    key = ln[:-1]  # services / volumes
                    j = i + 1
                    has_child = False
                    while j < len(lines):
                        nxt = lines[j]
                        if not nxt.strip() or nxt.lstrip().startswith("#"):
                            j += 1
                            continue
                        # корневой ключ следующей секции
                        if nxt and not nxt.startswith(" "):
                            break
                        # есть вложенные элементы
                        has_child = True
                        break
                    if not has_child:
                        out.append(f"{key}: {{}}")
                        i += 1
                        continue
                out.append(ln)
                i += 1
            return "\n".join(out)

        t = text.strip()
        if not t:
            return (
                "# Дополнительные сервисы для recovery-режима.\n"
                "# Управляется частично через `hc recovery compose enable|disable ...`\n"
                "\n"
                "services: {}\n"
                "\n"
                "volumes: {}\n"
            )
        if "services:" not in t:
            t = "services: {}\n\n" + t
        if "volumes:" not in t:
            t = t.rstrip() + "\n\nvolumes: {}\n"
        t = _normalize_empty_root_maps(t)
        return t.rstrip() + "\n"

    def _inject_into_root_section(text: str, section: str, block: str) -> str:
        """
        Вставляет block сразу после корневой строки `<section>:` (первое вхождение).
        Важно: не матчим вложенные `volumes:`/`services:` внутри сервисов.
        """
        lines = text.splitlines()
        out: list[str] = []
        inserted = False
        for ln in lines:
            if not inserted and ln == f"{section}: {{}}":
                # расширяем пустую мапу до полноценной секции
                out.append(f"{section}:")
                out.append(block.rstrip("\n"))
                inserted = True
                continue
            out.append(ln)
            if not inserted and ln == f"{section}:":
                out.append(block.rstrip("\n"))
                inserted = True
        if not inserted:
            # fallback: допишем в конец
            out.append("")
            out.append(f"{section}:")
            out.append(block.rstrip("\n"))
        return "\n".join(out).rstrip() + "\n"

    def _redis_blocks() -> tuple[str, str]:
        services_block = "\n".join(
            [
                "  # BEGIN hc recovery: redis",
                "  redis:",
                "    image: redis:7-alpine",
                "    container_name: redis",
                "    restart: unless-stopped",
                "    command: [\"redis-server\", \"--appendonly\", \"yes\"]",
                "    ports:",
                "      - \"6379:6379\"",
                "    volumes:",
                "      - redis-data:/data",
                "  # END hc recovery: redis",
                "",
            ]
        )
        volumes_block = "\n".join(
            [
                "  # BEGIN hc recovery: redis",
                "  redis-data:",
                "  # END hc recovery: redis",
                "",
            ]
        )
        return services_block, volumes_block

    def _postgres_blocks() -> tuple[str, str]:
        services_block = "\n".join(
            [
                "  # BEGIN hc recovery: postgres",
                "  postgres:",
                "    image: postgres:16-alpine",
                "    container_name: postgres",
                "    restart: unless-stopped",
                "    environment:",
                "      - POSTGRES_USER=postgres",
                "      - POSTGRES_PASSWORD=postgres",
                "      - POSTGRES_DB=core",
                "    ports:",
                "      - \"5432:5432\"",
                "    volumes:",
                "      - postgres-data:/var/lib/postgresql/data",
                "  # END hc recovery: postgres",
                "",
            ]
        )
        volumes_block = "\n".join(
            [
                "  # BEGIN hc recovery: postgres",
                "  postgres-data:",
                "  # END hc recovery: postgres",
                "",
            ]
        )
        return services_block, volumes_block

    def _pgadmin_blocks() -> tuple[str, str]:
        services_block = "\n".join(
            [
                "  # BEGIN hc recovery: pgadmin",
                "  pgadmin:",
                "    image: dpage/pgadmin4:latest",
                "    container_name: pgadmin",
                "    restart: unless-stopped",
                "    environment:",
                "      - PGADMIN_DEFAULT_EMAIL=admin@local",
                "      - PGADMIN_DEFAULT_PASSWORD=admin",
                "    ports:",
                "      - \"5050:80\"",
                "    depends_on:",
                "      - postgres",
                "    volumes:",
                "      - pgadmin-data:/var/lib/pgadmin",
                "  # END hc recovery: pgadmin",
                "",
            ]
        )
        volumes_block = "\n".join(
            [
                "  # BEGIN hc recovery: pgadmin",
                "  pgadmin-data:",
                "  # END hc recovery: pgadmin",
                "",
            ]
        )
        return services_block, volumes_block

    def _redisinsight_blocks() -> tuple[str, str]:
        services_block = "\n".join(
            [
                "  # BEGIN hc recovery: redisinsight",
                "  redisinsight:",
                "    image: redis/redisinsight:latest",
                "    container_name: redisinsight",
                "    restart: unless-stopped",
                "    ports:",
                "      - \"5540:5540\"",
                "    depends_on:",
                "      - redis",
                "    volumes:",
                "      - redisinsight-data:/data",
                "  # END hc recovery: redisinsight",
                "",
            ]
        )
        volumes_block = "\n".join(
            [
                "  # BEGIN hc recovery: redisinsight",
                "  redisinsight-data:",
                "  # END hc recovery: redisinsight",
                "",
            ]
        )
        return services_block, volumes_block

    def _ui_disable_blocks() -> tuple[str, str]:
        services_block = "\n".join(
            [
                "  # BEGIN hc recovery: ui",
                "  caddy:",
                "    profiles: [\"hc-disabled\"]",
                "  # END hc recovery: ui",
                "",
            ]
        )
        return services_block, ""

    @compose_app.command("info")
    def compose_info() -> None:
        """Показать, какие compose-файлы используются и какие сервисы доступны."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        project = compose_project_from_source(console, src)
        base = project.compose_file
        override = project.cwd / "docker-compose.recovery.yml"

        body = f"base: {base}\n"
        body += f"override: {override} ({'exists' if override.exists() else 'missing'})\n"
        console.print(Panel.fit(body, title="compose files"))

        services = _compose_capture(console, src, ["config", "--services"]).splitlines()
        tbl = Table(title="compose services")
        tbl.add_column("service", style="bold")
        for s in services:
            if s.strip():
                tbl.add_row(s.strip())
        console.print(tbl)

        override_text = _read_text(override)
        feats = [
            ("redis", "# BEGIN hc recovery: redis" in override_text),
            ("postgres", "# BEGIN hc recovery: postgres" in override_text),
            ("pgadmin", "# BEGIN hc recovery: pgadmin" in override_text),
            ("redisinsight", "# BEGIN hc recovery: redisinsight" in override_text),
            ("ui disabled (caddy)", "# BEGIN hc recovery: ui" in override_text),
        ]
        ft = Table(title="recovery features (override)")
        ft.add_column("feature", style="bold")
        ft.add_column("enabled")
        for name, on in feats:
            ft.add_row(name, "[green]yes[/green]" if on else "[dim]no[/dim]")
        console.print(ft)

    @compose_app.command("services")
    def compose_services() -> None:
        """Список сервисов (docker compose config --services)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        out = _compose_capture(console, src, ["config", "--services"])
        console.print(out)

    @compose_app.command("ps")
    def compose_ps() -> None:
        """Показать `docker compose ps`."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["ps"])

    @compose_app.command("config")
    def compose_config() -> None:
        """Показать итоговый config (после слияния base + override)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        out = _compose_capture(console, src, ["config"])
        console.print(out)

    @compose_app.command("lint")
    def compose_lint() -> None:
        """Проверить, что compose (base + override) валиден."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        try:
            _compose_capture(console, src, ["config"])
        except typer.Exit as e:
            raise e
        console.print("[green]✓[/green] compose валиден")

    @compose_app.command("enable")
    def compose_enable(service: str = typer.Argument(..., help="Что включить: redis|postgres|pgadmin|redisinsight|ui")) -> None:
        """Включить сервис в override-compose (без ручного редактирования)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        p = _override_path(console, src)

        name = service.strip().lower()
        supported = {"redis", "postgres", "pgadmin", "redisinsight", "ui"}
        if name not in supported:
            console.print(f"[red]Неизвестно: {name}[/red]")
            console.print("Поддерживается: redis, postgres, pgadmin, redisinsight, ui")
            raise typer.Exit(code=2)

        text = _ensure_sections(_read_text(p))
        text = _remove_managed_block(text, name)

        if name == "redis":
            svc_block, vol_block = _redis_blocks()
            text = _inject_into_root_section(text, "services", svc_block)
            text = _inject_into_root_section(text, "volumes", vol_block)
        elif name == "postgres":
            svc_block, vol_block = _postgres_blocks()
            text = _inject_into_root_section(text, "services", svc_block)
            text = _inject_into_root_section(text, "volumes", vol_block)
        elif name == "pgadmin":
            if "# BEGIN hc recovery: postgres" not in text:
                p_svc, p_vol = _postgres_blocks()
                text = _inject_into_root_section(text, "services", p_svc)
                text = _inject_into_root_section(text, "volumes", p_vol)
            svc_block, vol_block = _pgadmin_blocks()
            text = _inject_into_root_section(text, "services", svc_block)
            text = _inject_into_root_section(text, "volumes", vol_block)
        elif name == "redisinsight":
            if "# BEGIN hc recovery: redis" not in text:
                r_svc, r_vol = _redis_blocks()
                text = _inject_into_root_section(text, "services", r_svc)
                text = _inject_into_root_section(text, "volumes", r_vol)
            svc_block, vol_block = _redisinsight_blocks()
            text = _inject_into_root_section(text, "services", svc_block)
            text = _inject_into_root_section(text, "volumes", vol_block)
        elif name == "ui":
            text = _remove_managed_block(text, "ui")
        _write_text(p, text)
        console.print(f"[green]✓[/green] Включил {name} в {p}")

    @compose_app.command("disable")
    def compose_disable(service: str = typer.Argument(..., help="Что выключить: redis|postgres|pgadmin|redisinsight|ui")) -> None:
        """Выключить сервис из override-compose (без ручного редактирования)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        p = _override_path(console, src)

        name = service.strip().lower()
        supported = {"redis", "postgres", "pgadmin", "redisinsight", "ui"}
        if name not in supported:
            console.print(f"[red]Неизвестно: {name}[/red]")
            console.print("Поддерживается: redis, postgres, pgadmin, redisinsight, ui")
            raise typer.Exit(code=2)

        text = _read_text(p)
        if not text.strip():
            console.print("[yellow]override-compose отсутствует или пустой — выключать нечего.[/yellow]")
            raise typer.Exit(code=0)
        if name == "ui":
            t = _ensure_sections(text)
            t = _remove_managed_block(t, "ui")
            svc_block, _ = _ui_disable_blocks()
            t = _inject_into_root_section(t, "services", svc_block)
            _write_text(p, t)
            console.print(f"[green]✓[/green] UI отключён (caddy через profiles) в {p}")
            return

        text2 = _remove_managed_block(text, name)
        _write_text(p, _ensure_sections(text2))
        console.print(f"[green]✓[/green] Выключил {name} в {p}")

    def _core_env_file(src: CoreSource) -> Path:
        # В docker-compose env_file указан как ../../.env (от deploy/dev),
        # т.е. это `core-runtime-service/.env`.
        return src.path / ".env"

    def _read_env_kv(p: Path) -> dict[str, str]:
        if not p.exists():
            return {}
        out: dict[str, str] = {}
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not ln or ln.lstrip().startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def _upsert_env(p: Path, updates: dict[str, str]) -> None:
        lines: list[str] = []
        existing = set()
        if p.exists():
            raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            raw = []
        for ln in raw:
            if ln and not ln.lstrip().startswith("#") and "=" in ln:
                k, _ = ln.split("=", 1)
                key = k.strip()
                if key in updates:
                    lines.append(f"{key}={updates[key]}")
                    existing.add(key)
                    continue
            lines.append(ln)
        for k, v in updates.items():
            if k not in existing:
                lines.append(f"{k}={v}")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _remove_env_keys(p: Path, keys: set[str]) -> None:
        if not p.exists():
            return
        out: list[str] = []
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln and not ln.lstrip().startswith("#") and "=" in ln:
                k, _ = ln.split("=", 1)
                if k.strip() in keys:
                    continue
            out.append(ln)
        p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    core_app = typer.Typer(
        help="Операции над CoreRuntime через docker compose",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @core_app.command("status")
    def core_status_cmd() -> None:
        """Проверить статус CoreRuntime (docker compose)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["ps", "core-runtime"])

    @core_app.command("up")
    def core_up_cmd(no_ui: bool = typer.Option(True, "--no-ui/--with-ui", help="Поднимать без UI (по умолчанию)")) -> None:
        """Поднять CoreRuntime (docker compose)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        if no_ui:
            _run_compose_interactive(console, src, ["up", "-d", "core-runtime"])
            console.print("[green]✓[/green] CoreRuntime поднят (без UI).")
        else:
            _run_compose_interactive(console, src, ["up", "-d"])
            console.print("[green]✓[/green] CoreRuntime + UI подняты.")

    @core_app.command("down")
    def core_down_cmd(volumes: bool = typer.Option(False, "-v", "--volumes")) -> None:
        """Остановить CoreRuntime (docker compose)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        args = ["down"]
        if volumes:
            args.append("-v")
        _run_compose_interactive(console, src, args)
        console.print("[green]✓[/green] CoreRuntime остановлен.")

    @core_app.command("reset")
    def core_reset(remove_env: bool = typer.Option(False, "--remove-env", help="Удалить локальный .env Core")) -> None:
        """Полный локальный reset: down -v (+ опционально удалить .env)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["down", "-v"])
        if remove_env:
            p = core_env_path(src.path)
            try:
                if p.exists():
                    p.unlink()
                    console.print(f"[green]✓[/green] Удалил {p}")
            except OSError:
                pass
        console.print("[green]✓[/green] Core reset выполнен (volumes удалены).")

    @core_app.command("logs")
    def core_logs_cmd(
        follow: bool = typer.Option(False, "-f", "--follow"),
        tail: int = typer.Option(200, "--tail"),
    ) -> None:
        """Логи CoreRuntime (docker compose)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("-f")
        args.append("core-runtime")
        _run_compose_interactive(console, src, args)

    ui_app = typer.Typer(
        help="Управление UI (docker compose service)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @ui_app.command("up")
    def ui_up() -> None:
        """Поднять UI вместе с Core (аналог `hc recovery core up --with-ui`)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["up", "-d"])
        console.print("[green]✓[/green] CoreRuntime + UI подняты.")

    @ui_app.command("down")
    def ui_down(service: str = typer.Option("caddy", "--service", help="Имя UI-сервиса в compose (по умолчанию caddy)")) -> None:
        """Остановить UI (docker compose stop <service>)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["stop", service])

    @ui_app.command("status")
    def ui_status(service: str = typer.Option("caddy", "--service", help="Имя UI-сервиса в compose (по умолчанию caddy)")) -> None:
        """Статус UI (docker compose ps <service>)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["ps", service])

    @ui_app.command("dev")
    def ui_dev(run: bool = typer.Option(False, "--run", help="Запустить pnpm web (иначе только покажет команду)")) -> None:
        """Dev UI (Vite): запуск фронта вне docker, с proxy на core :18000."""
        console = Console()
        repo_root = _find_repo_root()
        if not repo_root:
            console.print("[red]Ошибка: не нашёл корень монорепы.[/red]")
            raise typer.Exit(code=1)
        web_root = repo_root / "platform-home-console"
        if not web_root.exists():
            console.print(f"[red]Ошибка: не найден {web_root}[/red]")
            raise typer.Exit(code=1)
        cmd = f"cd {shlex.quote(str(web_root))} && pnpm web"
        console.print("Команда для dev UI (Vite):")
        console.print(f"  [bold]{cmd}[/bold]")
        console.print("Потом открыть: http://localhost:5173 (proxy на core:18000 уже в Vite).")
        if not run:
            return
        try:
            subprocess.run(cmd, shell=True, check=False)  # noqa: S602,S603
        except FileNotFoundError:
            console.print("[red]Ошибка: pnpm не найден.[/red]")
            raise typer.Exit(code=1)

    db_app = typer.Typer(
        help="Операции над локальной БД Core (SQLite на volume /data)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @db_app.command("status")
    def db_status() -> None:
        """Статус storage: sqlite/vault-postgres + проверка контейнеров/файлов."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        env_file = _core_env_file(src)
        env = _read_env_kv(env_file)

        def _container_env(keys: list[str]) -> dict[str, str]:
            out: dict[str, str] = {}
            # печатаем в виде KEY=VALUE (даже если пусто)
            # Важно: не используем f-string внутри `${...}`, чтобы не конфликтовать с Python-скобками.
            script = "; ".join(["echo " + k + "=${" + k + "-}" for k in keys])
            raw = _compose_capture(console, src, ["exec", "-T", "core-runtime", "sh", "-lc", script])
            for ln in raw.splitlines():
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    out[k.strip()] = v.strip()
            return out

        def _mask_dsn(dsn: str) -> str:
            # best-effort маскирование пароля в postgresql://user:pass@host/db
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

        effective = _container_env(
            [
                "RUNTIME_STORAGE_MODE",
                "RUNTIME_VAULT_STORAGE_TYPE",
                "RUNTIME_VAULT_PG_DSN",
                "RUNTIME_DB_PATH",
                "RUNTIME_VAULT_DB_PATH",
                "RUNTIME_VAULT_SECRET_DB_PATH",
            ]
        )

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

        # Покажем отличие env_file vs container, если есть
        diff_keys = ["RUNTIME_DB_PATH", "RUNTIME_VAULT_DB_PATH", "RUNTIME_VAULT_SECRET_DB_PATH"]
        diffs = []
        for k in diff_keys:
            a = (env.get(k) or "").strip()
            b = (effective.get(k) or "").strip()
            if a and b and a != b:
                diffs.append((k, a, b))
        if diffs:
            dt = Table(title="env mismatch (core .env vs container)")
            dt.add_column("key", style="bold")
            dt.add_column("core-runtime-service/.env")
            dt.add_column("container env (effective)")
            for k, a, b in diffs:
                dt.add_row(k, a, b)
            console.print(dt)

        # Проверки:
        # 1) core-runtime контейнер и файлы /data/*.db
        console.print(Panel.fit("Проверка файлов SQLite в core-runtime (/data)", title="checks"))
        file_tbl = Table(title="sqlite files on volume (/data)")
        file_tbl.add_column("path", style="bold")
        file_tbl.add_column("exists")
        file_tbl.add_column("size")

        def _file_row(path: str) -> None:
            # `stat -c` не везде, поэтому используем `ls -lah` как best-effort.
            out = _compose_capture(
                console,
                src,
                ["exec", "-T", "core-runtime", "sh", "-lc", f"ls -lah {shlex.quote(path)} 2>/dev/null || true"],
            ).strip()
            if not out:
                file_tbl.add_row(path, "[red]no[/red]", "")
                return
            # формат: -rw-r--r-- 1 user group 160K date time /data/runtime.db
            parts = out.split()
            size = parts[4] if len(parts) >= 5 else ""
            file_tbl.add_row(path, "[green]yes[/green]", size)

        for p in ["/data/runtime.db", "/data/vault.db", "/data/vault_secret.db"]:
            _file_row(p)
        console.print(file_tbl)

        missing_secret = (
            vault_type not in {"postgresql", "postgres"}
            and bool((effective.get("RUNTIME_VAULT_SECRET_DB_PATH") or env.get("RUNTIME_VAULT_SECRET_DB_PATH") or "").strip())
            and _compose_capture(
                console,
                src,
                ["exec", "-T", "core-runtime", "sh", "-lc", "test -f /data/vault_secret.db && echo yes || echo no"],
            ).strip()
            != "yes"
        )
        if missing_secret:
            console.print("[yellow]Внимание:[/yellow] ожидается vault_secret DB, но файла нет на /data.")
            console.print("Часто он создаётся лениво при первом использовании SecretStore/VAULT.")
            console.print("Next steps:")
            console.print("- `hc recovery core up` (или `hc recovery core up --with-ui`)")
            console.print("- повтори `hc recovery db status` после первого захода в UI/любого действия, которое пишет секреты")

        # 2) если vault на Postgres — проверим, что сервис postgres вообще поднят и доступен
        if vault_type in {"postgresql", "postgres"}:
            console.print(Panel.fit("Проверка postgres (docker compose ps + pg_isready)", title="checks"))
            _run_compose_interactive(console, src, ["ps", "postgres"])
            # pg_isready внутри контейнера postgres; не валим команду, просто печатаем
            _run_compose_interactive(console, src, ["exec", "-T", "postgres", "pg_isready", "-U", "postgres"])

    @db_app.command("shell")
    def db_shell(
        cmd: str = typer.Option("sh", "--cmd", help="Команда внутри core-runtime (например: sh)"),
    ) -> None:
        """Зайти в контейнер core-runtime (best-effort)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["exec", "core-runtime", *shlex.split(cmd)])

    @db_app.command("backup")
    def db_backup(
        out_dir: Path = typer.Option(Path("."), "--out-dir", help="Куда сохранить копию (директория)"),
        prefix: str = typer.Option("hc-sqlite", "--prefix", help="Префикс имён файлов"),
    ) -> None:
        """Скопировать SQLite базы из контейнера на хост (docker compose cp)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        out_dir.mkdir(parents=True, exist_ok=True)
        items = [
            ("/data/runtime.db", out_dir / f"{prefix}-runtime.db"),
            ("/data/vault.db", out_dir / f"{prefix}-vault.db"),
            ("/data/vault_secret.db", out_dir / f"{prefix}-vault_secret.db"),
        ]
        for container_path, dst in items:
            _run_compose_interactive(console, src, ["cp", f"core-runtime:{container_path}", str(dst)])
        console.print(f"[green]✓[/green] Бэкап сохранён в: {out_dir}")

    @db_app.command("restore")
    def db_restore(
        in_dir: Path = typer.Option(Path("."), "--in-dir", help="Откуда брать файлы (директория)"),
        prefix: str = typer.Option("hc-sqlite", "--prefix", help="Префикс имён файлов"),
    ) -> None:
        """Залить SQLite базы в контейнер (docker compose cp)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)

        items = [
            (in_dir / f"{prefix}-runtime.db", "/data/runtime.db"),
            (in_dir / f"{prefix}-vault.db", "/data/vault.db"),
            (in_dir / f"{prefix}-vault_secret.db", "/data/vault_secret.db"),
        ]
        for src_file, container_path in items:
            if not src_file.exists():
                console.print(f"[yellow]Пропускаю (нет файла): {src_file}[/yellow]")
                continue
            _run_compose_interactive(console, src, ["cp", str(src_file), f"core-runtime:{container_path}"])
        console.print("[green]✓[/green] Restore выполнен. Перезапусти Core: `hc recovery core up`")

    @db_app.command("switch")
    def db_switch(mode: str = typer.Argument("volume", help="Режим: volume | relative | vault-postgres | vault-sqlite")) -> None:
        """
        Переключить хранилище в Core `.env`.

        - volume/relative: только SQLite пути (как сейчас)
        - vault-postgres: vault в Postgres (dual-mode), runtime DB остаётся SQLite
        - vault-sqlite: vault обратно в SQLite (dual-mode)
        """
        console = Console()
        src = _resolve_source(console)
        env_file = _core_env_file(src)
        if mode not in {"volume", "relative", "vault-postgres", "vault-sqlite"}:
            console.print("[red]Ошибка: mode должен быть volume, relative, vault-postgres или vault-sqlite[/red]")
            raise typer.Exit(code=2)

        if mode == "vault-postgres":
            # Гарантируем, что в recovery включён postgres (override-compose).
            # Это просто правка override; запуск делается через `hc recovery core up --with-ui` или `compose up`.
            try:
                compose_enable("postgres")  # type: ignore[misc]
            except Exception:
                pass

            updates = {
                "RUNTIME_STORAGE_MODE": "dual",
                "RUNTIME_VAULT_STORAGE_TYPE": "postgresql",
                "RUNTIME_VAULT_PG_DSN": "postgresql://postgres:postgres@postgres:5432/core",
            }
            # Если раньше было SQLite-хранилище vault — не мешаем, но убираем путь, чтобы конфиг не путал.
            # (Core валидирует по RUNTIME_VAULT_STORAGE_TYPE)
            _upsert_env(env_file, updates)
            console.print(f"[green]✓[/green] Обновил {env_file} (vault → postgres)")
            console.print("Дальше: `hc recovery compose enable postgres` (если ещё не) и `hc recovery core up --with-ui`")
            raise typer.Exit(code=0)

        if mode == "vault-sqlite":
            env = _read_env_kv(env_file)
            # Подбираем пути: если уже есть — оставляем; иначе делаем volume-вариант.
            v_path = env.get("RUNTIME_VAULT_DB_PATH") or "/data/vault.db"
            vs_path = env.get("RUNTIME_VAULT_SECRET_DB_PATH") or "/data/vault_secret.db"
            updates = {
                "RUNTIME_STORAGE_MODE": "dual",
                "RUNTIME_VAULT_STORAGE_TYPE": "sqlite",
                "RUNTIME_VAULT_DB_PATH": v_path,
                "RUNTIME_VAULT_SECRET_DB_PATH": vs_path,
            }
            _upsert_env(env_file, updates)
            _remove_env_keys(env_file, {"RUNTIME_VAULT_PG_DSN"})
            console.print(f"[green]✓[/green] Обновил {env_file} (vault → sqlite)")
            console.print("Дальше: `hc recovery core up --with-ui`")
            raise typer.Exit(code=0)

        if mode == "volume":
            updates = {
                "RUNTIME_DB_PATH": "/data/runtime.db",
                "RUNTIME_VAULT_DB_PATH": "/data/vault.db",
                "RUNTIME_VAULT_SECRET_DB_PATH": "/data/vault_secret.db",
            }
        else:
            updates = {
                "RUNTIME_DB_PATH": "data/runtime.db",
                "RUNTIME_VAULT_DB_PATH": "data/vault.db",
                "RUNTIME_VAULT_SECRET_DB_PATH": "data/vault_secret.db",
            }
        # На всякий случай, если до этого был vault-postgres, удалим DSN.
        _remove_env_keys(env_file, {"RUNTIME_VAULT_PG_DSN"})
        _upsert_env(env_file, updates)
        console.print(f"[green]✓[/green] Обновил {env_file}")
        console.print("Дальше: `hc recovery core up`")

    redis_app = typer.Typer(
        help="Redis (docker compose service)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @redis_app.command("status")
    def redis_status() -> None:
        """Статус Redis (docker compose ps redis)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["ps", "redis"])

    @redis_app.command("up")
    def redis_up() -> None:
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["up", "-d", "redis"])
        console.print("[green]✓[/green] Redis поднят.")

    @redis_app.command("down")
    def redis_down() -> None:
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["stop", "redis"])
        console.print("[green]✓[/green] Redis остановлен.")

    @redis_app.command("flush")
    def redis_flush() -> None:
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["exec", "redis", "redis-cli", "FLUSHALL"])

    @redis_app.command("cli")
    def redis_cli() -> None:
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        _run_compose_interactive(console, src, ["exec", "redis", "redis-cli"])

    config_app = typer.Typer(
        help="Конфиг и пути hc",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @config_app.command("open-setup-log")
    def open_setup_log() -> None:
        """Открыть setup.log в pager ($PAGER, иначе less)."""
        console = Console()
        if not SETUP_LOG_PATH.exists():
            console.print("[yellow]Лога setup нет.[/yellow]")
            raise typer.Exit(code=0)
        pager = os.getenv("PAGER") or "less -R"
        cmd = [*shlex.split(pager), str(SETUP_LOG_PATH)]
        try:
            subprocess.run(cmd, check=False)  # noqa: S603
        except FileNotFoundError:
            console.print(f"[red]Ошибка: pager не найден: {pager}[/red]")
            raise typer.Exit(code=1)

    @recovery_app.command("doctor")
    def doctor() -> None:
        """Проверка локального окружения: docker/git/source/compose/.env/logs (+ capabilities если Core доступен)."""
        console = Console()
        table = Table(title="hc doctor (local)")
        table.add_column("Check", style="bold")
        table.add_column("Result")

        def ok(v: bool) -> str:
            return "[green]ok[/green]" if v else "[red]fail[/red]"

        has_docker = bool(shutil.which("docker"))  # type: ignore[name-defined]
        has_git = bool(shutil.which("git"))  # type: ignore[name-defined]
        table.add_row("docker", ok(has_docker))
        table.add_row("git", ok(has_git))

        src_ok = True
        try:
            src = _resolve_source(console)
            table.add_row("core source", f"[green]{src.path}[/green]")
            compose = src.compose_file()
            table.add_row("compose", ok(compose.exists()))
            table.add_row(".env", ok(core_env_path(src.path).exists()))
        except typer.Exit:
            src_ok = False
            table.add_row("core source", "[red]not found[/red]")

        table.add_row("hc config", ok(CONFIG_PATH.exists()))
        table.add_row("setup.log", ok(SETUP_LOG_PATH.exists()))
        console.print(table)
        if not SETUP_LOG_PATH.exists():
            console.print("[yellow]setup.log не найден.[/yellow] Если был setup — проверь, что он запускался из этого же окружения.")
        else:
            console.print("Открыть лог: [bold]hc recovery config open-setup-log[/bold]")
        if not (has_docker and has_git and src_ok):
            raise typer.Exit(code=1)

        # Если Core доступен и есть токен, покажем capabilities (best-effort).
        try:
            from hc.capabilities import probe
            from hc.config import Config

            cfg = Config.load()
            if cfg.core.host.strip() and cfg.core.token.strip():
                caps = probe(cfg)
                caps_table = Table(title="capabilities (best-effort)")
                caps_table.add_column("feature", style="bold")
                caps_table.add_column("available")
                caps_table.add_row("monitor/health", ok(caps.monitor_health))
                caps_table.add_row("auth/bootstrap", ok(caps.auth_bootstrap))
                caps_table.add_row("auth/me", ok(caps.auth_me))
                caps_table.add_row("admin/status", ok(caps.admin_status))
                caps_table.add_row("inspector/plugins", ok(caps.inspector_plugins))
                caps_table.add_row("admin/auth/api-keys", ok(caps.api_keys))
                console.print(caps_table)
        except Exception:
            pass

    @config_app.command("paths")
    def paths() -> None:
        """Показать пути, которые использует hc (конфиг/кэш)."""
        console = Console()
        body = (
            f"config dir: {CONFIG_DIR}\n"
            f"config file: {CONFIG_PATH}\n"
            f"data dir: {DATA_DIR}\n"
        )
        console.print(Panel(body, title="hc paths"))

    @config_app.command("show")
    def show_config() -> None:
        """Показать текущий конфиг (без маскирования)."""
        console = Console()
        if not CONFIG_PATH.exists():
            console.print("[yellow]Конфига нет.[/yellow]")
            raise typer.Exit(code=0)
        console.print(CONFIG_PATH.read_text(encoding="utf-8", errors="replace"))

    @config_app.command("edit")
    def edit_config() -> None:
        """Открыть config.toml в редакторе ($VISUAL/$EDITOR)."""
        console = Console()
        editor = os.getenv("VISUAL") or os.getenv("EDITOR")
        if not editor:
            console.print("[red]Ошибка: не задан $EDITOR (или $VISUAL).[/red]")
            console.print("Пример: `export EDITOR=nano` (или `vim`, `code -w`).")
            raise typer.Exit(code=1)

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text("", encoding="utf-8")

        cmd = [*shlex.split(editor), str(CONFIG_PATH)]
        try:
            subprocess.run(cmd, check=True)  # noqa: S603
        except FileNotFoundError:
            console.print(f"[red]Ошибка: редактор не найден: {editor}[/red]")
            raise typer.Exit(code=1)
        except subprocess.CalledProcessError:
            console.print("[red]Ошибка: редактор завершился с ошибкой[/red]")
            raise typer.Exit(code=1)

    @recovery_app.command("hint")
    def hint() -> None:
        """Короткая памятка: что делать, если ничего не работает."""
        console = Console()
        console.print(Panel.fit(
            "Локальный recovery (без HTTP):\n"
            "1) `hc recovery core up`\n"
            "2) `hc recovery core status`\n"
            "3) `hc recovery core logs -f`\n"
            "4) `hc recovery doctor`\n"
            "5) `hc recovery ui up` (поднимет caddy)\n"
            "6) `hc recovery redis up` (поднимет redis из override-compose)\n"
            "7) `hc recovery db backup --out-dir ./backups`\n"
            "8) `hc recovery config edit`\n"
            "\n"
            "Очистка: `hc reset all`\n"
            f"Лог setup: {SETUP_LOG_PATH}",
            title="Recovery hint",
        ))

    @recovery_app.callback(invoke_without_command=True)
    def _root(ctx: typer.Context) -> None:
        # Без подкоманды показываем hint.
        if ctx.invoked_subcommand is None:
            hint()

    recovery_app.add_typer(core_app, name="core")
    recovery_app.add_typer(ui_app, name="ui")
    recovery_app.add_typer(db_app, name="db")
    recovery_app.add_typer(redis_app, name="redis")
    recovery_app.add_typer(config_app, name="config")
    recovery_app.add_typer(compose_app, name="compose")

    app.add_typer(recovery_app, name="recovery")

