from __future__ import annotations

import io
from pathlib import Path

import pytest
import typer
from rich.console import Console

from hc.core_ops import (
    ComposeProject,
    compose_project_from_source,
    core_status,
    require_docker,
    run_compose,
)
from hc.core_source import CoreSource
from hc.errors import DockerNotFoundError, HcCliError


class _Proc:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _console() -> Console:
    return Console(file=io.StringIO(), width=120, color_system=None)


def test_compose_project_cwd_is_compose_parent(tmp_path: Path) -> None:
    f = tmp_path / "a" / "docker-compose.yml"
    f.parent.mkdir(parents=True)
    f.touch()
    p = ComposeProject(compose_file=f)
    assert p.cwd == f.parent


def test_require_docker_raises_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr("hc.core_ops.shutil.which", lambda _name: None)
    with pytest.raises(DockerNotFoundError) as e:
        require_docker(_console())
    assert "docker" in e.value.message.lower()


def test_compose_project_from_source_missing_compose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.core_ops.ensure_core_env", lambda _c, _p: None)
    src = CoreSource(path=tmp_path)
    with pytest.raises(HcCliError) as exc:
        compose_project_from_source(_console(), src, mode="dev")
    assert "docker-compose" in exc.value.message


def test_run_compose_success_prints_stdout(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd: list[str], *a, **k) -> _Proc:  # noqa: ANN001
        assert "docker" in cmd
        return _Proc(0, "compose ok\n", "")

    monkeypatch.setattr("hc.core_ops.subprocess.run", fake_run)
    buf = io.StringIO()
    console = Console(file=buf, width=120, color_system=None)
    run_compose(console, ["docker", "compose"], tmp_path)
    assert "compose ok" in buf.getvalue()


def test_run_compose_permission_denied_socket(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*_a, **_k) -> _Proc:
        return _Proc(1, "", "permission denied while trying to connect to the docker daemon socket")

    monkeypatch.setattr("hc.core_ops.subprocess.run", fake_run)
    with pytest.raises(typer.Exit) as e:
        run_compose(_console(), ["docker", "compose"], tmp_path)
    assert int(e.value.exit_code) == 1


def test_run_compose_generic_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*_a, **_k) -> _Proc:
        return _Proc(1, "boom", "")

    monkeypatch.setattr("hc.core_ops.subprocess.run", fake_run)
    with pytest.raises(typer.Exit) as e:
        run_compose(_console(), ["docker", "compose"], tmp_path)
    assert int(e.value.exit_code) == 1


def test_core_status_not_running_exits_1(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "docker-compose.yml"
    f.write_text("{}", encoding="utf-8")
    project = ComposeProject(compose_file=f)

    seq = iter(
        [
            _Proc(0, "NAME\nid\n", ""),
            _Proc(0, "NAME\n", ""),
        ]
    )

    def fake_run(*_a, **_k) -> _Proc:
        return next(seq)

    monkeypatch.setattr("hc.core_ops.subprocess.run", fake_run)
    with pytest.raises(typer.Exit) as e:
        core_status(_console(), project)
    assert int(e.value.exit_code) == 1


def test_core_source_compose_paths_dev_and_image(tmp_path: Path) -> None:
    src = CoreSource(path=tmp_path)
    assert src.compose_file("dev") == tmp_path / "deploy" / "dev" / "docker-compose.yml"
    assert src.compose_file("dev-image") == tmp_path / "deploy" / "dev" / "docker-compose.image.yml"
    assert src.compose_file("dev-reload") == tmp_path / "deploy" / "dev" / "docker-compose.reload.yml"
    assert src.compose_file("prod") == tmp_path / "deploy" / "prod" / "docker-compose.image.yml"
    assert src.compose_file(None) == tmp_path / "deploy" / "dev" / "docker-compose.yml"
