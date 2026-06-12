"""Тесты `resolve_workspace_root` / `detect_workspace_root` и команды `hc workspace`."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import hc.core_source as cs


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _make_monorepo(root: Path) -> Path:
    """Создать минимальный layout, который проходит `_looks_like_monorepo`."""
    (root / "core-runtime-service").mkdir(parents=True, exist_ok=True)
    # Достаточно одного sibling.
    (root / "home-console-cli").mkdir(parents=True, exist_ok=True)
    return root


def test_looks_like_monorepo_requires_siblings(tmp_path: Path) -> None:
    # Один core-runtime-service без siblings — не монорепо.
    (tmp_path / "core-runtime-service").mkdir()
    assert cs._looks_like_monorepo(tmp_path) is False

    # Добавляем sibling — теперь монорепо.
    (tmp_path / "platform-home-console").mkdir()
    assert cs._looks_like_monorepo(tmp_path) is True


def test_detect_workspace_from_env(tmp_path: Path, monkeypatch) -> None:
    repo = _make_monorepo(tmp_path / "monorepo")
    monkeypatch.setenv("HC_WORKSPACE", str(repo))
    monkeypatch.chdir(tmp_path)  # cwd НЕ внутри монорепо
    assert cs.detect_workspace_root() == repo.resolve()


def test_detect_workspace_from_cwd(tmp_path: Path, monkeypatch) -> None:
    repo = _make_monorepo(tmp_path / "monorepo")
    deep = repo / "core-runtime-service" / "modules"
    deep.mkdir(parents=True)
    monkeypatch.delenv("HC_WORKSPACE", raising=False)
    monkeypatch.chdir(deep)
    assert cs.detect_workspace_root() == repo.resolve()


def test_detect_workspace_returns_none_when_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("HC_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    # И CLI лежит за пределами монорепо (в pipx venv так и есть).
    monkeypatch.setattr(cs, "__file__", str(tmp_path / "fake_hc" / "core_source.py"))
    assert cs.detect_workspace_root() is None


def test_resolve_workspace_falls_back_to_config(
    tmp_path: Path, monkeypatch, isolated_home
) -> None:
    repo = _make_monorepo(tmp_path / "monorepo")
    monkeypatch.delenv("HC_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    # __file__ → за пределами монорепо
    monkeypatch.setattr(cs, "__file__", str(tmp_path / "fake_hc" / "core_source.py"))

    from hc.config import Config

    cfg = Config.load()
    cfg.workspace.path = str(repo)
    cfg.save()

    assert cs.resolve_workspace_root() == repo.resolve()


def test_workspace_status_unset(
    runner: CliRunner, monkeypatch, isolated_home, tmp_path
) -> None:
    monkeypatch.delenv("HC_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cs, "__file__", str(tmp_path / "fake_hc" / "core_source.py"))

    from hc.main import app

    r = runner.invoke(app, ["workspace", "status"])
    assert r.exit_code == 0
    assert "Workspace не задан" in r.output or "managed-клон" in r.output


def test_workspace_set_writes_config(
    runner: CliRunner, monkeypatch, isolated_home, tmp_path: Path
) -> None:
    repo = _make_monorepo(tmp_path / "monorepo")
    monkeypatch.delenv("HC_WORKSPACE", raising=False)

    from hc.main import app

    r = runner.invoke(app, ["workspace", "set", str(repo)])
    assert r.exit_code == 0, r.output

    from hc.config import Config, invalidate_config_cache

    invalidate_config_cache()
    assert Config.load().workspace.path == str(repo.resolve())


def test_workspace_set_rejects_non_monorepo(
    runner: CliRunner, isolated_home, tmp_path: Path
) -> None:
    from hc.main import app

    r = runner.invoke(app, ["workspace", "set", str(tmp_path)])
    assert r.exit_code == 1
    assert "не похоже на монорепо" in r.output


def test_git_summary_returns_none_for_non_git(tmp_path: Path) -> None:
    """Не git-каталог → None (status тогда печатает `[не git]`)."""
    from hc.commands.workspace import _git_summary

    assert _git_summary(tmp_path) is None


def test_git_summary_handles_missing_git_binary(tmp_path: Path, monkeypatch) -> None:
    """Если бинарь git недоступен — функция не падает, а возвращает None."""
    import hc.commands.workspace as ws_mod

    (tmp_path / ".git").mkdir()

    def _no_git(*args, **kwargs):  # noqa: ANN001, ARG001
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(ws_mod.subprocess, "run", _no_git)
    assert ws_mod._git_summary(tmp_path) is None


def test_git_summary_parses_branch_and_dirty(tmp_path: Path, monkeypatch) -> None:
    """Парсинг: branch + dirty + ahead/behind."""
    import hc.commands.workspace as ws_mod

    (tmp_path / ".git").mkdir()

    # Очередь ответов git в порядке вызова: branch, status, ahead/behind.
    responses = [
        types_stub(returncode=0, stdout="feature/foo\n", stderr=""),
        types_stub(returncode=0, stdout=" M file.py\n", stderr=""),
        types_stub(returncode=0, stdout="3\t1\n", stderr=""),
    ]
    calls = iter(responses)

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ARG001
        return next(calls)

    monkeypatch.setattr(ws_mod.subprocess, "run", _fake_run)

    summary = ws_mod._git_summary(tmp_path)
    assert summary is not None
    assert "feature/foo" in summary
    assert "*" in summary  # dirty flag
    assert "3↑" in summary
    assert "1↓" in summary


def types_stub(*, returncode: int, stdout: str, stderr: str):  # noqa: ANN201
    """Маленький stub под subprocess.CompletedProcess для моков."""
    import types

    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
