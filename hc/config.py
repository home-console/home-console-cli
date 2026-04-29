from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from tomlkit import document, parse

from hc.constants import CONFIG_DIR, CONFIG_PATH, DEFAULT_HOST, DEFAULT_PORT


@dataclass(slots=True)
class CoreConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str = ""
    auth: str = "auto"  # auto|bearer|api-key
    verify_ssl: bool = True


@dataclass(slots=True)
class DisplayConfig:
    color: bool = True
    emoji: bool = True


@dataclass(slots=True)
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)

    @classmethod
    def load(cls) -> Self:
        if not CONFIG_PATH.exists():
            return cls()
        data = parse(CONFIG_PATH.read_text(encoding="utf-8"))
        core = data.get("core", {})
        display = data.get("display", {})
        return cls(
            core=CoreConfig(
                host=str(core.get("host", DEFAULT_HOST)),
                port=int(core.get("port", DEFAULT_PORT)),
                token=str(core.get("token", "")),
                auth=str(core.get("auth", "auto")),
                verify_ssl=bool(core.get("verify_ssl", True)),
            ),
            display=DisplayConfig(
                color=bool(display.get("color", True)),
                emoji=bool(display.get("emoji", True)),
            ),
        )

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        doc = document()
        doc["core"] = {
            "host": self.core.host,
            "port": self.core.port,
            "token": self.core.token,
            "auth": self.core.auth,
            "verify_ssl": self.core.verify_ssl,
        }
        doc["display"] = {"color": self.display.color, "emoji": self.display.emoji}
        CONFIG_PATH.write_text(doc.as_string(), encoding="utf-8")

    def is_configured(self) -> bool:
        return bool(self.core.host.strip()) and bool(self.core.token.strip())

