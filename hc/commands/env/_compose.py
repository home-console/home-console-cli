"""Docker compose operations for env commands."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from hc.commands.env._catalog import (
    _SERVICES, _DB_OPTIONS, EnvUpPlan, KNOWN_ENDPOINTS,
)


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


def _compose_ps_rows(project: "ComposeProject") -> list[dict[str, object]]:
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


def _env_ps_entries(project: "ComposeProject") -> list[dict[str, str]]:
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
    """After down -v, check for volumes that compose didn't remove."""
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        project = cwd.name
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


def _compose_with_profiles(
    project: "ComposeProject",
    running: set[str],
) -> list[str]:
    cmd = ["docker", "compose", "-f", str(project.compose_file)]
    for profile in sorted(_detect_active_profiles(running)):
        cmd += ["--profile", profile]
    return cmd


from hc.commands.env._catalog import (
    _SERVICES, _DB_OPTIONS, EnvUpPlan, KNOWN_ENDPOINTS,
)
from hc.core_ops import ComposeProject


def compose_project_name(project: ComposeProject) -> str:
    """Имя compose-проекта (метка com.docker.compose.project)."""
    r = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(project.compose_file),
            "config",
            "--format",
            "json",
        ],
        cwd=str(project.cwd),
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
    return project.cwd.name


def _compose_project_name(plan: EnvUpPlan) -> str:
    return compose_project_name(plan.project)


def planned_config_files_from_cmd(base_cmd: list[str]) -> str:
    """Собрать значение метки com.docker.compose.project.config_files из compose-команды."""
    files: list[str] = []
    i = 0
    while i < len(base_cmd):
        if base_cmd[i] == "-f" and i + 1 < len(base_cmd):
            files.append(str(Path(base_cmd[i + 1]).resolve()))
            i += 2
            continue
        i += 1
    return ",".join(files)


def compose_project_name_from_compose(project: "ComposeProject") -> str:
    """Имя compose-проекта без полного EnvUpPlan (для status/ps)."""
    import json as _json

    r = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(project.compose_file), "config", "--format", "json"],
        cwd=str(project.cwd),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode == 0:
        try:
            name = _json.loads(r.stdout).get("name")
            if name:
                return str(name)
        except _json.JSONDecodeError:
            pass
    return project.cwd.name


def _compose_base_cmd(plan: EnvUpPlan) -> list[str]:
    cmd = ["docker", "compose", "-f", str(plan.project.compose_file)]
    for cp in sorted(plan.compose_profiles):
        cmd += ["--profile", cp]
    return cmd


def _compose_up_with_stale_retry(
    console: Console,
    plan: EnvUpPlan,
    up_cmd: list[str],
    *,
    extra_env: dict[str, str] | None,
    force_recreate: bool = False,
) -> None:
    """docker compose up; при сбое — снести не-running контейнеры и повторить один раз."""
    if force_recreate:
        up_cmd = [*up_cmd, "--force-recreate"]

    try:
        _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)
    except typer.Exit:
        # Retry: prune stuck containers then re-run up
        try:
            console.print("\n[yellow]![/yellow] Compose up failed — pruning stuck containers and retrying...")
            _prune_non_running_compose_services(console, plan)
            _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)
        except typer.Exit:
            raise


def _prune_non_running_compose_services(
    console: Console,
    plan: EnvUpPlan,
    *,
    extra_force: set[str] | None = None,
) -> None:
    """
    Удалить контейнеры сервисов из плана, которые не в состоянии running.
    """
    from hc.core_ops import list_compose_containers

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
        text=True,
        timeout=30,
    )
