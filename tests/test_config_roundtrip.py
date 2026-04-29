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

