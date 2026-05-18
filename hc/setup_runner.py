from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from rich.console import Console

from hc.constants import CONFIG_DIR, SETUP_LOG_PATH, SETUP_PID_PATH


@dataclass(slots=True)
class SetupProcess:
    pid: int
    log_path: Path

    @classmethod
    def load(cls) -> Self | None:
        if not SETUP_PID_PATH.exists():
            return None
        raw = SETUP_PID_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        try:
            pid = int(raw.splitlines()[0].strip())
        except ValueError:
            return None
        return cls(pid=pid, log_path=SETUP_LOG_PATH)

    def is_running(self) -> bool:
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETUP_PID_PATH.write_text(f"{self.pid}\n", encoding="utf-8")

    @classmethod
    def cleanup(cls) -> None:
        SETUP_PID_PATH.unlink(missing_ok=True)


def start_background(command: list[str], cwd: Path) -> SetupProcess:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{ts}] Запуск: {' '.join(command)} (cwd={cwd})\n"
    SETUP_LOG_PATH.write_text(header, encoding="utf-8")

    # Важно: стартуем отдельной сессией, чтобы процесс не умер вместе с терминалом.
    import subprocess

    with SETUP_LOG_PATH.open("a", encoding="utf-8") as log:
        p = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(cwd),
            stdout=log,
            stderr=log,
            text=True,
            start_new_session=True,
        )

    sp = SetupProcess(pid=int(p.pid), log_path=SETUP_LOG_PATH)
    sp.save()
    return sp


def print_setup_hint(console: Console, sp: SetupProcess) -> None:
    console.print("[green]✓[/green] Мастер запущен в фоне.")
    console.print(f"Логи: [bold]{sp.log_path}[/bold]")
    console.print("Смотри: `hc setup logs --follow`")

