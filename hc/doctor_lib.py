from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client
from hc.config import Config
from hc.constants import CONFIG_PATH, CORE_SRC_DIR, SETUP_LOG_PATH
from hc.core_source import COMPOSE_MODES
from hc.diagnostics import detect_issues, fetch_container_logs, list_compose_containers
from hc.env_state import load_last_env
from hc.json_output import print_json

DoctorScope = Literal["full", "quick", "api", "recovery"]

CHECK_PORTS: dict[int, str] = {
    18080: "UI (caddy)",
    18000: "Core API",
    15173: "Vite HMR",
    5432: "PostgreSQL",
    6379: "Redis",
}


@dataclass(slots=True)
class DoctorCheck:
    label: str
    status: str  # ok | warn | fail | skip | info
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "status": self.status, "detail": self.detail}


@dataclass(slots=True)
class DoctorReport:
    scope: DoctorScope
    checks: list[DoctorCheck] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    modes: dict[str, str] = field(default_factory=dict)
    mode_warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and not any(c.status == "fail" for c in self.checks)


def collect_effective_modes(cfg: Config) -> tuple[dict[str, str], list[str]]:
    last = load_last_env()
    modes = {
        "recovery.mode": cfg.recovery.mode,
        "deploy.core_mode": cfg.deploy.core_mode,
        "env.last_up_mode": last.mode if last else "(не задано)",
        "env.last_up_db": last.db if last else "(не задано)",
    }
    warnings: list[str] = []
    if cfg.recovery.mode.strip() != cfg.deploy.core_mode.strip():
        warnings.append(
            f"recovery.mode ({cfg.recovery.mode}) ≠ deploy.core_mode ({cfg.deploy.core_mode}) — "
            "разные compose для hc core/recovery и hc deploy"
        )
    return modes, warnings


def _icon(status: str) -> str:
    return {
        "ok": "[green]✓[/green]",
        "warn": "[yellow]![/yellow]",
        "fail": "[red]✗[/red]",
        "skip": "[dim]—[/dim]",
        "info": "[cyan]i[/cyan]",
    }.get(status, "[dim]?[/dim]")


