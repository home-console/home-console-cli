from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


async def _agen(*msgs: str) -> AsyncIterator[str]:
    for m in msgs:
        yield m


def test_install_runs_stream_when_confirmed(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return [{"name": "p1", "version": "1.0", "description": "d", "dependencies": ["x"]}]

        def install_plugin(self, name: str):  # noqa: ANN001
            assert name == "p1"
            return _agen("step1", "step2")

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    monkeypatch.setattr("hc.commands.install.typer.confirm", lambda *a, **kw: True)
    from hc.main import app

    res = runner.invoke(app, ["install", "p1"])
    assert res.exit_code == 0
    assert "установлен" in res.output.lower()
