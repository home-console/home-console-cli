from __future__ import annotations

from hc.commands.recovery import compose as c


def test_ensure_sections_has_root_maps() -> None:
    txt = c._ensure_sections("")  # type: ignore[attr-defined]
    assert "services:" in txt
    assert "volumes:" in txt


def test_enable_disable_roundtrip_keeps_valid_sections() -> None:
    base = c._ensure_sections("")  # type: ignore[attr-defined]
    # enable redis: inject services+volumes blocks
    s, v = c._blocks("redis")  # type: ignore[attr-defined]
    t = c._inject_into_root_section(base, "services", s)  # type: ignore[attr-defined]
    t = c._inject_into_root_section(t, "volumes", v)  # type: ignore[attr-defined]
    assert "# BEGIN hc recovery: redis" in t
    # disable redis: remove managed block
    t2 = c._remove_managed_block(t, "redis")  # type: ignore[attr-defined]
    t2 = c._ensure_sections(t2)  # type: ignore[attr-defined]
    assert "# BEGIN hc recovery: redis" not in t2
    # still contains root sections
    assert "services" in t2
    assert "volumes" in t2

