from __future__ import annotations

# Здесь живут все “контрактные” пути, чтобы не размазывать строки по коду.
# Если Core переедет на /api/v1 — клиент должен пережить это без правок команд.

API_PREFIX_CANDIDATES: tuple[str, ...] = ("/api", "/api/v1")

# Core
HEALTH = "/health"
MONITOR_HEALTH = "/monitor/health"

# Admin
ADMIN_STATUS = "/admin/v1/status"
ADMIN_INSPECTOR_PLUGINS = "/admin/v1/inspector/plugins"
ADMIN_AUTH_API_KEYS = "/admin/v1/auth/api-keys"
ADMIN_AUTH_API_KEYS_REVOKE = "/admin/v1/auth/api-keys/revoke"
ADMIN_AUTH_API_KEYS_ROTATE = "/admin/v1/auth/api-keys/rotate"

# Auth v1
AUTH_BOOTSTRAP = "/auth/v1/bootstrap"
AUTH_INITIALIZE = "/auth/v1/initialize"
AUTH_LOGIN = "/auth/v1/login"
AUTH_LOGOUT = "/auth/v1/logout"
AUTH_REFRESH = "/auth/v1/refresh"
AUTH_ME = "/auth/v1/me"

# Plugins
PLUGINS = "/plugins"
PLUGIN = "/plugins/{name}"
PLUGIN_INSTALL = "/plugins/{name}/install"
PLUGIN_START = "/plugins/{name}/start"
PLUGIN_STOP = "/plugins/{name}/stop"

# Modules
MODULES = "/modules"

# Logs
LOGS = "/logs"

# Marketplace
MARKETPLACE_INDEX = "/marketplace/index"
MARKETPLACE_SEARCH = "/marketplace/search"

