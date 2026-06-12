"""Тесты hc upgrade — не сообщать об успехе если версия не изменилась."""
from __future__ import annotations

from unittest.mock import MagicMock

import hc.commands.cli_version as ver_mod


def test_version_reached() -> None:
    assert ver_mod._version_reached("0.0.19", "0.0.19") is True
    assert ver_mod._version_reached("0.0.19", "0.0.20") is True
    assert ver_mod._version_reached("0.0.19", "0.0.18") is False
    assert ver_mod._version_reached("0.0.19", None) is False


def test_upgrade_via_pipx_succeeds_on_first_try(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    versions = iter(["0.0.19"])

    monkeypatch.setattr(ver_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(ver_mod, "_pipx_package_version", lambda p: next(versions))

    result = ver_mod._upgrade_via_pipx(MagicMock(), "/usr/bin/pipx", "0.0.19")
    assert result == "0.0.19"
    assert calls[0][1:3] == ["upgrade", "homeconsole-cli"]
    assert len(calls) == 1


def test_upgrade_via_pipx_force_install_when_stale(monkeypatch) -> None:
    calls: list[list[str]] = []
    versions = iter(["0.0.18", "0.0.19"])

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(ver_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(ver_mod, "_pipx_package_version", lambda p: next(versions))

    result = ver_mod._upgrade_via_pipx(MagicMock(), "/usr/bin/pipx", "0.0.19")
    assert result == "0.0.19"
    assert len(calls) == 2
    assert calls[1][1:4] == ["install", "homeconsole-cli==0.0.19", "--force"]


def test_upgrade_via_pipx_returns_none_when_force_also_fails(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return MagicMock(returncode=0)

    monkeypatch.setattr(ver_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(ver_mod, "_pipx_package_version", lambda p: "0.0.18")

    result = ver_mod._upgrade_via_pipx(MagicMock(), "/usr/bin/pipx", "0.0.19")
    assert result is None


def test_run_cli_upgrade_falls_back_to_pip_when_pipx_unchanged(monkeypatch) -> None:
    """pipx не обновил → fallback на pip, без ложного «успеха»."""
    monkeypatch.setattr(ver_mod, "__version__", "0.0.18")
    monkeypatch.setattr(ver_mod, "get_update_notification", lambda v: "0.0.19")
    monkeypatch.setattr(ver_mod, "_pipx_has_package", lambda p: True)
    monkeypatch.setattr(ver_mod, "_upgrade_via_pipx", lambda c, p, t: None)
    monkeypatch.setattr(ver_mod.shutil, "which", lambda name: "/usr/bin/pipx" if name == "pipx" else None)

    pip_cmds: list[list[str]] = []

    def fake_pip_run(cmd, **kwargs):
        pip_cmds.append(list(cmd))
        return MagicMock(returncode=0)

    reexec_args: list[dict] = []

    def fake_reexec(console, **kwargs):
        reexec_args.append(kwargs)

    monkeypatch.setattr(ver_mod.subprocess, "run", fake_pip_run)
    monkeypatch.setattr(ver_mod, "_reexec", fake_reexec)

    monkeypatch.setattr(ver_mod, "_disk_package_version", lambda: "0.0.19")

    code = ver_mod.run_cli_upgrade(MagicMock())
    assert code == 0
    assert any("-m" in cmd and "pip" in cmd for cmd in pip_cmds)
    assert reexec_args
