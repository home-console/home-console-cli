from __future__ import annotations

from pathlib import Path

from hc.commands import _compose_helpers as h


def test_read_env_kv_parses_simple_lines(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "\n".join(
            [
                "# comment",
                "A=1",
                "B=hello world",
                "EMPTY=",
                "",
                "SPACED = nope",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = h.read_env_kv(p)
    assert env["A"] == "1"
    assert env["B"] == "hello world"
    assert env["EMPTY"] == ""
    # line with spaces around key isn't supported (kept as-is parsing before '='); ensure no crash
    assert "SPACED" in env


def test_upsert_env_updates_and_appends(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("A=1\nB=2\n", encoding="utf-8")
    h.upsert_env(p, {"A": "9", "C": "3"})
    txt = p.read_text(encoding="utf-8")
    assert "A=9" in txt
    assert "B=2" in txt
    assert "C=3" in txt


def test_remove_env_keys_removes_target_keys(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("A=1\nB=2\n#C=3\nC=3\n", encoding="utf-8")
    h.remove_env_keys(p, {"B", "C"})
    txt = p.read_text(encoding="utf-8")
    assert "A=1" in txt
    assert "B=2" not in txt
    assert "\nC=3\n" not in txt
    assert "#C=3" in txt


def test_container_env_script_is_valid_shell_snippet() -> None:
    s = h.container_env_script(["A", "B"])
    assert "echo A=${A-}" in s
    assert "echo B=${B-}" in s

