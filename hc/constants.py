from __future__ import annotations

from pathlib import Path

APP_NAME = "HomeConsole CLI"
APP_VERSION = "1.0"
ENV_TOKEN = "HC_TOKEN"

CONFIG_DIR = Path.home() / ".config" / "hc"
CONFIG_PATH = CONFIG_DIR / "config.toml"
HISTORY_PATH = CONFIG_DIR / "history"
SETUP_LOG_PATH = CONFIG_DIR / "setup.log"
SETUP_PID_PATH = CONFIG_DIR / "setup.pid"

DATA_DIR = Path.home() / ".local" / "share" / "hc"
STATE_DIR = Path.home() / ".local" / "state" / "hc"
CORE_SRC_DIR = DATA_DIR / "core-runtime-service"

# Нативный `hc core up --mode native`: PID и лог процесса `python main.py`.
NATIVE_CORE_PID_PATH = STATE_DIR / "native-core.pid"
NATIVE_CORE_LOG_PATH = STATE_DIR / "native-core.log"

DEFAULT_CORE_REPO = "https://github.com/home-console/core-runtime-service"
DEFAULT_CORE_REF = "master"

# Образ для prod / dev-image rollout (Ghcr). Локально собранный тег задаёт через --image.
DEFAULT_CORE_IMAGE = "ghcr.io/home-console/core-runtime"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080

API_PREFIX = "/api"

