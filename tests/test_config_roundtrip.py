from __future__ import annotations

import importlib
import os


def test_config_roundtrip(tmp_path) -> None:
    os.environ["HOME"] = str(tmp_path)

    import hc.constants as constants

    importlib.reload(constants)
    import hc.config as config

    importlib.reload(config)

    cfg = config.Config.load()
    assert cfg.core.host
    cfg.core.host = "example.com"
    cfg.core.port = 1234
    cfg.core.token = "t"
    cfg.save()

    cfg2 = config.Config.load()
    assert cfg2.core.host == "example.com"
    assert cfg2.core.port == 1234
    assert cfg2.core.token == "t"
    assert cfg2.is_configured()


def test_config_migrates_legacy_deploy_core_mode_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib

    import hc.constants as constants

    importlib.reload(constants)
    from hc.constants import CONFIG_PATH

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        "[core]\n"
        'host = "127.0.0.1"\n'
        "port = 8000\n"
        'token = ""\n'
        'refresh_token = ""\n'
        'auth = "auto"\n'
        "verify_ssl = true\n"
        "[display]\n"
        "color = true\n"
        "emoji = true\n"
        "[recovery]\n"
        'mode = "dev"\n'
        "[deploy]\n"
        'core_image = "ghcr.io/a/b"\n'
        'core_mode = "image"\n'
        'ssh = ""\n'
        'path = ""\n',
        encoding="utf-8",
    )
    import hc.config as config

    importlib.reload(config)
    cfg = config.Config.load()
    assert cfg.deploy.core_mode == "dev-image"
    saved = CONFIG_PATH.read_text(encoding="utf-8")
    assert "dev-image" in saved

