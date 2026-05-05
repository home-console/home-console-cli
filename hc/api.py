from __future__ import annotations

# Здесь живут все “контрактные” пути, чтобы не размазывать строки по коду.
# Если Core переедет на /api/v1 — клиент должен пережить это без правок команд.

API_PREFIX_CANDIDATES: tuple[str, ...] = ("/api", "/api/v1")

# Core
HEALTH = "/health"
# Current health endpoint in core-runtime-service (mounted under /api/v1/monitor).
MONITOR_HEALTH = "/api/v1/monitor/health"
# Legacy/compat (older builds).
MONITOR_HEALTH_LEGACY = "/monitor/health"

# Admin (core API contract)
ADMIN_STATUS = "/api/v1/admin/inspector/runtime"
ADMIN_INSPECTOR_PLUGINS = "/api/v1/admin/inspector/plugins"
ADMIN_AUTH_API_KEYS = "/api/v1/admin/auth/api-keys"
ADMIN_AUTH_API_KEYS_REVOKE = "/api/v1/admin/auth/api-keys/revoke"
ADMIN_AUTH_API_KEYS_ROTATE = "/api/v1/admin/auth/api-keys/rotate"
ADMIN_AUTH_USERS = "/api/v1/admin/auth/users"
ADMIN_AUTH_SESSIONS = "/api/v1/admin/auth/sessions"
ADMIN_AUTH_SESSIONS_REVOKE = "/api/v1/admin/auth/sessions/revoke"
ADMIN_AUTH_SESSIONS_REVOKE_ALL = "/api/v1/admin/auth/sessions/revoke-all"

# Auth v1
AUTH_BOOTSTRAP = "/api/v1/auth/bootstrap"
AUTH_INITIALIZE = "/api/v1/auth/initialize"
AUTH_LOGIN = "/api/v1/auth/login"
AUTH_LOGOUT = "/api/v1/auth/logout"
AUTH_REFRESH = "/api/v1/auth/refresh"
AUTH_ME = "/api/v1/auth/me"

# Plugins
PLUGINS = "/api/v1/plugins"
PLUGIN = "/api/v1/plugins/{name}"
PLUGIN_INSTALL = "/api/v1/plugins/{name}/install"
PLUGIN_START = "/api/v1/plugins/{name}/start"
PLUGIN_STOP = "/api/v1/plugins/{name}/stop"
PLUGIN_RELOAD = "/api/v1/admin/plugins/{name}/reload"
PLUGIN_RESTART_CONTAINER = "/api/v1/admin/plugins/{name}/restart-container"

# Modules
MODULES = "/api/v1/modules"
MODULE_START = "/api/v1/modules/{name}/start"
MODULE_STOP = "/api/v1/modules/{name}/stop"
MODULE_RESTART = "/api/v1/modules/{name}/restart"

# Logs
LOGS = "/api/v1/logs"

# Marketplace
MARKETPLACE_INDEX = "/api/v1/marketplace/index"
MARKETPLACE_SEARCH = "/api/v1/marketplace/search"

