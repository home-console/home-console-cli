from __future__ import annotations

from hc.commands.deploy import _fmt_s, _is_compose_running, _ssh_cmd


def test_fmt_s_under_one_minute() -> None:
    assert _fmt_s(0.0) == "0.0s"
    assert _fmt_s(59.9) == "59.9s"


def test_fmt_s_one_minute_and_more() -> None:
    assert _fmt_s(60.0) == "1m00s"
    assert _fmt_s(125.0) == "2m05s"


def test_is_compose_running_requires_header_plus_row() -> None:
    assert _is_compose_running("") is False
    assert _is_compose_running("NAME\n") is False
    assert _is_compose_running("NAME\nsvc\n") is True


def test_ssh_cmd_is_argv_list_no_shell() -> None:
    assert _ssh_cmd("u@h", "cd /srv && ls") == ["ssh", "u@h", "cd /srv && ls"]
