"""SafeFileHistory: переживает удаление parent-папки и работает на свежей FS."""
from __future__ import annotations

import shutil
from pathlib import Path

from hc.repl import SafeFileHistory


def test_store_creates_parent_when_missing(tmp_path: Path) -> None:
    """На свежей FS, без parent-папки — store_string должен сам её создать."""
    target = tmp_path / "deep" / "nested" / "history"
    assert not target.parent.exists()

    h = SafeFileHistory(str(target))
    h.store_string("env up")

    assert target.exists()
    assert "env up" in target.read_text(encoding="utf-8")


def test_store_after_parent_deleted_recreates(tmp_path: Path) -> None:
    """Симуляция `hc reset all` внутри живой REPL-сессии."""
    cfg_dir = tmp_path / ".config" / "hc"
    cfg_dir.mkdir(parents=True)
    history_file = cfg_dir / "history"

    h = SafeFileHistory(str(history_file))
    h.store_string("первая команда")

    # Юзер вызвал `hc reset all` — снесли всю папку
    shutil.rmtree(cfg_dir)
    assert not cfg_dir.exists()

    # Следующая команда из REPL не должна крашиться
    h.store_string("вторая команда")

    assert history_file.exists()
    content = history_file.read_text(encoding="utf-8")
    assert "вторая команда" in content


def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Загрузка истории до её существования не должна падать."""
    h = SafeFileHistory(str(tmp_path / "nonexistent"))
    assert list(h.load_history_strings()) == []


def test_load_works_after_store(tmp_path: Path) -> None:
    target = tmp_path / "history"
    h = SafeFileHistory(str(target))
    h.store_string("a")
    h.store_string("b")
    # FileHistory отдаёт строки в обратном порядке (свежие сверху)
    assert "a" in list(h.load_history_strings())
    assert "b" in list(h.load_history_strings())
