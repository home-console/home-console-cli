from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def env_mod(isolated_home, monkeypatch):
    import importlib

    import hc.commands.env._register as m

    importlib.reload(m)
    return m


def test_env_help_mentions_core_env(runner: CliRunner | None = None) -> None:
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "--help"])
    assert r.exit_code == 0
    assert "core env" in r.output.lower() or "hc core env" in r.output.lower()


def test_core_env_help_mentions_env_up(monkeypatch) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["core", "env", "--help"])
    assert r.exit_code == 0
    assert "env up" in r.output.lower()


def test_env_pull_clean_tree(env_mod, monkeypatch, tmp_path: Path) -> None:
    src_path = tmp_path / "core"
    src_path.mkdir()
    (src_path / ".git").mkdir()

    class _Src:
        path = src_path

    monkeypatch.setattr("hc.commands.env._resolve._resolve_source", lambda console: _Src())

    calls: list[list[str]] = []

    def _run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd)
        class _P:
            returncode = 0
            stdout = "" if "status" in cmd else "Already up to date.\n"
            stderr = ""

        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)

    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "pull"])
    assert r.exit_code == 0, r.output
    assert any("git" in " ".join(c) for c in calls)


def test_env_ps_json_table(env_mod, monkeypatch) -> None:
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/dev/docker-compose.reload.yml"

    class _Project:
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

    row = {
        "Service": "core-runtime",
        "State": "running",
        "Publishers": "0.0.0.0:18000->8000/tcp",
    }

    def _run(cmd, **kwargs):  # noqa: ANN001
        class _P:
            returncode = 0
            stdout = json.dumps(row) + "\n"
            stderr = ""

        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)

    from hc.main import app

    r = CliRunner().invoke(app, ["env", "ps"])
    assert r.exit_code == 0, r.output
    assert "core-runtime" in r.output
    assert "18000" in r.output or "localhost:18000" in r.output


def _stub_project(env_mod, monkeypatch) -> None:
    """Общий мок source+project — используется тестами reload/restart/status."""
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/dev/docker-compose.reload.yml"

    class _Project:
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


def test_env_restart_recreate_uses_force_recreate(env_mod, monkeypatch) -> None:
    """`env restart caddy --recreate` → docker compose up -d --force-recreate caddy."""
    _stub_project(env_mod, monkeypatch)

    seen: list[list[str]] = []

    def _run(cmd, **kwargs):  # noqa: ANN001
        seen.append(cmd)
        class _P:
            returncode = 0
        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "restart", "caddy", "--recreate"])
    assert r.exit_code == 0, r.output
    # Должна быть одна команда up -d --force-recreate caddy.
    assert any(
        "up" in c and "-d" in c and "--force-recreate" in c and "caddy" in c
        for c in seen
    ), seen
    # restart-команда НЕ должна вызываться.
    assert not any("restart" in c for c in seen), seen


def test_env_reload_is_alias_for_restart_recreate(env_mod, monkeypatch) -> None:
    """`env reload caddy` ведёт себя как `env restart caddy --recreate`."""
    _stub_project(env_mod, monkeypatch)

    seen: list[list[str]] = []

    def _run(cmd, **kwargs):  # noqa: ANN001
        seen.append(cmd)
        class _P:
            returncode = 0
        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "reload", "caddy"])
    assert r.exit_code == 0, r.output
    assert any(
        "up" in c and "-d" in c and "--force-recreate" in c and "caddy" in c
        for c in seen
    ), seen


def test_env_status_dashboard_shows_health_and_url(env_mod, monkeypatch) -> None:
    """`env status` (без --raw) рендерит таблицу со state/health/URL."""
    _stub_project(env_mod, monkeypatch)

    row = {
        "Service": "core-runtime",
        "State": "running",
        "Health": "healthy",
        "RunningFor": "5 minutes ago",
        "Publishers": "0.0.0.0:18000->8000/tcp",
    }

    def _run(cmd, **kwargs):  # noqa: ANN001
        class _P:
            returncode = 0
            stdout = json.dumps(row) + "\n"
            stderr = ""
        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "status"])
    assert r.exit_code == 0, r.output
    assert "core-runtime" in r.output
    assert "healthy" in r.output
    # Сводка по состояниям сверху
    assert "running" in r.output


def test_env_status_dashboard_shows_troubled_hint(env_mod, monkeypatch) -> None:
    """Если сервис в exited/unhealthy — даём подсказку про logs."""
    _stub_project(env_mod, monkeypatch)

    row = {
        "Service": "frontend-vite",
        "State": "exited",
        "Health": "",
        "RunningFor": "1 hour ago",
        "Publishers": "",
    }

    def _run(cmd, **kwargs):  # noqa: ANN001
        class _P:
            returncode = 0
            stdout = json.dumps(row) + "\n"
            stderr = ""
        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = CliRunner().invoke(app, ["env", "status"])
    assert r.exit_code == 0, r.output
    # Должна быть подсказка про logs / hc doctor
    assert "logs" in r.output
    assert "frontend-vite" in r.output


def test_env_logs_grep_rejects_invalid_regex(env_mod, monkeypatch) -> None:
    """Битый regex в --grep → exit 2, понятная ошибка."""
    _stub_project(env_mod, monkeypatch)
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    # «(unclosed» — невалидная скобка.
    r = CliRunner().invoke(app, ["env", "logs", "core-runtime", "--grep", "(unclosed"])
    assert r.exit_code == 2, r.output
    assert "regex" in r.output.lower() or "невалид" in r.output.lower()


def test_env_exec_builds_compose_cmd(env_mod, monkeypatch) -> None:
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return "deploy/dev/docker-compose.reload.yml"

    class _Project:
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
    monkeypatch.setattr(env_mod, "_get_running_services", lambda *a, **k: {"core-runtime"})

    seen: list[list[str]] = []

    def _run(cmd, **kwargs):  # noqa: ANN001
        seen.append(cmd)
        class _P:
            returncode = 0

        return _P()

    monkeypatch.setattr(env_mod.subprocess, "run", _run)

    from hc.main import app

    r = CliRunner().invoke(app, ["env", "exec", "core-runtime", "ls"])
    assert r.exit_code == 0, r.output
    assert seen
    assert "exec" in seen[0]
    assert "core-runtime" in seen[0]
    assert "ls" in seen[0]
