from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hc.commands.env._catalog import (
    _Svc, _SERVICES, _PROFILE_DEFAULT_MODE, _PROFILES,
    _DbOption, _DB_OPTIONS, _DB_KEY_MAP, EnvUpPlan, VAULT_PG_DSN_DEFAULT,
    _MODE_DEFAULT, _MODE_HELP, _PROFILE_HELP, _DB_HELP,
    _STATE_COLOR, _REBUILD_HINT_RE, _MIGRATION_HINT_RE,
    KNOWN_ENDPOINTS, QUESTIONARY_STYLE_KWARGS, _FRONTEND_VITE_OVERRIDE,
)
from hc.commands.env._git import (
    _git, PullResult, pull_git_repo, fetch_incoming_commits,
    _restore_stash, pull_core_source, _try_pull_source,
)
from hc.commands.env._diagnostics import (
    _collect_postmortem_targets, _run_postmortem, _print_postmortem,
    _get_needed_ports, _parse_docker_labels, _parse_published_ports,
    _process_command_line, _find_host_listeners, _find_port_conflicts,
    _kill_process, _offer_resolve_conflicts, _show_failure_logs,
    detect_compose_stack_split, detect_compose_stack_split_for_project,
    _warn_compose_stack_split, apply_compose_stack_split_fix,
    validate_core_source_tree,
)
from hc.commands.env._resolve import (
    _resolve_source, _pick_services_interactive, _pick_db_interactive,
    _resolve_services, _resolve_db, _resolve_mode, _resolve_env_up_plan,
)
from hc.commands.env._status import (
    _print_env_status_dashboard, _print_env_ps,
    _print_env_up_dry_run, _print_env_down_dry_run, _print_summary,
)
from hc.commands.env._compose import planned_config_files_from_cmd, compose_project_name
from hc.config import Config
from hc.core_ops import ComposeProject, compose_project_from_source, require_docker
from hc.core_source import (
    CoreSource,
    ensure_workspace_pinned,
    get_core_source_from_repo,
    get_core_source_local,
    init_core_source,
    init_platform_source,
    resolve_workspace_root,
)
from hc.diagnostics import (
    DetectedIssue,
    detect_issues,
    fetch_container_logs,
    list_compose_containers,
)
from hc.env_bootstrap import ensure_core_env
from hc.env_state import load_last_env, save_last_env
from hc.errors import CoreSourcesNotFoundError, HcCliError
from hc.hints import ENV_STACK_HELP, ENV_VS_CORE_DOTENV
from hc.constants import QUESTIONARY_STYLE_KWARGS
from hc.json_output import print_json
from hc.vault_ops import (
    DbKind,
    detect_running_db,
    reset_vault_postgres,
    reset_vault_sqlite,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

# `_find_repo_root` / `_MONOREPO_SIBLINGS` переехали в hc.core_source.
# Используй `resolve_workspace_root()` — он смотрит HC_WORKSPACE, cwd и конфиг.


def _run(cmd: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> None:
    env = {**os.environ, **extra_env} if extra_env else None
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    except subprocess.TimeoutExpired:
        from rich.console import Console as _C
        _C().print(f"[red]Таймаут:[/red] команда зависла: {' '.join(cmd[:3])}\nПроверь что docker daemon запущен: docker info")
        raise typer.Exit(code=1)
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


# ─── Post-mortem after failed up ──────────────────────────────────────────────

def _compose_with_profiles(
    project: ComposeProject,
    running: set[str],
) -> list[str]:
    cmd = ["docker", "compose", "-f", str(project.compose_file)]
    for profile in sorted(_detect_active_profiles(running)):
        cmd += ["--profile", profile]
    return cmd


# Дефолтные публичные порты для подсказок в `hc env ps` / status.
# Используй KNOWN_ENDPOINTS из hc.constants.
from hc.constants import KNOWN_ENDPOINTS


def _compose_ps_rows(project: ComposeProject) -> list[dict[str, object]]:
    import json

    r = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(project.compose_file),
            "ps",
            "-a",
            "--format",
            "json",
        ],
        cwd=str(project.cwd),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    rows: list[dict[str, object]] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows


def _env_ps_entries(project: ComposeProject) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for row in _compose_ps_rows(project):
        service = str(row.get("Service") or row.get("Name") or "?")
        ports = str(row.get("Publishers") or row.get("Ports") or "")
        if ports in ("", "[]"):
            ports = ""
        entries.append(
            {
                "service": service,
                "state": str(row.get("State") or row.get("Status") or "?"),
                "ports": ports,
                "url_hint": KNOWN_ENDPOINTS.get(service, ""),
            }
        )
    return entries


def _get_running_services(compose_file: Path, cwd: Path) -> set[str]:
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(compose_file),
             "ps", "--services", "--filter", "status=running"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if r.returncode == 0:
            return {s.strip() for s in r.stdout.splitlines() if s.strip()}
    except Exception:  # noqa: BLE001
        pass
    return set()


def _warn_orphan_volumes(console: Console, cwd: Path) -> None:
    """After down -v, check for volumes that compose didn't remove (not in top-level volumes:)."""
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        project = cwd.name  # compose project name = directory name
        orphans = [
            v for v in r.stdout.splitlines()
            if v.startswith(f"{project}_") or v.startswith("dev_")
        ]
        if orphans:
            console.print(f"[yellow]![/yellow] Volumes не удалены (нет в top-level volumes секции compose):")
            for v in orphans:
                console.print(f"  [dim]docker volume rm {v}[/dim]")
    except Exception:  # noqa: BLE001
        pass


def _detect_active_profiles(running: set[str]) -> set[str]:
    """Return compose profiles that have at least one running container."""
    profiles: set[str] = set()
    for svcs in _SERVICES.values():
        for s in svcs:
            if s.compose_profile and s.name in running:
                profiles.add(s.compose_profile)
    for opt in _DB_OPTIONS:
        if opt.compose_profile and opt.service and opt.service in running:
            profiles.add(opt.compose_profile)
    return profiles


def _detect_active_db(running: set[str]) -> str:
    """Return the DB key ('postgres' | 'sqlite') based on running containers."""
    for opt in _DB_OPTIONS:
        if opt.service and opt.service in running:
            return opt.key
    return "sqlite"



def _compose_project_name(plan: EnvUpPlan) -> str:
    import json

    base = _compose_base_cmd(plan)
    r = subprocess.run(  # noqa: S603
        [*base, "config", "--format", "json"],
        cwd=str(plan.project.cwd),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode == 0:
        try:
            name = json.loads(r.stdout).get("name")
            if name:
                return str(name)
        except json.JSONDecodeError:
            pass
    return plan.project.cwd.name


def _frontend_workspace_path(plan: "EnvUpPlan") -> Path:
    """Путь к platform-home-console (sibling core-runtime-service для volume mount)."""
    core_root = plan.project.cwd.parent.parent  # deploy/dev → core-runtime-service
    return core_root.parent / "platform-home-console"


def _prepare_env_source(console: Console, src: CoreSource, mode: str) -> CoreSource:
    """Привязать workspace, проверить исходники и собрать расщеплённый стек до env up."""
    ensure_workspace_pinned(console, quiet=True)
    validate_core_source_tree(src.path)

    project = compose_project_from_source(console, src, mode=mode)
    split = detect_compose_stack_split_for_project(compose_project_name(project))
    if split and (split.already_split or split.mixed_sources):
        _warn_compose_stack_split(console, split)
        if apply_compose_stack_split_fix(console, split):
            workspace = resolve_workspace_root()
            if workspace is not None:
                new_src = get_core_source_from_repo(workspace)
                if new_src is not None:
                    src = new_src
                    validate_core_source_tree(src.path)
    return src


def _strip_frontend_from_plan(console: Console, plan: "EnvUpPlan") -> None:
    plan.service_names[:] = [s for s in plan.service_names if s != "frontend-vite"]
    plan.compose_profiles[:] = [p for p in plan.compose_profiles if p != "frontend"]
    console.print("[yellow]![/yellow] frontend-vite убран из плана запуска")


def _ensure_frontend_workspace(
    console: Console,
    plan: "EnvUpPlan",
) -> tuple[bool, set[str]]:
    """
    Если в plan есть frontend-vite — гарантировать исходники platform-home-console.

    При отсутствии/пустой папке клонирует автоматически (как core init), без диалога.
    Возвращает (ok, services_to_recreate) — второй элемент для --force-recreate
    после свежего клона.
    """
    if "frontend-vite" not in plan.service_names:
        return True, set()

    workspace = _frontend_workspace_path(plan)
    pkg_json = workspace / "package.json"

    if pkg_json.is_file():
        return True, set()

    can_clone = (not workspace.exists()) or (
        workspace.exists() and not any(workspace.iterdir())
    )

    if can_clone:
        console.print(
            f"[cyan]→[/cyan] Скачиваю platform-home-console в [bold]{workspace}[/bold] ..."
        )
        try:
            init_platform_source(console, target=workspace)
        except typer.Exit:
            console.print("[red]Не удалось скачать platform-home-console.[/red]")
            _strip_frontend_from_plan(console, plan)
            return True, set()
        if not pkg_json.is_file():
            console.print(
                "[red]Клон завершился, но package.json не найден — пропускаю frontend-vite.[/red]"
            )
            _strip_frontend_from_plan(console, plan)
            return True, set()
        # Свежий клон — контейнер надо пересоздать (старый мог смонтировать пустую папку).
        return True, {"frontend-vite"}

    console.print(
        f"\n[yellow]⚠[/yellow] В [bold]{workspace}[/bold] нет [bold]package.json[/bold] "
        f"— frontend-vite не запустится."
    )
    console.print(
        "  [dim]Папка не пустая, автоклон не делаю (может быть чужое содержимое).[/dim]"
    )

    if sys.stdin.isatty():
        try:
            import questionary

            skip = questionary.confirm(
                "Продолжить без frontend-vite?",
                default=True,
            ).ask()
        except ImportError:
            skip = True
        if skip is None:
            return False, set()
        if skip:
            _strip_frontend_from_plan(console, plan)
            return True, set()
        return False, set()

    _strip_frontend_from_plan(console, plan)
    return True, set()


def _prune_non_running_compose_services(
    console: Console,
    plan: "EnvUpPlan",
    *,
    extra_force: set[str] | None = None,
) -> None:
    """
    Удалить контейнеры сервисов из плана, которые не в состоянии running.

    Лечит «network … not found» и старые bind-mount'ы (контейнер создан когда
    /workspace был пуст, а исходники появились позже).
    """
    extra_force = extra_force or set()
    try:
        containers = list_compose_containers(plan.project.compose_file, plan.project.cwd)
    except Exception:  # noqa: BLE001
        return

    by_service = {str(c.get("Service") or ""): c for c in containers if c.get("Service")}
    to_rm: list[str] = []

    for svc in plan.service_names:
        cont = by_service.get(svc)
        if not cont:
            continue
        state = str(cont.get("State") or cont.get("Status") or "").lower()
        if svc in extra_force or not state.startswith("running"):
            to_rm.append(svc)

    to_rm = list(dict.fromkeys(to_rm))
    if not to_rm:
        return

    base = _compose_base_cmd(plan)
    console.print(f"[dim]→ пересоздаю контейнеры: {', '.join(to_rm)}[/dim]")
    subprocess.run(  # noqa: S603
        [*base, "rm", "-sf", *to_rm],
        cwd=str(plan.project.cwd),
        check=False,
        capture_output=True,
    )


def _compose_up_with_stale_retry(
    console: Console,
    plan: "EnvUpPlan",
    up_cmd: list[str],
    *,
    extra_env: dict[str, str] | None,
    force_recreate: set[str] | None = None,
) -> None:
    """docker compose up; при сбое — снести не-running контейнеры и повторить один раз."""
    _prune_non_running_compose_services(console, plan, extra_force=force_recreate or set())

    try:
        _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)
    except typer.Exit as first_err:
        if (first_err.exit_code or 0) == 0:
            raise

        needed_ports = _get_needed_ports(plan)
        conflicts = _find_port_conflicts(needed_ports, plan)
        if conflicts:
            console.print("[yellow]→[/yellow] похоже, порты заняты — предлагаю освободить ...")
            _offer_resolve_conflicts(conflicts, console)
            _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)
            return

        console.print(
            "[yellow]→[/yellow] compose up не удался — очищаю зависшие контейнеры и повторяю ..."
        )
        _prune_non_running_compose_services(console, plan, extra_force=set(plan.service_names))
        try:
            _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)
        except typer.Exit:
            raise first_err from None


