from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
import time

import typer
from rich.console import Console
from rich.panel import Panel

from hc.config import Config
from hc.core_ops import compose_project_from_source, require_docker
from hc.core_source import CoreSource, get_core_source_from_repo, get_core_source_local
from hc.errors import (
    CoreSourcesNotFoundError,
    HealthyTimeoutError,
    HcCliError,
    InvalidModeError,
    json_error_payload,
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
    raise CoreSourcesNotFoundError(
        message="Исходники Core не найдены локально.",
        exit_code=1,
        hint="Сделай `hc core init` (скачает в ~/.local/share/hc) или запусти из монорепы.",
    )


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _fmt_s(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m{s:02d}s"


def _is_compose_running(ps_stdout: str) -> bool:
    out = (ps_stdout or "").strip()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return len(lines) >= 2


def _wait_core_healthy_local(
    console: Console,
    *,
    compose_file: Path,
    timeout_s: int,
    interval_s: float,
    health_url: str,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    console.print(f"[cyan]→[/cyan] Wait healthy (внутри контейнера, timeout={timeout_s}s)")
    while time.time() < deadline:
        ps = subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(compose_file), "ps", "--status", "running", "core-runtime"],
            cwd=str(compose_file.parent),
            text=True,
            capture_output=True,
        )
        if ps.returncode == 0 and _is_compose_running(ps.stdout):
            chk = subprocess.run(  # noqa: S603
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose_file),
                    "exec",
                    "-T",
                    "core-runtime",
                    "sh",
                    "-lc",
                    f"curl -fsS {shlex.quote(health_url)} >/dev/null && echo ok || echo no",
                ],
                cwd=str(compose_file.parent),
                text=True,
                capture_output=True,
            )
            if chk.returncode == 0 and (chk.stdout or "").strip() == "ok":
                console.print(f"[green]✓[/green] core healthy ([dim]{_fmt_s(time.monotonic() - started)}[/dim])")
                return
        now = time.monotonic()
        if now >= next_tick:
            elapsed = now - started
            console.print(f"[dim]… жду healthy: {_fmt_s(elapsed)} / {timeout_s}s[/dim]")
            next_tick = now + 5.0
        time.sleep(interval_s)
    raise HealthyTimeoutError(
        message="core не вышел в healthy за отведённое время.",
        exit_code=1,
        hint="Смотри логи: `hc deploy core logs -f` или `docker compose logs -f core-runtime`.",
    )


