"""Тесты корректной настройки frontend (Caddy HMR + compose override)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import hc.commands.env as env_mod


def _make_plan(tmp_path: Path, services: list[str]) -> SimpleNamespace:
    compose_cwd = tmp_path / "core-runtime-service" / "deploy" / "dev"
    compose_cwd.mkdir(parents=True, exist_ok=True)
    project = SimpleNamespace(
        cwd=compose_cwd,
        compose_file=compose_cwd / "docker-compose.reload.yml",
    )
    return SimpleNamespace(
        service_names=list(services),
        compose_profiles=["frontend"] if "frontend-vite" in services else [],
        db_option=SimpleNamespace(env={"POSTGRES_HOST": "postgres"}),
        project=project,
    )


def test_build_compose_env_sets_caddy_hmr(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, ["core-runtime", "caddy", "frontend-vite"])
    env = env_mod._build_compose_env(plan)
    assert env["CADDYFILE_PATH"] == "./Caddyfile.hmr"
    assert env["POSTGRES_HOST"] == "postgres"


def test_build_compose_env_no_caddy_hmr_without_vite(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, ["core-runtime", "caddy"])
    env = env_mod._build_compose_env(plan)
    assert "CADDYFILE_PATH" not in env


def test_compose_base_cmd_includes_override(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "override.yml"
    override.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(env_mod, "_write_frontend_compose_override", lambda p: override)

    plan = _make_plan(tmp_path, ["frontend-vite"])
    cmd = env_mod._compose_base_cmd(plan)
    assert "-f" in cmd
    assert str(override) in cmd
    assert "--profile" in cmd
    assert "frontend" in cmd


def test_write_frontend_override_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hc.constants.DATA_DIR", tmp_path)

    plan = _make_plan(tmp_path, ["frontend-vite"])
    path = env_mod._write_frontend_compose_override(plan)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "pnpm --filter=web dev" in text
    assert "pnpm api:gen" in text
    assert "VITE_CORE_PROXY_TARGET" in text


def test_override_mounts_core_runtime_for_api_gen(tmp_path: Path, monkeypatch) -> None:
    """api:gen из platform-home-console ищет ../core-runtime-service/openapi.json.

    Внутри контейнера cwd=/workspace, поэтому путь = /core-runtime-service/openapi.json.
    Override должен примонтировать core-runtime-service ровно по этому пути.
    """
    monkeypatch.setattr("hc.constants.DATA_DIR", tmp_path)

    plan = _make_plan(tmp_path, ["frontend-vite"])
    path = env_mod._write_frontend_compose_override(plan)
    text = path.read_text(encoding="utf-8")

    core_root = tmp_path / "core-runtime-service"
    assert f"{core_root}:/core-runtime-service:ro" in text


def test_override_body_changes_with_core_root() -> None:
    """Содержимое override параметризовано путём к core-runtime-service."""
    body_a = env_mod._render_frontend_override_body(Path("/a/core-runtime-service"))
    body_b = env_mod._render_frontend_override_body(Path("/b/core-runtime-service"))
    assert "/a/core-runtime-service:/core-runtime-service:ro" in body_a
    assert "/b/core-runtime-service:/core-runtime-service:ro" in body_b
    assert body_a != body_b
