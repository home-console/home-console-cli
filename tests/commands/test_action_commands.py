from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from hc.commands import action as action_cmds


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_action_list_invokes_client(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.list_actions = AsyncMock(
        return_value={"ok": True, "result": {"items": [{"id": "p.s"}], "total": 1}}
    )
    monkeypatch.setattr(action_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(action_cmds.action_app, ["list"])
    assert result.exit_code == 0
    mock_client.list_actions.assert_awaited_once_with(None)


def test_action_get_invokes_client(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.get_action = AsyncMock(return_value={"ok": True, "result": {"id": "p.s"}})
    monkeypatch.setattr(action_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(action_cmds.action_app, ["get", "p.s"])
    assert result.exit_code == 0
    mock_client.get_action.assert_awaited_once_with("p.s")


def test_action_invoke_passes_json_params(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.invoke_action = AsyncMock(
        return_value={"ok": True, "result": {"ok": True, "action_id": "p.s", "result": {"x": 1}}}
    )
    monkeypatch.setattr(action_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(action_cmds.action_app, ["invoke", "p.s", "--json-params", '{"n": 1}'])
    assert result.exit_code == 0
    mock_client.invoke_action.assert_awaited_once_with("p.s", {"n": 1})
