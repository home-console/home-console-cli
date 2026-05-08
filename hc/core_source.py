from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from hc.constants import CORE_SRC_DIR, DATA_DIR, DEFAULT_CORE_REF, DEFAULT_CORE_REPO


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
    try:
        subprocess.run(cmd, check=True, text=True)  # noqa: S603
    except subprocess.CalledProcessError:
        console.print("[red]Ошибка: не удалось клонировать репозиторий Core.[/red]")
        raise typer.Exit(code=1)

    return CoreSource(path=CORE_SRC_DIR)


def update_core_source(console: Console) -> CoreSource:
    _require_git(console)
    src = get_core_source_local()
    if not src:
        console.print("[red]Ошибка: локальная копия Core не инициализирована.[/red]")
        console.print("Сделай: `hc core init --repo <git-url>`")
        raise typer.Exit(code=1)
    try:
        subprocess.run(["git", "pull", "--ff-only"], cwd=str(src.path), check=True, text=True)  # noqa: S603
    except subprocess.CalledProcessError:
        console.print("[red]Ошибка: не удалось обновить репозиторий Core (git pull).[/red]")
        raise typer.Exit(code=1)
    return src
