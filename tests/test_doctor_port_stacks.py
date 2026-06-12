"""Тесты разделения портов doctor на DEV/PROD стеки и алиасов."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

import hc.doctor_lib as dl


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _patch_io(monkeypatch, *, running: set[str], listening: set[int]) -> None:
    """Замокать docker ps и socket-сканирование."""
    monkeypatch.setattr(dl, "_detect_running_stacks", lambda: running)
    monkeypatch.setattr(dl, "_port_listening", lambda p: p in listening)
    # smoke не нужен — ни один порт не listening для http
    monkeypatch.setattr(
        dl, "_http_smoke", lambda p, path="/", timeout=1.5: (True, "HTTP 200")
    )


def _labels(checks: list[dl.DoctorCheck]) -> list[str]:
    return [c.label for c in checks]


def test_ports_auto_shows_only_dev_when_only_dev_running(monkeypatch) -> None:
    _patch_io(monkeypatch, running={"dev"}, listening={18080, 18000})
    checks, _ = dl._checks_ports(stack="auto")
    labels = _labels(checks)
    assert any("Порты [DEV]" in l for l in labels)
    assert not any("Порты [PROD]" in l for l in labels)
    # 5432 PROD не должен показаться (free, hide)
    assert not any(":5432" in l for l in labels)


def test_ports_auto_shows_both_when_both_running(monkeypatch) -> None:
    _patch_io(
        monkeypatch,
        running={"dev", "prod"},
        listening={18080, 18000, 8080, 8000},
    )
    checks, _ = dl._checks_ports(stack="auto")
    labels = _labels(checks)
    assert any("[DEV]" in l for l in labels)
    assert any("[PROD]" in l for l in labels)


def test_ports_auto_no_containers_shows_nothing_or_info(monkeypatch) -> None:
    _patch_io(monkeypatch, running=set(), listening=set())
    checks, _ = dl._checks_ports(stack="auto")
    # Должна быть info-строка про отсутствие контейнеров либо вообще пусто
    assert checks == [] or any(c.status == "info" for c in checks)


def test_ports_explicit_dev_shows_free(monkeypatch) -> None:
    # Явный --dev: показываются ВСЕ dev-порты включая free.
    _patch_io(monkeypatch, running=set(), listening=set())
    checks, _ = dl._checks_ports(stack="dev")
    labels = _labels(checks)
    assert any(":18080" in l for l in labels)
    assert any(":15432" in l for l in labels)
    assert not any("[PROD]" in l for l in labels)


def test_ports_explicit_prod_shows_only_prod(monkeypatch) -> None:
    _patch_io(monkeypatch, running={"dev"}, listening={18080})  # dev running тоже
    checks, _ = dl._checks_ports(stack="prod")
    labels = _labels(checks)
    assert any("[PROD]" in l for l in labels)
    assert not any("[DEV]" in l for l in labels)
    assert any(":5432" in l for l in labels)
    assert any(":6379" in l for l in labels)


def test_ports_all_shows_both_with_free(monkeypatch) -> None:
    _patch_io(monkeypatch, running=set(), listening=set())
    checks, _ = dl._checks_ports(stack="all")
    labels = _labels(checks)
    assert any("[DEV]" in l for l in labels)
    assert any("[PROD]" in l for l in labels)
    assert any(":18080" in l for l in labels)
    assert any(":8080" in l for l in labels)


def test_doctor_cli_dev_flag(runner: CliRunner, monkeypatch, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    _patch_io(monkeypatch, running=set(), listening=set())
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--dev", "--json"])
    assert r.exit_code in (0, 1), r.output
    # JSON содержит DEV порты
    assert "DEV" in r.output or "18080" in r.output


def test_doctor_cli_dev_prod_mutually_exclusive(
    runner: CliRunner, monkeypatch, isolated_home
) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    from hc.main import app

    r = runner.invoke(app, ["doctor", "--dev", "--prod"])
    assert r.exit_code == 2
    assert "взаимоисключающие" in r.output


def test_env_doctor_alias(runner: CliRunner, monkeypatch, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    _patch_io(monkeypatch, running=set(), listening=set())
    from hc.main import app

    r = runner.invoke(app, ["env", "doctor", "--json"])
    assert r.exit_code in (0, 1), r.output


def test_deploy_doctor_alias(runner: CliRunner, monkeypatch, isolated_home) -> None:
    monkeypatch.setattr("hc.main.print_update_banner", lambda *a, **k: None)
    _patch_io(monkeypatch, running=set(), listening=set())
    from hc.main import app

    r = runner.invoke(app, ["deploy", "doctor", "--json"])
    assert r.exit_code in (0, 1), r.output


def test_http_health_paths_dispatched(monkeypatch) -> None:
    """Для известных портов doctor бьёт не «/», а конкретный health-эндпоинт."""
    calls: list[tuple[int, str]] = []

    def _fake_smoke(port: int, *, path: str = "/", timeout: float = 1.5):
        calls.append((port, path))
        return True, "HTTP 200"

    monkeypatch.setattr(dl, "_port_listening", lambda p: True)
    monkeypatch.setattr(dl, "_http_smoke", _fake_smoke)

    for port in (18080, 18000, 8080, 8000):
        dl._check_one_port(port, "test", smoke=True)

    paths = dict(calls)
    assert paths[18080] == "/_caddy/health"
    assert paths[18000] == "/api/v1/monitor/health"
    assert paths[8080] == "/_edge/health"
    assert paths[8000] == "/api/v1/monitor/health"


def test_http_smoke_default_path(monkeypatch) -> None:
    """Для неизвестных портов smoke остаётся на «/»."""
    captured: dict[str, str] = {}

    def _fake_smoke(port: int, *, path: str = "/", timeout: float = 1.5):
        captured["path"] = path
        return True, "HTTP 200"

    monkeypatch.setattr(dl, "_port_listening", lambda p: True)
    monkeypatch.setattr(dl, "_http_smoke", _fake_smoke)

    dl._check_one_port(15173, "Vite HMR", smoke=True)
    assert captured["path"] == "/"
