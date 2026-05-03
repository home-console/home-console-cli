from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
import os
import time

import typer
from rich.console import Console
from rich.panel import Panel

from hc.config import Config
from hc.core_source import CoreSource, get_core_source_from_repo, get_core_source_local
from hc.core_ops import compose_project_from_source, require_docker
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


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _run_env(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _ssh_cmd(ssh: str, remote_cmd: str) -> list[str]:
    # deliberately no shell=True
    return ["ssh", ssh, remote_cmd]

def _is_compose_running(ps_stdout: str) -> bool:
    out = (ps_stdout or "").strip()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # обычно первая строка — заголовок
    return len(lines) >= 2


def _fmt_s(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m{s:02d}s"


def _step_start(console: Console, title: str, *, quiet: bool) -> float:
    if not quiet:
        console.print(f"[cyan]→[/cyan] {title}")
    return time.monotonic()


def _step_ok(console: Console, title: str, t0: float, *, quiet: bool) -> float:
    dt = time.monotonic() - t0
    if not quiet:
        console.print(f"[green]✓[/green] {title} ([dim]{_fmt_s(dt)}[/dim])")
    return dt


def _wait_core_healthy_local(
    console: Console,
    *,
    compose_file: Path,
    timeout_s: int,
    interval_s: float,
    health_url: str,
    quiet: bool,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    if not quiet:
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
                if not quiet:
                    console.print(
                        f"[green]✓[/green] core healthy ([dim]{_fmt_s(time.monotonic() - started)}[/dim])"
                    )
                return
        now = time.monotonic()
        if not quiet and now >= next_tick:
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
    quiet: bool,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    if not quiet:
        console.print(f"[cyan]→[/cyan] Wait healthy remote (timeout={timeout_s}s) на [bold]{ssh}[/bold]")
    while time.time() < deadline:
        remote = (
            f"cd {shlex.quote(path)} && "
            f"docker compose -f {shlex.quote(compose_rel)} ps --status running core-runtime >/dev/null 2>&1 && "
            f"docker compose -f {shlex.quote(compose_rel)} exec -T core-runtime sh -lc "
            f"{shlex.quote(f'curl -fsS {health_url} >/dev/null && echo ok || echo no')}"
        )
        p = subprocess.run(_ssh_cmd(ssh, remote), text=True, capture_output=True, check=False)  # noqa: S603
        if p.returncode == 0 and (p.stdout or "").strip().endswith("ok"):
            if not quiet:
                console.print(
                    f"[green]✓[/green] core healthy remote ([dim]{_fmt_s(time.monotonic() - started)}[/dim])"
                )
            return
        now = time.monotonic()
        if not quiet and now >= next_tick:
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
    deploy_app = typer.Typer(
        help="Деплой: build/tag/push/rollout (docker compose)",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @deploy_app.callback(invoke_without_command=True)
    def _deploy_root(
        ctx: typer.Context,
        tag: str = typer.Option("latest", "--tag", help="Тег (по умолчанию latest)"),
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (для --ssh, по умолчанию из config)"),
        build: bool = typer.Option(True, "--build/--no-build", help="Собрать image локально (по умолчанию да)"),
        push: bool = typer.Option(True, "--push/--no-push", help="Запушить image в registry (по умолчанию да)"),
        rollout: bool = typer.Option(True, "--rollout/--no-rollout", help="Сделать compose pull+up (по умолчанию да)"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout (по умолчанию да)"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод (только итог/ошибка)"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        """
        Если запущено как `hc deploy` без подкоманд — выполняет полный пайплайн:
        docker build + tag + push + docker compose pull + up -d + wait(health).
        """
        if ctx.invoked_subcommand is not None:
            return

        console = Console()
        try:
            require_docker(console)
            cfg = Config.load()

            resolved_image = (image or cfg.deploy.core_image).strip()
            resolved_mode = (mode or cfg.deploy.core_mode).strip().lower()
            if resolved_mode not in {"dev", "image"}:
                raise InvalidModeError(
                    message="--mode должен быть dev или image.",
                    exit_code=2,
                    hint="Пример: `hc deploy --mode dev` или `hc deploy --mode image`.",
                )
            resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
            resolved_path = path if path is not None else (cfg.deploy.path or None)

            full = f"{resolved_image}:{tag}"
            target = f"remote {resolved_ssh}" if resolved_ssh else "local"

            total_t0 = time.monotonic()
            steps: list[dict[str, object]] = []

            if not quiet and not json_out:
                console.print(Panel.fit(f"{full}\nmode={resolved_mode}\ntarget={target}", title="hc deploy"))

            if build:
                src = _resolve_source(console)
                t0 = _step_start(console, f"Build {full}", quiet=quiet or json_out)
                _run(["docker", "build", "-t", full, str(src.path)], cwd=src.path)
                dt = _step_ok(console, "Build", t0, quiet=quiet or json_out)
                steps.append({"name": "build", "ok": True, "duration_s": dt})

            if push:
                t0 = _step_start(console, f"Push {full}", quiet=quiet or json_out)
                _run(["docker", "push", full])
                dt = _step_ok(console, "Push", t0, quiet=quiet or json_out)
                steps.append({"name": "push", "ok": True, "duration_s": dt})

            if rollout:
                t0 = _step_start(console, "Rollout (compose pull + up -d)", quiet=quiet or json_out)
                core_rollout(
                    image=resolved_image,
                    tag=tag,
                    ssh=resolved_ssh,
                    path=resolved_path,
                    mode=resolved_mode,
                    wait=False,
                )  # type: ignore[misc]
                dt = _step_ok(console, "Rollout", t0, quiet=quiet or json_out)
                steps.append({"name": "rollout", "ok": True, "duration_s": dt})

                if wait:
                    t0 = _step_start(console, "Wait healthy", quiet=quiet or json_out)
                    core_wait(
                        image=resolved_image,
                        tag=tag,
                        ssh=resolved_ssh,
                        path=resolved_path,
                        mode=resolved_mode,
                        timeout=timeout,
                        interval=interval,
                        health_url=health_url,
                        quiet=quiet or json_out,
                    )  # type: ignore[misc]
                    dt = _step_ok(console, "Wait healthy", t0, quiet=quiet or json_out)
                    steps.append({"name": "wait", "ok": True, "duration_s": dt})

            total_dt = time.monotonic() - total_t0

            if json_out:
                payload = {
                    "ok": True,
                    "command": "deploy",
                    "image": resolved_image,
                    "tag": tag,
                    "full": full,
                    "mode": resolved_mode,
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
                console.print(f"[green]✓[/green] Deploy ok ({full}, {target})")
                return

            console.print(f"[green]✓[/green] Deploy done ([dim]{_fmt_s(total_dt)}[/dim])")
        except HcCliError as e:
            if json_out:
                print(json.dumps(json_error_payload("deploy", e), ensure_ascii=False))
                raise typer.Exit(code=int(e.exit_code or 1))
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))
        except typer.Exit as e:
            if json_out:
                print(json.dumps(json_error_payload("deploy", e), ensure_ascii=False))
            raise
        except Exception as e:  # noqa: BLE001
            if json_out:
                print(json.dumps(json_error_payload("deploy", e), ensure_ascii=False))
                raise typer.Exit(code=1)
            raise

    cfg_app = typer.Typer(
        help="Дефолты для deploy (ssh/path/image/mode)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @cfg_app.command("show")
    def cfg_show() -> None:
        console = Console()
        cfg = Config.load()
        body = (
            f"deploy.core_image = {cfg.deploy.core_image}\n"
            f"deploy.core_mode  = {cfg.deploy.core_mode}\n"
            f"deploy.ssh        = {cfg.deploy.ssh or '(empty)'}\n"
            f"deploy.path       = {cfg.deploy.path or '(empty)'}\n"
        )
        console.print(Panel.fit(body, title="hc deploy config"))

    @cfg_app.command("set")
    def cfg_set(
        core_image: str | None = typer.Option(None, "--core-image", help="Напр. ghcr.io/org/core-runtime"),
        core_mode: str | None = typer.Option(None, "--core-mode", help="dev|image"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose"),
    ) -> None:
        console = Console()
        cfg = Config.load()
        if core_image is not None:
            cfg.deploy.core_image = core_image.strip()
        if core_mode is not None:
            m = core_mode.strip().lower()
            if m not in {"dev", "image"}:
                console.print("[red]Ошибка:[/red] --core-mode должен быть dev или image")
                raise typer.Exit(code=2)
            cfg.deploy.core_mode = m
        if ssh is not None:
            cfg.deploy.ssh = ssh.strip()
        if path is not None:
            cfg.deploy.path = path.strip()
        cfg.save()
        console.print("[green]✓[/green] deploy defaults сохранены")

    core_app = typer.Typer(
        help="Деплой core-runtime",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @core_app.command("info")
    def core_info() -> None:
        """Показать, где core source и какой compose используется."""
        console = Console()
        src = _resolve_source(console)
        require_docker(console)
        project = compose_project_from_source(console, src)
        body = f"core source: {src.path}\ncompose: {project.compose_file}\n"
        console.print(Panel.fit(body, title="deploy core info"))

    @core_app.command("build")
    def core_build(
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        push: bool = typer.Option(False, "--push", help="Сразу push в registry"),
    ) -> None:
        """Собрать docker image core-runtime из исходников."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        cfg = Config.load()
        image = (image or cfg.deploy.core_image).strip()
        full = f"{image}:{tag}"
        console.print(f"Build: [bold]{full}[/bold]")
        _run(["docker", "build", "-t", full, str(src.path)], cwd=src.path)
        console.print("[green]✓[/green] build ok")
        if push:
            _run(["docker", "push", full])
            console.print("[green]✓[/green] push ok")

    @core_app.command("push")
    def core_push(
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
    ) -> None:
        """Запушить docker image в registry."""
        console = Console()
        require_docker(console)
        cfg = Config.load()
        image = (image or cfg.deploy.core_image).strip()
        full = f"{image}:{tag}"
        console.print(f"Push: [bold]{full}[/bold]")
        _run(["docker", "push", full])
        console.print("[green]✓[/green] push ok")

    @core_app.command("rollout")
    def core_rollout(
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
    ) -> None:
        """
        Rollout (compose pull + up -d) для core-runtime.

        Локально: использует compose в core-runtime-service/deploy/dev.
        Удалённо: `--ssh user@host --path /srv/core-runtime-service` выполнит compose на сервере.
        """
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        cfg = Config.load()
        image = (image or cfg.deploy.core_image).strip()
        mode = (mode or cfg.deploy.core_mode).strip().lower()
        if mode not in {"dev", "image"}:
            console.print("[red]Ошибка:[/red] --mode должен быть dev или image")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)
        full = f"{image}:{tag}"

        compose_file = "docker-compose.image.yml" if mode == "image" else "docker-compose.yml"
        if ssh:
            if not path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                raise typer.Exit(code=2)
            remote = (
                f"cd {shlex.quote(path)} && "
                f"CORE_RUNTIME_IMAGE={shlex.quote(full)} "
                f"docker compose -f deploy/dev/{compose_file} pull core-runtime && "
                f"CORE_RUNTIME_IMAGE={shlex.quote(full)} "
                f"docker compose -f deploy/dev/{compose_file} up -d"
            )
            console.print(f"Remote rollout on [bold]{ssh}[/bold]")
            _run(_ssh_cmd(ssh, remote))
            console.print("[green]✓[/green] remote rollout ok")
            if wait:
                _wait_core_healthy_remote(
                    console,
                    ssh=ssh,
                    path=path,
                    compose_rel=f"deploy/dev/{compose_file}",
                    timeout_s=timeout,
                    interval_s=interval,
                    health_url=health_url,
                    quiet=False,
                )
            return

        # local rollout
        project = compose_project_from_source(console, src, mode=mode)
        console.print(f"Local rollout: [bold]{full}[/bold]")
        env = {**os.environ, "CORE_RUNTIME_IMAGE": full}
        _run_env(
            ["docker", "compose", "-f", str(project.compose_file), "pull", "core-runtime"],
            cwd=project.cwd,
            env=env,
        )
        _run_env(["docker", "compose", "-f", str(project.compose_file), "up", "-d"], cwd=project.cwd, env=env)
        console.print("[green]✓[/green] local rollout ok")
        if wait:
            _wait_core_healthy_local(
                console,
                compose_file=project.compose_file,
                timeout_s=timeout,
                interval_s=interval,
                health_url=health_url,
                quiet=False,
            )

    @core_app.command("wait")
    def core_wait(
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод"),
    ) -> None:
        """Дождаться, пока core-runtime станет healthy (через curl внутри контейнера)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        cfg = Config.load()
        image = (image or cfg.deploy.core_image).strip()
        mode = (mode or cfg.deploy.core_mode).strip().lower()
        if mode not in {"dev", "image"}:
            console.print("[red]Ошибка:[/red] --mode должен быть dev или image")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)
        _ = f"{image}:{tag}"  # для UX一致ности, но для wait не обязателен

        compose_file = "docker-compose.image.yml" if mode == "image" else "docker-compose.yml"
        if ssh:
            if not path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                raise typer.Exit(code=2)
            _wait_core_healthy_remote(
                console,
                ssh=ssh,
                path=path,
                compose_rel=f"deploy/dev/{compose_file}",
                timeout_s=timeout,
                interval_s=interval,
                health_url=health_url,
                quiet=quiet,
            )
            return
        project = compose_project_from_source(console, src, mode=mode)
        _wait_core_healthy_local(
            console,
            compose_file=project.compose_file,
            timeout_s=timeout,
            interval_s=interval,
            health_url=health_url,
            quiet=quiet,
        )

    @core_app.command("logs")
    def core_logs(
        follow: bool = typer.Option(False, "-f", "--follow", help="Следить за логами"),
        tail: int = typer.Option(200, "--tail", help="Сколько строк показать"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённых логов (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
    ) -> None:
        """Логи core-runtime (docker compose logs)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        cfg = Config.load()
        mode = (mode or cfg.deploy.core_mode).strip().lower()
        if mode not in {"dev", "image"}:
            console.print("[red]Ошибка:[/red] --mode должен быть dev или image")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)
        compose_file = "docker-compose.image.yml" if mode == "image" else "docker-compose.yml"

        args = ["docker", "compose", "-f", f"deploy/dev/{compose_file}", "logs", "--tail", str(tail)]
        if follow:
            args.append("-f")
        args.append("core-runtime")

        if ssh:
            if not path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                raise typer.Exit(code=2)
            remote = f"cd {shlex.quote(path)} && " + " ".join(shlex.quote(a) for a in args)
            _run(_ssh_cmd(ssh, remote))
            return

        project = compose_project_from_source(console, src, mode=mode)
        local_args = ["docker", "compose", "-f", str(project.compose_file), "logs", "--tail", str(tail)]
        if follow:
            local_args.append("-f")
        local_args.append("core-runtime")
        _run(local_args, cwd=project.cwd)

    @core_app.command("release")
    def core_release(
        tag: str = typer.Argument(..., help="Новый тег (напр. v0.1.0)"),
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help="dev|image (по умолчанию из config)"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
    ) -> None:
        """Короткий шорткат: rollout на конкретный tag."""
        core_rollout(
            image=image,
            tag=tag,
            ssh=ssh,
            path=path,
            mode=mode,
            wait=wait,
            timeout=timeout,
            interval=interval,
            health_url=health_url,
        )

    deploy_app.add_typer(cfg_app, name="config")
    deploy_app.add_typer(core_app, name="core")
    app.add_typer(deploy_app, name="deploy")

