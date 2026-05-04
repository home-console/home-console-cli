from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_deploy_platform_image_mode_uses_compose_image(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    compose_file = tmp_path / "docker-compose.image.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr("hc.commands.deploy._resolve_platform_root", lambda _console: tmp_path)
    monkeypatch.setattr(
        "hc.commands.deploy._run_env", lambda cmd, cwd=None, env=None: calls.append((cmd, cwd, env))
    )

    from hc.main import app

    r = runner.invoke(
        app,
        [
            "deploy",
            "platform",
            "--mode",
            "image",
            "--image",
            "ghcr.io/home-console/platform-home-console",
            "--tag",
            "sha123",
        ],
    )

    assert r.exit_code == 0, r.output
    assert len(calls) == 2
    assert calls[0][0][:4] == ["docker", "compose", "-f", str(compose_file)]
    assert calls[0][0][4:] == ["pull", "platform-web"]
    assert calls[0][1] == tmp_path
    assert (
        calls[0][2] is not None
        and calls[0][2]["PLATFORM_IMAGE"] == "ghcr.io/home-console/platform-home-console:sha123"
    )
    assert calls[1][0][:4] == ["docker", "compose", "-f", str(compose_file)]
    assert calls[1][0][4:] == ["up", "-d"]
