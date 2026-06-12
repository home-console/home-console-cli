from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from hc.constants import (
    CORE_SRC_DIR,
    DATA_DIR,
    DEFAULT_CORE_REF,
    DEFAULT_CORE_REPO,
    DEFAULT_PLATFORM_REF,
    DEFAULT_PLATFORM_REPO,
    PLATFORM_SRC_DIR,
)

# Сиблинги, которые подтверждают что найденная папка с core-runtime-service
# действительно является корнем монорепо разработчика (а не случайным клоном
# самого core-runtime-service в произвольном parent-каталоге).
_MONOREPO_SIBLINGS: frozenset[str] = frozenset(
    {"home-console-cli", "packages", "platform-home-console"}
)


def _looks_like_monorepo(root: Path) -> bool:
    """True если в `root` есть core-runtime-service + хотя бы один из siblings."""
    if not (root / "core-runtime-service").is_dir():
        return False
    return any((root / s).exists() for s in _MONOREPO_SIBLINGS)


def _scan_upwards_for_monorepo(start: Path) -> Path | None:
    """Подняться вверх от `start` и вернуть первый каталог, похожий на монорепо."""
    try:
        start = start.resolve()
    except (OSError, RuntimeError):
        return None
    for p in [start, *start.parents]:
        if _looks_like_monorepo(p):
            return p
    return None


