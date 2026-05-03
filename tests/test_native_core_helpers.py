from __future__ import annotations

from pathlib import Path

from hc.native_core import api_listen_display, parse_dotenv_file


def test_parse_dotenv_file_basic(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "API_PORT=9123\n# c\nexport API_HOST=127.0.0.1\nEMPTY=\n",
        encoding="utf-8",
    )
    d = parse_dotenv_file(p)
    assert d["API_PORT"] == "9123"
    assert d["API_HOST"] == "127.0.0.1"
    assert d.get("EMPTY") == ""


def test_parse_dotenv_file_quoted(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text('FOO="bar baz"\n', encoding="utf-8")
    assert parse_dotenv_file(p)["FOO"] == "bar baz"


def test_api_listen_display_defaults(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("", encoding="utf-8")
    port, host = api_listen_display(p)
    assert port == 8000
    assert host == "127.0.0.1"


def test_api_listen_display_zero_bind(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("API_PORT=3000\nAPI_HOST=0.0.0.0\n", encoding="utf-8")
    port, host = api_listen_display(p)
    assert port == 3000
    assert host == "127.0.0.1"
