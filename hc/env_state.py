from __future__ import annotations

import json
from dataclasses import dataclass

from hc.constants import STATE_DIR

LAST_ENV_PATH = STATE_DIR / "last_env.json"


@dataclass(slots=True)
class LastEnvSelection:
    mode: str
    services: list[str]
    db: str


def load_last_env() -> LastEnvSelection | None:
    try:
        data = json.loads(LAST_ENV_PATH.read_text(encoding="utf-8"))
        mode = str(data.get("mode", "")).strip()
        services = data.get("services")
        db = str(data.get("db", "")).strip()
        if not mode or not isinstance(services, list) or not db:
            return None
        names = [str(s).strip() for s in services if str(s).strip()]
        if not names:
            return None
        return LastEnvSelection(mode=mode, services=names, db=db)
    except Exception:
        return None


def save_last_env(*, mode: str, services: list[str], db: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": mode.strip(),
            "services": list(services),
            "db": db.strip(),
        }
        LAST_ENV_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass
