"""hc completion — генерация и установка shell completion для bash/zsh/fish."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console


# ---------------------------------------------------------------------------
# Генерация скрипта
# ---------------------------------------------------------------------------

def _gen_script(shell: str) -> str:
    """Получить completion-скрипт через Click env-var механизм."""
    exe = shutil.which("hc") or sys.argv[0]
    env = {**os.environ, "_HC_COMPLETE": f"source_{shell}"}
    try:
        result = subprocess.run(
            [exe], env=env, capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# Пути и RC-сниппеты
# ---------------------------------------------------------------------------

def _comp_dir() -> Path:
    return Path.home() / ".config" / "hc" / "completions"


def _comp_file(shell: str) -> Path:
    names = {"zsh": "_hc", "bash": "hc.bash", "fish": "hc.fish"}
    return _comp_dir() / names.get(shell, f"hc.{shell}")


def _rc_path(shell: str) -> Path:
    return Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")


def _fish_comp_path() -> Path:
    return Path.home() / ".config" / "fish" / "completions" / "hc.fish"


_MARKER = "# HomeConsole CLI completion"

def _bash_snippet(script_path: Path) -> str:
    return f'[ -f "{script_path}" ] && source "{script_path}"  {_MARKER}'

def _zsh_snippet(script_path: Path) -> str:
    return (
        f'fpath=("{script_path.parent}" $fpath)  {_MARKER}\n'
        f'autoload -Uz compinit && compinit -u  {_MARKER}'
    )


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------

def register(app: typer.Typer) -> None:
    comp_app = typer.Typer(
        help="Shell completion для bash / zsh / fish.",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    def _detect_shell() -> str:
        shell = os.environ.get("SHELL", "")
        if "zsh"  in shell: return "zsh"
        if "fish" in shell: return "fish"
        return "bash"

    @comp_app.command("generate")
    def generate(
        shell: str = typer.Option("auto", "--shell", "-s", help="bash | zsh | fish | auto"),
    ) -> None:
        """Вывести completion-скрипт в stdout.

        Примеры:
          hc completion generate --shell zsh
          hc completion generate --shell bash > /tmp/hc.bash
          eval "$(hc completion generate --shell bash)"
        """
        console = Console(stderr=True)
        s = shell if shell != "auto" else _detect_shell()
        script = _gen_script(s)
        if not script:
            console.print(f"[red]Не удалось сгенерировать скрипт для {s}.[/red]")
            raise typer.Exit(code=1)
        # Выводим скрипт в stdout без rich (чтобы можно было eval)
        print(script)

    @comp_app.command("install")
    def install(
        shell: str = typer.Option("auto", "--shell", "-s", help="bash | zsh | fish | auto"),
        force: bool = typer.Option(False, "--force", help="Перезаписать если уже установлено"),
    ) -> None:
        """Установить completion в shell.

        bash/zsh: сохраняет скрипт в ~/.config/hc/completions/ и добавляет
                  source/fpath строку в ~/.bashrc / ~/.zshrc.
        fish:     записывает напрямую в ~/.config/fish/completions/hc.fish.

        Примеры:
          hc completion install
          hc completion install --shell fish
          hc completion install --shell zsh --force
        """
        console = Console()
        s = shell if shell != "auto" else _detect_shell()

        # Генерируем скрипт
        script = _gen_script(s)
        if not script:
            console.print(f"[red]Не удалось сгенерировать скрипт для {s}.[/red]")
            raise typer.Exit(code=1)

        if s == "fish":
            dest = _fish_comp_path()
            if dest.exists() and not force:
                console.print(f"[yellow]Уже установлено:[/yellow] {dest}\nИспользуй --force для перезаписи.")
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(script + "\n", encoding="utf-8")
            console.print(f"[green]✓[/green] fish completion → [bold]{dest}[/bold]")
            console.print("[dim]Fish подхватит автоматически при следующем запуске.[/dim]")
            return

        # bash / zsh
        comp_file = _comp_file(s)
        rc_file   = _rc_path(s)

        # Проверяем RC файл
        rc_text = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
        if _MARKER in rc_text and not force:
            console.print(
                f"[yellow]Уже установлено[/yellow] в {rc_file}\n"
                "Используй [bold]--force[/bold] для перезаписи."
            )
            return

        # Пишем скрипт
        comp_file.parent.mkdir(parents=True, exist_ok=True)
        comp_file.write_text(script + "\n", encoding="utf-8")

        # Удаляем старый блок из RC
        if _MARKER in rc_text and force:
            lines = rc_text.splitlines(keepends=True)
            rc_text = "".join(l for l in lines if _MARKER not in l)

        # Добавляем новый блок
        snippet = _zsh_snippet(comp_file) if s == "zsh" else _bash_snippet(comp_file)
        with open(rc_file, "a", encoding="utf-8") as f:
            if rc_text and not rc_text.endswith("\n"):
                f.write("\n")
            f.write(f"\n{snippet}\n")

        console.print(f"[green]✓[/green] Скрипт → [bold]{comp_file}[/bold]")
        console.print(f"[green]✓[/green] Сниппет  → [bold]{rc_file}[/bold]")
        console.print(f"\nПрименить прямо сейчас:")
        console.print(f"  [bold]source {rc_file}[/bold]")
        console.print(f"\n[dim]После этого нажми Tab для автодополнения команд hc.[/dim]")

    @comp_app.command("uninstall")
    def uninstall(
        shell: str = typer.Option("auto", "--shell", "-s", help="bash | zsh | fish | auto"),
    ) -> None:
        """Удалить completion из shell."""
        console = Console()
        s = shell if shell != "auto" else _detect_shell()

        removed = []

        if s == "fish":
            dest = _fish_comp_path()
            if dest.exists():
                dest.unlink()
                removed.append(str(dest))
        else:
            comp_file = _comp_file(s)
            rc_file   = _rc_path(s)

            if comp_file.exists():
                comp_file.unlink()
                removed.append(str(comp_file))

            if rc_file.exists():
                text = rc_file.read_text(encoding="utf-8")
                if _MARKER in text:
                    cleaned = "".join(
                        l for l in text.splitlines(keepends=True)
                        if _MARKER not in l
                    )
                    rc_file.write_text(cleaned, encoding="utf-8")
                    removed.append(f"{rc_file} (строки completion)")

        if removed:
            for r in removed:
                console.print(f"[green]✓[/green] Удалено: {r}")
        else:
            console.print(f"[yellow]Completion для {s} не найдено.[/yellow]")

    @comp_app.command("status")
    def status() -> None:
        """Показать статус установки completion для всех шеллов."""
        console = Console()
        console.print("\n[bold]Shell completion — статус[/bold]\n")

        for s in ("bash", "zsh", "fish"):
            if s == "fish":
                dest = _fish_comp_path()
                installed = dest.exists()
                loc = str(dest)
            else:
                comp_file = _comp_file(s)
                rc_file   = _rc_path(s)
                rc_text   = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
                installed = comp_file.exists() and _MARKER in rc_text
                loc = str(comp_file)

            icon  = "[green]✓[/green]" if installed else "[dim]✗[/dim]"
            label = "[green]установлено[/green]" if installed else "[dim]не установлено[/dim]"
            console.print(f"  {icon}  [bold]{s:6}[/bold]  {label}  [dim]{loc}[/dim]")

        console.print(
            "\n[dim]Установить: [bold]hc completion install --shell <bash|zsh|fish>[/bold][/dim]\n"
        )

    app.add_typer(comp_app, name="completion")
