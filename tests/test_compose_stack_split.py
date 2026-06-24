"""Тесты детекции расщепления compose-стека в Docker Desktop."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hc.commands.env._diagnostics as diag_mod
from hc.commands.env._compose import planned_config_files_from_cmd


def _make_plan(tmp_path: Path, *, services: list[str] | None = None) -> SimpleNamespace:
    compose_cwd = tmp_path / "core-runtime-service" / "deploy" / "dev"
    compose_cwd.mkdir(parents=True, exist_ok=True)
    compose_file = compose_cwd / "docker-compose.reload.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    project = SimpleNamespace(
        cwd=compose_cwd,
        compose_file=compose_file,
    )
    return SimpleNamespace(
        service_names=services or ["core-runtime", "redis"],
        compose_profiles=[],
        project=project,
    )


def _container(
    *,
    project: str = "dev",
    service: str,
    config_files: str,
    state: str = "running",
) -> dict:
    return {
        "Names": f"dev-hc-{service}",
        "State": state,
        "Labels": (
            f"com.docker.compose.project={project},"
            f"com.docker.compose.service={service},"
            f"com.docker.compose.project.config_files={config_files}"
        ),
    }


def test_planned_config_files_from_cmd_resolves_absolute_paths(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    override = tmp_path / "override.yml"
    compose.touch()
    override.touch()
    cmd = ["docker", "compose", "-f", str(compose), "-f", str(override), "up"]
    assert planned_config_files_from_cmd(cmd) == f"{compose.resolve()},{override.resolve()}"


def test_detect_already_split_stack(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    ws_cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    managed_cfg = (
        "/Users/me/.local/share/hc/core-runtime-service/deploy/dev/docker-compose.reload.yml,"
        "/Users/me/.local/share/hc/compose-overrides/frontend-vite.hc.yml"
    )
    containers = [
        _container(service="core-runtime", config_files=ws_cfg),
        _container(service="redis", config_files=managed_cfg),
        _container(service="frontend-vite", config_files=managed_cfg),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=ws_cfg)
    assert issue is not None
    assert issue.already_split is True
    assert issue.would_split is False
    assert len(issue.groups) == 2


def test_detect_would_split_on_config_mismatch(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    existing_cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    planned_cfg = (
        f"{existing_cfg},"
        "/Users/me/HomeConsole/.local/share/hc/compose-overrides/frontend-vite.hc.yml"
    )
    containers = [_container(service="core-runtime", config_files=existing_cfg)]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout=json.dumps(containers[0]) + "\n",
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=planned_cfg)
    assert issue is not None
    assert issue.already_split is False
    assert issue.would_split is True


def test_no_issue_when_single_config(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    containers = [
        _container(service="core-runtime", config_files=cfg),
        _container(service="redis", config_files=cfg),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=cfg)
    assert issue is None


def test_detect_for_project_status_only(tmp_path: Path, monkeypatch) -> None:
    cfg_a = "/tmp/a/deploy/dev/docker-compose.reload.yml"
    cfg_b = "/tmp/b/deploy/dev/docker-compose.reload.yml"
    containers = [
        _container(service="caddy", config_files=cfg_a),
        _container(service="redis", config_files=cfg_b),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split_for_project("dev")
    assert issue is not None
    assert issue.already_split is True
    assert issue.would_split is False