def _check_disk_space(console: Console) -> None:
    """Проверить свободное место; предупредить и предложить расширение если мало."""
    import shutil as _shutil

    stat = _shutil.disk_usage("/")
    free_gb = stat.free / 1024 ** 3
    total_gb = stat.total / 1024 ** 3
    used_pct = stat.used / stat.total * 100

    if free_gb >= 2.0:
        return

    color = "red" if free_gb < 0.5 else "yellow"
    console.print(
        f"\n[{color}]⚠ Мало места на диске[/{color}]  "
        f"свободно [bold]{free_gb:.2f} GB[/bold] из {total_gb:.1f} GB "
        f"([bold]{used_pct:.0f}%[/bold] занято)"
    )
    console.print(
        "  [dim]Быстрая очистка:[/dim] "
        "[bold]docker system prune -af --volumes[/bold]"
    )

    lvm = _detect_lvm_opportunity()
    if lvm:
        _offer_lvm_extend(console, lvm)
    elif free_gb < 0.5:
        if not sys.stdin.isatty():
            from hc.errors import HcCliError
            raise HcCliError(
                message=f"Критически мало места: {free_gb:.2f} GB.",
                exit_code=1,
                hint="docker system prune -af --volumes",
            )
        try:
            import questionary
            if not questionary.confirm(
                "Критически мало места. Продолжить всё равно?", default=False
            ).ask():
                raise typer.Exit(code=1)
        except ImportError:
            pass

    console.print()


