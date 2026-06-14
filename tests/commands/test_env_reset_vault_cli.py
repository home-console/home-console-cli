from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hc.core_source import CoreSource
from hc.vault_ops import VaultResetResult


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _setup_common_mocks(monkeypatch, tmp_path: Path) -> None:
    """Замокать всё внешнее: docker, source resolution, compose project."""
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    monkeypatch.setattr("hc.commands.env._register.require_docker", lambda _console: None)
    monkeypatch.setattr(
        "hc.commands.env._register._resolve_source", lambda _console: CoreSource(path=tmp_path)
    )
    monkeypatch.setattr(
        "hc.commands.env._register.compose_project_from_source",
        lambda _console, _src, mode="dev-reload": type(
            "P", (), {"compose_file": tmp_path / "compose.yml", "cwd": tmp_path}
        )(),
    )


def test_env_reset_vault_postgres_explicit(monkeypatch, runner: CliRunner, isolated_home, tmp_path: Path) -> None:
    _setup_common_mocks(monkeypatch, tmp_path)
    called = {}

    def fake_reset_pg(*, compose_file: Path, cwd: Path) -> VaultResetResult:
        called["pg"] = True
        return VaultResetResult(
            db="postgres",
            actions=["DELETE FROM storage WHERE namespace IN (...)", "TRUNCATE storage_metadata"],
            success=True,
        )

    monkeypatch.setattr("hc.commands.env._register.reset_vault_postgres", fake_reset_pg)
    monkeypatch.setattr(
        "hc.commands.env._register.reset_vault_sqlite",
        lambda **kw: pytest.fail("sqlite reset called when --db=postgres"),
    )
    # core-runtime не запущен → restart не делаем.
    monkeypatch.setattr("hc.commands.env._register._get_running_services", lambda *a, **k: set())

    from hc.main import app

    r = runner.invoke(app, ["env", "reset-vault", "--db", "postgres", "--yes"])
    assert r.exit_code == 0, r.output
    assert called.get("pg") is True
    assert "vault сброшен" in r.output


def test_env_reset_vault_auto_picks_postgres_when_running(
    monkeypatch, runner: CliRunner, isolated_home, tmp_path: Path
) -> None:
    _setup_common_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr("hc.commands.env._register.detect_running_db", lambda *a, **k: "postgres")

    def fake_reset_pg(**kw) -> VaultResetResult:
        return VaultResetResult(db="postgres", actions=["ok"], success=True)

    monkeypatch.setattr("hc.commands.env._register.reset_vault_postgres", fake_reset_pg)
    monkeypatch.setattr(
        "hc.commands.env._register.reset_vault_sqlite",
        lambda **kw: pytest.fail("sqlite reset called in auto/postgres"),
    )
    monkeypatch.setattr("hc.commands.env._register._get_running_services", lambda *a, **k: set())

    from hc.main import app

    r = runner.invoke(app, ["env", "reset-vault", "--yes"])
    assert r.exit_code == 0, r.output
    assert "postgres" in r.output


def test_env_reset_vault_invalid_db_arg(
    monkeypatch, runner: CliRunner, isolated_home, tmp_path: Path
) -> None:
    _setup_common_mocks(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["env", "reset-vault", "--db", "mongo", "--yes"])
    assert r.exit_code != 0
    assert "неизвестен" in r.output


def test_env_reset_vault_restarts_core_when_running(
    monkeypatch, runner: CliRunner, isolated_home, tmp_path: Path
) -> None:
    _setup_common_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "hc.commands.env._register.reset_vault_sqlite",
        lambda **kw: VaultResetResult(db="sqlite", actions=["removed vault.db"], success=True),
    )
    monkeypatch.setattr("hc.commands.env._register._get_running_services", lambda *a, **k: {"core-runtime", "redis"})

    restart_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        restart_calls.append(list(cmd))

    monkeypatch.setattr("hc.commands.env._register._run", fake_run)

    from hc.main import app

    r = runner.invoke(app, ["env", "reset-vault", "--db", "sqlite", "--yes"])
    assert r.exit_code == 0, r.output
    assert restart_calls, "expected docker compose restart to be called"
    cmd = restart_calls[-1]
    assert "restart" in cmd
    assert "core-runtime" in cmd


def test_env_reset_vault_propagates_failure(
    monkeypatch, runner: CliRunner, isolated_home, tmp_path: Path
) -> None:
    _setup_common_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "hc.commands.env._register.reset_vault_postgres",
        lambda **kw: VaultResetResult(
            db="postgres", actions=[], success=False, message="psql failed"
        ),
    )
    monkeypatch.setattr("hc.commands.env._register._get_running_services", lambda *a, **k: set())

    from hc.main import app

    r = runner.invoke(app, ["env", "reset-vault", "--db", "postgres", "--yes"])
    assert r.exit_code != 0
    assert "psql failed" in r.output
