from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from hc.commands import skill as skill_cmds


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_skill_list_invokes_client(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.list_skills = AsyncMock(
        return_value={"ok": True, "result": {"items": [{"id": "p.s"}], "total": 1}}
    )
    monkeypatch.setattr(skill_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(skill_cmds.skill_app, ["list"])
    assert result.exit_code == 0
    mock_client.list_skills.assert_awaited_once_with(None)


def test_skill_get_invokes_client(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.get_skill = AsyncMock(return_value={"ok": True, "result": {"id": "p.s"}})
    monkeypatch.setattr(skill_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(skill_cmds.skill_app, ["get", "p.s"])
    assert result.exit_code == 0
    mock_client.get_skill.assert_awaited_once_with("p.s")


def test_skill_invoke_passes_json_params(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.invoke_skill = AsyncMock(
        return_value={"ok": True, "result": {"ok": True, "skill_id": "p.s", "result": {"x": 1}}}
    )
    monkeypatch.setattr(skill_cmds, "require_client", lambda _c: mock_client)
    result = runner.invoke(skill_cmds.skill_app, ["invoke", "p.s", "--json-params", '{"n": 1}'])
    assert result.exit_code == 0
    mock_client.invoke_skill.assert_awaited_once_with("p.s", {"n": 1})
