"""Service catalogue, DB options, dataclasses, and constants for env commands."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hc.constants import KNOWN_ENDPOINTS, QUESTIONARY_STYLE_KWARGS


# ─── Service catalogue ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Svc:
    name: str
    label: str
    default: bool
    compose_profile: str | None = None


# Postgres is NOT listed here — it's selected via the DB radio button, not the service checkbox.
_SERVICES: dict[str, list[_Svc]] = {
    "dev": [
        _Svc("core-runtime", "core-runtime  (Python бэкенд, build из src)",    default=True),
        _Svc("caddy",        "caddy         (edge proxy / статика)",            default=True),
        _Svc("redis",        "redis         (кэш / event bus)",                 default=False),
    ],
    "dev-reload": [
        _Svc("core-runtime",  "core-runtime   (Python hot-reload + watchfiles)", default=True),
        _Svc("caddy",         "caddy          (edge proxy / статика)",           default=True),
        _Svc("redis",         "redis          (кэш / event bus)",                default=False),
        _Svc("frontend-vite", "frontend-vite  (Vite HMR :15173)",               default=False,
             compose_profile="frontend"),
    ],
    "dev-image": [
        _Svc("core-runtime", "core-runtime   (образ из registry)", default=True),
        _Svc("edge",         "edge           (caddy proxy)",        default=True),
        _Svc("redis",        "redis          (кэш / event bus)",   default=False),
        _Svc("platform-web", "platform-web   (фронтенд образ)",    default=False),
    ],
}

_PROFILE_DEFAULT_MODE: dict[str, str] = {
    "base": "dev-reload",
    "backend": "dev-reload",
    "platform": "dev-image",
    "hmr": "dev-reload",
    "full": "dev-image",
}

_PROFILES: dict[str, dict[str, list[str]]] = {
    "base": {
        "dev": ["core-runtime", "caddy"],
        "dev-reload": ["core-runtime", "caddy"],
        "dev-image": ["core-runtime", "edge"],
    },
    "backend": {
        "dev": ["redis", "core-runtime", "caddy"],
        "dev-reload": ["redis", "core-runtime", "caddy"],
        "dev-image": ["redis", "core-runtime", "edge"],
    },
    "platform": {
        "dev-image": ["core-runtime", "edge", "platform-web"],
    },
    "hmr": {
        "dev-reload": ["redis", "core-runtime", "caddy", "frontend-vite"],
    },
    "full": {
        "dev-image": ["redis", "core-runtime", "edge", "platform-web"],
    },
}


# ─── DB options (radio) ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class _DbOption:
    key: str
    label: str
    env: dict[str, str] = field(default_factory=dict)
    service: str | None = None          # extra compose service name
    compose_profile: str | None = None  # docker compose --profile flag for that service


# NOTE: эта опция управляет ТОЛЬКО тем, поднимается ли контейнер postgres.
# Она НЕ переключает RUNTIME_VAULT_STORAGE_TYPE / какой backend использует vault —
# это делает `hc env vault-migrate --to sqlite|postgres` (меняет .env осознанно,
# с переносом данных). Если .env уже настроен на postgres-vault, контейнер
# postgres будет добавлен автоматически независимо от выбора здесь
# (см. _resolve_env_up_plan).
_DB_OPTIONS: list[_DbOption] = [
    _DbOption(
        key="sqlite",
        label="SQLite      (без доп. контейнера; vault-backend берётся из .env)",
    ),
    _DbOption(
        key="postgres",
        label="PostgreSQL  (контейнер postgres, dev порт :15432; нужен для vault-migrate --to postgres)",
        service="postgres",
        compose_profile="postgres",
    ),
]

# Vault DSN по умолчанию: отдельная роль/схема `vault` (search_path=vault),
# физически изолирована от основной схемы `public`.
# См. deploy/dev/postgres-initdb/01-init-vault.sh
VAULT_PG_DSN_DEFAULT = "postgresql://vault:vault@postgres:5432/homeconsole?sslmode=disable"

_DB_KEY_MAP: dict[str, _DbOption] = {o.key: o for o in _DB_OPTIONS}


@dataclass(frozen=True)
class EnvUpPlan:
    mode: str
    service_names: list[str]
    compose_profiles: list[str]
    db_option: _DbOption
    project: "ComposeProject"
    running: set[str]


# ─── Constants ────────────────────────────────────────────────────────────────

_MODE_DEFAULT = "dev-reload"
_MODE_HELP = "dev-reload | dev | dev-image  (без --mode профиль может выбрать режим автоматически)"
_PROFILE_HELP = (
    "Пресет: base | backend | platform | hmr | full  "
    "(без --profile: интерактивный выбор)"
)
_DB_HELP = "sqlite | postgres  (без --db: интерактивный выбор если core-runtime выбран)"

# Container state → Rich color mapping (used in `env ps`, `env status`)
_STATE_COLOR: dict[str, str] = {
    "running":    "green",
    "exited":     "red",
    "dead":       "red",
    "restarting": "yellow",
    "created":    "dim",
    "paused":     "yellow",
}

# Файлы, при изменении которых нужен `--build` (зависимости/образы/compose).
_REBUILD_HINT_RE = re.compile(
    r"(^|/)(requirements.*\.txt|package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock"
    r"|Dockerfile[^/]*|docker-compose[^/]*\.ya?ml)$"
)

# Новые alembic-миграции — нужен restart core-runtime, чтобы они применились.
_MIGRATION_HINT_RE = re.compile(r"(^|/)(alembic|migrations)/versions/.*\.py$")

# Frontend Vite compose override filename
_FRONTEND_VITE_OVERRIDE = "frontend-vite.hc.yml"


# Re-export from constants for backward compat
__all__ = [
    "_Svc", "_SERVICES", "_PROFILE_DEFAULT_MODE", "_PROFILES",
    "_DbOption", "_DB_OPTIONS", "_DB_KEY_MAP", "EnvUpPlan", "VAULT_PG_DSN_DEFAULT",
    "_MODE_DEFAULT", "_MODE_HELP", "_PROFILE_HELP", "_DB_HELP",
    "_STATE_COLOR", "_REBUILD_HINT_RE", "_MIGRATION_HINT_RE",
    "KNOWN_ENDPOINTS",
]
