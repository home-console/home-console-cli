"""Тесты для _check_frontend_workspace — превентивная проверка перед env up."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import hc.commands.env as env_mod


def _make_plan(tmp_path: Path, services: list[str]) -> SimpleNamespace:
    """
    Сэмулировать EnvUpPlan: project.cwd должен указывать на deploy/dev,
    тогда _check_frontend_workspace будет искать platform-home-console
    в tmp_path/platform-home-console.
    """
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
    assert env_mod._check_frontend_workspace(MagicMock(), plan) is True
    # План не тронут
    assert "frontend-vite" not in plan.service_names


def test_pass_when_workspace_with_package_json_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "platform-home-console"
    workspace.mkdir()
    (workspace / "package.json").write_text("{}")

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    assert env_mod._check_frontend_workspace(MagicMock(), plan) is True
    # frontend-vite остаётся в плане
    assert "frontend-vite" in plan.service_names


def test_skips_frontend_in_non_tty_when_missing(tmp_path: Path, monkeypatch) -> None:
    """В неинтерактивном режиме (CI) утилита не блокирует запуск,
    а оставляет план как есть — пусть упадёт сам, post-mortem подсветит."""
    monkeypatch.setattr(env_mod.sys.stdin, "isatty", lambda: False)
    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    result = env_mod._check_frontend_workspace(MagicMock(), plan)
    assert result is True
    assert "frontend-vite" in plan.service_names


def _fake_questionary(answer: str):
    """Сэмулировать questionary.select возвращающий заданный ответ."""

    class _Choice:
        def __init__(self, title=None, value=None, **_):
            self.title = title
            self.value = value

    return SimpleNamespace(
        Choice=_Choice,
        select=lambda *a, **kw: SimpleNamespace(ask=lambda: answer),
    )


def test_skip_action_removes_frontend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(env_mod.sys.stdin, "isatty", lambda: True)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "questionary", _fake_questionary("skip"))

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite", "caddy"])
    plan.compose_profiles = ["frontend"]
    result = env_mod._check_frontend_workspace(MagicMock(), plan)
    assert result is True
    assert "frontend-vite" not in plan.service_names
    assert "frontend" not in plan.compose_profiles
    assert "core-runtime" in plan.service_names
    assert "caddy" in plan.service_names


def test_abort_action_blocks_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(env_mod.sys.stdin, "isatty", lambda: True)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "questionary", _fake_questionary("abort"))

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    result = env_mod._check_frontend_workspace(MagicMock(), plan)
    assert result is False


def test_clone_action_invokes_init_platform_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(env_mod.sys.stdin, "isatty", lambda: True)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "questionary", _fake_questionary("clone"))

    captured: dict = {}

    def fake_init(console, target=None, **kw):
        captured["target"] = target
        # Сэмулировать успешный клон: создаём package.json
        target.mkdir(parents=True, exist_ok=True)
        (target / "package.json").write_text("{}")
        return target

    monkeypatch.setattr(env_mod, "init_platform_source", fake_init)

    plan = _make_plan(tmp_path, ["core-runtime", "frontend-vite"])
    result = env_mod._check_frontend_workspace(MagicMock(), plan)
    assert result is True
    # После клона frontend-vite ОСТАЁТСЯ в плане
    assert "frontend-vite" in plan.service_names
    assert captured["target"] == tmp_path / "platform-home-console"
