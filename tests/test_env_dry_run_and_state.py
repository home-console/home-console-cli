from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def env_modules(isolated_home, tmp_path, monkeypatch):
    import hc.constants as constants
    import hc.env_state as env_state
    import hc.commands.env as env_mod

    importlib.reload(constants)
    monkeypatch.setattr(env_state, "LAST_ENV_PATH", tmp_path / "last_env.json")
    importlib.reload(env_mod)
    return env_mod, env_state


def test_save_and_load_last_env(env_modules) -> None:
    _, env_state = env_modules
    env_state.save_last_env(mode="dev-reload", services=["core-runtime", "caddy"], db="postgres")
    last = env_state.load_last_env()
    assert last is not None
    assert last.mode == "dev-reload"
    assert last.services == ["core-runtime", "caddy"]
    assert last.db == "postgres"


def test_env_up_dry_run(monkeypatch, env_modules) -> None:
    env_mod, _ = env_modules
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return f"deploy/dev/docker-compose.{mode}.yml"

    class _Project:
        compose_file = Path("/fake/core/deploy/dev/docker-compose.dev-reload.yml")

        @property
        def cwd(self) -> Path:
            return self.compose_file.parent

    monkeypatch.setattr(env_mod, "_resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(env_mod, "_get_running_services", lambda *a, **k: set())

    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "env",
            "up",
            "--dry-run",
            "--mode",
            "dev-reload",
            "--profile",
            "base",
            "--db",
            "sqlite",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "dry run" in r.output.lower()
    assert "core-runtime" in r.output
    assert "docker compose" in r.output


def test_env_up_platform_profile_auto_uses_dev_image(monkeypatch, env_modules) -> None:
    env_mod, _ = env_modules
    monkeypatch.setattr(env_mod, "require_docker", lambda console: None)

    class _Src:
        path = Path("/fake/core")

        def compose_rel(self, mode: str) -> str:  # noqa: ANN001
            return f"deploy/dev/docker-compose.{mode}.yml"

    class _Project:
        compose_file = Path("/fake/core/deploy/dev/docker-compose.dev-image.yml")

        @property
        def cwd(self) -> Path:
            return self.compose_file.parent

    monkeypatch.setattr(env_mod, "_resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(env_mod, "_get_running_services", lambda *a, **k: set())

    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "env",
            "up",
            "--dry-run",
            "--profile",
            "platform",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "dev-image" in r.output
    assert "platform-web" in r.output
    assert "edge" in r.output


def test_env_down_dry_run(monkeypatch, env_modules) -> None:
    env_mod, _ = env_modules
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

    monkeypatch.setattr(env_mod, "_resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(
        env_mod,
        "_get_running_services",
        lambda *a, **k: {"core-runtime", "postgres"},
    )

    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["env", "down", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "dry run" in r.output.lower()
    assert "compose down" in r.output or " down" in r.output


def test_last_env_written_on_up_dry_run(env_modules, monkeypatch) -> None:
    env_mod, env_state = env_modules
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

    monkeypatch.setattr(env_mod, "_resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(env_mod, "_get_running_services", lambda *a, **k: set())

    from hc.main import app

    runner = CliRunner()
    runner.invoke(
        app,
        ["env", "up", "--dry-run", "--profile", "hmr", "--db", "postgres"],
    )
    last = env_state.load_last_env()
    assert last is not None
    assert last.db == "postgres"
    assert "core-runtime" in last.services


def test_env_up_creates_core_env_before_compose(env_modules, monkeypatch) -> None:
    env_mod, _ = env_modules
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

    calls: list[list[str]] = []
    ensured: list[Path] = []

    monkeypatch.setattr(env_mod, "_resolve_source", lambda console: _Src())
    monkeypatch.setattr(
        env_mod,
        "compose_project_from_source",
        lambda console, src, mode=None: _Project(),  # noqa: ANN001
    )
    monkeypatch.setattr(env_mod, "_get_running_services", lambda *a, **k: set())
    monkeypatch.setattr(env_mod, "_try_pull_source", lambda src, console: None)
    monkeypatch.setattr(env_mod, "ensure_core_env", lambda console, path: ensured.append(path))
    monkeypatch.setattr(env_mod, "_run", lambda cmd, cwd=None, extra_env=None: calls.append(cmd))
    # Disable upstream pre-flight side effects that would shell out to docker/lsof.
    monkeypatch.setattr(env_mod, "_check_disk_space", lambda console: None)
    monkeypatch.setattr(env_mod, "_get_needed_ports", lambda plan: {})
    monkeypatch.setattr(env_mod, "_find_port_conflicts", lambda needed, plan: [])

    from hc.main import app

    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "env",
            "up",
            "--profile",
            "base",
        ],
    )
    assert r.exit_code == 0, r.output
    assert ensured == [Path("/fake/core")]
    assert calls


def test_repl_commands_include_env_and_doctor() -> None:
    from hc.cli_registry import repl_root_commands

    cmds = repl_root_commands()
    assert "env" in cmds
    assert "doctor" in cmds
    assert "marketplace" in cmds
    assert "config" in cmds


def test_nav_env_lists_rebuild_stats_health() -> None:
    from hc.cli_registry import NAV_TREE

    env_children = NAV_TREE["env"]["children"]
    assert isinstance(env_children, dict)
    assert "rebuild" in env_children
    assert "stats" in env_children
    assert "health" in env_children
    assert "pull" in env_children
    assert "ps" in env_children
    assert "exec" in env_children