def _detect_lvm_opportunity() -> dict | None:
    """Вернуть info-словарь если / на LVM и в VG есть свободное место (>1 GB)."""
    try:
        df = subprocess.run(  # noqa: S603
            ["df", "/", "--output=source"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        lines = [l for l in df.stdout.strip().splitlines() if l.strip() and not l.startswith("Source")]
        if not lines or not lines[-1].startswith("/dev/mapper/"):
            return None
        dev = lines[-1].strip()

        # Try vgs without sudo first, then with sudo -n (no-password)
        for cmd_prefix in ([], ["sudo", "-n"]):
            vgs = subprocess.run(  # noqa: S603
                [*cmd_prefix, "vgs", "--units", "g", "--noheadings", "--nosuffix",
                 "-o", "vg_name,vg_free"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if vgs.returncode == 0:
                break
        else:
            # Can't read VGs — still LVM, report without sizes
            return {"dev": dev, "lv_path": None, "vg_name": None, "vg_free_gb": None}

        for line in vgs.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            try:
                vg_free_gb = float(parts[1])
            except ValueError:
                continue
            if vg_free_gb < 1.0:
                continue
            vg_name = parts[0]

            lv_path = None
            for lv_prefix in ([], ["sudo", "-n"]):
                lvs = subprocess.run(  # noqa: S603
                    [*lv_prefix, "lvs", "--noheadings", "-o", "lv_path,vg_name"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if lvs.returncode == 0:
                    for lv_line in lvs.stdout.splitlines():
                        lp = lv_line.strip().split()
                        if len(lp) >= 2 and lp[1] == vg_name:
                            lv_path = lp[0]
                            break
                    break

            return {"dev": dev, "lv_path": lv_path, "vg_name": vg_name, "vg_free_gb": vg_free_gb}

    except Exception:  # noqa: BLE001
        pass
    return None


def _offer_lvm_extend(console: Console, lvm: dict) -> None:
    """Показать или выполнить расширение LVM раздела."""
    vg_free: float | None = lvm.get("vg_free_gb")
    lv_path: str | None = lvm.get("lv_path")
    dev: str = lvm.get("dev", "")

    if vg_free and vg_free > 1.0:
        console.print(
            f"\n[green]LVM:[/green] обнаружено [bold]{vg_free:.1f} GB[/bold] "
            "свободного места в VG — можно расширить раздел."
        )
        lv = lv_path or "<lv_path>"
        console.print(f"  [dim]1.[/dim] [bold]sudo lvextend -l +100%FREE {lv}[/bold]")
        console.print(f"  [dim]2.[/dim] [bold]sudo resize2fs {dev}[/bold]")

        if sys.stdin.isatty() and lv_path:
            try:
                import questionary
                if questionary.confirm(
                    f"Расширить LVM автоматически (+{vg_free:.1f} GB)?",
                    default=True,
                ).ask():
                    r1 = subprocess.run(  # noqa: S603
                        ["sudo", "lvextend", "-l", "+100%FREE", lv_path], check=False
                    )
                    if r1.returncode != 0:
                        console.print("[red]✗[/red] lvextend завершился с ошибкой.")
                        return
                    r2 = subprocess.run(  # noqa: S603
                        ["sudo", "resize2fs", dev], check=False
                    )
                    if r2.returncode == 0:
                        import shutil as _shutil
                        free_after = _shutil.disk_usage("/").free / 1024 ** 3
                        console.print(
                            f"[green]✓[/green] Диск расширен! "
                            f"Свободно: [bold]{free_after:.1f} GB[/bold]"
                        )
                    else:
                        console.print("[red]✗[/red] resize2fs завершился с ошибкой.")
            except ImportError:
                pass
    else:
        console.print("\n[dim]LVM обнаружен, но свободного места в VG нет.[/dim]")


def _render_frontend_override_body(core_root: Path) -> str:
    # ВАЖНО: запускаем vite через `pnpm --filter=web exec vite`, а не через
    # `pnpm --filter=web dev -- --host …`. Скрипт dev в apps/web сводится к
    # `vite`, и `pnpm run … -- --host` добавляет лишний `--`, из-за чего vite
    # получает `vite -- --host …` и игнорирует флаги — поднимается на
    # localhost:5173 и Caddy не может достучаться (502).
    #
    # Перед exec собираем `@platform/*` пакеты (это делает скрипт dev,
    # повторяем явно).
    cmd = (
        "corepack enable && "
        "pnpm install --frozen-lockfile && "
        "pnpm api:gen && "
        "pnpm --filter='@platform/*' build && "
        "pnpm --filter=web exec vite --host 0.0.0.0 --port 5173"
    )
    return f"""\
services:
  frontend-vite:
    environment:
      VITE_CORE_PROXY_TARGET: http://core-runtime:8000
    volumes:
      - {core_root}:/core-runtime-service:ro
    command: ["sh", "-c", "{cmd}"]
"""


def _write_frontend_compose_override(plan: EnvUpPlan) -> Path | None:
    """Сгенерировать compose-override для корректной сборки web-приложения."""
    if "frontend-vite" not in plan.service_names:
        return None
    from hc.constants import DATA_DIR

    # core_root — это абсолютный путь к core-runtime-service на хосте.
    # _frontend_workspace_path делает parent.parent от compose cwd (deploy/dev),
    # это core-runtime-service; нам нужен сам core-runtime-service.
    core_root = plan.project.cwd.parent.parent
    body = _render_frontend_override_body(core_root)

    path = DATA_DIR / "compose-overrides" / _FRONTEND_VITE_OVERRIDE
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    return path


def _build_compose_env(plan: EnvUpPlan) -> dict[str, str]:
    """Переменные окружения для docker compose (DB + frontend/caddy + debug)."""
    env = dict(plan.db_option.env or {})
    if "frontend-vite" in plan.service_names:
        # Caddy должен проксировать UI на Vite, а не на пустую ./frontend
        env.setdefault("CADDYFILE_PATH", "./Caddyfile.hmr")
    # Dev modes: enable DEBUG for core-runtime (rate limiting, verbose logs)
    if plan.mode in ("dev", "dev-reload"):
        env.setdefault("DEBUG", "1")
        env.setdefault("DEBUG_MODE", "1")
    return env


def _compose_base_cmd(plan: EnvUpPlan) -> list[str]:
    cmd = ["docker", "compose", "-f", str(plan.project.compose_file)]
    override = _write_frontend_compose_override(plan)
    if override:
        cmd += ["-f", str(override)]
    for cp in sorted(plan.compose_profiles):
        cmd += ["--profile", cp]
    return cmd


def _ensure_frontend_static_build(console: Console, plan: EnvUpPlan) -> None:
    """
    Если caddy в плане без frontend-vite — нужна статика в deploy/dev/frontend.
    Собираем pnpm build:web в одноразовом node-контейнере, если index.html нет.
    """
    if "frontend-vite" in plan.service_names or "caddy" not in plan.service_names:
        return

    frontend_dir = plan.project.cwd / "frontend"
    if (frontend_dir / "index.html").is_file():
        return

    workspace = _frontend_workspace_path(plan)
    if not (workspace / "package.json").is_file():
        console.print(
            "\n[yellow]![/yellow] [bold]deploy/dev/frontend[/bold] пуст — UI через caddy (:18080) "
            "не будет работать.\n"
            "  Добавь [cyan]frontend-vite[/cyan] в env up или склонируй platform-home-console."
        )
        return

    hc_root = workspace.parent
    console.print(
        f"\n[cyan]→[/cyan] Собираю статику платформы → [bold]{frontend_dir}[/bold] …"
    )
    frontend_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "set -e; corepack enable; pnpm install --frozen-lockfile; "
        "pnpm build:web; cp -r apps/web/dist/. /out/"
    )
    r = subprocess.run(  # noqa: S603
        [
            "docker", "run", "--rm",
            "-v", f"{hc_root}:/hc",
            "-v", f"{frontend_dir}:/out",
            "-w", "/hc/platform-home-console",
            "node:22-alpine",
            "sh", "-c", script,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if r.returncode != 0 or not (frontend_dir / "index.html").is_file():
        err = (r.stderr or r.stdout or "build failed").strip()
        console.print(f"[red]Не удалось собрать frontend:[/red] {err[:500]}")
        console.print(
            "[dim]Подсказка: выбери frontend-vite в env up для HMR вместо статики.[/dim]"
        )
        return
    console.print("[green]✓[/green] Статика платформы собрана")


# ─── Command registration ─────────────────────────────────────────────────────

def register(app: typer.Typer) -> None:
    env_app = typer.Typer(
        help=ENV_STACK_HELP,
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @env_app.command("up")
    def env_up(
        mode: str | None = typer.Option(None, "--mode", "-m", help=_MODE_HELP),
        profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_HELP),
        db: str | None = typer.Option(None, "--db", help=_DB_HELP),
        pull: bool = typer.Option(False, "--pull/--no-pull", help="docker compose pull перед up"),
        build: bool = typer.Option(False, "--build/--no-build", help="Пересобрать образы перед up"),
        detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Запустить в фоне"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план без запуска compose"),
    ) -> None:
        """
        Поднять dev-окружение.

        Шаг 1: чекбоксы — выбор сервисов (уже запущенные помечены ● running).
        Шаг 2: radio — выбор БД (SQLite / PostgreSQL).

        Примеры:
          hc env up                           # интерактив
          hc env up --profile hmr             # core + caddy + Vite HMR, спросит DB
          hc env up --profile base --db pg    # core + caddy, PostgreSQL, без вопросов
          hc env up --build                   # пересобрать образ и поднять
        """
        console = Console()
        try:
            if not dry_run:
                require_docker(console)
                _check_disk_space(console)
            mode = _resolve_mode(mode, profile)
            if mode not in _SERVICES:
                console.print(f"[red]Ошибка:[/red] неизвестный режим {mode!r}. Допустимые: {' | '.join(_SERVICES)}")
                raise typer.Exit(code=2)

            src = _resolve_source(console)
            if not dry_run:
                src = _prepare_env_source(console, src, mode)
            created = False
            if not dry_run:
                created = ensure_core_env(console, src.path)
            if not dry_run:
                _try_pull_source(src, console)

            plan = _resolve_env_up_plan(
                console=console,
                mode=mode,
                profile=profile,
                db=db,
                src=src,
                first_run=created,
            )
            save_last_env(
                mode=plan.mode,
                services=plan.service_names,
                db=plan.db_option.key,
            )

            if not dry_run and created and "core-runtime" in plan.service_names:
                _apply_first_run_storage_choice(src.path / ".env", plan.db_option.key, console)

            if dry_run:
                _print_env_up_dry_run(console, plan, pull=pull, build=build, detach=detach)
                return

            # Источник исходников: workspace (dev-монорепо) vs managed-клон.
            # Если используется workspace — печатаем явно, чтобы человек понимал,
            # что контейнер смонтирует именно его рабочую копию.
            workspace = resolve_workspace_root()
            src_path = Path(plan.project.cwd).parent.parent.resolve()
            using_workspace = (
                workspace is not None
                and src_path == (workspace / "core-runtime-service").resolve()
            )
            if using_workspace:
                src_label = f"[green]workspace[/green] {workspace}"
            else:
                src_label = f"managed-клон {src_path}"

            console.print(
                f"\n[cyan]→[/cyan] env up  "
                f"mode=[bold]{plan.mode}[/bold]  "
                f"db=[bold]{plan.db_option.key}[/bold]  "
                f"services=[bold]{', '.join(plan.service_names)}[/bold]"
            )
            console.print(f"   [dim]src:[/dim] {src_label}")

            # Подсказка: cwd внутри монорепо, но мы используем managed-клон.
            # Часто это значит «юзер думает что редактирует свой репо, а
            # контейнер смотрит в чужой клон». Один совет ради сэкономленного
            # часа отладки.
            if not using_workspace:
                from hc.core_source import _scan_upwards_for_monorepo

                cwd_repo = _scan_upwards_for_monorepo(Path.cwd())
                if cwd_repo and Config.load().workspace.path.strip() == "":
                    console.print(
                        f"   [dim]hint:[/dim] [yellow]cwd внутри монорепо ({cwd_repo}),[/yellow]"
                        f" [yellow]но workspace не привязан — правки не дойдут до контейнера.[/yellow]"
                    )
                    console.print(
                        f"   [dim]      →[/dim] [cyan]hc workspace set {cwd_repo}[/cyan]"
                    )

            # frontend-vite в плане → автоклон platform-home-console (без диалога).
            ok, recreate = _ensure_frontend_workspace(console, plan)
            if not ok:
                raise typer.Exit(code=1)

            # caddy без vite → собрать статику в deploy/dev/frontend если пусто.
            _ensure_frontend_static_build(console, plan)

            # plan.service_names / compose override могли измениться — пересобираем:
            base_cmd = _compose_base_cmd(plan)
            extra_env = _build_compose_env(plan)

            split_issue = detect_compose_stack_split(
                plan,
                planned_config_files=planned_config_files_from_cmd(base_cmd),
            )
            if split_issue:
                _warn_compose_stack_split(console, split_issue)
                if apply_compose_stack_split_fix(console, split_issue):
                    src = _resolve_source(console)
                    plan = _resolve_env_up_plan(
                        console=console,
                        mode=mode,
                        profile=profile,
                        db=db,
                        src=src,
                        first_run=False,
                    )
                    workspace = resolve_workspace_root()
                    src_path = Path(plan.project.cwd).parent.parent.resolve()
                    using_workspace = (
                        workspace is not None
                        and src_path == (workspace / "core-runtime-service").resolve()
                    )
                    if using_workspace:
                        console.print(
                            f"   [dim]src:[/dim] [green]workspace[/green] {workspace}"
                        )
                    else:
                        console.print(f"   [dim]src:[/dim] managed-клон {src_path}")
                    ok, recreate = _ensure_frontend_workspace(console, plan)
                    if not ok:
                        raise typer.Exit(code=1)
                    _ensure_frontend_static_build(console, plan)
                    base_cmd = _compose_base_cmd(plan)
                    extra_env = _build_compose_env(plan)
                    split_issue = detect_compose_stack_split(
                        plan,
                        planned_config_files=planned_config_files_from_cmd(base_cmd),
                    )
                    if split_issue:
                        console.print(
                            "[yellow]![/yellow] стек всё ещё выглядит расщеплённым — "
                            "продолжаю env up."
                        )

            if pull:
                _run(
                    [*base_cmd, "pull", *plan.service_names],
                    cwd=plan.project.cwd,
                    extra_env=extra_env,
                )

            needed_ports = _get_needed_ports(plan)
            conflicts = _find_port_conflicts(needed_ports, plan)
            if conflicts:
                _offer_resolve_conflicts(conflicts, console)

            up_cmd = [*base_cmd, "up"]
            if detach:
                up_cmd.append("-d")
            if build:
                up_cmd.append("--build")
            up_cmd += plan.service_names

            try:
                _compose_up_with_stale_retry(
                    console,
                    plan,
                    up_cmd,
                    extra_env=extra_env,
                    force_recreate=recreate,
                )
            except typer.Exit as exit_exc:
                # docker compose ушёл с ошибкой:
                #   1) Сначала покажем сырые логи упавших сервисов (контекст для человека).
                #   2) Потом прогоним через детектор известных проблем и подскажем действия.
                if (exit_exc.exit_code or 0) != 0:
                    _show_failure_logs(console, plan)
                    issues, scanned = _run_postmortem(console, plan.project)
                    _print_postmortem(console, issues, scanned_services=scanned)
                raise

            if detach:
                console.print("[green]✓[/green] env up ok")
                _print_summary(
                    mode=plan.mode,
                    compose_file=plan.project.compose_file,
                    services=plan.service_names,
                    was_running=plan.running,
                    db_option=plan.db_option,
                    console=console,
                )

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("down")
    def env_down(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        volumes: bool = typer.Option(False, "--volumes", "-v", help="Удалить volumes (БД, кэш)"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план без остановки compose"),
    ) -> None:
        """Остановить dev-окружение (автоматически определяет активные профили)."""
        console = Console()
        try:
            if not dry_run:
                require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            running = _get_running_services(project.compose_file, project.cwd)
            active_profiles = _detect_active_profiles(running)
            active_db = _detect_active_db(running)

            if dry_run:
                _print_env_down_dry_run(
                    console,
                    mode=mode,
                    project=project,
                    running=running,
                    active_profiles=active_profiles,
                    active_db=active_db,
                    volumes=volumes,
                )
                return

            profile_hint = f"  profiles=[bold]{', '.join(sorted(active_profiles))}[/bold]" if active_profiles else ""
            console.print(f"[cyan]→[/cyan] env down  mode=[bold]{mode}[/bold]  db=[bold]{active_db}[/bold]{profile_hint}")

            cmd = ["docker", "compose", "-f", str(project.compose_file)]
            for profile in sorted(active_profiles):
                cmd += ["--profile", profile]
            cmd.append("down")

            if volumes:
                if active_db == "sqlite" and sys.stdin.isatty():
                    console.print(
                        "[yellow]![/yellow] SQLite хранит данные в volume [bold]core-data[/bold] — "
                        "они будут удалены вместе с остальными volumes."
                    )
                    try:
                        import questionary
                        confirmed = questionary.confirm(
                            "Удалить volumes (данные SQLite будут потеряны)?",
                            default=False,
                        ).ask()
                    except ImportError:
                        confirmed = True
                    if not confirmed:
                        console.print("[dim]Volumes оставлены.[/dim]")
                        raise typer.Exit(code=0)
                cmd.append("-v")

            _run(cmd, cwd=project.cwd)
            console.print("[green]✓[/green] env down ok")

            if volumes:
                _warn_orphan_volumes(console, project.cwd)

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("logs")
    def env_logs(
        service: str | None = typer.Argument(None, help="Сервис (пусто = все)"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        follow: bool = typer.Option(False, "-f", "--follow", help="Следить за логами"),
        tail: int = typer.Option(100, "--tail", help="Кол-во последних строк"),
        grep: str | None = typer.Option(
            None,
            "--grep",
            "-g",
            help="Фильтр regex (case-insensitive) по строкам лога. Работает и с -f.",
        ),
        invert: bool = typer.Option(
            False,
            "--invert",
            "-v",
            help="Инверсия фильтра --grep (показывать строки, НЕ соответствующие).",
        ),
    ) -> None:
        """Логи сервисов dev-окружения.

        Примеры:
          hc env logs -f                          — стрим всех сервисов
          hc env logs core-runtime --tail 500     — последние 500 строк ядра
          hc env logs caddy -f --grep "/api/v1"   — стрим только запросов к API
          hc env logs core-runtime -g ERROR -g WARN  — несколько паттернов: OR (пока один)
          hc env logs core-runtime -g 200 -v      — всё кроме «200» в строке
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            cmd = [
                "docker", "compose", "-f", str(project.compose_file),
                "logs", "--tail", str(tail),
            ]
            if follow:
                cmd.append("-f")
            if service:
                cmd.append(service)

            # Простой случай — без фильтра: пробросим вывод docker напрямую, чтобы
            # сохранить цвета и читаемость. Фильтр включается только когда нужно.
            if not grep:
                _run(cmd, cwd=project.cwd)
                return

            # Фильтрация: подписываемся на stdout/stderr docker compose построчно
            # и пропускаем через regex. `-f` отлично работает потому что мы
            # не ждём завершения процесса, а итерируемся по строкам.
            import re as _re

            try:
                pattern = _re.compile(grep, _re.IGNORECASE)
            except _re.error as exc:
                console.print(f"[red]Ошибка:[/red] невалидный regex '{grep}': {exc}")
                raise typer.Exit(code=2)

            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(project.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            try:
                assert proc.stdout is not None  # noqa: S101 (для типов)
                for line in proc.stdout:
                    match = bool(pattern.search(line))
                    if match ^ invert:
                        # Сохраняем перевод строки от docker.
                        console.file.write(line)
                        console.file.flush()
            except KeyboardInterrupt:
                proc.terminate()
            finally:
                rc = proc.wait()
                if rc not in (0, -15):  # 0 = OK, -15 = SIGTERM (наш Ctrl-C)
                    raise typer.Exit(code=rc)

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("restart")
    def env_restart(
        service: str | None = typer.Argument(None, help="Сервис (пусто = все запущенные)"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        build: bool = typer.Option(
            False,
            "--build",
            help="Пересобрать образ перед рестартом (только для сервисов build из src)",
        ),
        recreate: bool = typer.Option(
            False,
            "--recreate",
            help=(
                "Пересоздать контейнер (применяет новые volume mounts / env / "
                "конфиг-файлы вроде Caddyfile, которые `restart` не подхватит)."
            ),
        ),
    ) -> None:
        """Перезапустить сервис(ы).

        Различия:
          (по умолчанию)  — `docker compose restart`: быстрый рестарт процесса
                            в том же контейнере. НЕ подхватит изменения в
                            Caddyfile, mount, env или env_file.
          --recreate      — `docker compose up -d --force-recreate`: пересоздаёт
                            контейнер. Нужно когда поменял Caddyfile, volumes,
                            env, или хочешь сбросить состояние FS контейнера.
          --build         — пересобрать образ и поднять заново (для build-из-src).
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            base = ["docker", "compose", "-f", str(project.compose_file)]
            targets = [service] if service else []
            label = f"[bold]{service}[/bold]" if service else "all"

            if build:
                console.print(f"[cyan]→[/cyan] build {label}")
                _run([*base, "build", *targets], cwd=project.cwd)
                _run([*base, "up", "-d", *targets], cwd=project.cwd)
                console.print("[green]✓[/green] rebuild + up ok")
                return

            if recreate:
                console.print(f"[cyan]→[/cyan] recreate {label}")
                _run([*base, "up", "-d", "--force-recreate", *targets], cwd=project.cwd)
                console.print("[green]✓[/green] recreate ok")
                return

            console.print(f"[cyan]→[/cyan] restart {label}")
            _run([*base, "restart", *targets], cwd=project.cwd)
            console.print("[green]✓[/green] restart ok")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("reload")
    def env_reload(
        service: str | None = typer.Argument(None, help="Сервис (пусто = все запущенные)"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Алиас для `restart --recreate` — пересоздать контейнер с новым конфигом.

        Удобно сразу после правки Caddyfile / mount / env. Эквивалент:
          hc env restart [SERVICE] --recreate
        """
        env_restart(service=service, mode=mode, build=False, recreate=True)

    @env_app.command("rebuild")
    def env_rebuild(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_HELP),
        no_cache: bool = typer.Option(False, "--no-cache", help="Сборка без кэша Docker"),
    ) -> None:
        """
        Пересобрать образы и перезапустить сервисы (интерактивный выбор).

        Примеры:
          hc env rebuild                          # интерактив
          hc env rebuild --profile base           # core + caddy без вопросов
          hc env rebuild --no-cache               # интерактив, без кэша
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            if mode not in _SERVICES:
                console.print(f"[red]Ошибка:[/red] неизвестный режим {mode!r}. Допустимые: {' | '.join(_SERVICES)}")
                raise typer.Exit(code=2)

            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            running = _get_running_services(project.compose_file, project.cwd)

            selected = _resolve_services(mode=mode, profile=profile, console=console, running=running)
            service_names = [s.name for s in selected]

            console.print(
                f"\n[cyan]→[/cyan] env rebuild  "
                f"mode=[bold]{mode}[/bold]  "
                f"services=[bold]{', '.join(service_names)}[/bold]"
                + ("  [dim]--no-cache[/dim]" if no_cache else "")
            )

            base_cmd = ["docker", "compose", "-f", str(project.compose_file)]

            build_cmd = [*base_cmd, "build"]
            if no_cache:
                build_cmd.append("--no-cache")
            build_cmd += service_names
            _run(build_cmd, cwd=project.cwd)

            _run([*base_cmd, "up", "-d", *service_names], cwd=project.cwd)
            console.print(f"[green]✓[/green] rebuild ok")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("pull")
    def env_pull(
        build: bool = typer.Option(
            False, "--build", help="Если изменились зависимости/Dockerfile/compose — пересобрать запущенный стек"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Только показать входящие коммиты, без pull"
        ),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """
        Обновить исходники core-runtime-service и platform-home-console (git pull).

        Примеры:
          hc env pull              # обновить core + platform (если склонирован)
          hc env pull --dry-run     # показать, что подъедет, без pull
          hc env pull --build       # после pull пересобрать запущенный стек, если нужно
        """
        console = Console()
        try:
            src = _resolve_source(console)
            platform_path = src.path.parent / "platform-home-console"
            has_platform = (platform_path / ".git").exists()

            if dry_run:
                fetch_incoming_commits(src.path, console, label="core-runtime-service")
                if has_platform:
                    fetch_incoming_commits(platform_path, console, label="platform-home-console")
                return

            core_result = pull_core_source(src, console, quiet=False)

            platform_result = PullResult(updated=False)
            if has_platform:
                platform_result = pull_git_repo(
                    platform_path, console, label="platform-home-console", quiet=False, autostash=True
                )

            changed = [*core_result.changed_files, *platform_result.changed_files]
            rebuild_needed = any(_REBUILD_HINT_RE.search(f) for f in changed)
            migrations_changed = any(_MIGRATION_HINT_RE.search(f) for f in changed)

            if migrations_changed:
                console.print(
                    "\n[yellow]![/yellow] Обнаружены новые миграции БД — "
                    "перезапусти core-runtime, чтобы применить:"
                )
                console.print("  [cyan]hc env restart core-runtime[/cyan]")

            if rebuild_needed:
                hint_files = [f for f in changed if _REBUILD_HINT_RE.search(f)]
                if build:
                    require_docker(console)
                    console.print("\n[cyan]→[/cyan] Изменились зависимости/Dockerfile/compose — пересобираю...")
                    project = compose_project_from_source(console, src, mode=mode.strip().lower())
                    running = _get_running_services(project.compose_file, project.cwd)
                    if running:
                        base = _compose_with_profiles(project, running)
                        services = sorted(running)
                        _run([*base, "build", *services], cwd=project.cwd)
                        _run([*base, "up", "-d", *services], cwd=project.cwd)
                        console.print("[green]✓[/green] rebuild ok")
                    else:
                        console.print("[dim]Стек не запущен — пропускаю rebuild.[/dim]")
                else:
                    console.print(
                        "\n[yellow]![/yellow] Изменились зависимости/Dockerfile/compose:"
                    )
                    for f in hint_files:
                        console.print(f"  [dim]{f}[/dim]")
                    console.print("  → [cyan]hc env up --build[/cyan] (или повтори с `--build`)")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("ps")
    def env_ps(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        """Контейнеры dev-стека: состояние, порты и подсказки URL."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            _print_env_ps(console, project, json_out=json_out)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("exec")
    def env_exec(
        service: str = typer.Argument(..., help="Имя сервиса в compose"),
        command: list[str] = typer.Argument(
            None,
            help="Команда внутри контейнера (по умолчанию sh)",
        ),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Выполнить команду в контейнере (по умолчанию интерактивный sh)."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            running = _get_running_services(project.compose_file, project.cwd)
            base = _compose_with_profiles(project, running)
            exec_cmd = [*base, "exec", "-it", service]
            exec_cmd.extend(command if command else ["sh"])
            console.print(f"[dim]$ {' '.join(exec_cmd)}[/dim]")
            p = subprocess.run(exec_cmd, cwd=str(project.cwd), check=False)  # noqa: S603
            raise typer.Exit(code=p.returncode)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("status")
    def env_status(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        raw: bool = typer.Option(
            False, "--raw", help="Сырой `docker compose ps` без обработки."
        ),
    ) -> None:
        """Компактный dashboard dev-стека: контейнеры, health, URL, uptime.

        По умолчанию печатает богатую таблицу + блок URL endpoints. С --raw
        печатает оригинальный `docker compose ps`.
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            if raw:
                subprocess.run(  # noqa: S603
                    ["docker", "compose", "-f", str(project.compose_file), "ps"],
                    cwd=str(project.cwd),
                    check=False,
                )
                console.print(f"\n[dim]compose:[/dim] {project.compose_file}")
                console.print(ENV_VS_CORE_DOTENV)
                return

            _print_env_status_dashboard(console, project)

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("stats")
    def env_stats(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        watch: bool = typer.Option(False, "--watch", "-w", help="Обновлять каждые N секунд"),
        interval: float = typer.Option(3.0, "--interval", "-n", help="Интервал обновления (сек)"),
    ) -> None:
        """CPU%, RAM, NET I/O контейнеров dev-окружения."""
        import json
        import time
        from rich.live import Live
        from rich.table import Table

        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            def _stats_table() -> Table:
                r = subprocess.run(  # noqa: S603
                    ["docker", "compose", "-f", str(project.compose_file),
                     "stats", "--no-stream", "--format", "{{json .}}"],
                    cwd=str(project.cwd),
                    capture_output=True, text=True, check=False,
                )
                table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
                table.add_column("Сервис")
                table.add_column("CPU%",     justify="right")
                table.add_column("RAM",      justify="right")
                table.add_column("RAM%",     justify="right")
                table.add_column("NET I/O",  justify="right")
                table.add_column("BLOCK I/O",justify="right")
                table.add_column("PIDs",     justify="right")

                for line in r.stdout.strip().splitlines():
                    try:
                        d = json.loads(line)
                        cpu_s = d.get("CPUPerc", "0%")
                        try:
                            cpu_f = float(cpu_s.rstrip("%"))
                            cpu_color = "red" if cpu_f > 80 else "yellow" if cpu_f > 40 else "green"
                        except ValueError:
                            cpu_color = "white"
                        table.add_row(
                            d.get("Name", "?"),
                            f"[{cpu_color}]{cpu_s}[/{cpu_color}]",
                            d.get("MemUsage", "?"),
                            d.get("MemPerc", "?"),
                            d.get("NetIO", "?"),
                            d.get("BlockIO", "?"),
                            d.get("PIDs", "?"),
                        )
                    except (json.JSONDecodeError, KeyError):
                        pass
                return table

            if watch:
                with Live(refresh_per_second=1, screen=False) as live:
                    while True:
                        live.update(_stats_table())
                        time.sleep(interval)
            else:
                console.print(_stats_table())

        except (KeyboardInterrupt, typer.Abort):
            pass
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("health")
    def env_health(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Healthcheck статус каждого сервиса окружения."""
        import json
        from rich.table import Table

        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            r = subprocess.run(  # noqa: S603
                ["docker", "compose", "-f", str(project.compose_file),
                 "ps", "--format", "json"],
                cwd=str(project.cwd),
                capture_output=True, text=True, check=False,
            )

            table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
            table.add_column("Сервис")
            table.add_column("Статус")
            table.add_column("Health")
            table.add_column("Порты")

            _HEALTH_COLOR = {"healthy": "green", "unhealthy": "red",
                             "starting": "yellow", "none": "dim"}

            rows: list[dict] = []
            raw = r.stdout.strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    rows = parsed if isinstance(parsed, list) else [parsed]
                except json.JSONDecodeError:
                    for line in raw.splitlines():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            if not rows:
                console.print("[yellow]Нет запущенных контейнеров.[/yellow]")
                console.print(f"[dim]compose:[/dim] {project.compose_file}")
                return

            for row in rows:
                name = row.get("Service") or row.get("Name") or "?"
                state = str(row.get("State") or row.get("Status") or "?").lower()
                health = str(row.get("Health") or "none").lower()
                ports = row.get("Publishers") or row.get("Ports") or ""
                if isinstance(ports, list):
                    ports = ", ".join(
                        f"{p.get('PublishedPort', '')}→{p.get('TargetPort', '')}"
                        for p in ports if p.get("PublishedPort")
                    )

                sc = _STATE_COLOR.get(state, "white")
                hc_color = _HEALTH_COLOR.get(health, "white")
                health_icon = {"healthy": "✓", "unhealthy": "✗",
                               "starting": "…", "none": "—"}.get(health, health)

                table.add_row(
                    f"[bold]{name}[/bold]",
                    f"[{sc}]{state}[/{sc}]",
                    f"[{hc_color}]{health_icon} {health}[/{hc_color}]",
                    str(ports),
                )

            console.print(table)
            console.print(f"\n[dim]compose:[/dim] {project.compose_file}")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("clean")
    def env_clean(
        volumes: bool = typer.Option(False, "--volumes", help="Удалить также orphan volumes"),
        all_images: bool = typer.Option(False, "--all-images", help="Удалить все неиспользуемые образы (не только dangling)"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать что будет удалено без выполнения"),
    ) -> None:
        """Очистить orphan Docker ресурсы (dangling images, неиспользуемые volumes)."""
        console = Console()
        require_docker(console)

        image_cmd = ["docker", "image", "prune", "-f"]
        if all_images:
            image_cmd.append("--all")

        if dry_run:
            console.print("[yellow]Dry run:[/yellow] будет выполнено:")
            console.print(f"  {' '.join(image_cmd)}")
            if volumes:
                console.print("  docker volume prune -f")
            return

        console.print("[cyan]→[/cyan] Очистка Docker ресурсов...")
        p = subprocess.run(image_cmd, capture_output=True, text=True, check=False)  # noqa: S603
        out = (p.stdout or "").strip()
        if p.returncode == 0:
            console.print(f"[green]✓[/green] Images: {out or 'nothing to remove'}")
        else:
            console.print(f"[yellow]Images:[/yellow] {(p.stderr or '').strip()}")

        if volumes:
            p = subprocess.run(  # noqa: S603
                ["docker", "volume", "prune", "-f"], capture_output=True, text=True, check=False
            )
            out = (p.stdout or "").strip()
            if p.returncode == 0:
                console.print(f"[green]✓[/green] Volumes: {out or 'nothing to remove'}")
            else:
                console.print(f"[yellow]Volumes:[/yellow] {(p.stderr or '').strip()}")

    # --- hc env dotenv: управление .env файлом core-runtime-service ---

    _SECRET_RE = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|PASS|PRIVATE|MASTER)", re.IGNORECASE)

    def _dotenv_local_path() -> tuple[bool, "Path | None"]:
        """Найти .env локально через _resolve_source. Возвращает (found, path)."""
        from rich.console import Console as _C
        try:
            src = _resolve_source(_C(stderr=True))
            env_path = src.path / ".env"
            return True, env_path
        except SystemExit:
            return False, None

    def _parse_dotenv(text: str) -> list[tuple[str, str, str]]:
        """Parse .env → list of (raw_line, key, value). Preserves comments/blanks as ('line', '', '')."""
        result = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                result.append((line, "", ""))
            elif "=" in stripped:
                key, _, val = stripped.partition("=")
                result.append((line, key.strip(), val))
            else:
                result.append((line, "", ""))
        return result

    def _serialize_dotenv(entries: list[tuple[str, str, str]]) -> str:
        return "\n".join(e[0] for e in entries) + "\n"

    def _apply_first_run_storage_choice(env_path: Path, db_key: str, console: Console) -> None:
        """
        При первом создании .env (см. ensure_core_env) — преднастроить весь
        storage stack (core + vault) согласно выбору контейнера БД, чтобы не
        требовалась последующая миграция (vault ещё пуст на первом запуске).
        """
        if db_key != "postgres" or not env_path.exists():
            return
        entries = _parse_dotenv(env_path.read_text(encoding="utf-8", errors="replace"))
        overrides = {
            "RUNTIME_STORAGE_TYPE": "postgresql",
            "RUNTIME_PG_HOST": "postgres",
            "RUNTIME_PG_PORT": "5432",
            "RUNTIME_PG_DATABASE": "homeconsole",
            "RUNTIME_PG_USER": "homeconsole",
            "RUNTIME_PG_PASSWORD": "homeconsole",
            "RUNTIME_VAULT_STORAGE_TYPE": "postgresql",
            "RUNTIME_VAULT_PG_DSN": VAULT_PG_DSN_DEFAULT,
        }
        existing = {k for _, k, _ in entries if k}
        for i, (raw, k, v) in enumerate(entries):
            if k in overrides:
                entries[i] = (f"{k}={overrides[k]}", k, overrides[k])
        for k, v in overrides.items():
            if k not in existing:
                entries.append((f"{k}={v}", k, v))
        env_path.write_text(_serialize_dotenv(entries), encoding="utf-8")
        console.print(
            "[green]✓[/green] Первый запуск: .env настроен на PostgreSQL "
            "(core: schema public, vault: schema vault) — без миграции."
        )

    def _mask(key: str, val: str) -> str:
        return "***" if _SECRET_RE.search(key) and val else val

    dotenv_app = typer.Typer(
        help="Управление .env файлом core-runtime-service",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @dotenv_app.command("show")
    def dotenv_show(
        no_mask: bool = typer.Option(False, "--no-mask", help="Показать секреты без маскировки"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из deploy config)"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на удалённом сервере"),
    ) -> None:
        """Показать содержимое .env (секреты маскируются по умолчанию)."""
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file}"],
                capture_output=True, text=True, check=False,
            )
            if p.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p.stderr or '').strip()}")
                raise typer.Exit(code=p.returncode)
            text = p.stdout
            console.print(f"[dim]{resolved_ssh}:{remote_file}[/dim]")
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                console.print(f"[yellow].env не найден:[/yellow] {path}")
                raise typer.Exit(code=0)
            text = path.read_text(encoding="utf-8", errors="replace")
            console.print(f"[dim]{path}[/dim]")

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                console.print(f"[dim]{line}[/dim]")
            elif "=" in stripped:
                key, _, val = stripped.partition("=")
                display_val = val if no_mask else _mask(key.strip(), val)
                console.print(f"[bold cyan]{key}[/bold cyan]={display_val}")
            else:
                console.print(line)

    @dotenv_app.command("set")
    def dotenv_set(
        assignment: str = typer.Argument(..., help="KEY=VALUE"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из deploy config)"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на удалённом сервере"),
    ) -> None:
        """Добавить или обновить переменную в .env. Формат: KEY=VALUE."""
        console = Console()
        if "=" not in assignment:
            console.print("[red]Ошибка:[/red] формат KEY=VALUE")
            raise typer.Exit(code=1)
        key, _, val = assignment.partition("=")
        key = key.strip()
        if not key:
            console.print("[red]Ошибка:[/red] ключ не может быть пустым")
            raise typer.Exit(code=1)

        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            # Download, modify, upload
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            text = p.stdout or ""
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

        entries = _parse_dotenv(text)
        updated = False
        new_line = f"{key}={val}"
        for i, (raw, k, v) in enumerate(entries):
            if k == key:
                entries[i] = (new_line, key, val)
                updated = True
                break
        if not updated:
            entries.append((new_line, key, val))

        new_text = _serialize_dotenv(entries)

        if resolved_ssh:
            remote_file = env_path or f"{cfg.deploy.path}/.env"
            import shlex as _shlex
            # Atomic write: write to temp file, then mv (prevents corruption on disconnect)
            remote_tmp = f"{remote_file}.tmp.$$"
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_tmp)} && mv {_shlex.quote(remote_tmp)} {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] {key} {'обновлён' if updated else 'добавлен'} в {resolved_ssh}:{remote_file}")
        else:
            path.write_text(new_text, encoding="utf-8")  # type: ignore[union-attr]
            console.print(f"[green]✓[/green] {key} {'обновлён' if updated else 'добавлен'} в {path}")

    @dotenv_app.command("unset")
    def dotenv_unset(
        key: str = typer.Argument(..., help="Имя переменной для удаления"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на сервере"),
    ) -> None:
        """Удалить переменную из .env."""
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            text = p.stdout or ""
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                console.print(f"[yellow].env не найден:[/yellow] {path}")
                raise typer.Exit(code=0)
            text = path.read_text(encoding="utf-8", errors="replace")

        entries = _parse_dotenv(text)
        before = len(entries)
        entries = [(raw, k, v) for raw, k, v in entries if k != key]
        if len(entries) == before:
            console.print(f"[yellow]{key} не найден в .env[/yellow]")
            raise typer.Exit(code=0)

        new_text = _serialize_dotenv(entries)

        if resolved_ssh:
            import shlex as _shlex
            remote_file = env_path or f"{cfg.deploy.path}/.env"
            remote_tmp = f"{remote_file}.tmp.$$"
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_tmp)} && mv {_shlex.quote(remote_tmp)} {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] {key} удалён из {resolved_ssh}:{remote_file}")
        else:
            path.write_text(new_text, encoding="utf-8")  # type: ignore[union-attr]
            console.print(f"[green]✓[/green] {key} удалён из {path}")

    @dotenv_app.command("edit")
    def dotenv_edit(
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на сервере"),
    ) -> None:
        """Открыть .env в $EDITOR (для SSH — скачивает, редактирует, загружает)."""
        import shlex as _shlex
        import shutil as _shutil
        import tempfile as _tempfile
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
        if not editor:
            for cand in ("nvim", "vim", "nano", "micro"):
                if _shutil.which(cand):
                    editor = cand
                    break
        if not editor:
            console.print("[red]Ошибка:[/red] не задан редактор. Укажи переменную EDITOR или VISUAL.")
            raise typer.Exit(code=2)

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            with _tempfile.NamedTemporaryFile(suffix=".env", delete=False) as tmp:
                tmp_path = tmp.name
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            open(tmp_path, "w").write(p.stdout or "")  # noqa: WPS515
            cmd = [*_shlex.split(editor), tmp_path]
            subprocess.run(cmd, check=False)  # noqa: S603
            new_text = open(tmp_path).read()  # noqa: WPS515
            os.unlink(tmp_path)
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_tmp)} && mv {_shlex.quote(remote_tmp)} {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] .env сохранён на {resolved_ssh}:{remote_file}")
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                path.touch()
            cmd = [*_shlex.split(editor), str(path)]
            p = subprocess.run(cmd, check=False)  # noqa: S603
            if p.returncode != 0:
                console.print(f"[yellow]Редактор завершился с кодом {p.returncode}[/yellow]")
            else:
                console.print("[green]✓[/green] .env сохранён")

    env_app.add_typer(dotenv_app, name="dotenv")

    # ─── hc env reset-vault ────────────────────────────────────────────────

    @env_app.command("reset-vault")
    def env_reset_vault(
        db: str = typer.Option(
            "auto",
            "--db",
            help="Какой vault сбрасывать: auto | sqlite | postgres (auto = по запущенному стеку)",
        ),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
        restart: bool = typer.Option(
            True,
            "--restart/--no-restart",
            help="Перезапустить core-runtime после сброса",
        ),
    ) -> None:
        """
        Сбросить vault (шифрованное хранилище секретов) — нужно когда RUNTIME_MASTER_KEY
        не совпадает с тем, которым зашифрованы существующие записи.

        Что удаляется:
          • sqlite:   /data/vault.db и /data/vault_secret.db (+ WAL/SHM)
          • postgres: записи в storage с namespace в (secrets.store, _system.meta,
                      _system.root_hash, _system.audit_log) + TRUNCATE storage_metadata

        Что НЕ удаляется: данные core (runtime.db / основная схема Postgres) — их
        миграции и пользовательские записи остаются нетронутыми.

        После сброса core при следующем старте сгенерирует CSRF_SECRET и
        OAUTH_ENCRYPTION_KEY заново и положит в новый vault с текущим RUNTIME_MASTER_KEY.

        Примеры:
          hc env reset-vault              # auto-detect + подтверждение
          hc env reset-vault --db postgres --yes
          hc env reset-vault --no-restart # сбросить, но не перезапускать
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            db_key = db.strip().lower().replace("pg", "postgres")
            resolved: DbKind
            if db_key == "auto":
                detected = detect_running_db(project.compose_file, project.cwd)
                if detected is None:
                    # Фоллбэк на last_env, если стек не запущен.
                    last = load_last_env()
                    if last and last.db in {"sqlite", "postgres"}:
                        resolved = last.db  # type: ignore[assignment]
                        console.print(
                            f"[dim]Стек не запущен, использую db={resolved} из последнего env up[/dim]"
                        )
                    else:
                        console.print(
                            "[red]Ошибка:[/red] не удалось определить активную БД. "
                            "Укажи явно: --db sqlite или --db postgres"
                        )
                        raise typer.Exit(code=2)
                else:
                    resolved = detected
            elif db_key in {"sqlite", "postgres"}:
                resolved = db_key  # type: ignore[assignment]
            else:
                console.print(
                    f"[red]Ошибка:[/red] --db {db!r} неизвестен. Допустимые: auto | sqlite | postgres"
                )
                raise typer.Exit(code=2)

            # Предупреждение пользователю.
            console.print(
                f"\n[yellow]![/yellow] Сейчас будет сброшен vault для [bold]{resolved}[/bold]."
            )
            if resolved == "postgres":
                console.print(
                    "  [dim]Удалятся записи storage из vault-namespaces + "
                    "TRUNCATE storage_metadata.[/dim]"
                )
                console.print(
                    "  [dim]Core-данные (схемы Alembic, прочие записи) остаются.[/dim]"
                )
            else:
                console.print(
                    "  [dim]Удалятся файлы /data/vault.db и /data/vault_secret.db "
                    "(+ WAL/SHM) из volume core-data.[/dim]"
                )
                console.print("  [dim]Файл /data/runtime.db (core) остаётся.[/dim]")

            if not yes and sys.stdin.isatty():
                try:
                    import questionary
                    confirmed = questionary.confirm(
                        "Продолжить сброс vault?",
                        default=False,
                    ).ask()
                except ImportError:
                    confirmed = False
                if not confirmed:
                    console.print("[dim]Отменено.[/dim]")
                    raise typer.Exit(code=0)

            # Сам сброс.
            console.print(f"\n[cyan]→[/cyan] reset-vault [bold]{resolved}[/bold]")
            if resolved == "postgres":
                result = reset_vault_postgres(
                    compose_file=project.compose_file,
                    cwd=project.cwd,
                )
            else:
                result = reset_vault_sqlite(
                    compose_file=project.compose_file,
                    cwd=project.cwd,
                )

            for action in result.actions:
                console.print(f"  [dim]·[/dim] {action}")

            if not result.success:
                console.print(f"[red]✗[/red] reset-vault failed: {result.message}")
                raise typer.Exit(code=1)

            console.print(f"[green]✓[/green] vault сброшен ({result.db})")

            # Перезапуск core-runtime — он пересоздаст vault и runtime-секреты.
            if restart:
                running = _get_running_services(project.compose_file, project.cwd)
                if "core-runtime" in running:
                    console.print("[cyan]→[/cyan] restart core-runtime")
                    _run(
                        ["docker", "compose", "-f", str(project.compose_file),
                         "restart", "core-runtime"],
                        cwd=project.cwd,
                    )
                    console.print("[green]✓[/green] core-runtime перезапущен")
                    console.print(
                        "  [dim]Проверь:[/dim] [cyan]hc env health[/cyan] "
                        "или [cyan]hc env logs core-runtime --tail 50[/cyan]"
                    )
                else:
                    console.print(
                        "  [dim]core-runtime не запущен — подними его: [/dim][cyan]hc env up[/cyan]"
                    )

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    # ─── hc env vault-migrate ──────────────────────────────────────────────

    @env_app.command("vault-migrate")
    def env_vault_migrate(
        to: str = typer.Option(..., "--to", help="Целевой backend vault: sqlite | postgres"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        delete_source: bool = typer.Option(
            False, "--delete-source",
            help="Удалить данные из текущего backend после проверенной копии",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Только показать что будет скопировано, без изменений"
        ),
        restart: bool = typer.Option(
            True, "--restart/--no-restart", help="Перезапустить core-runtime после миграции"
        ),
    ) -> None:
        """
        Перенести vault-хранилище (secrets.store, _system.*) между backend'ами
        sqlite <-> postgres.

        Сама миграция выполняется внутри core-runtime через
        `python -m modules.storage.vault_migrate` — этот же модуль можно
        запустить и на проде, `hc env` тут лишь обёртка для dev-стека.

        Примеры:
          hc env vault-migrate --to postgres --dry-run   # показать что переедет
          hc env vault-migrate --to postgres             # скопировать sqlite → postgres
          hc env vault-migrate --to sqlite --delete-source
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()

            to_key = to.strip().lower().replace("pg", "postgres")
            if to_key not in ("sqlite", "postgres"):
                console.print(f"[red]Ошибка:[/red] --to {to!r} неизвестен. Допустимые: sqlite | postgres")
                raise typer.Exit(code=2)

            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            running = _get_running_services(project.compose_file, project.cwd)
            if "core-runtime" not in running:
                console.print(
                    "[red]Ошибка:[/red] core-runtime не запущен — нужен для выполнения миграции.\n"
                    "  [dim]hc env up[/dim]"
                )
                raise typer.Exit(code=1)

            found, env_path = _dotenv_local_path()
            if not found or env_path is None or not env_path.exists():
                console.print("[red]Ошибка:[/red] .env не найден.")
                raise typer.Exit(code=2)

            entries = _parse_dotenv(env_path.read_text(encoding="utf-8", errors="replace"))
            current_env = {k: v for _, k, v in entries if k}

            current_type = current_env.get("RUNTIME_VAULT_STORAGE_TYPE", "sqlite").strip().lower()
            current_key = "postgres" if current_type == "postgresql" else "sqlite"

            if current_key == to_key:
                console.print(f"[yellow]Vault уже на backend {to_key!r} — нечего переносить.[/yellow]")
                raise typer.Exit(code=0)

            default_pg_dsn = VAULT_PG_DSN_DEFAULT
            sqlite_path = current_env.get("RUNTIME_VAULT_DB_PATH", "data/vault.db").strip() or "data/vault.db"
            pg_dsn = current_env.get("RUNTIME_VAULT_PG_DSN", "").strip() or default_pg_dsn

            def _backend_args(key: str, role: str) -> list[str]:
                if key == "sqlite":
                    return [f"--{role}", "sqlite", f"--{role}-path", sqlite_path]
                return [f"--{role}", "postgres", f"--{role}-dsn", pg_dsn]

            migrate_cmd = [
                "docker", "compose", "-f", str(project.compose_file),
                "exec", "-T", "core-runtime",
                "python", "-m", "modules.storage.vault_migrate",
                *_backend_args(current_key, "from"),
                *_backend_args(to_key, "to"),
            ]
            if delete_source:
                migrate_cmd.append("--delete-source")
            if dry_run:
                migrate_cmd.append("--dry-run")

            console.print(
                f"\n[cyan]→[/cyan] vault-migrate  {current_key} → {to_key}"
                + ("  [dim](dry-run)[/dim]" if dry_run else "")
            )
            p = subprocess.run(migrate_cmd, cwd=str(project.cwd), check=False)  # noqa: S603
            if p.returncode != 0:
                console.print("[red]✗[/red] миграция завершилась с ошибкой")
                raise typer.Exit(code=p.returncode)

            if dry_run:
                console.print("[dim]Dry-run — .env не изменён.[/dim]")
                return

            console.print(f"[green]✓[/green] данные скопированы в {to_key}")

            # Обновить .env: переключить тип vault-хранилища на новый backend.
            new_type = "postgresql" if to_key == "postgres" else "sqlite"
            updated = False
            for i, (raw, k, v) in enumerate(entries):
                if k == "RUNTIME_VAULT_STORAGE_TYPE":
                    entries[i] = (f"RUNTIME_VAULT_STORAGE_TYPE={new_type}", k, new_type)
                    updated = True
                    break
            if not updated:
                entries.append((f"RUNTIME_VAULT_STORAGE_TYPE={new_type}", "RUNTIME_VAULT_STORAGE_TYPE", new_type))

            if to_key == "postgres" and "RUNTIME_VAULT_PG_DSN" not in current_env:
                entries.append((f"RUNTIME_VAULT_PG_DSN={pg_dsn}", "RUNTIME_VAULT_PG_DSN", pg_dsn))
            if to_key == "sqlite" and "RUNTIME_VAULT_DB_PATH" not in current_env:
                entries.append((f"RUNTIME_VAULT_DB_PATH={sqlite_path}", "RUNTIME_VAULT_DB_PATH", sqlite_path))

            env_path.write_text(_serialize_dotenv(entries), encoding="utf-8")
            console.print(f"[green]✓[/green] RUNTIME_VAULT_STORAGE_TYPE={new_type} записан в {env_path}")

            if restart:
                console.print("[cyan]→[/cyan] restart core-runtime")
                _run(
                    ["docker", "compose", "-f", str(project.compose_file),
                     "restart", "core-runtime"],
                    cwd=project.cwd,
                )
                console.print("[green]✓[/green] core-runtime перезапущен")
            else:
                console.print("  [dim]Перезапусти core-runtime чтобы изменения применились:[/dim] hc env restart core-runtime")

            if not delete_source:
                console.print(
                    f"\n[dim]Старые данные в {current_key} не удалены (без --delete-source).[/dim]"
                )

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("doctor")
    def env_doctor(
        quick: bool = typer.Option(False, "--quick", "-q"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Алиас `hc doctor --dev` — диагностика DEV-стека."""
        from hc.commands.doctor import run_doctor_cmd

        run_doctor_cmd(quick=quick, dev=True, json_out=json_out)

    app.add_typer(env_app, name="env")
