from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from hc.config import Config
from hc.core_ops import compose_project_from_source
from hc.core_source import CoreSource


def base_compose_file(compose_dir: Path) -> Path:
    mode = (Config.load().recovery.mode or "dev").strip().lower()
    if mode == "image":
        return compose_dir / "docker-compose.image.yml"
    return compose_dir / "docker-compose.yml"


def compose_file_args(compose_dir: Path, base_compose: Path | None = None) -> list[str]:
    """
    Возвращает список аргументов `-f ...` для docker compose.
    Если рядом лежит `docker-compose.recovery.yml`, он подхватывается автоматически.
    """
    base = base_compose or base_compose_file(compose_dir)
    args = ["-f", str(base)]
    override = compose_dir / "docker-compose.recovery.yml"
    if override.exists():
        args += ["-f", str(override)]
    return args


def override_compose_path(console: Console, src: CoreSource) -> Path:
    project = compose_project_from_source(console, src)
    return project.cwd / "docker-compose.recovery.yml"


def read_env_kv(p: Path) -> dict[str, str]:
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln or ln.lstrip().startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def upsert_env(p: Path, updates: dict[str, str]) -> None:
    lines: list[str] = []
    existing = set()
    if p.exists():
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        raw = []
    for ln in raw:
        if ln and not ln.lstrip().startswith("#") and "=" in ln:
            k, _ = ln.split("=", 1)
            key = k.strip()
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                existing.add(key)
                continue
        lines.append(ln)
    for k, v in updates.items():
        if k not in existing:
            lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_env_keys(p: Path, keys: set[str]) -> None:
    if not p.exists():
        return
    out: list[str] = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln and not ln.lstrip().startswith("#") and "=" in ln:
            k, _ = ln.split("=", 1)
            if k.strip() in keys:
                continue
        out.append(ln)
    p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def container_env_script(keys: list[str]) -> str:
    # печатаем KEY=VALUE (даже если пусто)
    return "; ".join(["echo " + k + "=${" + k + "-}" for k in keys])


def sh_quote(s: str) -> str:
    return shlex.quote(s)