def _wait_core_healthy_remote(
    console: Console,
    *,
    ssh: str,
    path: str,
    compose_rel: str,
    timeout_s: int,
    interval_s: float,
    health_url: str,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    console.print(f"[cyan]→[/cyan] Wait healthy remote (timeout={timeout_s}s) на [bold]{ssh}[/bold]")
    while time.time() < deadline:
        remote = (
            f"cd {shlex.quote(path)} && "
            f"docker compose -f {shlex.quote(compose_rel)} ps --status running core-runtime >/dev/null 2>&1 && "
            f"docker compose -f {shlex.quote(compose_rel)} exec -T core-runtime sh -lc "
            f"{shlex.quote(f'curl -fsS {health_url} >/dev/null && echo ok || echo no')}"
        )
        p = subprocess.run(["ssh", ssh, remote], text=True, capture_output=True, check=False)  # noqa: S603
        if p.returncode == 0 and (p.stdout or "").strip().endswith("ok"):
            console.print(f"[green]✓[/green] core healthy remote ([dim]{_fmt_s(time.monotonic() - started)}[/dim])")
            return
        now = time.monotonic()
        if now >= next_tick:
            elapsed = now - started
            console.print(f"[dim]… жду healthy remote: {_fmt_s(elapsed)} / {timeout_s}s[/dim]")
            next_tick = now + 5.0
        time.sleep(interval_s)
    raise HealthyTimeoutError(
        message="core не вышел в healthy за отведённое время (remote).",
        exit_code=1,
        hint="Смотри логи: `hc deploy core logs -f --ssh ... --path ...`.",
    )


def register(app: typer.Typer) -> None:
    update_app = typer.Typer(
        help="Update: обёртки над deploy/rollout",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @update_app.callback(invoke_without_command=True)
    def _update_root(
        ctx: typer.Context,
        tag: str = typer.Option("latest", "--tag", help="Тег (по умолчанию latest)"),
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого update (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (для --ssh, по умолчанию из config)"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после update (по умолчанию да)"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод (только итог/ошибка)"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        """
        Если запущено как `hc update` без подкоманд — обновляет core-runtime:
        compose pull core-runtime + compose up -d.
        """
        if ctx.invoked_subcommand is not None:
            return
        update_core(
            image=image,
            tag=tag,
            mode=mode,
            ssh=ssh,
            path=path,
            wait=wait,
            timeout=timeout,
            interval=interval,
            health_url=health_url,
            quiet=quiet,
            json_out=json_out,
        )  # type: ignore[misc]

    @update_app.command("core")
    def update_core(
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (для --ssh)"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после update"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод (только итог/ошибка)"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        """Обновить core-runtime до нового image:tag (compose pull + up -d)."""
        console = Console()
        try:
            require_docker(console)
            total_t0 = time.monotonic()
            src = _resolve_source(console)
            cfg = Config.load()

            image = (image or cfg.deploy.core_image).strip()
            mode = (mode or cfg.deploy.core_mode).strip().lower()
            if mode not in {"dev", "image"}:
                raise InvalidModeError(
                    message="--mode должен быть dev или image.",
                    exit_code=2,
                    hint="Пример: `hc update core --mode dev` или `hc update core --mode image`.",
                )
            ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
            path = path if path is not None else (cfg.deploy.path or None)

            full = f"{image}:{tag}"
            compose_file = "docker-compose.image.yml" if mode == "image" else "docker-compose.yml"
            target = f"remote {ssh}" if ssh else "local"

            if not quiet and not json_out:
                console.print(Panel.fit(f"{full}\nmode={mode}\ntarget={target}", title="hc update core"))

            steps: list[dict[str, object]] = []

            if ssh:
                if not path:
                    console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                    raise typer.Exit(code=2)
                t0 = time.monotonic()
                remote = (
                    f"cd {shlex.quote(path)} && "
                    f"CORE_RUNTIME_IMAGE={shlex.quote(full)} "
                    f"docker compose -f deploy/dev/{compose_file} pull core-runtime && "
                    f"CORE_RUNTIME_IMAGE={shlex.quote(full)} "
                    f"docker compose -f deploy/dev/{compose_file} up -d"
                )
                _run(["ssh", ssh, remote])
                dt = time.monotonic() - t0
                if not quiet and not json_out:
                    console.print(f"[green]✓[/green] update applied ([dim]{_fmt_s(dt)}[/dim])")
                steps.append({"name": "update", "ok": True, "duration_s": dt})
                if wait:
                    t0 = time.monotonic()
                    _wait_core_healthy_remote(
                        console,
                        ssh=ssh,
                        path=path,
                        compose_rel=f"deploy/dev/{compose_file}",
                        timeout_s=timeout,
                        interval_s=interval,
                        health_url=health_url,
                    )
                    dtw = time.monotonic() - t0
                    steps.append({"name": "wait", "ok": True, "duration_s": dtw})
            else:
                project = compose_project_from_source(console, src, mode=mode)
                env = {**os.environ, "CORE_RUNTIME_IMAGE": full}
                t0 = time.monotonic()
                _run(
                    ["docker", "compose", "-f", str(project.compose_file), "pull", "core-runtime"],
                    cwd=project.cwd,
                    env=env,
                )
                _run(["docker", "compose", "-f", str(project.compose_file), "up", "-d"], cwd=project.cwd, env=env)
                dt = time.monotonic() - t0
                if not quiet and not json_out:
                    console.print(f"[green]✓[/green] update applied ([dim]{_fmt_s(dt)}[/dim])")
                steps.append({"name": "update", "ok": True, "duration_s": dt})
                if wait:
                    t0 = time.monotonic()
                    _wait_core_healthy_local(
                        console,
                        compose_file=project.compose_file,
                        timeout_s=timeout,
                        interval_s=interval,
                        health_url=health_url,
                    )
                    dtw = time.monotonic() - t0
                    steps.append({"name": "wait", "ok": True, "duration_s": dtw})

            total_dt = time.monotonic() - total_t0

            if json_out:
                payload = {
                    "ok": True,
                    "command": "update.core",
                    "image": image,
                    "tag": tag,
                    "full": full,
                    "mode": mode,
                    "target": target,
                    "wait": bool(wait),
                    "timeout_s": int(timeout),
                    "interval_s": float(interval),
                    "health_url": health_url,
                    "steps": steps,
                    "duration_s": total_dt,
                }
                print(json.dumps(payload, ensure_ascii=False))
                return

            if quiet:
                console.print(f"[green]✓[/green] Update ok ({full}, {target})")
                return

            console.print(f"[green]✓[/green] Update done ([dim]{_fmt_s(total_dt)}[/dim])")
        except HcCliError as e:
            if json_out:
                print(json.dumps(json_error_payload("update.core", e), ensure_ascii=False))
                raise typer.Exit(code=int(e.exit_code or 1))
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))
        except typer.Exit as e:
            if json_out:
                print(json.dumps(json_error_payload("update.core", e), ensure_ascii=False))
            raise
        except Exception as e:  # noqa: BLE001
            if json_out:
                print(json.dumps(json_error_payload("update.core", e), ensure_ascii=False))
                raise typer.Exit(code=1)
            raise

    app.add_typer(update_app, name="update")

