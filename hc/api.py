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

# Marketplace admin — archive_path должен быть доступен процессу Core на сервере
ADMIN_MARKETPLACE_INSTALL = "/api/v1/admin/marketplace/install"
ADMIN_MARKETPLACE_INSTALL_UPLOAD = "/api/v1/admin/marketplace/install-upload"

# Auth v1
AUTH_BOOTSTRAP = "/api/v1/auth/bootstrap"
AUTH_INITIALIZE = "/api/v1/auth/initialize"
AUTH_LOGIN = "/api/v1/auth/login"
AUTH_LOGOUT = "/api/v1/auth/logout"
AUTH_REFRESH = "/api/v1/auth/refresh"
AUTH_ME = "/api/v1/auth/me"

# Plugins (пути относительно префикса из API_PREFIX_CANDIDATES: /api или /api/v1)
PLUGINS = "/plugins"
PLUGIN = "/plugins/{name}"
PLUGIN_INSTALL = "/plugins/{name}/install"
PLUGIN_START = "/plugins/{name}/start"
PLUGIN_STOP = "/plugins/{name}/stop"
PLUGIN_RELOAD = "/api/v1/admin/plugins/{name}/reload"
PLUGIN_RESTART_CONTAINER = "/api/v1/admin/plugins/{name}/restart-container"

# Modules (так же относительно префикса)
MODULES = "/modules"
MODULE_START = "/modules/{name}/start"
MODULE_STOP = "/modules/{name}/stop"
MODULE_RESTART = "/modules/{name}/restart"

# Logs
LOGS = "/logs"

# Marketplace
MARKETPLACE_INDEX = "/marketplace/index"
MARKETPLACE_SEARCH = "/marketplace/search"

