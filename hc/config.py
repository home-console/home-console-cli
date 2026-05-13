from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from tomlkit import document, parse

from hc.constants import CONFIG_DIR, CONFIG_PATH, DEFAULT_CORE_IMAGE, DEFAULT_HOST, DEFAULT_PORT


def normalize_deploy_core_mode(mode: str) -> str:
    """Синонимы для deploy.core_mode: `image` совпадает с `hc update --mode image`, не с compose-режимами deploy."""
    m = mode.strip().lower()
    if m == "image":
        return "dev-image"
    return m


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
    # Режим compose для `hc core up/down/logs` (локальная разработка / recovery).
    # dev        → deploy/dev/docker-compose.yml          (build из src)
    # dev-reload → deploy/dev/docker-compose.reload.yml   (build + hot-reload)
    # dev-image  → deploy/dev/docker-compose.image.yml    (образ + dev-инфра)
    # prod       → deploy/prod/docker-compose.image.yml   (образ из registry)
    mode: str = "dev"


@dataclass(slots=True)
class DeployConfig:
    # Режим compose для `hc deploy core ...` (deploy-пайплайн: build→push→rollout).
    # dev-image  → deploy/dev/docker-compose.image.yml    (образ + dev-инфра)
    # prod       → deploy/prod/docker-compose.image.yml   (образ из registry, PROD!)
    # dev        → deploy/dev/docker-compose.yml          (build из src; только local)
    # dev-reload → deploy/dev/docker-compose.reload.yml   (hot-reload; только local)
    core_image: str = DEFAULT_CORE_IMAGE  # например ghcr.io/home-console/core-runtime
    core_mode: str = "dev-image"  # dev | dev-reload | dev-image | prod
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
        raw_core_mode = str(deploy.get("core_mode", "dev-image"))
        core_mode = normalize_deploy_core_mode(raw_core_mode)
        migrate_legacy_image = raw_core_mode.strip().lower() == "image"
        inst = cls(
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
                core_image=str(deploy.get("core_image", DEFAULT_CORE_IMAGE)),
                core_mode=core_mode,
                ssh=str(deploy.get("ssh", "")),
                path=str(deploy.get("path", "")),
            ),
        )
        if migrate_legacy_image and CONFIG_PATH.exists():
            inst.save()
        return inst

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

