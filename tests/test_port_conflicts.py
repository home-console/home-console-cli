"""Тесты детекции и разрешения конфликтов портов."""
from __future__ import annotations

import json
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
        service_names=["core-runtime", "caddy"],
        compose_profiles=[],
        project=project,
    )


def test_parse_published_ports() -> None:
    ports = "0.0.0.0:18000->8000/tcp, [::]:18000->8000/tcp"
    assert env_mod._parse_published_ports(ports) == {18000}


def test_parse_docker_labels_string() -> None:
    labels = "com.docker.compose.project=dev,com.docker.compose.service=core-runtime"
    parsed = env_mod._parse_docker_labels(labels)
    assert parsed["com.docker.compose.project"] == "dev"
    assert parsed["com.docker.compose.service"] == "core-runtime"


def test_find_port_conflicts_detects_host_process(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(env_mod, "_compose_project_name", lambda p: "dev")
    monkeypatch.setattr(env_mod, "_find_host_listeners", lambda port: [
        {"pid": 4242, "command": "python3 main.py"},
    ])

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)

    conflicts = env_mod._find_port_conflicts({18000: "core-runtime"}, plan)
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "process"
    assert conflicts[0]["pid"] == 4242


def test_find_port_conflicts_skips_legit_running_service(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(env_mod, "_compose_project_name", lambda p: "dev")
    monkeypatch.setattr(env_mod, "_find_host_listeners", lambda port: [])

    container = {
        "ID": "abc123",
        "Names": "dev-hc-core-runtime",
        "Image": "core-runtime:dev",
        "Ports": "0.0.0.0:18000->8000/tcp",
        "Labels": "com.docker.compose.project=dev,com.docker.compose.service=core-runtime",
    }

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(returncode=0, stdout=json.dumps(container) + "\n")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)

    conflicts = env_mod._find_port_conflicts({18000: "core-runtime"}, plan)
    assert conflicts == []


def test_find_port_conflicts_detects_foreign_container(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(env_mod, "_compose_project_name", lambda p: "dev")
    monkeypatch.setattr(env_mod, "_find_host_listeners", lambda port: [])

    container = {
        "ID": "deadbeef1234",
        "Names": "old-core",
        "Image": "core-runtime:old",
        "Ports": "0.0.0.0:18000->8000/tcp",
        "Labels": "com.docker.compose.project=other",
    }

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(returncode=0, stdout=json.dumps(container) + "\n")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)

    conflicts = env_mod._find_port_conflicts({18000: "core-runtime"}, plan)
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "docker"
    assert conflicts[0]["name"] == "old-core"


def test_kill_process(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        killed.append((pid, sig))
        return None

    import signal

    monkeypatch.setattr(env_mod.os, "kill", fake_kill)
    assert env_mod._kill_process(99, signal_name="term") is True
    assert killed == [(99, signal.SIGTERM)]
