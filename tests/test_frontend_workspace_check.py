"""Тесты для _ensure_frontend_workspace — автоклон platform-home-console перед env up."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer

import hc.commands.env._register as env_mod


def _make_plan(tmp_path: Path, services: list[str]) -> SimpleNamespace:
    compose_cwd = tmp_path / "core-runtime-service" / "deploy" / "dev"
    compose_cwd.mkdir(parents=True, exist_ok=True)
    project = SimpleNamespace(
        cwd=compose_cwd,
        compose_file=compose_cwd / "docker-compose.reload.yml",
    )
    return SimpleNamespace(
        service_names=list(services),
        compose_profiles=["frontend"] if "frontend-vite" in services else [],
        project=project,
    )


def test_skip_when_no_frontend_vite(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, ["core-runtime", "caddy"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == set()
    assert "frontend-vite" not in plan.service_names


def test_pass_when_workspace_with_package_json_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "platform-home-console"
    workspace.mkdir()
    (workspace / "package.json").write_text("{}")

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == set()
    assert "frontend-vite" in plan.service_names


def test_auto_clones_when_missing_without_dialog(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_init(console, target=None, **kw):
        captured["target"] = target
        target.mkdir(parents=True, exist_ok=True)
        (target / "package.json").write_text('{"name":"platform"}')
        return target

    monkeypatch.setattr(env_mod, "init_platform_source", fake_init)

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == {"frontend-vite"}
    assert "frontend-vite" in plan.service_names
    assert captured["target"] == tmp_path / "platform-home-console"


def test_auto_clones_empty_dir_without_dialog(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "platform-home-console"
    workspace.mkdir()

    def fake_init(console, target=None, **kw):
        (target / "package.json").write_text("{}")
        return target

    monkeypatch.setattr(env_mod, "init_platform_source", fake_init)

    plan = _make_plan(tmp_path, ["frontend-vite"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == {"frontend-vite"}


def test_clone_failure_strips_frontend(tmp_path: Path, monkeypatch) -> None:
    def fake_init(console, target=None, **kw):
        raise typer.Exit(code=1)

    monkeypatch.setattr(env_mod, "init_platform_source", fake_init)

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == set()
    assert "frontend-vite" not in plan.service_names


def test_non_tty_strips_when_cannot_clone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(env_mod.sys.stdin, "isatty", lambda: False)
    workspace = tmp_path / "platform-home-console"
    workspace.mkdir()
    (workspace / "README").write_text("not a frontend repo")

    plan = _make_plan(tmp_path, ["frontend-vite"])
    ok, recreate = env_mod._ensure_frontend_workspace(MagicMock(), plan)
    assert ok is True
    assert recreate == set()
    assert "frontend-vite" not in plan.service_names


def test_frontend_workspace_path(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, ["core-runtime"])
    assert env_mod._frontend_workspace_path(plan) == tmp_path / "platform-home-console"
