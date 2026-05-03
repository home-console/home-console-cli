from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hc.core_source import CoreSource
from hc.commands.recovery import RecoveryContext


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _minimal_core_tree(core_root: Path) -> None:
    d = core_root / "deploy" / "dev"
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker-compose.yml").write_text(
        "services:\n  core-runtime:\n    image: test\n",
        encoding="utf-8",
    )


def test_recovery_hint_smoke(runner: CliRunner) -> None:
    from hc.main import app

    r = runner.invoke(app, ["recovery", "hint"])
    assert r.exit_code == 0
    assert "recovery" in r.output.lower() or "doctor" in r.output.lower()


def test_recovery_mode_show_default(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["recovery", "mode", "show"])
    assert r.exit_code == 0
    assert "recovery.mode" in r.output


def test_recovery_mode_set_rejects_invalid(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["recovery", "mode", "set", "prod"])
    assert r.exit_code == 2


def test_recovery_mode_set_dev_persists(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["recovery", "mode", "set", "dev"])
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["recovery", "mode", "show"])
    assert r2.exit_code == 0
    assert "dev" in r2.output


def test_recovery_config_paths_panel(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".config" / "hc"
    monkeypatch.setattr("hc.commands.recovery.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("hc.commands.recovery.config.CONFIG_PATH", cfg_dir / "config.toml")
    monkeypatch.setattr("hc.commands.recovery.config.DATA_DIR", tmp_path / "data")

    from hc.main import app

    r = runner.invoke(app, ["recovery", "config", "paths"])
    assert r.exit_code == 0
    assert "config" in r.output.lower()


def test_recovery_config_show_missing_ok(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_file = cfg_dir / "config.toml"
    monkeypatch.setattr("hc.commands.recovery.config.CONFIG_PATH", cfg_file)

    from hc.main import app

    r = runner.invoke(app, ["recovery", "config", "show"])
    assert r.exit_code == 0
    assert "конфига нет" in r.output.lower() or "нет" in r.output.lower()


def test_recovery_config_show_prints_toml(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text('[core]\nhost = "h"\n', encoding="utf-8")
    monkeypatch.setattr("hc.commands.recovery.config.CONFIG_PATH", cfg_file)

    from hc.main import app

    r = runner.invoke(app, ["recovery", "config", "show"])
    assert r.exit_code == 0
    assert "host" in r.output


def test_recovery_config_edit_requires_editor(runner: CliRunner, monkeypatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)

    from hc.main import app

    r = runner.invoke(app, ["recovery", "config", "edit"])
    assert r.exit_code == 1
    assert "EDITOR" in r.output or "VISUAL" in r.output


def test_recovery_config_open_setup_log_missing_ok(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "no-setup.log"
    monkeypatch.setattr("hc.commands.recovery.config.SETUP_LOG_PATH", log)

    from hc.main import app

    r = runner.invoke(app, ["recovery", "config", "open-setup-log"])
    assert r.exit_code == 0


def test_recovery_doctor_ok_with_patched_source(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    core_root = tmp_path / "core-runtime"
    _minimal_core_tree(core_root)
    monkeypatch.setattr("hc.core_ops.ensure_core_env", lambda _c, _p: None)
    monkeypatch.setattr("hc.commands.recovery.require_docker", lambda _c: None)

    def _resolve(self, console) -> CoreSource:  # noqa: ANN001
        return CoreSource(path=core_root)

    monkeypatch.setattr(RecoveryContext, "resolve_source", _resolve)

    from hc.main import app

    r = runner.invoke(app, ["recovery", "doctor"])
    assert r.exit_code == 0
    assert "docker" in r.output.lower()
    assert "core" in r.output.lower()
