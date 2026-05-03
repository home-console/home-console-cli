from __future__ import annotations

from hc.commands.update import _fmt_s, _is_compose_running


def test_fmt_s_seconds() -> None:
    assert _fmt_s(12.345) == "12.3s"


def test_fmt_s_minutes() -> None:
    assert _fmt_s(125.0) == "2m05s"


def test_is_compose_running_requires_at_least_two_nonempty_lines() -> None:
    assert _is_compose_running("") is False
    assert _is_compose_running("a") is False
    assert _is_compose_running("a\nb") is True
    assert _is_compose_running("a\n \nb") is True
