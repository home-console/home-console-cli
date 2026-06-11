from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _patch_reset_paths(monkeypatch, home: Path) -> None:
    cfg_dir = home / ".config" / "hc"
    data_dir = home / ".local" / "share" / "hc"
    core_dir = data_dir / "core-runtime-service"
    platform_dir = data_dir / "platform-home-console"
    monkeypatch.setattr("hc.commands.reset.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("hc.commands.reset.CONFIG_PATH", cfg_dir / "config.toml")
    monkeypatch.setattr("hc.commands.reset.HISTORY_PATH", cfg_dir / "history")
    monkeypatch.setattr("hc.commands.reset.SETUP_LOG_PATH", cfg_dir / "setup.log")
    monkeypatch.setattr("hc.commands.reset.SETUP_PID_PATH", cfg_dir / "setup.pid")
    monkeypatch.setattr("hc.commands.reset.DATA_DIR", data_dir)
    monkeypatch.setattr("hc.commands.reset.CORE_SRC_DIR", core_dir)
    monkeypatch.setattr("hc.commands.reset.PLATFORM_SRC_DIR", platform_dir)


def test_reset_core_no_cache(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["reset", "core"])
    assert r.exit_code == 0
    assert "не найден" in r.output.lower()


def test_reset_core_decline_keeps_dir(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    core = tmp_path / ".local" / "share" / "hc" / "core-runtime-service"
    core.mkdir(parents=True)
    marker = core / "keep.txt"
    marker.write_text("x", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "core"], input="n\n")
    assert r.exit_code == 0
    assert marker.exists()


def test_reset_core_confirm_removes_cache(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    core = tmp_path / ".local" / "share" / "hc" / "core-runtime-service"
    core.mkdir(parents=True)

    from hc.main import app

    r = runner.invoke(app, ["reset", "core"], input="y\n")
    assert r.exit_code == 0
    assert not core.exists()


def test_reset_config_no_files(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["reset", "config"])
    assert r.exit_code == 0
    assert "не найден" in r.output.lower()


def test_reset_config_confirm_removes(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text("x=1\n", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "config"], input="y\n")
    assert r.exit_code == 0
    assert not cfg_file.exists()


def test_reset_all_yes_flag_clears_paths(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("hc.commands.reset._docker_cmd_ok", lambda: False)
    core = tmp_path / ".local" / "share" / "hc" / "core-runtime-service"
    core.mkdir(parents=True)
    (core / "a.txt").write_text("1", encoding="utf-8")
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text("k=v\n", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "all", "--yes"])
    assert r.exit_code == 0
    assert not core.exists()
    assert not (cfg_dir / "config.toml").exists()


def test_reset_platform_no_cache(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    from hc.main import app

    r = runner.invoke(app, ["reset", "platform"])
    assert r.exit_code == 0
    assert "не найден" in r.output.lower()


def test_reset_platform_confirm_removes(runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    platform = tmp_path / ".local" / "share" / "hc" / "platform-home-console"
    platform.mkdir(parents=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "platform"], input="y\n")
    assert r.exit_code == 0
    assert not platform.exists()


def test_reset_all_keep_config_preserves_cfg(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("hc.commands.reset._docker_cmd_ok", lambda: False)
    core = tmp_path / ".local" / "share" / "hc" / "core-runtime-service"
    core.mkdir(parents=True)
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.toml"
    cfg.write_text("k=v\n", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "all", "--yes", "--keep-config"])
    assert r.exit_code == 0
    assert not core.exists()
    assert cfg.exists(), "конфиг должен быть сохранён с --keep-config"


def test_reset_all_clears_platform_too(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    _patch_reset_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("hc.commands.reset._docker_cmd_ok", lambda: False)
    platform = tmp_path / ".local" / "share" / "hc" / "platform-home-console"
    platform.mkdir(parents=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")

    from hc.main import app

    r = runner.invoke(app, ["reset", "all", "--yes"])
    assert r.exit_code == 0
    assert not platform.exists()


def test_reset_docker_dry_run(runner: CliRunner, isolated_home, monkeypatch) -> None:
    """В dry-run должно показывать список найденных volumes/images, но не удалять."""
    monkeypatch.setattr("hc.commands.reset._docker_cmd_ok", lambda: True)
    monkeypatch.setattr(
        "hc.commands.reset._docker_targets",
        lambda: (["dev_pgdata"], ["core-runtime-service:latest"]),
    )
    rm_called: list[bool] = []
    monkeypatch.setattr(
        "hc.commands.reset._docker_remove",
        lambda *a, **kw: rm_called.append(True),
    )

    from hc.main import app

    r = runner.invoke(app, ["reset", "docker", "--dry-run"])
    assert r.exit_code == 0
    assert "dev_pgdata" in r.output
    assert "core-runtime-service:latest" in r.output
    assert not rm_called, "dry-run не должен вызывать удаление"


def test_reset_docker_no_targets(runner: CliRunner, isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("hc.commands.reset._docker_cmd_ok", lambda: True)
    monkeypatch.setattr("hc.commands.reset._docker_targets", lambda: ([], []))

    from hc.main import app

    r = runner.invoke(app, ["reset", "docker", "--yes"])
    assert r.exit_code == 0
    assert "ничего не найдено" in r.output.lower()
