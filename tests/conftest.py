from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """
    Изолируем HOME, чтобы тесты не трогали реальный ~/.config/hc/config.toml.
    Возвращаем перезагруженный модуль `hc.config`.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    import hc.constants as constants

    importlib.reload(constants)
    import hc.config as config

    importlib.reload(config)
    return config

