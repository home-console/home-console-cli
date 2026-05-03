from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from hc.core_source import CoreSource


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _fake_core_src(tmp_path: Path) -> Path:
    core_src = tmp_path / "core-runtime"
    core_src.mkdir(parents=True, exist_ok=True)
    return core_src


def _patch_resolve_local_core(monkeypatch, core_src: Path) -> None:
    """Без монорепы и без кэшированного CORE_SRC_DIR в `hc.core_source`."""
    monkeypatch.setattr("hc.commands.core._find_repo_root", lambda: None)
    monkeypatch.setattr(
        "hc.commands.core.get_core_source_local",
        lambda: CoreSource(path=core_src),
    )


def test_core_env_show_masks_sensitive_keys(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _fake_core_src(tmp_path)
    (core_src / ".env.example").write_text(
        "RUNTIME_MASTER_KEY=\nAPI_TOKEN=secret123\nPLAIN=visible\n",
        encoding="utf-8",
    )
    _patch_resolve_local_core(monkeypatch, core_src)

    from hc.main import app

    r = runner.invoke(app, ["core", "env", "show", "--mask"])
    assert r.exit_code == 0
    assert "PLAIN=visible" in r.output
    assert "secret123" not in r.output
    assert "API_TOKEN=***" in r.output


def test_core_env_show_unmasked(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _fake_core_src(tmp_path)
    (core_src / ".env.example").write_text("RUNTIME_MASTER_KEY=\nAPI_TOKEN=secret123\n", encoding="utf-8")
    _patch_resolve_local_core(monkeypatch, core_src)

    from hc.main import app

    r = runner.invoke(app, ["core", "env", "show"])
    assert r.exit_code == 0
    assert "secret123" in r.output


def test_core_env_path_prints_dotenv_path(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _fake_core_src(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)

    from hc.main import app

    r = runner.invoke(app, ["core", "env", "path"])
    assert r.exit_code == 0
    # Rich может вставить мягкий перенос в длинный путь — убираем \n для сравнения.
    assert str(core_src / ".env") in r.output.replace("\n", "")


def _native_fake_tree(tmp_path: Path) -> Path:
    core_src = tmp_path / "core-runtime"
    core_src.mkdir(parents=True, exist_ok=True)
    (core_src / "main.py").write_text("# stub\n", encoding="utf-8")
    (core_src / ".env.example").write_text("RUNTIME_MASTER_KEY=\nAPI_PORT=8123\n", encoding="utf-8")
    return core_src


def test_core_up_native_invalid_mode(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)
    from hc.main import app

    r = runner.invoke(app, ["core", "up", "--mode", "podman"])
    assert r.exit_code == 1
    assert "docker" in r.output.lower() or "native" in r.output.lower()


def test_core_up_native_already_running(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)
    monkeypatch.setattr("hc.native_core._read_pid_file", lambda: 424242)
    monkeypatch.setattr("hc.native_core._pid_alive", lambda pid: True)

    from hc.main import app

    r = runner.invoke(app, ["core", "up", "--mode", "native"])
    assert r.exit_code == 1
    assert "уже запущен" in r.output.lower() or "PID" in r.output


def test_core_up_native_starts_and_waits_health(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)

    mock_proc = MagicMock()
    mock_proc.pid = 99901

    monkeypatch.setattr("hc.native_core.subprocess.Popen", lambda *a, **k: mock_proc)
    monkeypatch.setattr("hc.native_core.wait_for_health", lambda *a, **k: True)

    from hc.main import app

    r = runner.invoke(app, ["core", "up", "--mode", "native", "--use-hc-python"])
    assert r.exit_code == 0
    assert "8123" in r.output or "native" in r.output.lower()
    assert mock_proc.pid == 99901 or "99901" in r.output


def test_core_down_native_no_pid_ok(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)
    monkeypatch.setattr("hc.native_core._read_pid_file", lambda: None)

    from hc.main import app

    r = runner.invoke(app, ["core", "down", "--mode", "native"])
    assert r.exit_code == 0


def test_core_ps_native_missing_pid(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)
    monkeypatch.setattr("hc.native_core._read_pid_file", lambda: None)

    from hc.main import app

    r = runner.invoke(app, ["core", "ps", "--mode", "native"])
    assert r.exit_code == 1


def test_core_native_logs_missing_file(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    core_src = _native_fake_tree(tmp_path)
    _patch_resolve_local_core(monkeypatch, core_src)

    from hc.main import app

    r = runner.invoke(app, ["core", "docker-logs", "--mode", "native"])
    assert r.exit_code == 1
    assert "лог" in r.output.lower()
