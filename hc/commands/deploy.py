from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from hc.config import Config, normalize_deploy_core_mode
from hc.core_ops import ComposeProject, compose_project_from_source, require_docker
from hc.core_source import (
    IMAGE_MODES,
    VALID_MODES,
    CoreSource,
    get_core_source_from_repo,
    get_core_source_local,
)
from hc.errors import (
    CoreSourcesNotFoundError,
    HcCliError,
    HealthyTimeoutError,
    InvalidModeError,
    json_error_payload,
)

_MODE_HELP = (
    "dev | dev-reload | dev-image | prod  (по умолчанию из config; "
    "алиас image → dev-image)"
)
_DEPLOY_MODE_HELP = _MODE_HELP
_DEV_PROFILE_SERVICES: dict[str, list[str]] = {
    "core+proxy": ["core-runtime", "caddy"],
    "core+proxy+platform": ["core-runtime", "caddy", "platform-web"],
    "core+proxy+platform+cache": ["core-runtime", "caddy", "platform-web", "redis"],
    "core+proxy+platform+cache+db": ["core-runtime", "caddy", "platform-web", "redis", "postgres"],
}
_DEV_PROFILE_ALIASES: dict[str, str] = {
    "base": "core+proxy",
    "platform": "core+proxy+platform",
    "cache": "core+proxy+platform+cache",
    "db": "core+proxy+platform+cache+db",
}
_STACK_ENV_COMPOSE: dict[str, str] = {
    "dev": "deploy/dev/docker-compose.image.yml",
    "prod": "deploy/prod/docker-compose.image.yml",
}


_MONOREPO_SIBLINGS = frozenset({"home-console-cli", "packages", "platform-home-console"})


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            if any((p / s).exists() for s in _MONOREPO_SIBLINGS):
                return p
    return None


def _find_platform_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        candidate = p / "platform-home-console"
        if candidate.exists():
            return candidate
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


