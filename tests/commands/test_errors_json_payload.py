from __future__ import annotations

import typer

from hc.errors import InvalidModeError, json_error_payload


def test_json_error_payload_hc_cli_error() -> None:
    exc = InvalidModeError(message="bad", exit_code=2, hint="do x")
    payload = json_error_payload("update.core", exc)
    assert payload["ok"] is False
    assert payload["command"] == "update.core"
    assert payload["exit_code"] == 2
    assert payload["error"] == "InvalidModeError"
    assert payload["message"] == "bad"
    assert payload["hint"] == "do x"


def test_json_error_payload_typer_exit() -> None:
    payload = json_error_payload("update.core", typer.Exit(code=7))
    assert payload["ok"] is False
    assert payload["exit_code"] == 7
    assert payload["error"] == "Exit"


def test_json_error_payload_generic_exception() -> None:
    payload = json_error_payload("x", ValueError("nope"))
    assert payload["ok"] is False
    assert payload["message"] == "nope"
