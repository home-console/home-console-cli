from __future__ import annotations

import os
from pathlib import Path

import pytest

from hc.setup_runner import SetupProcess


def test_setup_process_load_no_pid_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.setup_runner.SETUP_PID_PATH", tmp_path / "nope.pid")
    assert SetupProcess.load() is None


def test_setup_process_load_empty_file(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "setup.pid"
    pid_file.write_text("\n", encoding="utf-8")
    monkeypatch.setattr("hc.setup_runner.SETUP_PID_PATH", pid_file)
    assert SetupProcess.load() is None


def test_setup_process_load_invalid_pid(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "setup.pid"
    pid_file.write_text("not-a-pid\n", encoding="utf-8")
    monkeypatch.setattr("hc.setup_runner.SETUP_PID_PATH", pid_file)
    assert SetupProcess.load() is None


def test_setup_process_load_ok(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "setup.pid"
    log_file = tmp_path / "setup.log"
    pid_file.write_text("424242\n", encoding="utf-8")
    monkeypatch.setattr("hc.setup_runner.SETUP_PID_PATH", pid_file)
    monkeypatch.setattr("hc.setup_runner.SETUP_LOG_PATH", log_file)
    sp = SetupProcess.load()
    assert sp is not None
    assert sp.pid == 424242
    assert sp.log_path == log_file


def test_setup_process_is_running_false_when_kill_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.setup_runner.os.kill", lambda _pid, _sig: (_ for _ in ()).throw(OSError()))

    sp = SetupProcess(pid=999001, log_path=tmp_path / "l.log")
    assert sp.is_running() is False


def test_setup_process_is_running_true_when_kill_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.setup_runner.os.kill", lambda _pid, _sig: None)

    sp = SetupProcess(pid=999002, log_path=tmp_path / "l.log")
    assert sp.is_running() is True


def test_setup_process_save_writes_pid(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / ".config" / "hc"
    pid_path = cfg_dir / "setup.pid"
    monkeypatch.setattr("hc.setup_runner.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("hc.setup_runner.SETUP_PID_PATH", pid_path)

    sp = SetupProcess(pid=os.getpid(), log_path=tmp_path / "x.log")
    sp.save()
    assert pid_path.read_text(encoding="utf-8").strip() == str(os.getpid())
