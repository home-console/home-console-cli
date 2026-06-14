from __future__ import annotations

from pathlib import Path

APP_NAME = "HomeConsole CLI"
ENV_TOKEN = "HC_TOKEN"

CONFIG_DIR = Path.home() / ".config" / "hc"
CONFIG_PATH = CONFIG_DIR / "config.toml"
HISTORY_PATH = CONFIG_DIR / "history"
SETUP_LOG_PATH = CONFIG_DIR / "setup.log"
SETUP_PID_PATH = CONFIG_DIR / "setup.pid"

DATA_DIR = Path.home() / ".local" / "share" / "hc"
STATE_DIR = Path.home() / ".local" / "state" / "hc"
CORE_SRC_DIR = DATA_DIR / "core-runtime-service"
PLATFORM_SRC_DIR = DATA_DIR / "platform-home-console"

# Нативный `hc core up --mode native`: PID и лог процесса `python main.py`.
NATIVE_CORE_PID_PATH = STATE_DIR / "native-core.pid"
NATIVE_CORE_LOG_PATH = STATE_DIR / "native-core.log"

DEFAULT_CORE_REPO = "https://github.com/home-console/core-runtime-service"
DEFAULT_CORE_REF = "master"

DEFAULT_PLATFORM_REPO = "https://github.com/home-console/platform-home-console"
DEFAULT_PLATFORM_REF = "master"

# Образ для prod / dev-image rollout (Ghcr). Локально собранный тег задаёт через --image.
DEFAULT_CORE_IMAGE = "ghcr.io/home-console/core-runtime-service"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080

API_PREFIX = "/api"

# Dev stack ports (prefix "1" for dev, standard for prod)
PORT_CORE_RUNTIME = 18000
PORT_CADDY = 18080
PORT_EDGE = 8080
PORT_FRONTEND_VITE = 15173
PORT_POSTGRES = 15432
PORT_REDIS = 16379
PORT_PLATFORM_WEB = 3000

# Known service → URL mappings for `hc env ps` / status
KNOWN_ENDPOINTS: dict[str, str] = {
    "core-runtime": f"http://localhost:{PORT_CORE_RUNTIME}",
    "caddy": f"http://localhost:{PORT_CADDY}",
    "edge": f"http://localhost:{PORT_EDGE}",
    "frontend-vite": f"http://localhost:{PORT_FRONTEND_VITE}",
    "postgres": f"localhost:{PORT_POSTGRES}",
    "platform-web": f"http://localhost:{PORT_PLATFORM_WEB}",
    "redis": f"localhost:{PORT_REDIS}",
}

# Shared questionary style for interactive prompts
QUESTIONARY_STYLE_KWARGS: dict[str, str] = {
    "qmark": "fg:#00bfff bold",
    "question": "bold",
    "pointer": "fg:#00bfff bold",
    "highlighted": "fg:#00bfff bold",
    "selected": "fg:#00ff00",
    "instruction": "fg:#808080 italic",
}

