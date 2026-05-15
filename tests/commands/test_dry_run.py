from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ── install --dry-run ─────────────────────────────────────────────────────────

class _InstallClient:
    async def get_marketplace_index(self):  # noqa: ANN001
        return [{"name": "p1", "version": "1.0", "description": "desc", "dependencies": ["x"]}]

    async def install_plugin(self, name: str) -> AsyncIterator[str]:  # noqa: ANN001
        raise AssertionError("install_plugin must not be called in dry-run")
        if False:  # pragma: no cover
            yield ""


def test_install_dry_run_exits_zero(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _InstallClient())
    from hc.main import app

    res = runner.invoke(app, ["install", "p1", "--dry-run"])
    assert res.exit_code == 0
    assert "dry run" in res.output.lower()


def test_install_dry_run_shows_plugin_info(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _InstallClient())
    from hc.main import app

    res = runner.invoke(app, ["install", "p1", "--dry-run"])
    assert "p1" in res.output
    assert "1.0" in res.output


def test_install_dry_run_does_not_call_install(monkeypatch, runner: CliRunner) -> None:
    called = []

    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return [{"name": "p1", "version": "2.0"}]

        def install_plugin(self, name: str):  # noqa: ANN001
            called.append(name)
            return iter([])

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    from hc.main import app

    runner.invoke(app, ["install", "p1", "--dry-run"])
    assert called == [], "install_plugin should not be called in dry-run"


# ── remove --dry-run ──────────────────────────────────────────────────────────

class _RemoveClient:
    async def get_plugins(self):  # noqa: ANN001
        return [
            {"name": "dep-plugin", "dependencies": ["p1"]},
            {"name": "other", "dependencies": []},
        ]

    async def remove_plugin(self, name: str) -> dict:  # noqa: ANN001
        raise AssertionError("remove_plugin must not be called in dry-run")


def test_remove_dry_run_exits_zero(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _RemoveClient())
    from hc.main import app

    res = runner.invoke(app, ["remove", "p1", "--dry-run"])
    assert res.exit_code == 0
    assert "dry run" in res.output.lower()


def test_remove_dry_run_shows_dependents(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _RemoveClient())
    from hc.main import app

    res = runner.invoke(app, ["remove", "p1", "--dry-run"])
    assert "dep-plugin" in res.output


def test_remove_dry_run_does_not_call_remove(monkeypatch, runner: CliRunner) -> None:
    called = []

    class _Client:
        async def get_plugins(self):  # noqa: ANN001
            return []

        async def remove_plugin(self, name: str) -> dict:  # noqa: ANN001
            called.append(name)
            return {}

    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _Client())
    from hc.main import app

    runner.invoke(app, ["remove", "p1", "--dry-run"])
    assert called == [], "remove_plugin should not be called in dry-run"


def test_remove_dry_run_with_dependents_no_force_still_exits_zero(
    monkeypatch, runner: CliRunner
) -> None:
    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _RemoveClient())
    from hc.main import app

    res = runner.invoke(app, ["remove", "p1", "--dry-run"])
    assert res.exit_code == 0


# ── deploy --dry-run ──────────────────────────────────────────────────────────

def test_deploy_dry_run_exits_zero(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda console: None)

    class _Cfg:
        class _Deploy:
            core_image = "ghcr.io/org/core"
            core_mode = "dev"
            ssh = None
            path = None
        deploy = _Deploy()

    class _Src:
        from pathlib import Path
        path = Path("/fake/src")
        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/dev/docker-compose.yml"

    monkeypatch.setattr("hc.commands.deploy.Config.load", lambda: _Cfg())
    monkeypatch.setattr("hc.commands.deploy._resolve_source", lambda console: _Src())
    from hc.main import app

    res = runner.invoke(app, ["deploy", "--dry-run", "--no-build", "--no-push"])
    assert res.exit_code == 0
    assert "dry run" in res.output.lower()


def test_deploy_dry_run_shows_plan(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda console: None)

    class _Cfg:
        class _Deploy:
            core_image = "myregistry/core"
            core_mode = "prod"
            ssh = None
            path = None
        deploy = _Deploy()

    class _Src:
        from pathlib import Path
        path = Path("/fake/src")
        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/prod/docker-compose.yml"

    monkeypatch.setattr("hc.commands.deploy.Config.load", lambda: _Cfg())
    monkeypatch.setattr("hc.commands.deploy._resolve_source", lambda console: _Src())
    from hc.main import app

    res = runner.invoke(app, ["deploy", "--dry-run", "--no-build"])
    assert "myregistry/core" in res.output
