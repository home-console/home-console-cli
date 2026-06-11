from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hc import vault_ops


@dataclass
class _FakeResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def test_vault_namespaces_match_core_critical_namespaces() -> None:
    # Жёсткий контракт: список namespaces должен совпадать с CRITICAL+SYSTEM
    # в core. Если кто-то добавит новый critical namespace и не обновит CLI —
    # этот тест сломается и подскажет об этом.
    expected = {
        "secrets.store",
        "_system.meta",
        "_system.root_hash",
        "_system.audit_log",
    }
    assert set(vault_ops.VAULT_NAMESPACES) == expected


def test_reset_vault_postgres_fails_when_pg_not_running(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kw):
        if "ps" in cmd:
            return _FakeResult(returncode=0, stdout="")  # ничего не запущено
        pytest.fail(f"unexpected call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = vault_ops.reset_vault_postgres(
        compose_file=tmp_path / "compose.yml", cwd=tmp_path
    )
    assert res.success is False
    assert "не запущен" in res.message


def test_reset_vault_postgres_happy_path(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if "ps" in cmd:
            return _FakeResult(returncode=0, stdout="postgres\n")
        if "psql" in cmd:
            # Проверим что SQL содержит DELETE FROM storage и TRUNCATE storage_metadata
            sql = next(arg for arg in cmd if "DELETE FROM storage" in arg)
            assert "secrets.store" in sql
            assert "TRUNCATE storage_metadata" in sql
            return _FakeResult(returncode=0, stdout="DELETE 3\nTRUNCATE TABLE\n")
        pytest.fail(f"unexpected call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = vault_ops.reset_vault_postgres(
        compose_file=tmp_path / "compose.yml", cwd=tmp_path
    )
    assert res.success
    assert res.db == "postgres"
    assert any("DELETE FROM storage" in a for a in res.actions)
    assert any("TRUNCATE storage_metadata" in a for a in res.actions)


def test_reset_vault_postgres_treats_missing_tables_as_success(
    monkeypatch, tmp_path: Path
) -> None:
    """Если storage ещё не создан (первый запуск без core) — нечего сбрасывать."""

    def fake_run(cmd, **kw):
        if "ps" in cmd:
            return _FakeResult(returncode=0, stdout="postgres\n")
        if "psql" in cmd:
            return _FakeResult(
                returncode=1,
                stderr='ERROR:  relation "storage" does not exist',
            )
        pytest.fail(f"unexpected call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = vault_ops.reset_vault_postgres(
        compose_file=tmp_path / "compose.yml", cwd=tmp_path
    )
    assert res.success
    assert any("нечего сбрасывать" in a for a in res.actions)


def test_reset_vault_sqlite_skips_when_volume_missing(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kw):
        if cmd[:3] == ["docker", "compose", "-f"] and "ls" in cmd:
            return _FakeResult(returncode=0, stdout="[]")
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return _FakeResult(returncode=1, stderr="No such volume")
        pytest.fail(f"unexpected call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = vault_ops.reset_vault_sqlite(
        compose_file=tmp_path / "compose.yml", cwd=tmp_path
    )
    assert res.success
    assert any("не существует" in a or "нечего сбрасывать" in a for a in res.actions)


def test_reset_vault_sqlite_happy_path(monkeypatch, tmp_path: Path) -> None:
    rm_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        if cmd[:3] == ["docker", "compose", "-f"] and "ls" in cmd:
            return _FakeResult(returncode=0, stdout="[]")
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return _FakeResult(returncode=0, stdout="[]")
        if cmd[:3] == ["docker", "run", "--rm"]:
            rm_calls.append(list(cmd))
            # имитируем что после rm в /data остались только core-файлы
            return _FakeResult(returncode=0, stdout="runtime.db\nruntime.db-wal\n")
        pytest.fail(f"unexpected call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = vault_ops.reset_vault_sqlite(
        compose_file=tmp_path / "compose.yml", cwd=tmp_path
    )
    assert res.success
    assert res.db == "sqlite"
    # Команда rm должна упоминать оба vault-файла, но НЕ runtime.db (core БД).
    assert rm_calls
    sh_arg = next(arg for arg in rm_calls[0] if arg.startswith("rm -f"))
    assert "/data/vault.db" in sh_arg
    assert "/data/vault_secret.db" in sh_arg
    assert "/data/runtime.db" not in sh_arg


def test_detect_running_db_returns_postgres_when_running(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kw):
        return _FakeResult(returncode=0, stdout="postgres\nredis\ncore-runtime\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert vault_ops.detect_running_db(tmp_path / "compose.yml", tmp_path) == "postgres"


def test_detect_running_db_returns_sqlite_when_no_postgres(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kw):
        return _FakeResult(returncode=0, stdout="redis\ncore-runtime\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert vault_ops.detect_running_db(tmp_path / "compose.yml", tmp_path) == "sqlite"


def test_detect_running_db_returns_none_when_nothing_running(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kw):
        return _FakeResult(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert vault_ops.detect_running_db(tmp_path / "compose.yml", tmp_path) is None
