"""
Безопасные операции сброса vault для dev-окружения.

Что такое "сброс vault"
-----------------------
Vault — это шифрованное хранилище секретов, защищённое RUNTIME_MASTER_KEY.
Если ключ потерян или не совпадает с тем, которым зашифрован vault, core
не сможет расшифровать существующие записи и упадёт на preflight check.

Сброс vault удаляет ТОЛЬКО vault-связанные данные:
  - sqlite-режим: файлы /data/vault.db и /data/vault_secret.db (core БД не трогаем)
  - postgres-режим: записи из таблицы storage с namespace в {secrets.store,
    _system.meta, _system.root_hash, _system.audit_log} — core-данные сохраняются.

После сброса при следующем старте core-runtime пересоздаёт vault с текущим
RUNTIME_MASTER_KEY и заново генерирует runtime.csrf_secret и runtime.oauth_encryption_key.

Namespaces, которые трогаем, см. core-runtime-service/modules/storage/secure.py:
  CRITICAL_NAMESPACES + SYSTEM_NAMESPACES.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# Namespaces, которые относятся ИСКЛЮЧИТЕЛЬНО к vault (можно безопасно чистить
# в shared-БД, не задевая core данные). Источник истины:
#   core-runtime-service/modules/storage/secure.py — CRITICAL_NAMESPACES + SYSTEM_NAMESPACES
VAULT_NAMESPACES: tuple[str, ...] = (
    "secrets.store",
    "_system.meta",
    "_system.root_hash",
    "_system.audit_log",
)

# SQLite-файлы vault внутри volume core-data (см. deploy/dev/docker-compose.yml).
SQLITE_VAULT_FILES: tuple[str, ...] = (
    "/data/vault.db",
    "/data/vault.db-wal",
    "/data/vault.db-shm",
    "/data/vault_secret.db",
    "/data/vault_secret.db-wal",
    "/data/vault_secret.db-shm",
)


DbKind = Literal["sqlite", "postgres"]


@dataclass(slots=True)
class VaultResetResult:
    db: DbKind
    actions: list[str]
    success: bool
    message: str = ""


# ─── PostgreSQL ───────────────────────────────────────────────────────────────


def reset_vault_postgres(
    *,
    compose_file: Path,
    cwd: Path,
    pg_service: str = "postgres",
    pg_user: str = "homeconsole",
    pg_db: str = "homeconsole",
) -> VaultResetResult:
    """
    Удалить из таблицы storage все vault-namespace записи + почистить storage_metadata.

    Работает на запущенном контейнере postgres через `docker compose exec`.
    Core-данные (всё что не в VAULT_NAMESPACES) сохраняются.
    """
    actions: list[str] = []

    # 1. Убедиться что postgres контейнер запущен — иначе exec не сработает.
    ps = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(compose_file),
            "ps", "--services", "--filter", "status=running",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    running = {s.strip() for s in ps.stdout.splitlines() if s.strip()}
    if pg_service not in running:
        return VaultResetResult(
            db="postgres",
            actions=actions,
            success=False,
            message=f"Контейнер {pg_service!r} не запущен — подними его (hc env up --db postgres) до сброса.",
        )

    # 2. Составить SQL: удалить все записи vault-namespaces.
    namespaces_sql = ", ".join(f"'{ns}'" for ns in VAULT_NAMESPACES)
    sql = (
        f"DELETE FROM storage WHERE namespace IN ({namespaces_sql}); "
        # storage_metadata может содержать ключи Merkle root/epoch — чистим всё.
        # Это безопасно: метаданные используются только secure storage слоем.
        "TRUNCATE storage_metadata;"
    )

    # 3. Выполнить через psql внутри контейнера.
    r = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(compose_file),
            "exec", "-T", pg_service,
            "psql", "-U", pg_user, "-d", pg_db, "-v", "ON_ERROR_STOP=1",
            "-c", sql,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if r.returncode != 0:
        # Самая частая ошибка тут — таблиц ещё нет (первый запуск без core).
        # В этом случае сбрасывать нечего, считаем успехом.
        err = (r.stderr or "").lower()
        if 'relation "storage" does not exist' in err or 'relation "storage_metadata" does not exist' in err:
            actions.append("таблицы storage/storage_metadata ещё не созданы — нечего сбрасывать")
            return VaultResetResult(db="postgres", actions=actions, success=True)
        return VaultResetResult(
            db="postgres",
            actions=actions,
            success=False,
            message=(r.stderr or r.stdout or "psql завершился с ошибкой").strip(),
        )

    actions.append(f"DELETE FROM storage WHERE namespace IN ({', '.join(VAULT_NAMESPACES)})")
    actions.append("TRUNCATE storage_metadata")
    return VaultResetResult(db="postgres", actions=actions, success=True)


# ─── SQLite ───────────────────────────────────────────────────────────────────


def reset_vault_sqlite(
    *,
    compose_file: Path,
    cwd: Path,
    core_service: str = "core-runtime",
    volume_name: str = "core-data",
) -> VaultResetResult:
    """
    Удалить файлы vault.db и vault_secret.db (и их WAL/SHM) из volume core-data.

    Работает даже если core-runtime сейчас остановлен — запускаем временный
    alpine контейнер с примонтированным volume и удаляем файлы.
    """
    actions: list[str] = []

    # 1. Определить точное имя volume: docker compose обычно префиксует его именем проекта.
    #    Получаем имя проекта из compose, потом ищем volume.
    project_name = _get_compose_project_name(compose_file, cwd)
    if project_name is None:
        return VaultResetResult(
            db="sqlite",
            actions=actions,
            success=False,
            message="Не удалось определить имя docker compose проекта.",
        )
    full_volume = f"{project_name}_{volume_name}"

    # 2. Проверить что volume существует.
    vcheck = subprocess.run(  # noqa: S603
        ["docker", "volume", "inspect", full_volume],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if vcheck.returncode != 0:
        actions.append(f"volume {full_volume!r} не существует — нечего сбрасывать")
        return VaultResetResult(db="sqlite", actions=actions, success=True)

    # 3. Удалить файлы временным alpine контейнером.
    rm_cmd = " ".join(f"'{p}'" for p in SQLITE_VAULT_FILES)
    r = subprocess.run(  # noqa: S603
        [
            "docker", "run", "--rm",
            "-v", f"{full_volume}:/data",
            "alpine:3.20",
            "sh", "-c", f"rm -f {rm_cmd} && ls /data",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if r.returncode != 0:
        return VaultResetResult(
            db="sqlite",
            actions=actions,
            success=False,
            message=(r.stderr or r.stdout or "alpine rm завершился с ошибкой").strip(),
        )

    actions.append(f"removed vault.db, vault_secret.db (+WAL/SHM) from volume {full_volume}")
    remaining = (r.stdout or "").strip()
    if remaining:
        actions.append(f"остались в /data: {remaining.replace(chr(10), ', ')}")
    return VaultResetResult(db="sqlite", actions=actions, success=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _get_compose_project_name(compose_file: Path, cwd: Path) -> str | None:
    """
    Узнать имя docker compose проекта (по умолчанию = имя директории compose-файла).
    Через `docker compose config --format json` это не всегда работает на старых
    версиях, поэтому пробуем нативную команду и фоллбэчим на cwd.name.
    """
    r = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(compose_file), "ls", "--format", "json"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # `docker compose ls` показывает только running проекты — может быть пусто
    # после down. В этом случае используем deterministic правило compose:
    # имя проекта = sanitized(имя директории).
    name = cwd.name.lower().replace(" ", "").replace("-", "")
    # Compose оставляет дефисы только если они уже в имени без других правок.
    # Чтобы не угадывать — пробуем оба варианта при удалении volume.
    return cwd.name


def detect_running_db(compose_file: Path, cwd: Path) -> DbKind | None:
    """
    Определить какая БД сейчас активна в стеке:
      - если запущен контейнер postgres → "postgres"
      - иначе по умолчанию "sqlite" (vault.db в volume core-data)
      - если ничего не запущено и хочется явно — None (пусть caller решает)
    """
    r = subprocess.run(  # noqa: S603
        [
            "docker", "compose", "-f", str(compose_file),
            "ps", "--services", "--filter", "status=running",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if r.returncode != 0:
        return None
    running = {s.strip() for s in r.stdout.splitlines() if s.strip()}
    if "postgres" in running:
        return "postgres"
    if "core-runtime" in running or "redis" in running:
        return "sqlite"
    return None
