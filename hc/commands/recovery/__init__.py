from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from hc.constants import CONFIG_PATH, SETUP_LOG_PATH
from hc.core_ops import compose_project_from_source, require_docker
from hc.core_source import (
    CoreSource,
    get_core_source_from_repo,
    get_core_source_local,
    resolve_workspace_root,
)

from hc.commands._compose_helpers import compose_file_args, read_env_kv


@dataclass(slots=True)
class RecoveryContext:
    def find_repo_root(self) -> Path | None:
        return resolve_workspace_root()

    def resolve_source(self, console: Console) -> CoreSource:
        repo_root = self.find_repo_root()
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

    def run_compose(self, console: Console, src: CoreSource, args: list[str]) -> None:
        project = compose_project_from_source(console, src)
        cmd = ["docker", "compose", *compose_file_args(project.cwd, None), *args]
        try:
            p = subprocess.run(cmd, cwd=str(project.cwd), check=False)  # noqa: S603
        except FileNotFoundError:
            console.print("[red]Ошибка: docker не найден.[/red]")
            raise typer.Exit(code=1)
        if p.returncode != 0:
            raise typer.Exit(code=p.returncode)

    def compose_capture(self, console: Console, src: CoreSource, args: list[str]) -> str:
        project = compose_project_from_source(console, src)
        cmd = ["docker", "compose", *compose_file_args(project.cwd, None), *args]
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

    def timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def recovery_backup(self, console: Console, src: CoreSource, out_dir: Path) -> Path:
        """
        Снапшот recovery-состояния:
        - копии sqlite DB из /data (runtime/vault/vault_secret если есть)
        - копия core-runtime-service/.env
        - docker compose config (base + override)
        - hc config (config.toml)
        """
        require_docker(console)
        out_dir = out_dir.expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        snap = (out_dir / f"hc-recovery-backup-{self.timestamp()}").resolve()
        snap.mkdir(parents=True, exist_ok=True)

        # sqlite db files (best-effort, only if container running and file exists)
        try:
            running = self.compose_capture(console, src, ["ps", "--status", "running", "core-runtime"]).strip()
            core_running = bool(running) and len([ln for ln in running.splitlines() if ln.strip()]) >= 2
        except typer.Exit:
            core_running = False

        if core_running:
            for container_path, dst in [
                ("/data/runtime.db", snap / "sqlite-runtime.db"),
                ("/data/vault.db", snap / "sqlite-vault.db"),
                ("/data/vault_secret.db", snap / "sqlite-vault_secret.db"),
            ]:
                try:
                    exists = self.compose_capture(
                        console,
                        src,
                        ["exec", "-T", "core-runtime", "sh", "-lc", f"test -f {container_path} && echo yes || echo no"],
                    ).strip()
                    if exists != "yes":
                        continue
                    self.run_compose(console, src, ["cp", f"core-runtime:{container_path}", str(dst.resolve())])
                except typer.Exit:
                    continue

        # core env
        env_file = src.path / ".env"
        if env_file.exists():
            (snap / "core.env").write_text(env_file.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        # hc config
        if CONFIG_PATH.exists():
            (snap / "hc.config.toml").write_text(
                CONFIG_PATH.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )

        # compose config (merged)
        try:
            cfg = self.compose_capture(console, src, ["config"])
            (snap / "compose.config.yml").write_text(cfg + "\n", encoding="utf-8")
        except typer.Exit:
            pass

        return snap


def register(app: typer.Typer) -> None:
    recovery_app = typer.Typer(
        help="Recovery режим (локальный: файлы + docker)",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    ctx = RecoveryContext()

    # Ленивая загрузка подмодулей, чтобы избежать циклических импортов.
    from . import compose as compose_mod
    from . import config as config_mod
    from . import core as core_mod
    from . import db as db_mod
    from . import mode as mode_mod
    from . import redis as redis_mod
    from . import ui as ui_mod

    recovery_app.add_typer(core_mod.build_app(ctx), name="core")
    recovery_app.add_typer(ui_mod.build_app(ctx), name="ui")
    recovery_app.add_typer(db_mod.build_app(ctx), name="db")
    recovery_app.add_typer(redis_mod.build_app(ctx), name="redis")
    recovery_app.add_typer(compose_mod.build_app(ctx), name="compose")
    recovery_app.add_typer(mode_mod.build_app(ctx), name="mode")
    recovery_app.add_typer(config_mod.build_app(ctx), name="config")

    @recovery_app.command("doctor")
    def doctor(
        json_out: bool = typer.Option(False, "--json", help="Вывод в JSON"),
    ) -> None:
        """Алиас recovery-проверок → `hc doctor --quick` (+ setup.log)."""
        from hc.doctor_lib import print_doctor_report, run_doctor

        console = Console()
        console.print(
            "[dim]Совет: для полной диагностики —[/dim] "
            "[bold]hc doctor[/bold]  "
            "[dim]| API —[/dim] [bold]hc doctor --api[/bold]"
        )
        report = run_doctor(console, scope="recovery")
        print_doctor_report(console, report, json_out=json_out)
        if SETUP_LOG_PATH.exists() and not json_out:
            console.print("Открыть лог: [bold]hc recovery config open-setup-log[/bold]")

    @recovery_app.command("hint")
    def hint() -> None:
        console = Console()
        console.print(
            Panel.fit(
                "Локальный recovery:\n"
                "1) `hc recovery core up`\n"
                "2) `hc recovery core status`\n"
                "3) `hc recovery core logs -f`\n"
                "4) `hc recovery doctor`\n"
                "5) `hc recovery ui dev --run` (Vite)\n"
                "6) `hc recovery backup --out-dir ./backups`\n"
                "\n"
                f"Лог setup: {SETUP_LOG_PATH}",
                title="Recovery hint",
            )
        )

    @recovery_app.command("backup")
    def backup(out_dir: Path = typer.Option(Path("./backups"), "--out-dir", help="Куда складывать бэкап")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        snap = ctx.recovery_backup(console, src, out_dir)
        console.print(f"[green]✓[/green] Backup: {snap}")

    app.add_typer(recovery_app, name="recovery")
    app.add_typer(recovery_app, name="local")