def _resolve_platform_root(console: Console) -> Path:
    platform_root = _find_platform_root()
    if platform_root:
        return platform_root
    console.print("[red]Ошибка:[/red] platform-home-console не найден.")
    console.print("Запусти из монорепы HomeConsole или укажи `--platform-path`.")
    raise typer.Exit(code=1)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _run_env(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _run_pull(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    image: str,
) -> None:
    """docker compose pull с понятной подсказкой при ошибке авторизации."""
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        registry = image.split("/")[0] if "/" in image else "ghcr.io"
        raise HcCliError(
            message=f"Не удалось загрузить образ {image}",
            hint=(
                f"Образ недоступен (denied / unauthorized).\n"
                f"  Авторизуйся в реестре:\n"
                f"  docker login {registry} -u <github_username> -p <PAT_read:packages>"
            ),
            exit_code=p.returncode,
        )


def _copy_dir_contents(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


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


def _normalize_edge_health_path(p: str) -> str:
    v = (p or "").strip()
    if not v:
        return "/api/v1/monitor/health"
    if not v.startswith("/"):
        v = "/" + v
    return v


def _resolve_dev_profile(profile: str) -> tuple[str, list[str]]:
    raw = (profile or "").strip().lower()
    resolved = _DEV_PROFILE_ALIASES.get(raw, raw)
    if resolved not in _DEV_PROFILE_SERVICES:
        choices = " | ".join(
            [*sorted(_DEV_PROFILE_SERVICES.keys()), *sorted(_DEV_PROFILE_ALIASES.keys())]
        )
        raise InvalidModeError(
            message=f"--profile {profile!r} недопустим.",
            exit_code=2,
            hint=f"Допустимые профили: {choices}",
        )
    return resolved, _DEV_PROFILE_SERVICES[resolved]


def _resolve_stack_env(env: str | None, compose_rel: str) -> tuple[str, str]:
    raw = (env or "prod").strip().lower()
    if raw not in _STACK_ENV_COMPOSE:
        raise InvalidModeError(
            message=f"stack env {env!r} недопустим.",
            exit_code=2,
            hint="Допустимые: dev | prod",
        )
    default_compose = _STACK_ENV_COMPOSE[raw]
    resolved_compose = compose_rel if compose_rel.strip() else default_compose
    return raw, resolved_compose


def _wait_http_ok(
    *,
    url: str,
    timeout_s: int,
    interval_s: float,
    insecure_tls: bool,
    quiet: bool,
    console: Console,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    if not quiet:
        console.print(f"[cyan]→[/cyan] External check: GET {url} (timeout={timeout_s}s)")
    while time.time() < deadline:
        cmd = ["curl", "-fsS", url]
        if insecure_tls:
            cmd.insert(1, "-k")
        p = subprocess.run(cmd, text=True, capture_output=True, check=False)  # noqa: S603
        if p.returncode == 0:
            if not quiet:
                console.print(
                    f"[green]✓[/green] external ok ([dim]{_fmt_s(time.monotonic() - started)}[/dim])"
                )
            return
        now = time.monotonic()
        if not quiet and now >= next_tick:
            console.print(f"[dim]… external ждём: {_fmt_s(now - started)} / {timeout_s}s[/dim]")
            next_tick = now + 5.0
        time.sleep(interval_s)
    raise HealthyTimeoutError(
        message="external check не прошёл за отведённое время.",
        exit_code=1,
        hint=f"Проверь доступность снаружи: {url}",
    )


def _wait_http_contains(
    *,
    url: str,
    must_contain: str,
    timeout_s: int,
    interval_s: float,
    insecure_tls: bool,
    quiet: bool,
    console: Console,
) -> None:
    deadline = time.time() + timeout_s
    started = time.monotonic()
    next_tick = 0.0
    if not quiet:
        console.print(f"[cyan]→[/cyan] External check: GET {url} contains {must_contain!r} (timeout={timeout_s}s)")
    while time.time() < deadline:
        cmd = ["curl", "-fsS", url]
        if insecure_tls:
            cmd.insert(1, "-k")
        p = subprocess.run(cmd, text=True, capture_output=True, check=False)  # noqa: S603
        body = (p.stdout or "") if p.returncode == 0 else ""
        if p.returncode == 0 and must_contain in body:
            if not quiet:
                console.print(
                    f"[green]✓[/green] external content ok ([dim]{_fmt_s(time.monotonic() - started)}[/dim])"
                )
            return
        now = time.monotonic()
        if not quiet and now >= next_tick:
            console.print(f"[dim]… external ждём content: {_fmt_s(now - started)} / {timeout_s}s[/dim]")
            next_tick = now + 5.0
        time.sleep(interval_s)
    raise HealthyTimeoutError(
        message="external content check не прошёл за отведённое время.",
        exit_code=1,
        hint=f"Проверь выдачу контента снаружи: {url}",
    )


def _normalize_db_mode(db: str | None) -> str | None:
    if db is None:
        return None
    v = db.strip().lower()
    if v in {"sqlite", "sqlite3"}:
        return "sqlite"
    if v in {"pg", "postgres", "postgresql"}:
        return "postgres"
    return v


def _normalize_cache_mode(cache: str | None) -> str | None:
    if cache is None:
        return None
    v = cache.strip().lower()
    if v in {"mem", "memory"}:
        return "memory"
    if v in {"redis"}:
        return "redis"
    return v


def _compose_env_overrides(*, db: str | None, cache: str | None) -> dict[str, str]:
    """
    Environment overrides for docker compose rollout.

    Notes:
    - `db` currently maps to vault backend (`RUNTIME_VAULT_STORAGE_TYPE`).
    - `cache` maps to event bus backend; redis container may still run even in memory mode.
    """
    env: dict[str, str] = {}
    dbn = _normalize_db_mode(db)
    if dbn == "sqlite":
        env["RUNTIME_VAULT_STORAGE_TYPE"] = "sqlite"
    elif dbn == "postgres":
        env["RUNTIME_VAULT_STORAGE_TYPE"] = "postgresql"
    elif dbn is not None:
        raise InvalidModeError(
            message="--db должен быть sqlite или postgres.",
            exit_code=2,
            hint="Пример: `hc deploy --db sqlite` или `hc deploy --db postgres`.",
        )

    cn = _normalize_cache_mode(cache)
    if cn == "memory":
        env["EVENT_BUS_BACKEND"] = "memory"
    elif cn == "redis":
        env["EVENT_BUS_BACKEND"] = "redis"
    elif cn is not None:
        raise InvalidModeError(
            message="--cache должен быть memory или redis.",
            exit_code=2,
            hint="Пример: `hc deploy --cache redis` или `hc deploy --cache memory`.",
        )
    return env


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
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "ps",
                "--status",
                "running",
                "core-runtime",
            ],
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
        console.print(
            f"[cyan]→[/cyan] Wait healthy remote (timeout={timeout_s}s) на [bold]{ssh}[/bold]"
        )
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


def _running_core_image(compose_file: Path) -> str | None:
    """Текущий образ core-runtime до rollout (для отката)."""
    ps = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "ps",
            "-q",
            "core-runtime",
        ],
        cwd=str(compose_file.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    cid = (ps.stdout or "").strip().splitlines()
    if not cid:
        return None
    insp = subprocess.run(  # noqa: S603
        ["docker", "inspect", "-f", "{{.Config.Image}}", cid[0]],
        text=True,
        capture_output=True,
        check=False,
    )
    image = (insp.stdout or "").strip()
    return image or None


def _rollback_core_local(
    console: Console,
    *,
    compose_file: Path,
    previous_image: str,
    db: str | None,
    cache: str | None,
    quiet: bool,
) -> None:
    if not quiet:
        console.print(f"[yellow]Rollback:[/yellow] откат на образ [bold]{previous_image}[/bold]")
    env = {**os.environ, "CORE_RUNTIME_IMAGE": previous_image, **_compose_env_overrides(db=db, cache=cache)}
    _run_env(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        cwd=compose_file.parent,
        env=env,
    )
    if not quiet:
        console.print("[green]✓[/green] rollback compose up выполнен")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE env file, skip comments and blank lines."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            result[key.strip()] = val
    return result


def _save_last_deploy(image: str, tag: str) -> None:
    """Best-effort persist of last successful deploy tag for `hc rollback`."""
    try:
        cfg = Config.load()
        cfg.deploy.last_tag = tag
        cfg.deploy.last_image = image
        cfg.save()
    except Exception:  # noqa: BLE001
        pass


def _do_rollout(
    console: Console,
    *,
    image: str,
    tag: str,
    ssh: str | None,
    path: str | None,
    mode: str,
    db: str | None,
    cache: str | None,
    wait: bool,
    timeout: int,
    interval: float,
    health_url: str,
    pull: bool,
    rollback_on_failure: bool,
    save_on_success: bool = True,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Shared rollout logic for core_rollout and hc rollback."""
    src = _resolve_source(console)
    full = f"{image}:{tag}"
    compose_rel = src.compose_rel(mode)
    do_pull = bool(pull and mode in IMAGE_MODES)
    compose_overrides = {**_compose_env_overrides(db=db, cache=cache), **(extra_env or {})}

    if ssh:
        if not path:
            console.print("[red]Ошибка:[/red] для --ssh нужен --path")
            raise typer.Exit(code=2)
        env_pairs = " ".join(
            f"{k}={shlex.quote(v)}"
            for k, v in {
                "CORE_RUNTIME_IMAGE": full,
                **compose_overrides,
            }.items()
        )
        pull_cmd = (
            f"{env_pairs} docker compose -f {compose_rel} pull core-runtime && "
            if do_pull
            else ""
        )
        remote = (
            f"cd {shlex.quote(path)} && "
            f"{pull_cmd}"
            f"{env_pairs} docker compose -f {compose_rel} up -d"
        )
        console.print(f"Remote rollout on [bold]{ssh}[/bold]")
        _run(_ssh_cmd(ssh, remote))
        console.print("[green]✓[/green] remote rollout ok")
        if wait:
            _wait_core_healthy_remote(
                console,
                ssh=ssh,
                path=path,
                compose_rel=compose_rel,
                timeout_s=timeout,
                interval_s=interval,
                health_url=health_url,
                quiet=False,
            )
        if save_on_success:
            _save_last_deploy(image, tag)
        return

    project = compose_project_from_source(console, src, mode=mode)
    previous_image = _running_core_image(project.compose_file) if wait and rollback_on_failure else None
    console.print(f"Local rollout: [bold]{full}[/bold]")
    env = {**os.environ, "CORE_RUNTIME_IMAGE": full, **compose_overrides}
    if do_pull:
        _run_pull(
            ["docker", "compose", "-f", str(project.compose_file), "pull", "core-runtime"],
            cwd=project.cwd,
            env=env,
            image=full,
        )
    _run_env(
        ["docker", "compose", "-f", str(project.compose_file), "up", "-d"],
        cwd=project.cwd,
        env=env,
    )
    console.print("[green]✓[/green] local rollout ok")
    if wait:
        try:
            _wait_core_healthy_local(
                console,
                compose_file=project.compose_file,
                timeout_s=timeout,
                interval_s=interval,
                health_url=health_url,
                quiet=False,
            )
        except HealthyTimeoutError:
            if rollback_on_failure and previous_image and previous_image != full:
                _rollback_core_local(
                    console,
                    compose_file=project.compose_file,
                    previous_image=previous_image,
                    db=db,
                    cache=cache,
                    quiet=False,
                )
            raise
    if save_on_success:
        _save_last_deploy(image, tag)


def register(app: typer.Typer) -> None:
    deploy_app = typer.Typer(
        help="Деплой: build/tag/push/rollout и sync platform frontend в core",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @deploy_app.callback(invoke_without_command=True)
    def _deploy_root(
        ctx: typer.Context,
        tag: str = typer.Option("latest", "--tag", help="Тег (по умолчанию latest)"),
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
        mode: str | None = typer.Option(None, "--mode", help=_DEPLOY_MODE_HELP),
        db: str | None = typer.Option(
            None,
            "--db",
            help="Vault DB backend: sqlite|postgres (пробрасывает env в compose)",
        ),
        cache: str | None = typer.Option(
            None,
            "--cache",
            help="Cache/event bus backend: memory|redis (пробрасывает env в compose)",
        ),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path с compose (для --ssh, по умолчанию из config)"
        ),
        build: bool = typer.Option(
            True, "--build/--no-build", help="Собрать image локально (по умолчанию да)"
        ),
        push: bool = typer.Option(
            True, "--push/--no-push", help="Запушить image в registry (по умолчанию да)"
        ),
        rollout: bool = typer.Option(
            True, "--rollout/--no-rollout", help="Сделать compose pull+up (по умолчанию да)"
        ),
        wait: bool = typer.Option(
            True, "--wait/--no-wait", help="Дождаться healthy после rollout (по умолчанию да)"
        ),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод (только итог/ошибка)"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план деплоя без реального выполнения"),
        rollback_on_failure: bool = typer.Option(
            True,
            "--rollback-on-failure/--no-rollback-on-failure",
            help="При падении wait healthy откатить compose на предыдущий образ (local)",
        ),
        env_file: Path | None = typer.Option(
            None, "--env-file", help="Файл с дополнительными KEY=VALUE переменными (прокидываются в compose)"
        ),
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
            resolved_mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
            if resolved_mode not in VALID_MODES:
                raise InvalidModeError(
                    message=f"--mode {resolved_mode!r} недопустим.",
                    exit_code=2,
                    hint=f"Допустимые: {' | '.join(sorted(VALID_MODES))}",
                )
            resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
            resolved_path = path if path is not None else (cfg.deploy.path or None)
            resolved_extra_env: dict[str, str] | None = None
            if env_file is not None:
                env_file_path = Path(env_file).expanduser().resolve()
                if not env_file_path.exists():
                    console.print(f"[red]Ошибка:[/red] --env-file не найден: {env_file_path}")
                    raise typer.Exit(code=1)
                resolved_extra_env = _parse_env_file(env_file_path)
                if not quiet and not json_out:
                    console.print(f"[dim]env-file:[/dim] {len(resolved_extra_env)} переменных из {env_file_path.name}")

            full = f"{resolved_image}:{tag}"
            target = f"remote {resolved_ssh}" if resolved_ssh else "local"

            total_t0 = time.monotonic()
            steps: list[dict[str, object]] = []

            if not quiet and not json_out:
                console.print(
                    Panel.fit(f"{full}\nmode={resolved_mode}\ntarget={target}", title="hc deploy")
                )

            if dry_run:
                from rich.table import Table as _Table
                plan = _Table(title="Dry run — план деплоя")
                plan.add_column("Шаг", style="bold")
                plan.add_column("Действие")
                if build:
                    plan.add_row("Build", f"docker build -t {full} <src>")
                if push:
                    plan.add_row("Push", f"docker push {full}")
                if rollout:
                    plan.add_row("Rollout", f"compose pull + up -d → {target}")
                if rollout and wait:
                    plan.add_row("Wait healthy", f"{health_url} (timeout={timeout}s)")
                console.print(plan)
                console.print("[yellow]Dry run:[/yellow] деплой не выполнен")
                raise typer.Exit(code=0)

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

            previous_image: str | None = None
            rollback_project: ComposeProject | None = None
            if rollout and wait and not resolved_ssh and rollback_on_failure:
                try:
                    src_rb = _resolve_source(console)
                    rollback_project = compose_project_from_source(console, src_rb, mode=resolved_mode)
                    previous_image = _running_core_image(rollback_project.compose_file)
                except Exception:  # noqa: BLE001
                    previous_image = None

            if rollout:
                t0 = _step_start(console, "Rollout (compose pull + up -d)", quiet=quiet or json_out)
                _do_rollout(
                    console,
                    image=resolved_image,
                    tag=tag,
                    ssh=resolved_ssh,
                    path=resolved_path,
                    mode=resolved_mode,
                    db=db,
                    cache=cache,
                    wait=False,
                    timeout=timeout,
                    interval=interval,
                    health_url=health_url,
                    pull=True,
                    rollback_on_failure=rollback_on_failure,
                    save_on_success=False,
                    extra_env=resolved_extra_env,
                )
                dt = _step_ok(console, "Rollout", t0, quiet=quiet or json_out)
                steps.append({"name": "rollout", "ok": True, "duration_s": dt})

                if wait:
                    t0 = _step_start(console, "Wait healthy", quiet=quiet or json_out)
                    try:
                        core_wait(
                            image=resolved_image,
                            tag=tag,
                            ssh=resolved_ssh,
                            path=resolved_path,
                            mode=resolved_mode,
                            db=db,
                            cache=cache,
                            timeout=timeout,
                            interval=interval,
                            health_url=health_url,
                            quiet=quiet or json_out,
                        )  # type: ignore[misc]
                    except HealthyTimeoutError:
                        if (
                            rollback_on_failure
                            and previous_image
                            and rollback_project is not None
                            and not resolved_ssh
                            and previous_image != full
                        ):
                            _rollback_core_local(
                                console,
                                compose_file=rollback_project.compose_file,
                                previous_image=previous_image,
                                db=db,
                                cache=cache,
                                quiet=quiet or json_out,
                            )
                        raise
                    dt = _step_ok(console, "Wait healthy", t0, quiet=quiet or json_out)
                    steps.append({"name": "wait", "ok": True, "duration_s": dt})

            total_dt = time.monotonic() - total_t0
            if rollout:
                _save_last_deploy(resolved_image, tag)

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

    @deploy_app.command("platform")
    def deploy_platform(
        platform_path: str | None = typer.Option(
            None, "--platform-path", help="Путь к platform-home-console"
        ),
        core_path: str | None = typer.Option(
            None, "--core-path", help="Путь к core-runtime-service"
        ),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для remote sync (копия dist на сервер)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path к core-runtime-service (для --ssh)"
        ),
        mode: str = typer.Option("dev", "--mode", help="dev|image (по умолчанию dev)"),
        image: str = typer.Option(
            "ghcr.io/home-console/platform-home-console",
            "--image",
            help="Имя platform image без тега",
        ),
        tag: str = typer.Option("latest", "--tag", help="Тег image"),
        build: bool = typer.Option(
            True, "--build/--no-build", help="Собрать platform web перед копированием"
        ),
        start: bool = typer.Option(
            True, "--start/--no-start", help="Запустить dev stage core после копирования"
        ),
        restart_remote: bool = typer.Option(
            True,
            "--restart-remote/--no-restart-remote",
            help="После remote sync перезапустить caddy (docker compose restart)",
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план деплоя без реального выполнения"),
    ) -> None:
        """Локально собрать platform web или запустить platform image из GHCR."""
        console = Console()

        resolved_mode = mode.strip().lower()
        if resolved_mode not in {"dev", "image"}:
            console.print("[red]Ошибка:[/red] --mode должен быть dev или image")
            raise typer.Exit(code=2)

        platform_root = (
            Path(platform_path).expanduser().resolve()
            if platform_path
            else _resolve_platform_root(console)
        )

        if dry_run:
            from rich.table import Table as _Table
            plan = _Table(title="Dry run — план деплоя platform")
            plan.add_column("Шаг", style="bold")
            plan.add_column("Действие")
            plan.add_row("Режим", resolved_mode)
            plan.add_row("Platform root", str(platform_root))
            if resolved_mode == "image":
                plan.add_row("Image", f"{image}:{tag}")
                if start:
                    plan.add_row("Start", "docker compose pull + up -d")
            else:
                if build:
                    plan.add_row("Build", "pnpm --filter=web build")
                plan.add_row("Sync dist", str(platform_root / "apps" / "web" / "dist"))
                if ssh:
                    plan.add_row("Remote sync", f"rsync dist → {ssh}:{path}/deploy/dev/frontend/")
                    if restart_remote:
                        plan.add_row("Restart caddy", f"docker compose restart caddy на {ssh}")
                elif start:
                    plan.add_row("Start core", "bash deploy/dev/start.sh")
            console.print(plan)
            console.print("[yellow]Dry run:[/yellow] деплой не выполнен")
            raise typer.Exit(code=0)

        if resolved_mode == "image":
            require_docker(console)
            compose_file = platform_root / "docker-compose.image.yml"
            if not compose_file.exists():
                console.print(
                    f"[red]Ошибка:[/red] не найден compose для image mode: {compose_file}"
                )
                raise typer.Exit(code=1)

            full_image = f"{image}:{tag}"
            env = {**os.environ, "PLATFORM_IMAGE": full_image}
            console.print(f"[cyan]→[/cyan] Deploy platform image [bold]{full_image}[/bold]")
            if start:
                _run_pull(
                    ["docker", "compose", "-f", str(compose_file), "pull", "platform-web"],
                    cwd=platform_root,
                    env=env,
                    image=full_image,
                )
                _run_env(
                    ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                    cwd=platform_root,
                    env=env,
                )
                console.print("[green]✓[/green] platform image deployed")
            else:
                console.print("[green]✓[/green] platform image ready (start skipped)")
            return

        core_root = (
            Path(core_path).expanduser().resolve() if core_path else _resolve_source(console).path
        )
        dist_dir = platform_root / "apps" / "web" / "dist"
        frontend_dir = core_root / "deploy" / "dev" / "frontend"

        if build:
            if shutil.which("pnpm") is None:
                console.print("[red]Ошибка:[/red] pnpm не найден.")
                console.print("Установи pnpm или добавь его в PATH и повтори.")
                raise typer.Exit(code=1)
            console.print(f"[cyan]→[/cyan] Build platform web in [bold]{platform_root}[/bold]")
            _run(["pnpm", "--filter=web", "build"], cwd=platform_root)
            console.print("[green]✓[/green] platform web build ok")

        if not dist_dir.exists():
            console.print(f"[red]Ошибка:[/red] dist не найден: {dist_dir}")
            console.print("Сначала собери platform web: `pnpm --filter=web build`")
            raise typer.Exit(code=1)

        frontend_dir.parent.mkdir(parents=True, exist_ok=True)
        _copy_dir_contents(dist_dir, frontend_dir)
        console.print(f"[green]✓[/green] frontend synced to [bold]{frontend_dir}[/bold]")

        if ssh:
            if not path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path (к core-runtime-service на сервере)")
                raise typer.Exit(code=2)
            if shutil.which("rsync") is None:
                console.print("[red]Ошибка:[/red] rsync не найден.")
                console.print("Установи rsync и повтори (на macOS: `brew install rsync`).")
                raise typer.Exit(code=1)
            remote_frontend = f"{path.rstrip('/')}/deploy/dev/frontend/"
            console.print(f"[cyan]→[/cyan] Remote sync dist → [bold]{ssh}[/bold]:{remote_frontend}")
            p = subprocess.run(  # noqa: S603
                ["rsync", "-az", "--delete", f"{str(dist_dir).rstrip('/')}/", f"{ssh}:{remote_frontend}"],
                text=True,
                check=False,
            )
            if p.returncode != 0:
                console.print(f"[red]Ошибка:[/red] rsync завершился с кодом {p.returncode}")
                raise typer.Exit(code=p.returncode)
            console.print("[green]✓[/green] remote frontend synced")
            if restart_remote:
                remote = (
                    f"cd {shlex.quote(path)} && "
                    f"docker compose -f deploy/dev/docker-compose.yml restart caddy || "
                    f"docker compose -f deploy/dev/docker-compose.yml up -d caddy"
                )
                _run(_ssh_cmd(ssh, remote))
                console.print("[green]✓[/green] remote caddy restarted")
            return

        if not start:
            return

        require_docker(console)
        start_script = core_root / "deploy" / "dev" / "start.sh"
        if not start_script.exists():
            console.print(f"[red]Ошибка:[/red] не найден start.sh: {start_script}")
            raise typer.Exit(code=1)

        console.print(f"[cyan]→[/cyan] Start core dev stage in [bold]{core_root}[/bold]")
        _run(["bash", str(start_script)], cwd=core_root)
        console.print("[green]✓[/green] platform deployed to core")

    @deploy_app.command("stack")
    def deploy_stack(
        env: str = typer.Argument(
            "prod",
            help="Окружение стека: dev | prod (пример: `hc deploy stack dev`)",
        ),
        core_image: str | None = typer.Option(
            None, "--core-image", help="Core image без тега (по умолчанию из deploy.core_image)"
        ),
        core_tag: str = typer.Option("latest", "--core-tag", help="Тег core image"),
        platform_image: str = typer.Option(
            "ghcr.io/home-console/platform-home-console",
            "--platform-image",
            help="Platform image без тега",
        ),
        platform_tag: str = typer.Option("latest", "--platform-tag", help="Тег platform image"),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённого rollout (по умолчанию из deploy.ssh)"
        ),
        path: str | None = typer.Option(
            None,
            "--path",
            help="remote path к core-runtime-service (по умолчанию из deploy.path)",
        ),
        compose_rel: str = typer.Option(
            "",
            "--compose",
            help="Путь к compose (если не указан: выбирается по env: dev/prod)",
        ),
        core_runtime_url: str = typer.Option(
            "http://core-runtime:8000",
            "--core-runtime-url",
            help="CORE_RUNTIME_URL для platform-web (внутри docker сети)",
        ),
        secure_cookies: bool = typer.Option(
            True, "--secure-cookies/--insecure-cookies", help="Флаги cookie для platform-web"
        ),
        pull: bool = typer.Option(
            True,
            "--pull/--no-pull",
            help="Делать `docker compose pull` перед up (для локальных image ставь --no-pull)",
        ),
        domain: str = typer.Option(
            "localhost",
            "--domain",
            help="Домен для edge (DOMAIN в compose/Caddy). Для прод: реальный домен.",
        ),
        http_port: int = typer.Option(
            80,
            "--http-port",
            help="Порт, который edge публикует наружу (HTTP_PORT в compose)",
        ),
        https_port: int = typer.Option(
            443,
            "--https-port",
            help="Порт, который edge публикует наружу (HTTPS_PORT в compose)",
        ),
        edge_health_path: str = typer.Option(
            "/api/v1/monitor/health",
            "--edge-health-path",
            help="Путь health на edge (проверяется через edge внутри контейнера)",
        ),
        external_base_url: str = typer.Option(
            "",
            "--external-url",
            help="Опционально: проверка доступности С НАРУЖИ (напр. https://example.com или http://host:8088).",
        ),
        external_insecure_tls: bool = typer.Option(
            False,
            "--external-insecure-tls",
            help="Для внешней проверки: отключить проверку TLS сертификата (curl -k).",
        ),
        external_path: str = typer.Option(
            "/api/v1/monitor/health",
            "--external-path",
            help="Путь для внешней проверки (добавляется к --external-url, если он без path).",
        ),
        external_ui: bool = typer.Option(
            False,
            "--external-ui/--no-external-ui",
            help="Дополнительно проверить, что фронт реально отдаётся через edge (HTML).",
        ),
        external_ui_path: str = typer.Option(
            "/",
            "--external-ui-path",
            help="Путь для внешней проверки UI (по умолчанию /).",
        ),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy core и UI"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        quiet: bool = typer.Option(False, "--quiet", help="Минимальный вывод"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план деплоя без реального выполнения"),
    ) -> None:
        """
        Деплой всего стека (core-runtime + platform-web) из image’ов.

        Локально: читает compose из исходников core-runtime-service.
        Удалённо: `--ssh user@host --path /srv/core-runtime-service` применит compose на сервере.
        """
        console = Console()
        try:
            require_docker(console)
            cfg = Config.load()
            src = _resolve_source(console)
            resolved_env, resolved_compose_rel = _resolve_stack_env(env, compose_rel)

            resolved_core_image = (core_image or cfg.deploy.core_image).strip()
            full_core = f"{resolved_core_image}:{core_tag}"
            full_platform = f"{platform_image}:{platform_tag}"

            resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
            resolved_path = path if path is not None else (cfg.deploy.path or None)

            env_pairs = {
                "CORE_RUNTIME_IMAGE": full_core,
                "PLATFORM_IMAGE": full_platform,
                "CORE_RUNTIME_URL": core_runtime_url,
                "SECURE_COOKIES": "true" if secure_cookies else "false",
                "DOMAIN": (domain or "localhost").strip(),
                "HTTP_PORT": str(int(http_port)),
                "HTTPS_PORT": str(int(https_port)),
            }
            # Prefer not relying on remote `.env`: if caller provided master key locally,
            # pass it through to remote compose via `ssh` exports.
            master_key = (os.getenv("RUNTIME_MASTER_KEY") or "").strip()
            if master_key:
                env_pairs["RUNTIME_MASTER_KEY"] = master_key

            edge_health_path = _normalize_edge_health_path(edge_health_path)

            total_t0 = time.monotonic()
            steps: list[dict[str, object]] = []

            if not quiet and not json_out:
                console.print(
                    Panel.fit(
                        f"core={full_core}\nplatform={full_platform}\ncompose={resolved_compose_rel}\n"
                        f"env={resolved_env}\n"
                        f"target={'remote ' + resolved_ssh if resolved_ssh else 'local'}",
                        title="hc deploy stack",
                    )
                )

            if dry_run:
                from rich.table import Table as _Table
                target_str = f"remote {resolved_ssh}" if resolved_ssh else "local"
                plan = _Table(title="Dry run — план деплоя stack")
                plan.add_column("Шаг", style="bold")
                plan.add_column("Действие")
                plan.add_row("Target", target_str)
                plan.add_row("Env", resolved_env)
                plan.add_row("Core image", full_core)
                plan.add_row("Platform image", full_platform)
                plan.add_row("Compose", resolved_compose_rel)
                if pull:
                    plan.add_row("Pull", "docker compose pull core-runtime platform-web edge")
                plan.add_row("Rollout", "docker compose up -d")
                if wait:
                    plan.add_row("Wait healthy", f"edge → core ({edge_health_path}, timeout={timeout}s)")
                if (external_base_url or "").strip():
                    plan.add_row("External check", (external_base_url or "").strip())
                console.print(plan)
                console.print("[yellow]Dry run:[/yellow] деплой не выполнен")
                raise typer.Exit(code=0)

            if resolved_ssh:
                if not resolved_path:
                    raise HcCliError(
                        message="для --ssh нужен --path",
                        exit_code=2,
                        hint=f"Пример: `hc deploy stack {resolved_env} --ssh user@host --path /srv/core-runtime-service`",
                    )
                t0 = _step_start(
                    console, "Rollout stack remote (compose pull + up -d)", quiet=quiet or json_out
                )
                exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env_pairs.items())
                remote = f"cd {shlex.quote(resolved_path)} && "
                if pull:
                    remote += (
                        f"{exports} docker compose -f {shlex.quote(resolved_compose_rel)} pull core-runtime platform-web edge && "
                    )
                remote += f"{exports} docker compose -f {shlex.quote(resolved_compose_rel)} up -d"
                _run(_ssh_cmd(resolved_ssh, remote))
                dt = _step_ok(console, "Rollout", t0, quiet=quiet or json_out)
                steps.append({"name": "rollout", "ok": True, "duration_s": dt})

                if wait:
                    t0 = _step_start(
                        console, "Wait healthy (edge routes to core + UI) remote", quiet=quiet or json_out
                    )
                    deadline = time.time() + timeout
                    started = time.monotonic()
                    next_tick = 0.0
                    while time.time() < deadline:
                        chk = (
                            f"cd {shlex.quote(resolved_path)} && "
                            f"docker compose -f {shlex.quote(resolved_compose_rel)} exec -T edge sh -lc "
                            f"{shlex.quote(f'curl -fsS http://127.0.0.1{edge_health_path} >/dev/null && echo edge_core_ok || echo edge_core_no')} && "
                            f"docker compose -f {shlex.quote(resolved_compose_rel)} exec -T edge sh -lc "
                            f"{shlex.quote('wget -qO- http://127.0.0.1/ >/dev/null && echo edge_ui_ok || echo edge_ui_no')}"
                        )
                        p = subprocess.run(_ssh_cmd(resolved_ssh, chk), text=True, capture_output=True, check=False)  # noqa: S603
                        out = (p.stdout or "").strip()
                        if p.returncode == 0 and "edge_core_ok" in out and "edge_ui_ok" in out:
                            break
                        now = time.monotonic()
                        if not quiet and not json_out and now >= next_tick:
                            console.print(f"[dim]… жду healthy: {_fmt_s(now - started)} / {timeout}s[/dim]")
                            next_tick = now + 5.0
                        time.sleep(interval)
                    else:
                        raise HealthyTimeoutError(
                            message="stack не вышел в healthy за отведённое время (remote).",
                            exit_code=1,
                            hint="Смотри логи: `docker compose -f ... logs -f edge core-runtime platform-web` (на сервере).",
                        )
                    dt = _step_ok(console, "Wait healthy", t0, quiet=quiet or json_out)
                    steps.append({"name": "wait", "ok": True, "duration_s": dt})
            else:
                # local: apply compose from repo source (core-runtime-service)
                compose_file = src.path / resolved_compose_rel
                if not compose_file.exists():
                    raise HcCliError(
                        message=f"Не найден compose файл: {compose_file}",
                        exit_code=1,
                        hint="Проверь `--compose` или используй `hc deploy stack dev|prod`.",
                    )
                env = {**os.environ, **env_pairs}
                t0 = _step_start(console, "Rollout stack local (compose pull + up -d)", quiet=quiet or json_out)
                if pull:
                    _run_env(
                        [
                            "docker",
                            "compose",
                            "-f",
                            str(compose_file),
                            "pull",
                            "core-runtime",
                            "platform-web",
                            "edge",
                        ],
                        cwd=compose_file.parent,
                        env=env,
                    )
                _run_env(
                    ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                    cwd=compose_file.parent,
                    env=env,
                )
                dt = _step_ok(console, "Rollout", t0, quiet=quiet or json_out)
                steps.append({"name": "rollout", "ok": True, "duration_s": dt})

                if wait:
                    t0 = _step_start(
                        console, "Wait healthy (edge routes to core + UI)", quiet=quiet or json_out
                    )
                    deadline = time.time() + timeout
                    started = time.monotonic()
                    next_tick = 0.0
                    while time.time() < deadline:
                        core_chk = subprocess.run(  # noqa: S603
                            [
                                "docker",
                                "compose",
                                "-f",
                                str(compose_file),
                                "exec",
                                "-T",
                                "edge",
                                "sh",
                                "-lc",
                                f"curl -fsS http://127.0.0.1{edge_health_path} >/dev/null && echo ok || echo no",
                            ],
                            cwd=str(compose_file.parent),
                            text=True,
                            capture_output=True,
                        )
                        ui_chk = subprocess.run(  # noqa: S603
                            [
                                "docker",
                                "compose",
                                "-f",
                                str(compose_file),
                                "exec",
                                "-T",
                                "edge",
                                "sh",
                                "-lc",
                                "wget -qO- http://127.0.0.1/ >/dev/null && echo ok || echo no",
                            ],
                            cwd=str(compose_file.parent),
                            text=True,
                            capture_output=True,
                        )
                        if (
                            core_chk.returncode == 0
                            and (core_chk.stdout or "").strip() == "ok"
                            and ui_chk.returncode == 0
                            and (ui_chk.stdout or "").strip() == "ok"
                        ):
                            break
                        now = time.monotonic()
                        if not quiet and not json_out and now >= next_tick:
                            console.print(f"[dim]… жду healthy: {_fmt_s(now - started)} / {timeout}s[/dim]")
                            next_tick = now + 5.0
                        time.sleep(interval)
                    else:
                        raise HealthyTimeoutError(
                            message="stack не вышел в healthy за отведённое время.",
                            exit_code=1,
                            hint="Смотри логи: `docker compose -f ... logs -f edge core-runtime platform-web`.",
                        )
                    dt = _step_ok(console, "Wait healthy", t0, quiet=quiet or json_out)
                    steps.append({"name": "wait", "ok": True, "duration_s": dt})

            # Optional external check from the machine running `hc`.
            external = (external_base_url or "").strip()
            if external and not json_out:
                # if external doesn't include scheme, assume http
                if "://" not in external:
                    external = "http://" + external
                # if external has no path, append external_path
                if "/" not in external.split("://", 1)[1]:
                    external = external.rstrip("/") + _normalize_edge_health_path(external_path)
                t0 = _step_start(console, "External check (caller → edge)", quiet=quiet)
                _wait_http_ok(
                    url=external,
                    timeout_s=timeout,
                    interval_s=interval,
                    insecure_tls=external_insecure_tls,
                    quiet=quiet,
                    console=console,
                )
                _step_ok(console, "External check", t0, quiet=quiet)

                if external_ui:
                    base = external_base_url.strip()
                    if "://" not in base:
                        base = "http://" + base
                    ui_url = base.rstrip("/") + _normalize_edge_health_path(external_ui_path)
                    t0 = _step_start(console, "External UI check (caller → edge)", quiet=quiet)
                    _wait_http_contains(
                        url=ui_url,
                        must_contain="<!doctype html",
                        timeout_s=timeout,
                        interval_s=interval,
                        insecure_tls=external_insecure_tls,
                        quiet=quiet,
                        console=console,
                    )
                    _step_ok(console, "External UI check", t0, quiet=quiet)

            total_dt = time.monotonic() - total_t0
            if json_out:
                payload = {
                    "ok": True,
                    "command": "deploy.stack",
                    "core": full_core,
                    "platform": full_platform,
                    "env": resolved_env,
                    "compose": resolved_compose_rel,
                    "target": f"remote {resolved_ssh}" if resolved_ssh else "local",
                    "wait": bool(wait),
                    "timeout_s": int(timeout),
                    "interval_s": float(interval),
                    "secure_cookies": bool(secure_cookies),
                    "core_runtime_url": core_runtime_url,
                    "edge_health_path": edge_health_path,
                    "domain": env_pairs["DOMAIN"],
                    "http_port": int(http_port),
                    "https_port": int(https_port),
                    "steps": steps,
                    "duration_s": total_dt,
                }
                print(json.dumps(payload, ensure_ascii=False))
                return

            if quiet:
                console.print("[green]✓[/green] Stack deploy ok")
                return
            console.print(f"[green]✓[/green] Stack deploy done ([dim]{_fmt_s(total_dt)}[/dim])")
        except HcCliError as e:
            if json_out:
                print(json.dumps(json_error_payload("deploy.stack", e), ensure_ascii=False))
                raise typer.Exit(code=int(e.exit_code or 1))
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    dev_app = typer.Typer(
        help="Dev stack shortcuts (up/down профили сервисов)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @dev_app.command("up")
    def dev_up(
        profile: str = typer.Option(
            "core+proxy",
            "--profile",
            help=(
                "Профиль сервисов: core+proxy | core+proxy+platform | "
                "core+proxy+platform+cache | core+proxy+platform+cache+db "
                "(алиасы: base|platform|cache|db)"
            ),
        ),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённого запуска (по умолчанию из deploy.ssh)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path к core-runtime-service (для --ssh)"
        ),
        pull: bool = typer.Option(
            False,
            "--pull/--no-pull",
            help="Перед up сделать docker compose pull для сервисов профиля",
        ),
    ) -> None:
        """Поднять dev compose только с нужным набором сервисов. (Используй `hc env up` для hot-reload.)"""
        console = Console()
        try:
            require_docker(console)
            src = _resolve_source(console)
            cfg = Config.load()
            resolved_profile, services = _resolve_dev_profile(profile)
            resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
            resolved_path = path if path is not None else (cfg.deploy.path or None)

            compose_rel = src.compose_rel("dev")
            service_list = " ".join(services)
            console.print(f"[cyan]→[/cyan] dev up profile=[bold]{resolved_profile}[/bold] services={service_list}")

            if resolved_ssh:
                if not resolved_path:
                    console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                    raise typer.Exit(code=2)
                pull_cmd = (
                    f"docker compose -f {shlex.quote(compose_rel)} pull {' '.join(shlex.quote(s) for s in services)} && "
                    if pull
                    else ""
                )
                remote = (
                    f"cd {shlex.quote(resolved_path)} && "
                    f"{pull_cmd}"
                    f"docker compose -f {shlex.quote(compose_rel)} up -d {' '.join(shlex.quote(s) for s in services)}"
                )
                _run(_ssh_cmd(resolved_ssh, remote))
                console.print("[green]✓[/green] remote dev profile started")
                return

            project = compose_project_from_source(console, src, mode="dev")
            if pull:
                _run(
                    ["docker", "compose", "-f", str(project.compose_file), "pull", *services],
                    cwd=project.cwd,
                )
            _run(
                ["docker", "compose", "-f", str(project.compose_file), "up", "-d", *services],
                cwd=project.cwd,
            )
            console.print("[green]✓[/green] local dev profile started")
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    cfg_app = typer.Typer(
        help="Дефолты для deploy (ssh/path/image/mode)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @cfg_app.command("show")
    def cfg_show() -> None:
        console = Console()
        from hc.constants import CONFIG_PATH

        cfg = Config.load()
        body = (
            f"deploy.core_image = {cfg.deploy.core_image}\n"
            f"deploy.core_mode  = {cfg.deploy.core_mode}\n"
            f"deploy.ssh        = {cfg.deploy.ssh or '(empty)'}\n"
            f"deploy.path       = {cfg.deploy.path or '(empty)'}\n"
            f"\nфайл: {CONFIG_PATH}\n"
        )
        console.print(Panel.fit(body, title="hc deploy config"))

    @cfg_app.command("edit")
    def cfg_edit() -> None:
        """Открыть config.toml в $EDITOR или $VISUAL (полный конфиг hc, не только deploy)."""
        console = Console()
        from hc.constants import CONFIG_PATH

        if not CONFIG_PATH.exists():
            Config.load().save()
        editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
        if not editor:
            for cand in ("nvim", "vim", "nano", "micro"):
                if shutil.which(cand):
                    editor = cand
                    break
        if not editor:
            console.print("[red]Ошибка:[/red] не задан редактор. Укажи переменную EDITOR или VISUAL.")
            console.print(f"[dim]Файл:[/dim] {CONFIG_PATH}")
            raise typer.Exit(code=2)
        cmd = [*shlex.split(editor), str(CONFIG_PATH)]
        p = subprocess.run(cmd, check=False)  # noqa: S603
        if p.returncode != 0:
            console.print(f"[yellow]Редактор завершился с кодом {p.returncode}[/yellow]")
            raise typer.Exit(code=p.returncode)
        console.print("[green]✓[/green] редактор закрыт")

    @cfg_app.command("set")
    def cfg_set(
        core_image: str | None = typer.Option(
            None, "--core-image", help="Напр. ghcr.io/org/core-runtime"
        ),
        core_mode: str | None = typer.Option(None, "--core-mode", help=_MODE_HELP),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose"),
    ) -> None:
        console = Console()
        cfg = Config.load()
        if core_image is not None:
            cfg.deploy.core_image = core_image.strip()
        if core_mode is not None:
            m = normalize_deploy_core_mode(core_mode)
            if m not in VALID_MODES:
                console.print(f"[red]Ошибка:[/red] --core-mode {m!r} недопустим. Допустимые: {' | '.join(sorted(VALID_MODES))}")
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
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
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
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
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
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path с compose (по умолчанию из config)"
        ),
        mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
        db: str | None = typer.Option(None, "--db", help="Vault DB backend: sqlite|postgres"),
        cache: str | None = typer.Option(None, "--cache", help="Cache backend: memory|redis"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        pull: bool = typer.Option(
            True,
            "--pull/--no-pull",
            help="docker compose pull core-runtime (только для dev-image и prod; для dev/dev-reload не используется)",
        ),
        rollback_on_failure: bool = typer.Option(
            True,
            "--rollback-on-failure/--no-rollback-on-failure",
            help="При падении wait healthy откатить compose на предыдущий образ (local)",
        ),
        env_file: Path | None = typer.Option(
            None, "--env-file", help="Файл с дополнительными KEY=VALUE переменными (прокидываются в compose)"
        ),
    ) -> None:
        """
        Rollout (compose pull + up -d) для core-runtime.

        Режимы: dev | dev-reload | dev-image | prod
        Для remote (--ssh): рекомендуется dev-image или prod.
        prod → deploy/prod/docker-compose.image.yml (образ из registry).
        """
        console = Console()
        require_docker(console)
        cfg = Config.load()
        image = (image or cfg.deploy.core_image).strip()
        mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
        if mode not in VALID_MODES:
            console.print(f"[red]Ошибка:[/red] --mode {mode!r} недопустим. Допустимые: {' | '.join(sorted(VALID_MODES))}")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)
        resolved_extra_env: dict[str, str] | None = None
        if env_file is not None:
            env_file_path = Path(env_file).expanduser().resolve()
            if not env_file_path.exists():
                console.print(f"[red]Ошибка:[/red] --env-file не найден: {env_file_path}")
                raise typer.Exit(code=1)
            resolved_extra_env = _parse_env_file(env_file_path)

        _do_rollout(
            console,
            image=image,
            tag=tag,
            ssh=ssh,
            path=path,
            mode=mode,
            db=db,
            cache=cache,
            wait=wait,
            timeout=timeout,
            interval=interval,
            health_url=health_url,
            pull=pull,
            rollback_on_failure=rollback_on_failure,
            save_on_success=True,
            extra_env=resolved_extra_env,
        )

    @core_app.command("wait")
    def core_wait(
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
        tag: str = typer.Option("latest", "--tag", help="Тег"),
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path с compose (по умолчанию из config)"
        ),
        mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
        db: str | None = typer.Option(None, "--db", help="Vault DB backend: sqlite|postgres"),
        cache: str | None = typer.Option(None, "--cache", help="Cache backend: memory|redis"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
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
        mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
        if mode not in VALID_MODES:
            console.print(f"[red]Ошибка:[/red] --mode {mode!r} недопустим. Допустимые: {' | '.join(sorted(VALID_MODES))}")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)

        compose_rel = src.compose_rel(mode)
        if ssh:
            if not path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path")
                raise typer.Exit(code=2)
            _wait_core_healthy_remote(
                console,
                ssh=ssh,
                path=path,
                compose_rel=compose_rel,
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
        ssh: str | None = typer.Option(
            None, "--ssh", help="user@host для удалённых логов (по умолчанию из config)"
        ),
        path: str | None = typer.Option(
            None, "--path", help="remote path с compose (по умолчанию из config)"
        ),
        mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
    ) -> None:
        """Логи core-runtime (docker compose logs)."""
        console = Console()
        require_docker(console)
        src = _resolve_source(console)
        cfg = Config.load()
        mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
        if mode not in VALID_MODES:
            console.print(f"[red]Ошибка:[/red] --mode {mode!r} недопустим. Допустимые: {' | '.join(sorted(VALID_MODES))}")
            raise typer.Exit(code=2)
        ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        path = path if path is not None else (cfg.deploy.path or None)
        compose_rel = src.compose_rel(mode)

        args = [
            "docker",
            "compose",
            "-f",
            compose_rel,
            "logs",
            "--tail",
            str(tail),
        ]
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
        local_args = [
            "docker",
            "compose",
            "-f",
            str(project.compose_file),
            "logs",
            "--tail",
            str(tail),
        ]
        if follow:
            local_args.append("-f")
        local_args.append("core-runtime")
        _run(local_args, cwd=project.cwd)

    @core_app.command("release")
    def core_release(
        tag: str = typer.Argument(..., help="Новый тег (напр. v0.1.0)"),
        image: str | None = typer.Option(
            None, "--image", help="Имя image без тега (по умолчанию из config)"
        ),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из config)"),
        path: str | None = typer.Option(
            None, "--path", help="remote path (по умолчанию из config)"
        ),
        mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
        pull: bool = typer.Option(True, "--pull/--no-pull", help="передать в rollout ( dev-image/prod )"),
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
            pull=pull,
        )

    @deploy_app.command("rollback")
    def deploy_rollback(
        tag: str | None = typer.Argument(None, help="Тег для отката (по умолчанию: последний задеплоенный из config)"),
        image: str | None = typer.Option(None, "--image", help="Имя image без тега (по умолчанию из config)"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host для удалённого rollout (по умолчанию из config)"),
        path: str | None = typer.Option(None, "--path", help="remote path с compose (по умолчанию из config)"),
        mode: str | None = typer.Option(None, "--mode", help=_MODE_HELP),
        db: str | None = typer.Option(None, "--db", help="Vault DB backend: sqlite|postgres"),
        cache: str | None = typer.Option(None, "--cache", help="Cache backend: memory|redis"),
        wait: bool = typer.Option(True, "--wait/--no-wait", help="Дождаться healthy после rollout"),
        timeout: int = typer.Option(180, "--timeout", help="Таймаут ожидания healthy (сек)"),
        interval: float = typer.Option(1.0, "--interval", help="Интервал проверки healthy (сек)"),
        health_url: str = typer.Option(
            "http://localhost:8000/api/v1/monitor/health",
            "--health-url",
            help="URL health внутри контейнера core-runtime",
        ),
    ) -> None:
        """Откатить core-runtime на тег. Без тега — берёт последний задеплоенный из config."""
        console = Console()
        require_docker(console)
        cfg = Config.load()
        resolved_image = (image or cfg.deploy.core_image).strip()
        resolved_mode = normalize_deploy_core_mode(mode or cfg.deploy.core_mode)
        if resolved_mode not in VALID_MODES:
            console.print(f"[red]Ошибка:[/red] --mode {resolved_mode!r} недопустим. Допустимые: {' | '.join(sorted(VALID_MODES))}")
            raise typer.Exit(code=2)
        resolved_ssh = ssh if ssh is not None else (cfg.deploy.ssh or None)
        resolved_path = path if path is not None else (cfg.deploy.path or None)
        resolved_tag = tag or cfg.deploy.last_tag
        if not resolved_tag:
            console.print("[red]Ошибка:[/red] тег не указан и нет сохранённого last_tag.")
            console.print("Укажи явно: [bold]hc deploy rollback v0.1.0[/bold]")
            raise typer.Exit(code=1)
        console.print(f"[yellow]→[/yellow] Rollback: [bold]{resolved_image}:{resolved_tag}[/bold]")
        _do_rollout(
            console,
            image=resolved_image,
            tag=resolved_tag,
            ssh=resolved_ssh,
            path=resolved_path,
            mode=resolved_mode,
            db=db,
            cache=cache,
            wait=wait,
            timeout=timeout,
            interval=interval,
            health_url=health_url,
            pull=True,
            rollback_on_failure=False,
            save_on_success=True,
        )
        console.print(f"[green]✓[/green] Rollback → {resolved_image}:{resolved_tag} done")

    deploy_app.add_typer(cfg_app, name="config")
    deploy_app.add_typer(core_app, name="core")
    deploy_app.add_typer(dev_app, name="dev")
    app.add_typer(deploy_app, name="deploy")
