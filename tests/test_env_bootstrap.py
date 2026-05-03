from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from hc.env_bootstrap import core_env_path, ensure_core_env


def test_core_env_path() -> None:
    p = Path("/tmp/x")
    assert core_env_path(p) == p / ".env"


def test_ensure_core_env_noop_when_env_exists(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEEP=1\n", encoding="utf-8")
    buf = io.StringIO()
    ensure_core_env(Console(file=buf, width=80, color_system=None), tmp_path)
    assert env.read_text() == "KEEP=1\n"
    assert buf.getvalue() == ""


def test_ensure_core_env_warns_when_no_example(tmp_path: Path) -> None:
    buf = io.StringIO()
    ensure_core_env(Console(file=buf, width=80, color_system=None), tmp_path)
    out = buf.getvalue()
    assert ".env.example" in out or "шаблон" in out.lower()


def test_ensure_core_env_from_example_replaces_master_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.env_bootstrap.secrets.token_hex", lambda _n: "0" * 64)
    (tmp_path / ".env.example").write_text("RUNTIME_MASTER_KEY=\nFOO=bar\n", encoding="utf-8")
    buf = io.StringIO()
    ensure_core_env(Console(file=buf, width=80, color_system=None), tmp_path)
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RUNTIME_MASTER_KEY=" + "0" * 64 in written
    assert "FOO=bar" in written
    assert buf.getvalue() != ""


def test_ensure_core_env_prepends_key_when_example_has_no_runtime_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.env_bootstrap.secrets.token_hex", lambda _n: "ab" * 32)
    (tmp_path / ".env.example").write_text("FOO=1\n", encoding="utf-8")
    ensure_core_env(Console(file=io.StringIO(), width=80, color_system=None), tmp_path)
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("RUNTIME_MASTER_KEY=")
    assert "FOO=1" in lines
