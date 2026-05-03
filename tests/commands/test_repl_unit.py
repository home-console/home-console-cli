from __future__ import annotations

from prompt_toolkit.completion.base import Completion
from prompt_toolkit.document import Document

from hc.repl import _HCCompleter, _split_batch


def test_split_batch_splits_semicolon_and_and_outside_quotes() -> None:
    assert _split_batch('echo "a;b" && foo; bar') == [
        ('echo "a;b"', "&&"),
        ("foo", ";"),
        ("bar", None),
    ]


def test_split_batch_handles_single_quotes() -> None:
    assert _split_batch("x 'a;b' && y") == [("x 'a;b'", "&&"), ("y", None)]


def _completion_texts(completions: list[Completion]) -> list[str]:
    return sorted({c.text for c in completions})


def test_hc_completer_offers_plugin_names_for_install() -> None:
    c = _HCCompleter(commands=["install", "status"], plugins=["p1", "p2"])
    doc = Document("install ", cursor_position=len("install "))
    comps = list(c.get_completions(doc, complete_event=None))  # type: ignore[arg-type]
    assert _completion_texts(comps) == ["p1", "p2"]


def test_hc_completer_offers_plugin_names_for_plugin_subcommands() -> None:
    c = _HCCompleter(commands=["plugin"], plugins=["alpha"])
    doc = Document("plugin start ", cursor_position=len("plugin start "))
    comps = list(c.get_completions(doc, complete_event=None))  # type: ignore[arg-type]
    assert _completion_texts(comps) == ["alpha"]
