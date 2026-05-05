from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO

import httpx
import typer
from rich.console import Console

from hc.core_source import CoreSource
from hc.env_bootstrap import core_env_path, ensure_core_env


def _native_paths() -> tuple[Path, Path]:
    from hc.constants import NATIVE_CORE_LOG_PATH, NATIVE_CORE_PID_PATH

    return NATIVE_CORE_PID_PATH, NATIVE_CORE_LOG_PATH

_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$",
)


def parse_dotenv_file(path: Path) -> dict[str, str]:
    """Минимальный разбор `.env` (без кавычек/мультилиний)."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = val[1:-1].replace("\\n", "\n")
        elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def api_listen_display(env_path: Path) -> tuple[int, str]:
    """Порт и хост для URL в сообщениях (как в runtime при API_HOST=0.0.0.0)."""
    env = parse_dotenv_file(env_path)
    port_s = env.get("API_PORT", "8000").strip()
    try:
        port = int(port_s)
    except ValueError:
        port = 8000
    host = env.get("API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    display = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    return port, display


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid_file() -> int | None:
    pid_path, _ = _native_paths()
    if not pid_path.is_file():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _write_pid_file(pid: int) -> None:
    pid_path, _ = _native_paths()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(pid), encoding="utf-8")


def _remove_pid_file() -> None:
    pid_path, _ = _native_paths()
    try:
        pid_path.unlink()
    except OSError:
        pass


def resolve_core_python(console: Console, core_root: Path, *, use_hc_python: bool) -> str:
    env_py = (os.environ.get("HC_CORE_PYTHON") or "").strip()
    if env_py:
        p = Path(env_py)
        if p.is_file():
            return str(p)
        if shutil.which(env_py):
            return env_py
        console.print(
            f"[red]Ошибка: HC_CORE_PYTHON указывает на неизвестный интерпретатор: {env_py!r}[/red]"
        )
        raise typer.Exit(code=1)

    if sys.platform == "win32":
        venv_py = core_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = core_root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)

    if use_hc_python:
        return sys.executable

    py3 = shutil.which("python3")
    if py3:
        return py3
    console.print(
        "[red]Ошибка: не найден интерпретатор Python для Core.[/red] Создай `.venv` в корне "
        "исходников (`python3 -m venv .venv && pip install -r requirements.txt`), задай "
        "`HC_CORE_PYTHON`, или используй `--use-hc-python`."
    )
    raise typer.Exit(code=1)


def wait_for_health(base_url: str, *, timeout: float = 60.0, interval: float = 0.5) -> bool:
    urls = [
        base_url.rstrip("/") + "/api/v1/monitor/health",
        base_url.rstrip("/") + "/monitor/health",
    ]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for url in urls:
            try:
                r = httpx.get(url, timeout=2.0, verify=True)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                continue
        time.sleep(interval)
    return False


def native_up(
    console: Console,
    src: CoreSource,
    *,
    use_hc_python: bool,
    no_ui: bool,
    wait_health: bool = True,
    health_timeout: float = 90.0,
) -> None:
    ensure_core_env(console, src.path)
    env_path = core_env_path(src.path)
    main_py = src.path / "main.py"
    if not main_py.is_file():
        console.print(f"[red]Ошибка: не найден {main_py}[/red]")
        raise typer.Exit(code=1)

    if no_ui is False:
        console.print(
            "[dim]В режиме native всегда поднимается только API (без Caddy/UI). "
            "Флаг --with-ui игнорируется.[/dim]"
        )

    existing = _read_pid_file()
    if existing is not None and _pid_alive(existing):
        console.print(
            f"[red]Ошибка: нативный Core уже запущен (PID {existing}).[/red] "
            "Останови: `hc core down --mode native`"
        )
        raise typer.Exit(code=1)
    if existing is not None:
        _remove_pid_file()

    py_exe = resolve_core_python(console, src.path, use_hc_python=use_hc_python)
    _, log_path = _native_paths()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f: IO[bytes] = open(log_path, "ab", buffering=0)  # noqa: SIM115
    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(  # noqa: S603
                [py_exe, str(main_py)],
                cwd=str(src.path),
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            )
        else:
            proc = subprocess.Popen(  # noqa: S603
                [py_exe, str(main_py)],
                cwd=str(src.path),
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as e:
        log_f.close()
        console.print(f"[red]Ошибка: не удалось запустить процесс: {e}[/red]")
        raise typer.Exit(code=1) from e
    finally:
        try:
            log_f.close()
        except OSError:
            pass

    _write_pid_file(proc.pid)
    port, display_host = api_listen_display(env_path)
    base_url = f"http://{display_host}:{port}"

    if wait_health:
        console.print(f"[dim]Ожидаю health: {base_url}/api/v1/monitor/health …[/dim]")
        if not wait_for_health(base_url, timeout=health_timeout):
            console.print(
                "[red]Ошибка: Core не ответил на health за отведённое время.[/red] "
                f"Смотри лог: {log_path}"
            )
            _terminate_process_tree(proc.pid, grace=2.0)
            _remove_pid_file()
            raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Core (native) запущен, PID {proc.pid}. API: [bold]{base_url}[/bold]")
    console.print(
        "[dim]Полный стек с UI — через Docker (`hc core up`). Лог процесса: "
        f"{log_path}[/dim]"
    )


def _terminate_process_tree(pid: int, *, grace: float) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        time.sleep(min(grace, 3.0))
        if _pid_alive(pid):
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def native_down(console: Console, *, volumes: bool) -> None:
    if volumes:
        console.print(
            "[dim]Флаг -v/--volumes относится к docker volumes; в режиме native не применяется.[/dim]"
        )
    pid = _read_pid_file()
    if pid is None:
        console.print("[yellow]Нативный Core не запущен (нет PID-файла).[/yellow]")
        return

    if not _pid_alive(pid):
        console.print("[yellow]Процесс не найден — удаляю устаревший PID-файл.[/yellow]")
        _remove_pid_file()
        return

    _terminate_process_tree(pid, grace=8.0)
    if _pid_alive(pid):
        console.print("[red]Ошибка: не удалось остановить процесс.[/red]")
        raise typer.Exit(code=1)
    _remove_pid_file()
    console.print("[green]✓[/green] Нативный Core остановлен.")


def native_ps(console: Console, src: CoreSource) -> None:
    env_path = core_env_path(src.path)
    port, display_host = api_listen_display(env_path)
    base_url = f"http://{display_host}:{port}"

    pid = _read_pid_file()
    if pid is None:
        console.print("[yellow]Нативный Core: PID-файл отсутствует (не запущен через `hc core up --mode native`).[/yellow]")
        raise typer.Exit(code=1)
    if not _pid_alive(pid):
        console.print(f"[yellow]PID {pid} не существует — устаревший PID-файл.[/yellow]")
        _remove_pid_file()
        raise typer.Exit(code=1)

    console.print(f"native core-runtime  PID={pid}  API={base_url}")
    try:
        r = httpx.get(base_url.rstrip("/") + "/api/v1/monitor/health", timeout=3.0)
        if r.status_code == 200:
            console.print("[green]health: OK[/green]")
        else:
            console.print(f"[yellow]health: HTTP {r.status_code}[/yellow]")
    except httpx.HTTPError as e:
        console.print(f"[yellow]health: недоступен ({e})[/yellow]")


def native_logs(console: Console, *, follow: bool, tail: int) -> None:
    _, log_path = _native_paths()
    if not log_path.is_file():
        console.print(
            f"[red]Ошибка: лог не найден ({log_path}).[/red] "
            "Запусти: `hc core up --mode native`"
        )
        raise typer.Exit(code=1)

    if follow:
        _tail_follow(console, log_path, tail_lines=tail)
        return

    lines = _read_tail_lines(log_path, tail)
    if lines:
        console.print("".join(lines), end="")


def _read_tail_lines(path: Path, n: int) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if not data:
        return []
    text = data.decode("utf-8", errors="replace")
    all_lines = text.splitlines(keepends=True)
    if n <= 0:
        return all_lines
    return all_lines[-n:]


def _tail_follow(console: Console, path: Path, *, tail_lines: int) -> None:
    for chunk in _read_tail_lines(path, tail_lines):
        console.print(chunk, end="")
    with open(path, "rb") as f:  # noqa: PTH123
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                console.print(line.decode("utf-8", errors="replace"), end="")
            else:
                time.sleep(0.3)
