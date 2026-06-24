"""
Emergency direct DB access — работает без запущенного Core API.

Читает RUNTIME_DB_PATH из .env Core (дефолт: data/runtime.db относительно core root).
Схема: одна таблица storage (namespace TEXT, key TEXT, value TEXT/JSON).

Пароли: bcrypt (модуль modules/api/auth/passwords.py в Core).
Namespaces: auth_users, auth_sessions, auth_api_keys (см. modules/api/auth/constants.py).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import bcrypt


AUTH_USERS_NS = "auth_users"
AUTH_SESSIONS_NS = "auth_sessions"
AUTH_API_KEYS_NS = "auth_api_keys"

MARKETPLACE_NS = "marketplace"
MARKETPLACE_INSTALLED_KEY = "installed"


def resolve_db_path(core_root: Path) -> Path:
    """Найти файл БД: RUNTIME_DB_PATH из .env или дефолт data/runtime.db."""
    from hc.native_core import parse_dotenv_file

    env_file = core_root / ".env"
    env = parse_dotenv_file(env_file)
    db_rel = env.get("RUNTIME_DB_PATH", "data/runtime.db").strip()
    db_path = Path(db_rel)
    if not db_path.is_absolute():
        db_path = core_root / db_path
    return db_path


def _connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(
            f"БД не найдена: {db_path}\n"
            "Убедись что Core запускался хотя бы один раз и путь верный."
        )
    conn = sqlite3.connect(f"file:{db_path}" + ("?mode=ro" if readonly else ""), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get(conn: sqlite3.Connection, namespace: str, key: str) -> Any:
    row = conn.execute(
        "SELECT value FROM storage WHERE namespace=? AND key=?", (namespace, key)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Повреждённые данные в БД (namespace={namespace!r}, key={key!r}): {exc}"
        ) from exc


def _set(conn: sqlite3.Connection, namespace: str, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO storage (namespace, key, value) VALUES (?, ?, ?)",
        (namespace, key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def _list_keys(conn: sqlite3.Connection, namespace: str) -> list[str]:
    rows = conn.execute(
        "SELECT key FROM storage WHERE namespace=?", (namespace,)
    ).fetchall()
    return [r["key"] for r in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_users(db_path: Path) -> list[dict[str, Any]]:
    """Вернуть список пользователей напрямую из БД."""
    with _connect(db_path, readonly=True) as conn:
        keys = _list_keys(conn, AUTH_USERS_NS)
        users = []
        for uid in keys:
            data = _get(conn, AUTH_USERS_NS, uid)
            if isinstance(data, dict):
                users.append({
                    "user_id": uid,
                    "username": data.get("username", ""),
                    "is_admin": bool(data.get("is_admin", False)),
                    "has_password": bool(data.get("password_hash")),
                    "created_at": data.get("created_at"),
                })
        return users


def list_sessions(db_path: Path) -> list[dict[str, Any]]:
    """Вернуть список активных сессий напрямую из БД."""
    with _connect(db_path, readonly=True) as conn:
        keys = _list_keys(conn, AUTH_SESSIONS_NS)
        sessions = []
        for sid in keys:
            data = _get(conn, AUTH_SESSIONS_NS, sid)
            if isinstance(data, dict):
                sessions.append({
                    "session_id": sid[:16] + "…",
                    "user_id": data.get("user_id", ""),
                    "created_at": data.get("created_at"),
                    "expires_at": data.get("expires_at"),
                })
        return sessions


def list_api_keys(db_path: Path) -> list[dict[str, Any]]:
    """Вернуть список API-ключей напрямую из БД."""
    with _connect(db_path, readonly=True) as conn:
        keys = _list_keys(conn, AUTH_API_KEYS_NS)
        result = []
        for kid in keys:
            data = _get(conn, AUTH_API_KEYS_NS, kid)
            if isinstance(data, dict):
                result.append({
                    "key_id": kid,
                    "name": data.get("name", ""),
                    "user_id": data.get("user_id", ""),
                    "created_at": data.get("created_at"),
                    "revoked": bool(data.get("revoked", False)),
                })
        return result


def inspect_storage(db_path: Path) -> dict[str, int]:
    """Вернуть количество записей по всем namespace."""
    with _connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT namespace, COUNT(*) AS cnt FROM storage GROUP BY namespace ORDER BY namespace"
        ).fetchall()
        return {r["namespace"]: r["cnt"] for r in rows}


def reset_password(db_path: Path, user_id: str, new_password: str) -> None:
    """Сбросить пароль пользователя напрямую в БД (bcrypt, как в Core).

    Это emergency-операция: валидация политики пароля не применяется.
    После сброса все сессии пользователя рекомендуется инвалидировать вручную.
    """
    with _connect(db_path) as conn:
        data = _get(conn, AUTH_USERS_NS, user_id)
        if data is None:
            raise ValueError(f"Пользователь {user_id!r} не найден в БД.")
        if not isinstance(data, dict):
            raise ValueError(f"Некорректные данные пользователя {user_id!r}.")

        password_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        data["password_hash"] = password_hash
        data["password_set_at"] = time.time()
        data["emergency_reset_at"] = time.time()

        _set(conn, AUTH_USERS_NS, user_id, data)


def list_marketplace_plugins(db_path: Path) -> dict[str, Any]:
    """Вернуть словарь установленных плагинов из marketplace storage.

    Ключ — имя плагина, значение — dict с полями name, version, enabled, …
    Пробует оба формата хранилища:
      - namespace='marketplace', key='installed'  (новый формат)
      - namespace='marketplace.installed', key=<plugin_name>  (legacy)
    """
    with _connect(db_path, readonly=True) as conn:
        data = _get(conn, MARKETPLACE_NS, MARKETPLACE_INSTALLED_KEY)
        if isinstance(data, dict) and data:
            return data
        # Legacy: все строки namespace='marketplace.installed'
        rows = conn.execute(
            "SELECT key, value FROM storage WHERE namespace=?",
            (f"{MARKETPLACE_NS}.{MARKETPLACE_INSTALLED_KEY}",),
        ).fetchall()
        if rows:
            result: dict[str, Any] = {}
            for row in rows:
                try:
                    result[row["key"]] = json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return result
        return {}


def disable_plugin(db_path: Path, plugin_name: str) -> None:
    """Пометить плагин как disabled в marketplace storage (emergency, без API).

    После этого при следующем запуске Core не загрузит плагин.
    """
    with _connect(db_path) as conn:
        # Новый формат: одна строка namespace='marketplace', key='installed'
        data = _get(conn, MARKETPLACE_NS, MARKETPLACE_INSTALLED_KEY)
        if isinstance(data, dict) and data:
            if plugin_name not in data:
                raise ValueError(
                    f"Плагин {plugin_name!r} не найден. "
                    f"Установленные: {', '.join(sorted(data)) or '(нет)'}"
                )
            data[plugin_name]["enabled"] = False
            data[plugin_name]["disabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            data[plugin_name]["emergency_disabled"] = True
            _set(conn, MARKETPLACE_NS, MARKETPLACE_INSTALLED_KEY, data)
            return

        # Legacy: отдельная строка на плагин
        legacy_ns = f"{MARKETPLACE_NS}.{MARKETPLACE_INSTALLED_KEY}"
        plugin_data = _get(conn, legacy_ns, plugin_name)
        if plugin_data is None:
            raise ValueError(
                f"Плагин {plugin_name!r} не найден ни в новом, ни в legacy формате хранилища."
            )
        if not isinstance(plugin_data, dict):
            raise ValueError(f"Повреждённые данные плагина {plugin_name!r}.")
        plugin_data["enabled"] = False
        plugin_data["disabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        plugin_data["emergency_disabled"] = True
        _set(conn, legacy_ns, plugin_name, plugin_data)


def unlock_db(db_path: Path) -> dict[str, bool]:
    """Удалить WAL/SHM файлы SQLite (только когда Core остановлен!).

    Нужно когда процесс завис и оставил БД в locked/WAL-режиме.
    Возвращает dict с именами удалённых файлов.
    """
    removed: dict[str, bool] = {}
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db_path) + suffix)
        if extra.exists():
            extra.unlink()
            removed[extra.name] = True
    return removed


def revoke_all_user_sessions(db_path: Path, user_id: str) -> int:
    """Удалить все сессии пользователя из БД. Возвращает количество удалённых."""
    with _connect(db_path) as conn:
        keys = _list_keys(conn, AUTH_SESSIONS_NS)
        removed = 0
        for sid in keys:
            data = _get(conn, AUTH_SESSIONS_NS, sid)
            if isinstance(data, dict) and data.get("user_id") == user_id:
                conn.execute(
                    "DELETE FROM storage WHERE namespace=? AND key=?",
                    (AUTH_SESSIONS_NS, sid),
                )
                removed += 1
        conn.commit()
        return removed