def _checks_docker_git() -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    docker_bin = shutil.which("docker")
    if docker_bin:
        ver = subprocess.run(  # noqa: S603
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        docker_ver = ver.stdout.strip() or "?"
        out.append(DoctorCheck("Docker", "ok", f"v{docker_ver}  ({docker_bin})"))
    else:
        out.append(DoctorCheck("Docker", "fail", "не найден — установи Docker или OrbStack"))

    compose_ver = subprocess.run(  # noqa: S603
        ["docker", "compose", "version", "--short"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if compose_ver.returncode == 0:
        out.append(DoctorCheck("Docker Compose", "ok", compose_ver.stdout.strip()))
    else:
        out.append(DoctorCheck("Docker Compose", "warn", "не определена версия"))

    git_bin = shutil.which("git")
    if git_bin:
        git_ver = subprocess.run(  # noqa: S603
            ["git", "--version"], capture_output=True, text=True, check=False
        )
        out.append(DoctorCheck("git", "ok", git_ver.stdout.strip()))
    else:
        out.append(DoctorCheck("git", "warn", "не найден — нужен для hc core init/update"))
    return out


def _checks_config() -> tuple[list[DoctorCheck], list[str]]:
    issues: list[str] = []
    checks: list[DoctorCheck] = []
    if CONFIG_PATH.exists():
        cfg = Config.load()
        host_ok = bool(cfg.core.host.strip())
        token_ok = bool(cfg.core.token.strip())
        checks.append(DoctorCheck("Конфиг (~/.config/hc)", "ok", str(CONFIG_PATH)))
        checks.append(
            DoctorCheck(
                "  Core host",
                "ok" if host_ok else "warn",
                f"{cfg.core.host}:{cfg.core.port}" if host_ok else "не задан",
            )
        )
        checks.append(
            DoctorCheck(
                "  Token",
                "ok" if token_ok else "warn",
                "задан" if token_ok else "не задан — hc connect",
            )
        )
    else:
        checks.append(DoctorCheck("Конфиг (~/.config/hc)", "warn", "не найден — hc connect или hc setup"))
        issues.append("Конфиг не найден")
    return checks, issues


def _checks_modes(cfg: Config | None = None) -> tuple[list[DoctorCheck], dict[str, str], list[str]]:
    cfg = cfg or Config.load()
    modes, warnings = collect_effective_modes(cfg)
    checks = [
        DoctorCheck(f"  {key}", "info", value)
        for key, value in modes.items()
    ]
    return checks, modes, warnings


def _checks_core_sources() -> tuple[list[DoctorCheck], list[str]]:
    issues: list[str] = []
    checks: list[DoctorCheck] = []
    if CORE_SRC_DIR.exists():
        checks.append(DoctorCheck("Core исходники", "ok", str(CORE_SRC_DIR)))
        for mode, rel in COMPOSE_MODES.items():
            cf = CORE_SRC_DIR / rel
            checks.append(
                DoctorCheck(
                    f"  compose [{mode}]",
                    "ok" if cf.exists() else "warn",
                    rel if cf.exists() else f"{rel}  ← не найден",
                )
            )
        env_file = CORE_SRC_DIR / ".env"
        checks.append(
            DoctorCheck(
                "  .env",
                "ok" if env_file.exists() else "warn",
                ".env готов" if env_file.exists() else "нет — создастся при hc env up",
            )
        )
    else:
        checks.append(DoctorCheck("Core исходники", "warn", "не найдены — hc core init"))
        issues.append("Core исходники не найдены")
    return checks, issues


# HTTP smoke-check: какие порты, кроме TCP-listen, ещё дёргаем GET / и ждём
# непустой ответ. Если порт открыт, но HTTP пуст/connection refused — это
# симптом «caddy/vite живой, но upstream не отвечает».
_HTTP_SMOKE_PORTS: dict[int, str] = {
    18080: "UI (caddy)",
    15173: "Vite HMR",
}


def _http_smoke(port: int, *, timeout: float = 1.5) -> tuple[bool, str]:
    """Сделать GET / и вернуть (ok, краткий статус)."""
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/", timeout=timeout)
        if r.status_code >= 500:
            return False, f"HTTP {r.status_code} ({len(r.content)}B)"
        if r.status_code in (200, 301, 302, 304, 404, 405):
            return True, f"HTTP {r.status_code} ({len(r.content)}B)"
        return True, f"HTTP {r.status_code}"
    except httpx.RemoteProtocolError:
        return False, "empty reply"
    except httpx.ConnectError:
        return False, "connection refused"
    except httpx.TimeoutException:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"err: {type(exc).__name__}"


def _checks_ports() -> tuple[list[DoctorCheck], list[str]]:
    checks: list[DoctorCheck] = []
    issues: list[str] = []
    for port, label in CHECK_PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            in_use = s.connect_ex(("127.0.0.1", port)) == 0

        if not in_use:
            checks.append(DoctorCheck(f"  :{port} {label}", "skip", "free"))
            continue

        if port in _HTTP_SMOKE_PORTS:
            ok, detail = _http_smoke(port)
            if ok:
                checks.append(DoctorCheck(f"  :{port} {label}", "ok", detail))
            else:
                checks.append(
                    DoctorCheck(
                        f"  :{port} {label}",
                        "fail",
                        f"listening, но {detail}",
                    )
                )
                issues.append(
                    f":{port} {label} слушает, но HTTP не отвечает ({detail}). "
                    f"Проверь логи: docker logs dev-hc-{'caddy' if port == 18080 else 'frontend-vite'}"
                )
        else:
            checks.append(DoctorCheck(f"  :{port} {label}", "ok", "listening"))

    return checks, issues


def _checks_disk() -> tuple[list[DoctorCheck], list[str]]:
    issues: list[str] = []
    checks: list[DoctorCheck] = []
    try:
        stat = shutil.disk_usage(Path.home())
        free_gb = stat.free / 1024**3
        total_gb = stat.total / 1024**3
        used_pct = (stat.used / stat.total) * 100
        status = "fail" if used_pct > 90 else "warn" if used_pct > 75 else "ok"
        checks.append(
            DoctorCheck(
                "Диск (home)",
                status,
                f"{free_gb:.1f} GB свободно из {total_gb:.1f} GB ({used_pct:.0f}% занято)",
            )
        )
        if used_pct > 90:
            issues.append(f"Диск почти полон ({used_pct:.0f}%)")
    except Exception:
        pass
    return checks, issues


def _checks_api(console: Console) -> tuple[list[DoctorCheck], list[str]]:
    issues: list[str] = []
    checks: list[DoctorCheck] = []
    cfg = Config.load()
    host = cfg.core.host.strip() or "localhost"
    port = cfg.core.port
    checks.append(DoctorCheck("API target", "info", f"http://{host}:{port}"))

    client = require_client(console)
    t0 = time.monotonic()

    async def _probe() -> dict | None:
        health = await client.admin_status()
        if not health:
            health = await client.health()
        return health

    try:
        health = anyio.run(_probe)
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        checks.append(DoctorCheck("Core API", "fail", str(e)))
        issues.append("Core API недоступен")
        return checks, issues

    latency_ms = (time.monotonic() - t0) * 1000
    if not health:
        checks.append(DoctorCheck("Core API", "fail", "нет ответа /health"))
        issues.append("Core API недоступен")
        return checks, issues

    version = str(health.get("version", "?"))
    status = str(health.get("status", "running"))
    checks.append(
        DoctorCheck(
            "Core API",
            "ok" if status.lower() in {"running", "ok"} else "warn",
            f"{status}  v{version}  ({latency_ms:.0f}ms)",
        )
    )
    plugins = anyio.run(client.get_plugins)
    if isinstance(plugins, list):
        running = sum(1 for p in plugins if str(p.get("status", "")).lower() == "running")
        checks.append(DoctorCheck("  Плагинов running", "info", str(running)))
    return checks, issues


def _checks_recovery_extras() -> list[DoctorCheck]:
    return [
        DoctorCheck("setup.log", "ok" if SETUP_LOG_PATH.exists() else "skip", str(SETUP_LOG_PATH)),
    ]


def _checks_runtime_logs() -> tuple[list[DoctorCheck], list[str]]:
    """
    Прогнать недавние логи core-runtime через каталог known issues.
    Срабатывает только если стек запущен и compose-файл известен — иначе skip.

    Если найдены известные проблемы — каждая попадает в issues, чтобы doctor
    завершился с кодом 1 (нужно действие пользователя).
    """
    checks: list[DoctorCheck] = []
    issues: list[str] = []

    last = load_last_env()
    if not last or not last.mode:
        return checks, issues

    rel = COMPOSE_MODES.get(last.mode)
    if not rel:
        return checks, issues

    compose_file = CORE_SRC_DIR / rel
    if not compose_file.exists():
        return checks, issues

    try:
        containers = list_compose_containers(compose_file, compose_file.parent)
    except Exception:  # noqa: BLE001
        return checks, issues

    runtime = next(
        (c for c in containers if str(c.get("Service") or "") == "core-runtime"),
        None,
    )
    if runtime is None:
        return checks, issues

    state = str(runtime.get("_effective_state") or runtime.get("State") or "").lower()
    if state == "running":
        # Здоровый running — заглянем в логи на всякий случай (короткий хвост),
        # но фейлить doctor не будем.
        try:
            logs = fetch_container_logs(compose_file, compose_file.parent, "core-runtime", tail=50)
        except Exception:  # noqa: BLE001
            return checks, issues
        found = detect_issues(logs, service="core-runtime")
        if not found:
            checks.append(DoctorCheck("Логи core-runtime", "ok", "известных ошибок не найдено"))
            return checks, issues
        # Running но в логах что-то подозрительное — warning, не error.
        checks.append(
            DoctorCheck(
                "Логи core-runtime",
                "warn",
                f"найдено подозрительных шаблонов: {len(found)} (см. ниже)",
            )
        )
        for det in found:
            issues.append(f"core-runtime: {det.issue.title}")
        return checks, issues

    # Не running — это серьёзно. Подтянем больше логов.
    try:
        logs = fetch_container_logs(compose_file, compose_file.parent, "core-runtime", tail=200)
    except Exception:  # noqa: BLE001
        return checks, issues
    found = detect_issues(logs, service="core-runtime")
    if found:
        checks.append(
            DoctorCheck(
                "Логи core-runtime",
                "fail",
                f"контейнер не running, найдено проблем: {len(found)}",
            )
        )
        for det in found:
            issues.append(
                f"core-runtime: {det.issue.title} → см. `hc env up` для предложения по починке"
            )
    else:
        checks.append(
            DoctorCheck(
                "Логи core-runtime",
                "warn",
                f"контейнер не running (state={state}), но известных ошибок не найдено",
            )
        )

    return checks, issues


def run_doctor(console: Console, *, scope: DoctorScope) -> DoctorReport:
    report = DoctorReport(scope=scope)
    cfg = Config.load() if CONFIG_PATH.exists() else Config()

    if scope == "api":
        report.checks.extend(_checks_config()[0])
        api_checks, api_issues = _checks_api(console)
        report.checks.extend(api_checks)
        report.issues.extend(api_issues)
        mode_checks, modes, warnings = _checks_modes(cfg)
        report.checks.extend(mode_checks)
        report.modes = modes
        report.mode_warnings = warnings
        return report

    report.checks.extend(_checks_docker_git())
    cfg_checks, cfg_issues = _checks_config()
    report.checks.extend(cfg_checks)
    report.issues.extend(cfg_issues)

    mode_checks, modes, warnings = _checks_modes(cfg)
    report.checks.extend(mode_checks)
    report.modes = modes
    report.mode_warnings = warnings

    if scope in {"full", "recovery"}:
        src_checks, src_issues = _checks_core_sources()
        report.checks.extend(src_checks)
        report.issues.extend(src_issues)

    if scope == "full":
        port_checks, port_issues = _checks_ports()
        report.checks.extend(port_checks)
        report.issues.extend(port_issues)
        disk_checks, disk_issues = _checks_disk()
        report.checks.extend(disk_checks)
        report.issues.extend(disk_issues)

        log_checks, log_issues = _checks_runtime_logs()
        report.checks.extend(log_checks)
        report.issues.extend(log_issues)

    if scope == "recovery":
        report.checks.extend(_checks_recovery_extras())

    if any(c.label == "Docker" and c.status == "fail" for c in report.checks):
        report.issues.append("Docker не найден")

    return report


def print_doctor_report(console: Console, report: DoctorReport, *, json_out: bool = False) -> None:
    if json_out:
        print_json(
            {
                "ok": report.ok,
                "scope": report.scope,
                "issues": report.issues,
                "mode_warnings": report.mode_warnings,
                "modes": report.modes,
                "checks": [c.as_dict() for c in report.checks],
            }
        )
        if not report.ok:
            raise typer.Exit(code=1)
        return

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(min_width=28)
    table.add_column()
    table.add_column(style="dim")

    prev_section = False
    for check in report.checks:
        if check.label.startswith("  ") and not prev_section:
            prev_section = True
        elif not check.label.startswith("  "):
            if prev_section:
                table.add_row("")
            prev_section = False
        table.add_row(check.label, _icon(check.status), check.detail)

    console.print(table)

    if report.mode_warnings:
        console.print()
        for w in report.mode_warnings:
            console.print(f"[yellow]![/yellow] {w}")

    if report.issues:
        console.print()
        for issue in report.issues:
            console.print(f"[yellow]![/yellow] {issue}")
    elif report.ok:
        console.print("\n[green]✓ Всё в порядке[/green]")

    if not report.ok:
        raise typer.Exit(code=1)
