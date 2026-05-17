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
    foreground: bool = False,
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

    if foreground:
        _native_up_foreground(console, src, py_exe=py_exe, main_py=main_py, env_path=env_path)
        return

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


def _native_up_foreground(
    console: Console,
    src: CoreSource,
    *,
    py_exe: str,
    main_py: Path,
    env_path: Path,
) -> None:
    """Запустить Core в foreground — stdout/stderr прямо в терминал.

    PID-файл пишется до передачи управления процессу и удаляется при выходе.
    Ctrl+C → SIGINT → graceful shutdown Core (uvicorn сам обрабатывает).
    """
    port, display_host = api_listen_display(env_path)
    base_url = f"http://{display_host}:{port}"
    console.print(f"[dim]Core (native, foreground). API будет на [bold]{base_url}[/bold]. Ctrl+C для остановки.[/dim]")

    try:
        proc = subprocess.Popen(  # noqa: S603
            [py_exe, str(main_py)],
            cwd=str(src.path),
        )
    except OSError as e:
        console.print(f"[red]Ошибка: не удалось запустить процесс: {e}[/red]")
        raise typer.Exit(code=1) from e

    _write_pid_file(proc.pid)
    try:
        proc.wait()
    except KeyboardInterrupt:
        console.print("\n[dim]Получен Ctrl+C, жду завершения Core…[/dim]")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        _remove_pid_file()

    if proc.returncode not in (0, -2, -15):  # 0=ok, SIGINT, SIGTERM
        console.print(f"[yellow]Core завершился с кодом {proc.returncode}.[/yellow]")
        raise typer.Exit(code=proc.returncode)


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


def _proc_stats(pid: int) -> dict[str, str]:
    """Получить uptime, RSS (МБ) и CPU% процесса без psutil."""
    stats: dict[str, str] = {}
    if sys.platform == "win32":
        return stats
    try:
        result = subprocess.run(  # noqa: S603
            ["ps", "-p", str(pid), "-o", "etime=,rss=,pcpu="],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 1:
                stats["uptime"] = parts[0]
            if len(parts) >= 2:
                try:
                    rss_kb = int(parts[1])
                    stats["rss"] = f"{rss_kb / 1024:.1f} MB"
                except ValueError:
                    pass
            if len(parts) >= 3:
                stats["cpu"] = f"{parts[2]}%"
    except Exception:  # noqa: BLE001
        pass
    return stats


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

    stats = _proc_stats(pid)
    uptime = stats.get("uptime", "—")
    rss    = stats.get("rss",    "—")
    cpu    = stats.get("cpu",    "—")

    console.print(
        f"[bold]native core-runtime[/bold]  "
        f"PID=[cyan]{pid}[/cyan]  "
        f"uptime=[cyan]{uptime}[/cyan]  "
        f"mem=[cyan]{rss}[/cyan]  "
        f"cpu=[cyan]{cpu}[/cyan]"
    )
    console.print(f"API: [bold]{base_url}[/bold]")
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


# ---------------------------------------------------------------------------
# Signal / named aliases
# ---------------------------------------------------------------------------

_SIGNAL_ALIASES: dict[str, int] = {
    "reload":  signal.SIGHUP.value  if hasattr(signal, "SIGHUP")  else 1,
    "dump":    signal.SIGUSR1.value if hasattr(signal, "SIGUSR1") else 10,
    "quit":    signal.SIGQUIT.value if hasattr(signal, "SIGQUIT") else 3,
    "term":    signal.SIGTERM.value,
    "int":     signal.SIGINT.value,
}


def native_signal(console: Console, sig: str) -> None:
    """Послать сигнал native-процессу Core.

    sig — имя (reload|dump|quit|term|int) или номер сигнала (строка).
    """
    if sys.platform == "win32":
        console.print("[red]Ошибка: сигналы не поддерживаются на Windows.[/red]")
        raise typer.Exit(code=1)

    pid = _read_pid_file()
    if pid is None:
        console.print("[red]Ошибка: нативный Core не запущен (нет PID-файла).[/red]")
        raise typer.Exit(code=1)
    if not _pid_alive(pid):
        console.print(f"[yellow]PID {pid} не существует — устаревший PID-файл.[/yellow]")
        _remove_pid_file()
        raise typer.Exit(code=1)

    sig_lower = sig.strip().lower()
    signum: int | None = _SIGNAL_ALIASES.get(sig_lower)
    if signum is None:
        try:
            signum = int(sig)
        except ValueError:
            known = ", ".join(_SIGNAL_ALIASES.keys())
            console.print(f"[red]Ошибка: неизвестный сигнал {sig!r}. Известные: {known}, или номер (напр. 15).[/red]")
            raise typer.Exit(code=1)

    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        console.print(f"[yellow]Процесс {pid} не найден.[/yellow]")
        _remove_pid_file()
        raise typer.Exit(code=1)
    except PermissionError:
        console.print(f"[red]Ошибка: нет прав на отправку сигнала процессу {pid}.[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Сигнал {sig} ({signum}) → PID {pid}")
