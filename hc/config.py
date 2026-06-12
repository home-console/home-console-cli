from __future__ import annotations

import fcntl
import os
import threading
from dataclasses import dataclass, field
from typing import Self

from tomlkit import document, parse

from hc.constants import CONFIG_DIR, CONFIG_PATH, DEFAULT_CORE_IMAGE, DEFAULT_HOST, DEFAULT_PORT

_config_lock = threading.RLock()
_cached_config: "Config | None" = None
_cached_mtime: float | None = None


def normalize_deploy_core_mode(mode: str) -> str:
    """Синонимы для deploy.core_mode: `image` совпадает с `hc update --mode image`, не с compose-режимами deploy."""
    m = mode.strip().lower()
    if m == "image":
        return "dev-image"
    return m


def invalidate_config_cache() -> None:
    """Сбросить in-process кэш (тесты, внешние правки файла)."""
    global _cached_config, _cached_mtime
    with _config_lock:
        _cached_config = None
        _cached_mtime = None


@dataclass(slots=True)
class CoreConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str = ""
    refresh_token: str = ""  # session_id cookie from login, used for token refresh
    auth: str = "auto"  # auto|bearer|api-key
    verify_ssl: bool = True
    socket_path: str = ""  # Unix domain socket (RUNTIME_SOCKET_PATH). Если задан — используется вместо HTTP.


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
class WorkspaceConfig:
    """Привязка к локальному монорепо разработчика.

    Когда задано, hc env/core/etc. используют исходники из этой папки
    (`<path>/core-runtime-service`, `<path>/platform-home-console`) вместо
    managed-копий в ~/.local/share/hc. Это позволяет редактировать код
    в IDE и видеть изменения в контейнерах (live volume mount + watchfiles).
    """

    path: str = ""  # абсолютный путь к корню монорепо


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
    last_tag: str = ""  # last successfully deployed tag (for `hc rollback`)
    last_image: str = ""  # last successfully deployed image


@dataclass(slots=True)
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)

    @classmethod
    def load(cls) -> Self:
        global _cached_config, _cached_mtime
        with _config_lock:
            mtime: float | None = None
            if CONFIG_PATH.exists():
                mtime = CONFIG_PATH.stat().st_mtime
                if _cached_config is not None and _cached_mtime == mtime:
                    return _cached_config

            inst = cls._load_unlocked()
            _cached_config = inst
            _cached_mtime = mtime
            return inst

    @classmethod
    def _load_unlocked(cls) -> Self:
        if not CONFIG_PATH.exists():
            return cls()
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                raw = fh.read()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        try:
            data = parse(raw)
        except Exception as exc:  # noqa: BLE001
            from rich.console import Console as _Console
            _Console().print(
                f"[red]Ошибка:[/red] конфиг повреждён: {CONFIG_PATH}\n"
                f"  [dim]{exc}[/dim]\n"
                f"  Исправь файл или удали его (будет пересоздан с дефолтами)."
            )
            raise SystemExit(1) from exc
        core = data.get("core", {})
        display = data.get("display", {})
        recovery = data.get("recovery", {})
        deploy = data.get("deploy", {})
        workspace = data.get("workspace", {})
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
                socket_path=str(core.get("socket_path", "")),
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
                last_tag=str(deploy.get("last_tag", "")),
                last_image=str(deploy.get("last_image", "")),
            ),
            workspace=WorkspaceConfig(
                path=str(workspace.get("path", "")),
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
            "socket_path": self.core.socket_path,
        }
        doc["display"] = {"color": self.display.color, "emoji": self.display.emoji}
        doc["recovery"] = {"mode": self.recovery.mode}
        doc["deploy"] = {
            "core_image": self.deploy.core_image,
            "core_mode": self.deploy.core_mode,
            "ssh": self.deploy.ssh,
            "path": self.deploy.path,
            "last_tag": self.deploy.last_tag,
            "last_image": self.deploy.last_image,
        }
        doc["workspace"] = {"path": self.workspace.path}
        payload = doc.as_string()
        tmp_path = CONFIG_PATH.with_suffix(".toml.tmp")
        with _config_lock:
            with tmp_path.open("w", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            os.replace(tmp_path, CONFIG_PATH)
            try:
                CONFIG_PATH.chmod(0o600)
            except OSError:
                pass
            global _cached_config, _cached_mtime
            _cached_config = self
            _cached_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None

    def is_configured(self) -> bool:
        return bool(self.core.host.strip()) and bool(self.core.token.strip())
