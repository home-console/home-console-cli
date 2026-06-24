"""Тесты детекции расщепления compose-стека в Docker Desktop."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hc.commands.env._diagnostics as diag_mod
from hc.commands.env._compose import planned_config_files_from_cmd
from hc.constants import CORE_SRC_DIR


def _make_monorepo(tmp_path: Path, name: str = "HomeConsole") -> Path:
    root = tmp_path / name
    (root / "core-runtime-service" / "deploy" / "dev").mkdir(parents=True)
    (root / "home-console-cli").mkdir(parents=True)
    return root


def _make_plan(tmp_path: Path, *, services: list[str] | None = None) -> SimpleNamespace:
    compose_cwd = tmp_path / "core-runtime-service" / "deploy" / "dev"
    compose_cwd.mkdir(parents=True, exist_ok=True)
    compose_file = compose_cwd / "docker-compose.reload.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    project = SimpleNamespace(
        cwd=compose_cwd,
        compose_file=compose_file,
    )
    return SimpleNamespace(
        service_names=services or ["core-runtime", "redis"],
        compose_profiles=[],
        project=project,
    )


def _container(
    *,
    project: str = "dev",
    service: str,
    config_files: str,
    state: str = "running",
) -> dict:
    return {
        "Names": f"dev-hc-{service}",
        "State": state,
        "Labels": (
            f"com.docker.compose.project={project},"
            f"com.docker.compose.service={service},"
            f"com.docker.compose.project.config_files={config_files}"
        ),
    }


def test_planned_config_files_from_cmd_resolves_absolute_paths(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    override = tmp_path / "override.yml"
    compose.touch()
    override.touch()
    cmd = ["docker", "compose", "-f", str(compose), "-f", str(override), "up"]
    assert planned_config_files_from_cmd(cmd) == f"{compose.resolve()},{override.resolve()}"


def test_detect_already_split_stack(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    ws_cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    managed_cfg = (
        "/Users/me/.local/share/hc/core-runtime-service/deploy/dev/docker-compose.reload.yml,"
        "/Users/me/.local/share/hc/compose-overrides/frontend-vite.hc.yml"
    )
    containers = [
        _container(service="core-runtime", config_files=ws_cfg),
        _container(service="redis", config_files=managed_cfg),
        _container(service="frontend-vite", config_files=managed_cfg),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=ws_cfg)
    assert issue is not None
    assert issue.already_split is True
    assert issue.would_split is False
    assert len(issue.groups) == 2


def test_detect_would_split_on_config_mismatch(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    existing_cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    planned_cfg = (
        f"{existing_cfg},"
        "/Users/me/HomeConsole/.local/share/hc/compose-overrides/frontend-vite.hc.yml"
    )
    containers = [_container(service="core-runtime", config_files=existing_cfg)]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout=json.dumps(containers[0]) + "\n",
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=planned_cfg)
    assert issue is not None
    assert issue.already_split is False
    assert issue.would_split is True


def test_no_issue_when_single_config(tmp_path: Path, monkeypatch) -> None:
    plan = _make_plan(tmp_path)
    monkeypatch.setattr(diag_mod, "_compose_project_name", lambda p: "dev")

    cfg = "/Users/me/HomeConsole/core-runtime-service/deploy/dev/docker-compose.reload.yml"
    containers = [
        _container(service="core-runtime", config_files=cfg),
        _container(service="redis", config_files=cfg),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split(plan, planned_config_files=cfg)
    assert issue is None


def test_detect_for_project_status_only(tmp_path: Path, monkeypatch) -> None:
    cfg_a = "/tmp/a/deploy/dev/docker-compose.reload.yml"
    cfg_b = "/tmp/b/deploy/dev/docker-compose.reload.yml"
    containers = [
        _container(service="caddy", config_files=cfg_a),
        _container(service="redis", config_files=cfg_b),
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "ps"]:
            return MagicMock(
                returncode=0,
                stdout="\n".join(json.dumps(c) for c in containers),
            )
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    issue = diag_mod.detect_compose_stack_split_for_project("dev")
    assert issue is not None
    assert issue.already_split is True
    assert issue.would_split is False


def test_suggest_workspace_prefers_cwd_monorepo(tmp_path: Path, monkeypatch) -> None:
    repo = _make_monorepo(tmp_path)
    monkeypatch.chdir(repo / "core-runtime-service")
    monkeypatch.setattr("hc.core_source.detect_workspace_root", lambda: repo.resolve())

    ws_cfg = str(repo / "core-runtime-service/deploy/dev/docker-compose.reload.yml")
    managed_cfg = str(CORE_SRC_DIR / "deploy/dev/docker-compose.reload.yml")
    issue = diag_mod.ComposeStackSplitIssue(
        project_name="dev",
        groups=(
            diag_mod.ComposeStackSplitGroup(ws_cfg, ("postgres",), "workspace"),
            diag_mod.ComposeStackSplitGroup(managed_cfg, ("frontend-vite",), "managed"),
        ),
        mixed_sources=True,
        already_split=True,
    )
    assert diag_mod.suggest_workspace_for_split_fix(issue) == repo.resolve()


def test_suggest_workspace_from_running_workspace_group(tmp_path: Path, monkeypatch) -> None:
    repo = _make_monorepo(tmp_path, "Work")
    monkeypatch.setattr("hc.core_source.detect_workspace_root", lambda: None)

    ws_cfg = str(repo / "core-runtime-service/deploy/dev/docker-compose.reload.yml")
    managed_cfg = str(CORE_SRC_DIR / "deploy/dev/docker-compose.reload.yml")
    issue = diag_mod.ComposeStackSplitIssue(
        project_name="dev",
        groups=(
            diag_mod.ComposeStackSplitGroup(ws_cfg, ("postgres",), "workspace"),
            diag_mod.ComposeStackSplitGroup(managed_cfg, ("frontend-vite",), "managed"),
        ),
        mixed_sources=True,
        already_split=True,
    )
    assert diag_mod.suggest_workspace_for_split_fix(issue) == repo.resolve()


def test_apply_fix_persists_workspace_and_stops_project(tmp_path: Path, monkeypatch) -> None:
    import sys

    repo = _make_monorepo(tmp_path)
    issue = diag_mod.ComposeStackSplitIssue(
        project_name="dev",
        groups=(diag_mod.ComposeStackSplitGroup("a", ("postgres",), None),),
        already_split=True,
    )
    monkeypatch.setattr(diag_mod, "suggest_workspace_for_split_fix", lambda _i: repo.resolve())

    saved: dict[str, str] = {}

    class _Cfg:
        class workspace:
            path = ""

        def save(self) -> None:
            saved["path"] = self.workspace.path

    monkeypatch.setattr("hc.config.Config.load", lambda: _Cfg())

    docker_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        docker_calls.append(list(cmd))
        if cmd[:3] == ["docker", "ps", "-aq"]:
            return MagicMock(returncode=0, stdout="cid1\ncid2\n")
        if cmd[:3] == ["docker", "rm", "-f"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr(diag_mod.subprocess, "run", fake_run)

    console = MagicMock()
    assert diag_mod.apply_compose_stack_split_fix(console, issue) is True
    assert saved["path"] == str(repo.resolve())
    assert ["docker", "rm", "-f", "cid1", "cid2"] in docker_calls


def test_apply_fix_works_without_tty(monkeypatch, tmp_path: Path) -> None:
    repo = _make_monorepo(tmp_path)
    issue = diag_mod.ComposeStackSplitIssue(
        project_name="dev",
        groups=(),
        already_split=True,
    )
    monkeypatch.setattr(diag_mod, "suggest_workspace_for_split_fix", lambda _i: repo.resolve())
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))

    class _Cfg:
        class workspace:
            path = ""

        def save(self) -> None:
            pass

    monkeypatch.setattr("hc.config.Config.load", lambda: _Cfg())
    monkeypatch.setattr(
        diag_mod.subprocess,
        "run",
        lambda *a, **k: MagicMock(returncode=0, stdout=""),
    )

    console = MagicMock()
    assert diag_mod.apply_compose_stack_split_fix(console, issue) is True


def test_validate_core_source_tree_rejects_merge_conflicts(tmp_path: Path) -> None:
    core = tmp_path / "core-runtime-service"
    bad_file = core / "core" / "kernel" / "broken.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text(
        'def f():\n    """doc"""\n<<<<<<< Updated upstream\n    pass\n',
        encoding="utf-8",
    )
    try:
        diag_mod.validate_core_source_tree(core)
        assert False, "expected HcCliError"
    except diag_mod.HcCliError as exc:
        assert "merge conflict" in exc.message


def test_ensure_workspace_pinned_saves_detected_monorepo(tmp_path: Path, monkeypatch) -> None:
    from hc.core_source import ensure_workspace_pinned

    repo = _make_monorepo(tmp_path)
    monkeypatch.setattr("hc.core_source.detect_workspace_root", lambda: repo.resolve())

    saved: dict[str, str] = {}

    class _Cfg:
        class workspace:
            path = ""

        def save(self) -> None:
            saved["path"] = self.workspace.path

    monkeypatch.setattr("hc.config.Config.load", lambda: _Cfg())

    assert ensure_workspace_pinned(quiet=True) == repo.resolve()
    assert saved["path"] == str(repo.resolve())


def test_offer_fix_skipped_when_not_tty(monkeypatch, tmp_path: Path) -> None:
    """offer_fix — алиас apply; без монорепо фикс не применяется."""
    issue = diag_mod.ComposeStackSplitIssue(
        project_name="dev",
        groups=(),
        already_split=True,
    )
    monkeypatch.setattr(diag_mod, "suggest_workspace_for_split_fix", lambda _i: None)
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    console = MagicMock()
    assert diag_mod.offer_fix_compose_stack_split(console, issue) is False
