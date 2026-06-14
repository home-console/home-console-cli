"""Diagnostics: port conflicts, post-mortem, failure logs."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from hc.commands.env._catalog import EnvUpPlan, QUESTIONARY_STYLE_KWARGS
from hc.commands.env._compose import _compose_ps_rows, _compose_project_name
from hc.errors import HcCliError


def _collect_postmortem_targets(project: ComposeProject) -> list[str]:
    """Имена сервисов, которые имеет смысл сканировать (упавшие/unhealthy)."""
    try:
        candidates = list_compose_containers(
            project.compose_file,
            project.cwd,
            only_states=("exited", "unhealthy", "restarting", "dead"),
        )
    except Exception:  # noqa: BLE001
        return []

    if not candidates:
        try:
            candidates = list_compose_containers(project.compose_file, project.cwd)
        except Exception:  # noqa: BLE001
            return []
        candidates = [c for c in candidates if c.get("_effective_state") != "running"]

    names: list[str] = []
    for cont in candidates:
        service = str(cont.get("Service") or cont.get("Name") or "")
        if service and service not in names:
            names.append(service)
    return names



def _run_postmortem(console: Console, project: ComposeProject) -> tuple[list[DetectedIssue], list[str]]:
    """
    Найти упавшие/unhealthy контейнеры, подтянуть их логи и распознать
    известные ошибки через каталог diagnostics.

    Возвращает (список найденных проблем, список просканированных сервисов).
    """
    services = _collect_postmortem_targets(project)
    found: list[DetectedIssue] = []
    for service in services:
        try:
            logs = fetch_container_logs(project.compose_file, project.cwd, service, tail=200)
        except Exception:  # noqa: BLE001
            continue
        found.extend(detect_issues(logs, service=service))

    return found, services



def _print_postmortem(
    console: Console,
    issues: list[DetectedIssue],
    *,
    scanned_services: list[str] | None = None,
) -> None:
    """Красиво отрисовать найденные проблемы с готовыми командами для починки."""
    if not issues:
        console.print(
            "\n[yellow]![/yellow] Стек поднялся не до конца, но известных шаблонов ошибок не нашёл."
        )
        # Берём реальные имена упавших сервисов вместо хардкода core-runtime.
        targets = scanned_services or ["core-runtime"]
        targets_str = " ".join(targets)
        console.print(
            f"  [dim]Посмотри полные логи:[/dim] [cyan]hc env logs --follow {targets_str}[/cyan]"
        )
        console.print(
            "  [dim]Если паттерн повторяется — открой issue с этим логом, "
            "добавим в диагностику.[/dim]"
        )
        return

    console.print(
        f"\n[bold red]✗ Обнаружено известных проблем: {len(issues)}[/bold red]\n"
    )
    for i, det in enumerate(issues, 1):
        issue = det.issue
        header = f"[bold]{i}. {issue.title}[/bold]"
        if det.service:
            header += f"  [dim](в {det.service})[/dim]"
        console.print(header)
        for line in issue.cause.splitlines():
            console.print(f"   [dim]{line}[/dim]")
        if det.matched_line:
            console.print(f"   [dim]└ строка лога:[/dim] [yellow]{det.matched_line}[/yellow]")
        if issue.fix_commands:
            console.print("   [bold cyan]Что сделать:[/bold cyan]")
            has_shell = any(fix.kind == "shell" for fix in issue.fix_commands)
            for fix in issue.fix_commands:
                if fix.kind == "shell":
                    # Shell-команда: явно показываем что это для обычного терминала,
                    # а не для hc REPL (где доступны только `hc *`).
                    console.print(
                        f"     [yellow]$[/yellow] [bold]{fix.command}[/bold]"
                        f"   [dim]# {fix.description} [shell, не hc][/dim]"
                    )
                else:
                    console.print(
                        f"     [green]→[/green] [cyan]{fix.command}[/cyan]"
                        f"   [dim]# {fix.description}[/dim]"
                    )
            if has_shell:
                console.print(
                    "     [dim italic]Легенда: [green]→[/green] — можно ввести прямо в этом REPL · "
                    "[yellow]$[/yellow] — выполни в обычном shell[/dim italic]"
                )
        console.print()




def _get_needed_ports(plan: EnvUpPlan) -> dict[int, str]:
    """Return {host_port: service_name} for all services in the plan."""
    import json

    r = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(plan.project.compose_file),
            *[arg for cp in sorted(plan.compose_profiles) for arg in ("--profile", cp)],
            "config", "--format", "json",
        ],
        cwd=str(plan.project.cwd),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode != 0:
        return {}

    try:
        cfg = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}

    result: dict[int, str] = {}
    for svc_name in plan.service_names:
        svc = cfg.get("services", {}).get(svc_name, {})
        for p in svc.get("ports", []):
            published = p.get("published") if isinstance(p, dict) else None
            if published:
                try:
                    result[int(published)] = svc_name
                except (ValueError, TypeError):
                    pass
    return result



def _parse_docker_labels(labels_val: object) -> dict[str, str]:
    if isinstance(labels_val, dict):
        return {str(k): str(v) for k, v in labels_val.items()}
    out: dict[str, str] = {}
    for part in str(labels_val or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out



def _parse_published_ports(ports_str: str) -> set[int]:
    held: set[int] = set()
    for m in re.finditer(r":(\d+)->", ports_str):
        held.add(int(m.group(1)))
    return held



def _process_command_line(pid: int) -> str:
    r = subprocess.run(  # noqa: S603
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    cmd = (r.stdout or "").strip()
    return cmd or f"pid {pid}"



def _find_host_listeners(port: int) -> list[dict[str, object]]:
    """Процессы на хосте, слушающие TCP-порт (lsof / ss)."""
    holders: list[dict[str, object]] = []
    seen_pids: set[int] = set()

    if shutil.which("lsof"):
        r = subprocess.run(  # noqa: S603
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            pid: int | None = None
            comm = ""
            name = ""
            for line in r.stdout.splitlines():
                if not line:
                    continue
                tag, val = line[0], line[1:]
                if tag == "p":
                    if pid is not None and pid not in seen_pids:
                        holders.append({"pid": pid, "command": name or comm})
                        seen_pids.add(pid)
                    pid = int(val)
                    comm = ""
                    name = ""
                elif tag == "c":
                    comm = val
                elif tag == "n":
                    name = val
            if pid is not None and pid not in seen_pids:
                holders.append({"pid": pid, "command": name or comm})
                seen_pids.add(pid)

        if not holders:
            r2 = subprocess.run(  # noqa: S603
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in (r2.stdout or "").splitlines():
                if line.strip().isdigit():
                    pid = int(line.strip())
                    if pid not in seen_pids:
                        holders.append({"pid": pid, "command": _process_command_line(pid)})
                        seen_pids.add(pid)

    if not holders and shutil.which("ss"):
        r3 = subprocess.run(  # noqa: S603
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for m in re.finditer(r"pid=(\d+)", r3.stdout or ""):
            pid = int(m.group(1))
            if pid not in seen_pids:
                holders.append({"pid": pid, "command": _process_command_line(pid)})
                seen_pids.add(pid)

    return holders



def _find_port_conflicts(
    needed: dict[int, str],
    plan: EnvUpPlan,
) -> list[dict[str, object]]:
    """
  Найти кто занимает нужные порты: чужие контейнеры и процессы на хосте.

  Порты, которые уже держит running-контейнер нашего compose-проекта
  для сервиса из плана, не считаются конфликтом.
    """
    import json

    if not needed:
        return []

    project_name = _compose_project_name(plan)
    conflicts: list[dict[str, object]] = []
    legit_ports: set[int] = set()
    docker_covered_ports: set[int] = set()

    r = subprocess.run(  # noqa: S603
        ["docker", "ps", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    containers: list[dict] = []
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for c in containers:
        labels = _parse_docker_labels(c.get("Labels", ""))
        svc = labels.get("com.docker.compose.service", "")
        proj = labels.get("com.docker.compose.project", "")
        held = _parse_published_ports(str(c.get("Ports", "")))
        if proj == project_name and svc in plan.service_names:
            for p in held:
                if p in needed and needed[p] == svc:
                    legit_ports.add(p)

    for c in containers:
        labels = _parse_docker_labels(c.get("Labels", ""))
        held = _parse_published_ports(str(c.get("Ports", "")))
        blocking = {
            p: needed[p]
            for p in held
            if p in needed and p not in legit_ports
        }
        if not blocking:
            continue
        docker_covered_ports.update(blocking)
        conflicts.append({
            "kind": "docker",
            "id": (c.get("ID") or "")[:12],
            "name": c.get("Names", "?"),
            "image": c.get("Image", "?"),
            "ports": blocking,
        })

    for port, svc in needed.items():
        if port in legit_ports or port in docker_covered_ports:
            continue
        for holder in _find_host_listeners(port):
            cmd = str(holder.get("command", ""))
            if "docker-proxy" in cmd:
                continue
            conflicts.append({
                "kind": "process",
                "pid": holder["pid"],
                "command": cmd,
                "ports": {port: svc},
            })

    return conflicts



def _kill_process(pid: int, *, signal_name: str) -> bool:
    import signal

    sig = signal.SIGKILL if signal_name == "kill" else signal.SIGTERM
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False



def _offer_resolve_conflicts(
    conflicts: list[dict[str, object]],
    console: Console,
) -> None:
    """Интерактивно остановить контейнеры или убить процессы, занимающие порты."""
    console.print("\n[yellow]⚠ Конфликт портов[/yellow] — порты заняты:\n")

    for c in conflicts:
        ports_info = ", ".join(
            f"[bold]:{p}[/bold] (нужен для {svc})"
            for p, svc in sorted(c["ports"].items())  # type: ignore[union-attr]
        )
        if c.get("kind") == "process":
            console.print(
                f"  [yellow]процесс[/yellow]  PID [bold]{c['pid']}[/bold]  "
                f"[dim]{c['command']}[/dim]  {ports_info}"
            )
        else:
            console.print(
                f"  [cyan]контейнер[/cyan]  {c['name']}  {c['image']}  {ports_info}"
            )

    if not sys.stdin.isatty():
        hints = []
        for c in conflicts:
            if c.get("kind") == "process":
                hints.append(f"kill {c['pid']}")
            else:
                hints.append(f"docker stop {c['name']}")
        raise HcCliError(
            message="Конфликт портов: порты заняты другими процессами/контейнерами.",
            exit_code=1,
            hint="Освободи порты: " + "; ".join(hints),
        )

    try:
        import questionary
        from questionary import Style as QStyle
    except ImportError:
        raise HcCliError(
            message="Пакет questionary не установлен.",
            exit_code=1,
            hint="pip install questionary",
        )

    style = QStyle(list(QUESTIONARY_STYLE_KWARGS.items()))

    choices = []
    for c in conflicts:
        if c.get("kind") == "process":
            title = (
                f"PID {c['pid']}  {c['command'][:60]}  "
                + ", ".join(f":{p}" for p in sorted(c["ports"]))  # type: ignore[union-attr]
            )
        else:
            title = (
                f"{c['name']}  [{c['image']}]  "
                + ", ".join(f":{p}" for p in sorted(c["ports"]))  # type: ignore[union-attr]
            )
        choices.append(questionary.Choice(title=title, value=c, checked=True))

    selected = questionary.checkbox(
        "Выбери что освободить (SPACE = вкл/выкл  ENTER = применить):",
        choices=choices,
        style=style,
    ).ask()

    if selected is None:
        raise typer.Abort()

    if not selected:
        raise HcCliError(
            message="Конфликт портов не разрешён.",
            exit_code=1,
            hint="Останови конфликтующие процессы/контейнеры вручную и повтори.",
        )

    has_docker = any(c.get("kind") != "process" for c in selected)
    has_process = any(c.get("kind") == "process" for c in selected)

    docker_action = None
    process_action = None

    if has_docker:
        docker_action = questionary.select(
            "Действие для контейнеров:",
            choices=[
                questionary.Choice("Остановить  (docker stop)", value="stop"),
                questionary.Choice("Удалить     (docker rm -f)", value="rm"),
            ],
            style=style,
        ).ask()
        if docker_action is None:
            raise typer.Abort()

    if has_process:
        process_action = questionary.select(
            "Действие для процессов:",
            choices=[
                questionary.Choice("Завершить  (SIGTERM)", value="term"),
                questionary.Choice("Убить      (SIGKILL)", value="kill"),
            ],
            style=style,
        ).ask()
        if process_action is None:
            raise typer.Abort()

    for c in selected:
        if c.get("kind") == "process":
            pid = int(c["pid"])
            if _kill_process(pid, signal_name=str(process_action)):
                console.print(f"[green]✓[/green] процесс [bold]{pid}[/bold] завершён")
            else:
                console.print(f"[red]✗[/red] не удалось завершить процесс [bold]{pid}[/bold]")
            continue

        cid = c["id"] or c["name"]
        if docker_action == "stop":
            r = subprocess.run(["docker", "stop", cid], capture_output=True, check=False)  # noqa: S603
            if r.returncode == 0:
                console.print(f"[green]✓[/green] остановлен [bold]{c['name']}[/bold]")
            else:
                console.print(f"[red]✗[/red] не удалось остановить [bold]{c['name']}[/bold]")
        else:
            r = subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)  # noqa: S603
            if r.returncode == 0:
                console.print(f"[green]✓[/green] удалён [bold]{c['name']}[/bold]")
            else:
                console.print(f"[red]✗[/red] не удалось удалить [bold]{c['name']}[/bold]")

    console.print()



def _show_failure_logs(console: Console, plan: EnvUpPlan) -> None:
    """После неудачного up показать логи упавших контейнеров."""
    import json as _json

    rows = _compose_ps_rows(plan.project)
    failed: list[str] = []
    for row in rows:
        svc = str(row.get("Service") or row.get("Name") or "")
        if svc not in plan.service_names:
            continue
        state = str(row.get("State") or row.get("Status") or "").lower()
        health = str(row.get("Health") or "").lower()
        if state in ("exited", "dead", "restarting") or health == "unhealthy":
            failed.append(svc)

    targets = failed if failed else plan.service_names

    console.print(f"\n[red]── Логи упавших сервисов: {', '.join(targets)} ──[/red]\n")
    subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(plan.project.compose_file),
            "logs", "--tail", "60", "--no-log-prefix",
            *targets,
        ],
        cwd=str(plan.project.cwd),
        check=False,
    )
    console.print(f"\n[dim]Полные логи: hc env logs --follow {' '.join(targets)}[/dim]")


_FRONTEND_VITE_OVERRIDE = "frontend-vite.hc.yml"

# Исправляет проблемы compose из core-runtime-service для frontend-vite:
#
# 1. pnpm dev запускал ВСЕ apps (web + mobile + desktop) → меняем на
#    pnpm --filter=web dev.
#
# 2. api:gen вызывает `openapi-typescript ../core-runtime-service/openapi.json …`
#    Внутри контейнера cwd=/workspace, поэтому путь резолвится в
#    /core-runtime-service/openapi.json. В upstream-compose примонтирован
#    только /workspace — отсюда ENOENT. Монтируем core-runtime-service
#    как /core-runtime-service:ro (read-only — vite туда писать не должен).
#
# 3. VITE_CORE_PROXY_TARGET — прокси /api на core-runtime через docker DNS,
#    а не на localhost:18000 хоста.


