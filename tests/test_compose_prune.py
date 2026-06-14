"""Тесты пересоздания зависших compose-контейнеров перед env up."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hc.commands.env._register as env_mod


def _make_plan(tmp_path: Path) -> SimpleNamespace:
    compose_cwd = tmp_path / "core-runtime-service" / "deploy" / "dev"
    compose_cwd.mkdir(parents=True, exist_ok=True)
    project = SimpleNamespace(
        cwd=compose_cwd,
        compose_file=compose_cwd / "docker-compose.reload.yml",
    )
    return SimpleNamespace(
        service_names=["core-runtime", "frontend-vite", "redis"],
        compose_profiles=["frontend"],
        project=project,
    )


def test_prune_removes_non_running_services(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(
        env_mod,
        "list_compose_containers",
        lambda *a, **kw: [
            {"Service": "redis", "State": "running"},
            {"Service": "frontend-vite", "State": "exited"},
            {"Service": "core-runtime", "State": "created"},
        ],
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)

    env_mod._prune_non_running_compose_services(MagicMock(), plan)

    assert len(calls) == 1
    assert "rm" in calls[0]
    assert "-sf" in calls[0]
    assert "frontend-vite" in calls[0]
    assert "core-runtime" in calls[0]
    assert "redis" not in calls[0]


def test_prune_force_running_service_when_extra_force(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(
        env_mod,
        "list_compose_containers",
        lambda *a, **kw: [
            {"Service": "frontend-vite", "State": "running"},
        ],
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)

    env_mod._prune_non_running_compose_services(
        MagicMock(), plan, extra_force={"frontend-vite"}
    )

    assert "frontend-vite" in calls[0]
