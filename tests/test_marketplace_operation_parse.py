"""Тесты разбора ответа marketplace install (вложенная операция)."""

from hc.marketplace_operation import parse_marketplace_operation_view


def test_parse_success_wrapped() -> None:
    payload = {
        "ok": True,
        "result": {
            "status": "completed",
            "result": {
                "status": "success",
                "data": {"name": "p", "version": "1.0.0", "path": "/app/plugins/p"},
                "error": None,
            },
        },
    }
    v = parse_marketplace_operation_view(payload)
    assert v.ok is True
    assert v.data and v.data["name"] == "p"


def test_parse_domain_failure_with_hints() -> None:
    payload = {
        "ok": True,
        "result": {
            "status": "completed",
            "result": {
                "status": "failure",
                "error": "[marketplace:manifest] bad",
                "error_stage": "manifest",
                "user_message": "RU\nПодробнее: bad",
                "data": None,
            },
        },
    }
    v = parse_marketplace_operation_view(payload)
    assert v.ok is False
    assert v.error_stage == "manifest"
    assert "RU" in (v.user_message or "")


def test_parse_top_level_ok_false() -> None:
    v = parse_marketplace_operation_view({"ok": False, "error": "Forbidden", "code": "X"})
    assert v.ok is False
    assert "Forbidden" in (v.error or "")


def test_parse_operation_failed_worker() -> None:
    payload = {
        "ok": True,
        "result": {
            "status": "failed",
            "error_code": "failed",
            "error": "boom",
        },
    }
    v = parse_marketplace_operation_view(payload)
    assert v.ok is False
    assert "boom" in (v.error or "")


def test_parse_raw_operation_no_envelope() -> None:
    payload = {
        "status": "completed",
        "result": {
            "status": "failure",
            "error": "e",
            "user_message": "u",
        },
    }
    v = parse_marketplace_operation_view(payload)
    assert v.ok is False
    assert v.user_message == "u"
