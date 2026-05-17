"""
hc shell-config — генерация и установка shell-конфига (zsh/bash).

Добавляет в ~/.zshrc или ~/.bashrc:
  - Промпт с индикатором статуса Core
  - Короткие алиасы без префикса hc
  - Функцию hcs для быстрого входа в hc shell
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax


_ZSH_CONFIG = '''\
# ── HomeConsole CLI ─────────────────────────────────────────────────────────
# Добавлено командой: hc shell-config install --shell zsh

# Статус Core в промпт (RPROMPT — правая часть)
_hc_core_status() {
    local out
    out=$(hc status --short 2>/dev/null) && echo "%F{green}● hc%f" || echo "%F{red}○ hc%f"
}
setopt PROMPT_SUBST
RPROMPT='$(_hc_core_status)'

# Алиасы — управление ядром без префикса hc
alias plugins='hc plugin list'
alias plugin='hc plugin'
alias events='hc event tail'
alias core-status='hc status'
alias core-logs='hc core logs -f'
alias core-restart='hc core restart'
alias core-signal='hc core signal'
alias emergency='hc emergency'
alias services='hc service list'

# hcs — быстрый вход в hc shell (Core terminal)
hcs() {
    echo "Connecting to HomeConsole Core..."
    hc shell "$@"
}

# Автодополнение для hc (если есть)
if command -v hc &>/dev/null; then
    eval "$(hc --show-completion zsh 2>/dev/null || true)"
fi
# ────────────────────────────────────────────────────────────────────────────
'''

_BASH_CONFIG = '''\
# ── HomeConsole CLI ─────────────────────────────────────────────────────────
# Добавлено командой: hc shell-config install --shell bash

# Статус Core в промпт
_hc_core_status() {
    hc status --short &>/dev/null && echo "● hc" || echo "○ hc"
}
# Добавить в PS1 (раскомментировать если хочешь статус в промпте)
# PS1="\\[\\033[36m\\]\\$(_hc_core_status)\\[\\033[0m\\] $PS1"

# Алиасы — управление ядром без префикса hc
alias plugins='hc plugin list'
alias plugin='hc plugin'
alias events='hc event tail'
alias core-status='hc status'
alias core-logs='hc core logs -f'
alias core-restart='hc core restart'
alias core-signal='hc core signal'
alias emergency='hc emergency'
alias services='hc service list'

# hcs — быстрый вход в hc shell (Core terminal)
hcs() {
    echo "Connecting to HomeConsole Core..."
    hc shell "$@"
}
# ────────────────────────────────────────────────────────────────────────────
'''

_FISH_CONFIG = '''\
# ── HomeConsole CLI ─────────────────────────────────────────────────────────
# Добавлено командой: hc shell-config install --shell fish
# Поместить в ~/.config/fish/conf.d/homeconsole.fish

# Алиасы
alias plugins 'hc plugin list'
alias events  'hc event tail'
alias core-status 'hc status'
alias core-logs   'hc core logs -f'
alias core-restart 'hc core restart'
alias emergency   'hc emergency'
alias services    'hc service list'

# hcs — быстрый вход в hc shell
function hcs
    echo "Connecting to HomeConsole Core..."
    hc shell $argv
end

# Статус Core в правой части промпта
function fish_right_prompt
    if hc status --short &>/dev/null
        set_color green; echo -n "● hc"; set_color normal
    else
        set_color red; echo -n "○ hc"; set_color normal
    end
end
# ────────────────────────────────────────────────────────────────────────────
'''


def _detect_shell() -> str:
    """Определить текущий shell по $SHELL."""
    shell_bin = os.environ.get("SHELL", "")
    if "zsh" in shell_bin:
        return "zsh"
    if "fish" in shell_bin:
        return "fish"
    return "bash"


def _config_for_shell(shell: str) -> tuple[str, Path]:
    """Вернуть (содержимое конфига, путь к RC-файлу)."""
    if shell == "zsh":
        return _ZSH_CONFIG, Path.home() / ".zshrc"
    if shell == "fish":
        fish_dir = Path.home() / ".config" / "fish" / "conf.d"
        return _FISH_CONFIG, fish_dir / "homeconsole.fish"
    return _BASH_CONFIG, Path.home() / ".bashrc"


def register(app: typer.Typer) -> None:
    sc_app = typer.Typer(
        help="Генерация shell-конфига (zsh/bash/fish) с алиасами и промптом Core",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @sc_app.command("show")
    def show(
        shell: str = typer.Option("auto", "--shell", "-s", help="zsh | bash | fish | auto"),
    ) -> None:
        """Показать конфиг который будет добавлен в shell RC-файл."""
        console = Console()
        s = shell if shell != "auto" else _detect_shell()
        config, rc_path = _config_for_shell(s)
        console.print(f"\n[dim]Для установки:[/dim] [bold]hc shell-config install --shell {s}[/bold]")
        console.print(f"[dim]RC-файл:[/dim] {rc_path}\n")
        console.print(Syntax(config.strip(), "bash", theme="monokai", line_numbers=False))

    @sc_app.command("install")
    def install(
        shell: str = typer.Option("auto", "--shell", "-s", help="zsh | bash | fish | auto"),
        rc_path: Path | None = typer.Option(
            None, "--rc", help="Путь к RC-файлу (дефолт: ~/.zshrc / ~/.bashrc)"
        ),
        force: bool = typer.Option(False, "--force", help="Перезаписать если блок уже есть"),
    ) -> None:
        """Добавить HomeConsole алиасы и промпт в shell RC-файл."""
        console = Console()
        s = shell if shell != "auto" else _detect_shell()
        config, default_rc = _config_for_shell(s)
        target = rc_path or default_rc

        # Создать папку если нужно (для fish conf.d)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Проверить что блок не добавлен уже
        marker = "HomeConsole CLI"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if marker in existing and not force:
            console.print(
                f"[yellow]Уже установлено[/yellow] в {target}\n"
                "Используй [bold]--force[/bold] для перезаписи."
            )
            return

        if marker in existing and force:
            # Удалить старый блок
            lines = existing.splitlines(keepends=True)
            out, skip = [], False
            for line in lines:
                if "── HomeConsole CLI" in line:
                    skip = True
                if skip:
                    if line.strip().endswith("──────────────────────────────────────────────────────────────────────────────"):
                        skip = False
                    continue
                out.append(line)
            existing = "".join(out)

        with open(target, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n")
            f.write(config)

        console.print(f"[green]✓[/green] Установлено в [bold]{target}[/bold]")
        console.print(f"\n[dim]Применить прямо сейчас:[/dim]")
        if s == "fish":
            console.print(f"  [bold]source {target}[/bold]")
        else:
            console.print(f"  [bold]source {target}[/bold]")
        console.print(f"\n[dim]Доступные команды после применения:[/dim]")
        console.print("  [cyan]plugins[/cyan]        — hc plugin list")
        console.print("  [cyan]events[/cyan]         — hc event tail")
        console.print("  [cyan]core-status[/cyan]    — hc status")
        console.print("  [cyan]core-restart[/cyan]   — hc core restart")
        console.print("  [cyan]emergency[/cyan]      — hc emergency")
        console.print("  [cyan]services[/cyan]       — hc service list")
        console.print("  [cyan]hcs[/cyan]            — открыть hc shell (Core terminal)")

    @sc_app.command("uninstall")
    def uninstall(
        shell: str = typer.Option("auto", "--shell", "-s", help="zsh | bash | fish | auto"),
        rc_path: Path | None = typer.Option(None, "--rc"),
    ) -> None:
        """Удалить HomeConsole блок из shell RC-файла."""
        console = Console()
        s = shell if shell != "auto" else _detect_shell()
        _, default_rc = _config_for_shell(s)
        target = rc_path or default_rc

        if not target.exists():
            console.print(f"[yellow]{target} не найден.[/yellow]")
            return

        text = target.read_text(encoding="utf-8")
        if "HomeConsole CLI" not in text:
            console.print(f"[yellow]Блок HomeConsole не найден в {target}.[/yellow]")
            return

        lines = text.splitlines(keepends=True)
        out, skip = [], False
        for line in lines:
            if "── HomeConsole CLI" in line:
                skip = True
            if skip:
                if line.strip().endswith("──────────────────────────────────────────────────────────────────────────────"):
                    skip = False
                continue
            out.append(line)

        target.write_text("".join(out), encoding="utf-8")
        console.print(f"[green]✓[/green] Удалено из {target}")

    app.add_typer(sc_app, name="shell-config")
