"""
Каталог известных проблем + утилиты анализа логов контейнеров.

Используется в трёх местах:
  - hc env up      — пост-mortem после неудачного docker compose up
  - hc env logs    — опционально, подсветка известных ошибок
  - hc doctor      — проактивная проверка логов запущенных сервисов

Добавление новой болячки = одна запись в KNOWN_ISSUES, и она автоматически
становится видна везде.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


__all__ = [
    "FixCommand",
    "KnownIssue",
    "DetectedIssue",
    "KNOWN_ISSUES",
    "detect_issues",
    "fetch_container_logs",
    "list_compose_containers",
]


FixKind = Literal["hc", "shell"]


@dataclass(frozen=True, slots=True)
class FixCommand:
    """
    Одна рекомендованная команда для починки.

    kind:
      "hc"    — команда `hc *`, её можно ввести прямо в REPL.
      "shell" — обычная команда (ls, openssl, docker и т.п.); REPL её не выполнит,
                нужно открыть отдельный shell.
    """

    command: str
    description: str
    kind: FixKind = "hc"


# Backward-compat alias: разрешаем передавать (command, description) tuple или
# (command, description, kind) tuple — преобразуется в FixCommand при загрузке.
_FixSpec = tuple[str, str] | tuple[str, str, FixKind] | FixCommand


def _to_fix_command(spec: _FixSpec) -> FixCommand:
    if isinstance(spec, FixCommand):
        return spec
    if len(spec) == 2:
        cmd, desc = spec
        return FixCommand(command=cmd, description=desc, kind="hc")
    cmd, desc, kind = spec
    return FixCommand(command=cmd, description=desc, kind=kind)


@dataclass(frozen=True, slots=True)
class KnownIssue:
    """Известный класс ошибки, который мы умеем распознавать в логах."""

    id: str
    title: str
    cause: str
    pattern: re.Pattern[str]
    fix_commands: tuple[FixCommand, ...] = ()
    severity: str = "error"  # error | warn | info


@dataclass(slots=True)
class DetectedIssue:
    """Распознанная ошибка с указанием совпавшей строки лога."""

    issue: KnownIssue
    matched_line: str = ""
    service: str = ""


# ─── Каталог известных проблем ────────────────────────────────────────────────

_RAW_ISSUES: list[tuple[KnownIssue, tuple[_FixSpec, ...]]] = [
    (
        KnownIssue(
            id="master_key_mismatch",
            title="RUNTIME_MASTER_KEY не совпадает с зашифрованным vault",
            cause=(
                "Vault БД содержит секреты, зашифрованные другим мастер-ключом.\n"
                "AES-GCM не может проверить тег аутентификации → расшифровка невозможна.\n\n"
                "Типичные причины:\n"
                "  • переключение между sqlite/postgres (volume остался от прошлой установки)\n"
                "  • RUNTIME_MASTER_KEY в .env изменился или потерян\n"
                "  • vault БД восстановлена из бэкапа без соответствующего ключа"
            ),
            pattern=re.compile(
                r"Decryption failed.*InvalidTag|vault was recreated|passphrase changed",
                re.IGNORECASE,
            ),
        ),
        (
            ("hc env reset-vault", "снести vault и пересоздать с текущим ключом (рекомендую для dev)", "hc"),
            ("hc env logs core-runtime --tail 200", "полные логи если хочешь разобраться сам", "hc"),
        ),
    ),
    (
        KnownIssue(
            id="missing_required_secrets",
            title="Не все обязательные секреты заданы",
            cause=(
                "Core при старте не нашёл обязательные секреты ни в SecretStore (vault), ни в .env.\n"
                "Обычно это следствие master_key_mismatch — vault есть, но расшифровать нельзя,\n"
                "а в .env эти значения не дублируются."
            ),
            pattern=re.compile(r"Missing required secrets", re.IGNORECASE),
        ),
        (
            ("hc env reset-vault", "если vault сломан — пересоздать", "hc"),
            ("openssl rand -hex 32", "сгенерировать значение секрета (выполни в shell)", "shell"),
            (
                "hc env dotenv set CSRF_SECRET=<вставь-значение-из-openssl>",
                "положить секрет в .env вручную (менее безопасно)",
                "hc",
            ),
        ),
    ),
    (
        KnownIssue(
            id="master_key_missing",
            title="RUNTIME_MASTER_KEY не задан",
            cause=(
                "Core требует RUNTIME_MASTER_KEY (или RUNTIME_MASTER_KEY_FILE) для работы SecretStore.\n"
                "Без него невозможно ни прочитать, ни сохранить шифрованные секреты."
            ),
            pattern=re.compile(r"RUNTIME_MASTER_KEY is required", re.IGNORECASE),
        ),
        (
            ("openssl rand -hex 32", "сгенерировать мастер-ключ (выполни в shell, скопируй вывод)", "shell"),
            (
                "hc env dotenv set RUNTIME_MASTER_KEY=<вставь-значение-из-openssl>",
                "сохранить мастер-ключ в .env",
                "hc",
            ),
        ),
    ),
    (
        KnownIssue(
            id="postgres_connection_refused",
            title="core-runtime не может подключиться к PostgreSQL",
            cause=(
                "PostgreSQL ещё не готов или сетевая связь между контейнерами нарушена.\n"
                "Чаще всего это race: core стартует быстрее, чем pg успевает поднять TCP."
            ),
            pattern=re.compile(
                r"could not connect to server.*connection refused|"
                r"connection refused.*5432|"
                r"OperationalError.*connection refused|"
                r"psycopg2\.OperationalError",
                re.IGNORECASE,
            ),
        ),
        (
            ("hc env restart core-runtime", "просто перезапустить core когда pg уже healthy", "hc"),
            ("hc env health", "проверить здоровье всех контейнеров", "hc"),
        ),
    ),
    (
        KnownIssue(
            id="port_already_in_use",
            title="Порт уже занят на хосте",
            cause=(
                "Один из портов, который пробрасывает compose, уже занят другим процессом\n"
                "(например, второй запущенный стек, локальный postgres, или старый prod-edge на :80)."
            ),
            pattern=re.compile(
                r"bind: address already in use|port is already allocated|"
                r"Bind for 0\.0\.0\.0:\d+ failed",
                re.IGNORECASE,
            ),
        ),
        (
            ("hc doctor", "посмотреть какие порты заняты", "hc"),
            ("hc env down", "остановить текущий стек если что-то висит", "hc"),
        ),
    ),
    (
        KnownIssue(
            id="alembic_migration_failed",
            title="Не удалось применить миграции БД",
            cause=(
                "Alembic не смог применить миграцию (несовместимая схема, плохой downgrade, конфликт версий)."
            ),
            pattern=re.compile(r"alembic.*error|FAILED.*alembic|MultipleHeads", re.IGNORECASE),
        ),
        (
            ("hc env logs core-runtime --tail 200", "посмотреть полную трассировку миграции", "hc"),
        ),
    ),
    (
        KnownIssue(
            id="storage_corruption",
            title="Обнаружена коррупция secure storage",
            cause=(
                "Merkle root не сходится с записями в БД — кто-то менял данные мимо secure_set,\n"
                "или файл БД повреждён."
            ),
            pattern=re.compile(
                r"StorageCorruptionError|Root hash mismatch|Merkle.*mismatch",
                re.IGNORECASE,
            ),
        ),
        (
            ("hc env reset-vault", "пересоздать vault если это dev", "hc"),
            ("hc env logs core-runtime --tail 500", "посмотреть детали повреждения", "hc"),
        ),
    ),
    (
        KnownIssue(
            id="docker_network_not_found",
            title="Docker не нашёл network при старте контейнера (race)",
            cause=(
                "Docker создал контейнер, но к моменту его старта network уже не существует.\n"
                "Типичные причины:\n"
                "  • перезапуск Docker daemon во время поднятия стека\n"
                "  • параллельный `compose down` стёр network пока другой `up` его использовал\n"
                "  • остатки от старого запуска (network был пересоздан с новым ID)\n\n"
                "Лечится полным пересозданием стека."
            ),
            pattern=re.compile(
                r"failed to set up container networking.*network.*not found|"
                r"network [0-9a-f]{32,} not found",
                re.IGNORECASE,
            ),
        ),
        (
            ("hc env down", "снести стек", "hc"),
            ("hc env up", "поднять заново — network пересоздастся", "hc"),
            ("docker network prune -f", "если down не помогает — снести orphan networks", "shell"),
        ),
    ),
    (
        KnownIssue(
            id="frontend_workspace_missing",
            title="frontend-vite не нашёл package.json в /workspace",
            cause=(
                "Контейнер frontend-vite ожидает исходник фронтенда (platform-home-console)\n"
                "смонтированным в /workspace. Compose монтирует sibling-папку\n"
                "../../../platform-home-console — то есть platform-home-console должна лежать\n"
                "РЯДОМ с core-runtime-service.\n\n"
                "В standalone-установке через `hc core init` рядом ничего нет, поэтому /workspace\n"
                "получается пустым, и pnpm падает на ERR_PNPM_NO_PKG_MANIFEST.\n\n"
                "Утилита должна была отловить это ДО запуска через _check_frontend_workspace —\n"
                "если ты это видишь, значит проверка прошла мимо, сообщи об issue."
            ),
            pattern=re.compile(
                r"ERR_PNPM_NO_PKG_MANIFEST|No package\.json found in /workspace",
                re.IGNORECASE,
            ),
        ),
        (
            # Порядок важен: сначала путь "просто работает" (отключить vite),
            # потом более сложный (склонировать платформу).
            (
                "hc env up --profile base",
                "поднять без frontend-vite (рекомендуется — Vite HMR обычно не нужен)",
                "hc",
            ),
            (
                "git clone https://github.com/home-console/platform-home-console "
                "~/.local/share/hc/platform-home-console",
                "склонировать фронт рядом с core-runtime-service, потом hc env up",
                "shell",
            ),
        ),
    ),
]


def _build_known_issues() -> list[KnownIssue]:
    """Применить fix-команды к KnownIssue (через replace, т.к. dataclass frozen)."""
    import dataclasses as _dc

    out: list[KnownIssue] = []
    for issue, specs in _RAW_ISSUES:
        fixes = tuple(_to_fix_command(s) for s in specs)
        out.append(_dc.replace(issue, fix_commands=fixes))
    return out


KNOWN_ISSUES: list[KnownIssue] = _build_known_issues()


# ─── Детектор ─────────────────────────────────────────────────────────────────


def detect_issues(text: str, *, service: str = "") -> list[DetectedIssue]:
    """
    Прогнать произвольный текст через KNOWN_ISSUES и вернуть совпадения.

    Каждая проблема попадает в результат не более одного раза — даже если матч есть
    в нескольких строках, оставляем самую первую (она обычно ближе к корню стектрейса).
    """
    if not text:
        return []

    seen: set[str] = set()
    found: list[DetectedIssue] = []

    for line in text.splitlines():
        for issue in KNOWN_ISSUES:
            if issue.id in seen:
                continue
            if issue.pattern.search(line):
                found.append(DetectedIssue(issue=issue, matched_line=line.strip(), service=service))
                seen.add(issue.id)

    return found


# ─── Helpers: получение списка контейнеров и их логов ─────────────────────────


def list_compose_containers(
    compose_file: Path,
    cwd: Path,
    *,
    only_states: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """
    Вернуть список контейнеров стека в JSON-формате.

    only_states — если задано, оставить только контейнеры с State из этого набора
    (например ("exited", "restarting")).
    """
    import json

    r = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(compose_file),
            "ps", "-a", "--format", "json",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    rows: list[dict[str, str]] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        state = str(item.get("State") or "").lower()
        health = str(item.get("Health") or "").lower()
        # Combine State + Health into something easier to filter on.
        effective = state
        if state == "running" and health in {"unhealthy", "starting"}:
            effective = health
        item["_effective_state"] = effective
        if only_states and effective not in set(only_states):
            continue
        rows.append(item)
    return rows


def fetch_container_logs(
    compose_file: Path,
    cwd: Path,
    service: str,
    *,
    tail: int = 200,
) -> str:
    """Получить последние строки логов одного сервиса через docker compose logs."""
    r = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(compose_file),
            "logs", "--no-color", "--tail", str(tail), service,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return r.stdout + r.stderr