def detect_workspace_root() -> Path | None:
    """
    Найти корень монорепо разработчика без чтения конфига.

    Источники (в порядке убывания приоритета):
      1. `HC_WORKSPACE` — явная переменная окружения.
      2. Текущая рабочая директория (cwd) и её родители.
      3. Расположение исходников самого CLI (для `pip install -e .`).

    Возвращает None, если ничего не найдено.
    """
    env_path = os.environ.get("HC_WORKSPACE", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if _looks_like_monorepo(p):
            return p.resolve()

    cwd_root = _scan_upwards_for_monorepo(Path.cwd())
    if cwd_root:
        return cwd_root

    cli_root = _scan_upwards_for_monorepo(Path(__file__).resolve().parent)
    if cli_root:
        return cli_root

    return None


def resolve_workspace_root() -> Path | None:
    """То же, что `detect_workspace_root`, но плюс читает workspace.path из конфига.

    Конфиг имеет наинизший приоритет: HC_WORKSPACE и cwd важнее. Это нужно
    чтобы можно было одноразово переопределить workspace через cd в другую
    папку, не редактируя конфиг.
    """
    found = detect_workspace_root()
    if found:
        return found

    try:
        from hc.config import Config

        cfg_path = Config.load().workspace.path.strip()
    except Exception:
        cfg_path = ""

    if cfg_path:
        p = Path(cfg_path).expanduser()
        if _looks_like_monorepo(p):
            return p.resolve()
    return None


# ─── Канонический маппинг режимов → compose-файл ──────────────────────────────
#
# Режим         Compose-файл (rel to core-runtime-service)      Когда использовать
# ──────────────────────────────────────────────────────────────────────────────────
# dev           deploy/dev/docker-compose.yml                   Сборка из src (разработка)
# dev-reload    deploy/dev/docker-compose.reload.yml            Как dev + live volume mount +
#                                                               watchfiles (горячий рестарт)
# dev-image     deploy/dev/docker-compose.image.yml            Готовый образ, dev-инфра
#                                                               (caddy+статика); проверка образа
# prod          deploy/prod/docker-compose.image.yml           Образ из registry + prod-инфра
#                                                               (edge+platform-web); ТОЛЬКО registry
# ──────────────────────────────────────────────────────────────────────────────────
COMPOSE_MODES: dict[str, str] = {
    "dev":        "deploy/dev/docker-compose.yml",
    "dev-reload": "deploy/dev/docker-compose.reload.yml",
    "dev-image":  "deploy/dev/docker-compose.image.yml",
    "prod":       "deploy/prod/docker-compose.image.yml",
}

# Удобное множество для валидации
VALID_MODES: frozenset[str] = frozenset(COMPOSE_MODES)

# Режимы, требующие готовый образ (без build из src)
IMAGE_MODES: frozenset[str] = frozenset({"dev-image", "prod"})

# Режимы, пригодные для remote rollout через SSH
DEPLOY_MODES: frozenset[str] = frozenset({"dev-image", "prod"})


@dataclass(slots=True)
class CoreSource:
    path: Path

    def compose_file(self, mode: str | None = None) -> Path:
        """
        Возвращает абсолютный путь к compose-файлу для данного режима.

        Допустимые значения mode: dev | dev-reload | dev-image | prod.
        При mode=None используется "dev".
        """
        m = (mode or "dev").strip().lower()
        rel = COMPOSE_MODES.get(m)
        if rel is None:
            valid = " | ".join(sorted(COMPOSE_MODES))
            raise ValueError(
                f"Неизвестный режим {m!r}. Допустимые: {valid}"
            )
        return self.path / rel

    def compose_rel(self, mode: str | None = None) -> str:
        """Путь к compose-файлу относительно корня core-runtime-service (для SSH)."""
        m = (mode or "dev").strip().lower()
        rel = COMPOSE_MODES.get(m)
        if rel is None:
            valid = " | ".join(sorted(COMPOSE_MODES))
            raise ValueError(
                f"Неизвестный режим {m!r}. Допустимые: {valid}"
            )
        return rel


def get_core_source_from_repo(repo_root: Path) -> CoreSource | None:
    p = repo_root / "core-runtime-service"
    return CoreSource(path=p) if p.exists() else None


def get_core_source_local() -> CoreSource | None:
    return CoreSource(path=CORE_SRC_DIR) if CORE_SRC_DIR.exists() else None


def _require_git(console: Console) -> None:
    if shutil.which("git") is None:
        console.print("[red]Ошибка: git не найден. Установи git и повтори.[/red]")
        raise typer.Exit(code=1)


def init_core_source(console: Console, repo_url: str | None, ref: str | None) -> CoreSource:
    _require_git(console)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if CORE_SRC_DIR.exists():
        console.print(f"[yellow]Уже есть локальная копия:[/yellow] {CORE_SRC_DIR}")
        return CoreSource(path=CORE_SRC_DIR)

    repo_url = repo_url or DEFAULT_CORE_REPO
    ref = ref or DEFAULT_CORE_REF

    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [repo_url, str(CORE_SRC_DIR)]
    r = subprocess.run(cmd, check=False, text=True, capture_output=True)  # noqa: S603
    if r.returncode != 0:
        _print_git_error(console, "clone", repo_url, r.stderr or r.stdout)
        raise typer.Exit(code=1)

    return CoreSource(path=CORE_SRC_DIR)


def update_core_source(console: Console) -> CoreSource:
    _require_git(console)
    src = get_core_source_local()
    if not src:
        console.print("[red]Ошибка: локальная копия Core не инициализирована.[/red]")
        console.print("Сделай: `hc core init --repo <git-url>`")
        raise typer.Exit(code=1)
    r = subprocess.run(  # noqa: S603
        ["git", "pull", "--ff-only"], cwd=str(src.path), check=False, text=True, capture_output=True
    )
    if r.returncode != 0:
        _print_git_error(console, "pull", str(src.path), r.stderr or r.stdout)
        raise typer.Exit(code=1)
    return src


def get_platform_source_local() -> Path | None:
    """Возвращает путь к локально склонированному platform-home-console (или None)."""
    return PLATFORM_SRC_DIR if (PLATFORM_SRC_DIR / "package.json").is_file() else None


def init_platform_source(
    console: Console,
    repo_url: str | None = None,
    ref: str | None = None,
    target: Path | None = None,
) -> Path:
    """
    Склонировать platform-home-console (нужен для сервиса frontend-vite).

    По умолчанию клонирует в PLATFORM_SRC_DIR (sibling-папка к CORE_SRC_DIR).
    Если target указан явно — клонирует туда (нужно для проверки на лету,
    когда compose монтирует ../../../platform-home-console и target может
    отличаться от стандартного PLATFORM_SRC_DIR).
    """
    _require_git(console)
    target = target or PLATFORM_SRC_DIR
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and any(target.iterdir()):
        console.print(f"[yellow]Папка уже не пустая:[/yellow] {target}")
        return target

    # Удаляем пустую папку чтобы git clone не ругался "already exists".
    if target.exists():
        try:
            target.rmdir()
        except OSError:
            pass

    repo_url = repo_url or DEFAULT_PLATFORM_REPO
    ref = ref or DEFAULT_PLATFORM_REF

    cmd = ["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(target)]
    console.print(f"[cyan]→ git clone {repo_url} → {target}[/cyan]")
    r = subprocess.run(cmd, check=False, text=True, capture_output=True)  # noqa: S603
    if r.returncode != 0:
        _print_git_error(console, "clone", repo_url, r.stderr or r.stdout)
        raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] platform-home-console склонирован: {target}")
    return target


def _print_git_error(console: Console, op: str, target: str, stderr: str) -> None:
    s = (stderr or "").lower()
    if "could not resolve host" in s or "unable to access" in s:
        hint = "Нет доступа к серверу. Проверь интернет-соединение."
    elif "authentication failed" in s or "403" in s or "denied" in s:
        hint = "Ошибка аутентификации. Проверь права доступа к репозиторию."
    elif "repository not found" in s or "not found" in s:
        hint = f"Репозиторий не найден: {target}"
    elif "already exists" in s:
        hint = f"Директория уже существует: {CORE_SRC_DIR}"
    else:
        hint = (stderr or "").strip() or f"git {op} завершился с ошибкой."
    console.print(f"[red]Ошибка git {op}:[/red] {hint}")
