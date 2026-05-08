from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hc.core_source import CoreSource


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _patch_deploy_prereqs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _c: None)
    monkeypatch.setattr(
        "hc.commands.deploy._resolve_source",
        lambda _c: CoreSource(path=tmp_path),
    )


def test_deploy_core_rollout_ssh_requires_path(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_deploy_prereqs(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(
        app,
        ["deploy", "core", "rollout", "--ssh", "u@h", "--no-wait", "--mode", "dev"],
    )
    assert r.exit_code == 2
    assert "path" in r.output.lower()


def test_deploy_core_wait_ssh_requires_path(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_deploy_prereqs(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["deploy", "core", "wait", "--ssh", "u@h", "--mode", "dev"])
    assert r.exit_code == 2


def test_deploy_core_logs_ssh_requires_path(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_deploy_prereqs(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["deploy", "core", "logs", "--ssh", "u@h", "--mode", "dev"])
    assert r.exit_code == 2


def test_deploy_core_rollout_rejects_bad_mode(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_deploy_prereqs(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(
        app, ["deploy", "core", "rollout", "--mode", "not-a-deploy-mode", "--no-wait"]
    )
    assert r.exit_code == 2
    assert "mode" in r.output.lower()
