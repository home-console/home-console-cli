from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_doctor_quick_skips_ports(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    monkeypatch.setattr(
        "hc.doctor_lib._checks_ports",
        lambda: (_ for _ in ()).throw(AssertionError("ports should not run")),
    )
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--quick"])
    assert r.exit_code in (0, 1)
    assert ":18080" not in r.output


def test_doctor_json_output(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--quick", "--json"])
    assert r.exit_code in (0, 1)
    data = json.loads(r.output)
    assert "checks" in data
    assert "modes" in data
    assert data["scope"] == "quick"


def test_doctor_quick_and_api_exclusive(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--quick", "--api"])
    assert r.exit_code == 2


def test_doctor_shows_effective_modes(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--quick"])
    assert "recovery.mode" in r.output
    assert "deploy.core_mode" in r.output


def test_status_json(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)

    class _Client:
        async def admin_status(self):
            return {"version": "1.0", "status": "running", "uptime": "1h"}

        async def health(self):
            return None

        async def get_plugins(self):
            return [{"name": "p", "status": "running"}]

        async def get_modules(self):
            return [{"status": "ok"}, {"status": "running"}]

    monkeypatch.setattr("hc.commands.status.require_client", lambda console: _Client())

    from hc.main import app

    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["ok"] is True
    assert data["version"] == "1.0"


def test_plugin_list_json(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)

    class _Client:
        async def inspector_plugins(self):
            return {"ok": True, "result": [{"name": "ui", "version": "1", "status": "running"}]}

    monkeypatch.setattr("hc.commands.plugin.require_client", lambda console: _Client())

    from hc.main import app

    r = runner.invoke(app, ["plugin", "list", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["ok"] is True
    assert data["plugins"][0]["name"] == "ui"


def test_env_ps_json(monkeypatch, runner: CliRunner, isolated_home) -> None:
    import hc.commands.env._register as env_mod

    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        from pathlib import Path

        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/dev/docker-compose.reload.yml"

    class _Project:
        from pathlib import Path

        compose_file = Path("/fake/core/deploy/dev/docker-compose.reload.yml")

        @property
        def cwd(self) -> Path:
            return self.compose_file.parent

    monkeypatch.setattr("hc.commands.env._resolve._resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(
        "hc.commands.env._compose._compose_ps_rows",
        lambda project: [  # noqa: ANN001
            {"Service": "core-runtime", "State": "running", "Ports": "0.0.0.0:18000->8000/tcp"}
        ],
    )

    from hc.main import app

    r = runner.invoke(app, ["env", "ps", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["ok"] is True
    assert data["containers"][0]["service"] == "core-runtime"
