"""Разбор ответа admin marketplace / операций ядра (вложенный Operation.result).

Нюансы контракта Core:
- HTTP обычно 200 и ``{"ok": true, "result": <operation.to_dict()>}`` даже если
  установка логически провалилась: смотреть ``result.result.status``.
- При доменной ошибке маркетплейса в ``result.result`` есть ``error``, опционально
  ``user_message`` и ``error_stage``.
- При падении исполнения операции (исключение в воркере) у операции ``status`` = ``failed``
  и верхний ``error`` / ``error_code`` на уровне ``result``, без нормализованного ``.result``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketplaceOperationView:
    """Унифицированный вывод для CLI/UI."""

    ok: bool
    user_message: str | None  # русская подсказка с сервера, если есть
    error: str | None  # техстрока
    error_stage: str | None
    operation_status: str | None  # completed | failed | …
    domain_status: str | None  # success | failure внутри result.result (если есть)
    data: dict[str, Any] | None
    raw_ok: bool | None  # поле верхнего уровня API, если распознано


def unwrap_api_envelope(payload: dict[str, Any]) -> tuple[bool | None, dict[str, Any] | Any]:
    """
    Если ответ роутера ``{"ok": <bool>, "result": {...}}`` — вернуть (ok_flag, inner_operation).

    Сырой ``operation.to_dict()`` тоже содержит ключ ``result``, но без ``ok`` — тогда не трогаем.
    """
    if "ok" in payload and "result" in payload and isinstance(payload["result"], dict):
        return bool(payload["ok"]), payload["result"]
    return None, payload


def parse_marketplace_operation_view(payload: dict[str, Any] | None) -> MarketplaceOperationView:
    """
    Разобрать JSON ответ ``admin.v1.marketplace.*`` после ``operation.to_dict()``
    или целиком ответ роутера (с обёрткой ``ok``/``result``).
    """
    if not isinstance(payload, dict):
        return MarketplaceOperationView(
            ok=False,
            user_message=None,
            error="Пустой или некорректный ответ API",
            error_stage=None,
            operation_status=None,
            domain_status=None,
            data=None,
            raw_ok=None,
        )

    if payload.get("ok") is False:
        return MarketplaceOperationView(
            ok=False,
            user_message=None,
            error=str(payload.get("error") or "Запрос отклонён"),
            error_stage=None,
            operation_status=None,
            domain_status=None,
            data=None,
            raw_ok=False,
        )

    raw_ok, inner = unwrap_api_envelope(payload)
    op = inner if isinstance(inner, dict) else {}

    op_status = op.get("status")
    if isinstance(op_status, str):
        op_status_l = op_status.lower()
    else:
        op_status_l = None

    nested = op.get("result")
    if isinstance(nested, dict):
        domain_status = nested.get("status")
        if isinstance(domain_status, str):
            domain_l = domain_status.lower()
        else:
            domain_l = None
        err = nested.get("error")
        err_s = str(err) if err is not None else None
        um = nested.get("user_message")
        um_s = str(um) if um is not None else None
        st = nested.get("error_stage")
        st_s = str(st) if st is not None else None
        data = nested.get("data")
        data_d = data if isinstance(data, dict) else None

        domain_ok = domain_l == "success"
        if domain_ok:
            return MarketplaceOperationView(
                ok=True,
                user_message=None,
                error=None,
                error_stage=None,
                operation_status=op_status_l,
                domain_status=domain_l,
                data=data_d,
                raw_ok=raw_ok,
            )
        primary = um_s or err_s or "Операция marketplace завершилась с ошибкой"
        return MarketplaceOperationView(
            ok=False,
            user_message=um_s,
            error=err_s or primary,
            error_stage=st_s,
            operation_status=op_status_l,
            domain_status=domain_l,
            data=data_d,
            raw_ok=raw_ok,
        )

    # Операция упала на уровне воркера / нет handler result
    op_err = op.get("error")
    op_detail = op.get("error_details") or op.get("details")
    err_s = str(op_err) if op_err is not None else None
    if op_detail:
        detail_s = str(op_detail)
        err_full = f"{err_s or 'Ошибка операции'}: {detail_s}" if err_s else detail_s
    else:
        err_full = err_s or "Операция не выполнена"

    return MarketplaceOperationView(
        ok=False,
        user_message=None,
        error=err_full,
        error_stage=None,
        operation_status=op_status_l,
        domain_status=None,
        data=None,
        raw_ok=raw_ok,
    )
