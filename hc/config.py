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
    refresh_token: str = ""  # session_id cookie from login, used for token refresh
    auth: str = "auto"  # auto|bearer|api-key
    verify_ssl: bool = True


@dataclass(slots=True)
class DisplayConfig:
    color: bool = True
    emoji: bool = True


@dataclass(slots=True)
class RecoveryConfig:
    # dev: compose c build (docker-compose.yml)
    # image: compose c image (docker-compose.image.yml)
    mode: str = "dev"


@dataclass(slots=True)
class DeployConfig:
    # defaults for `hc deploy core ...`
    core_image: str = "dev-core-runtime"
    core_mode: str = "image"  # dev|image
    ssh: str = ""  # user@host
    path: str = ""  # remote path with compose


@dataclass(slots=True)
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)

    @classmethod
    def load(cls) -> Self:
        if not CONFIG_PATH.exists():
            return cls()
        data = parse(CONFIG_PATH.read_text(encoding="utf-8"))
        core = data.get("core", {})
        display = data.get("display", {})
        recovery = data.get("recovery", {})
        deploy = data.get("deploy", {})
        return cls(
            core=CoreConfig(
                host=str(core.get("host", DEFAULT_HOST)),
                port=int(core.get("port", DEFAULT_PORT)),
                token=str(core.get("token", "")),
                refresh_token=str(core.get("refresh_token", "")),
                auth=str(core.get("auth", "auto")),
                verify_ssl=bool(core.get("verify_ssl", True)),
            ),
            display=DisplayConfig(
                color=bool(display.get("color", True)),
                emoji=bool(display.get("emoji", True)),
            ),
            recovery=RecoveryConfig(
                mode=str(recovery.get("mode", "dev")),
            ),
            deploy=DeployConfig(
                core_image=str(deploy.get("core_image", "dev-core-runtime")),
                core_mode=str(deploy.get("core_mode", "image")),
                ssh=str(deploy.get("ssh", "")),
                path=str(deploy.get("path", "")),
            ),
        )

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        doc = document()
        doc["core"] = {
            "host": self.core.host,
            "port": self.core.port,
            "token": self.core.token,
            "refresh_token": self.core.refresh_token,
            "auth": self.core.auth,
            "verify_ssl": self.core.verify_ssl,
        }
        doc["display"] = {"color": self.display.color, "emoji": self.display.emoji}
        doc["recovery"] = {"mode": self.recovery.mode}
        doc["deploy"] = {
            "core_image": self.deploy.core_image,
            "core_mode": self.deploy.core_mode,
            "ssh": self.deploy.ssh,
            "path": self.deploy.path,
        }
        CONFIG_PATH.write_text(doc.as_string(), encoding="utf-8")

    def is_configured(self) -> bool:
        return bool(self.core.host.strip()) and bool(self.core.token.strip())

