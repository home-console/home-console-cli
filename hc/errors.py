from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HcCliError(Exception):
    """
    Ошибка уровня CLI: понятное сообщение + опциональная подсказка + exit code.

    Важно: текст берём из `message`, а не из стандартного `Exception.args`,
    чтобы сериализация в JSON была стабильной.
    """

    message: str
    exit_code: int = 1
    hint: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return self.message


class DockerNotFoundError(HcCliError):
    pass


class CoreSourcesNotFoundError(HcCliError):
    pass


class InvalidModeError(HcCliError):
    pass


class HealthyTimeoutError(HcCliError):
    pass


def json_error_payload(command: str, exc: BaseException, *, default_exit_code: int = 1) -> dict[str, object]:
    """
    Унифицированный JSON для ошибок:
    - ok: false
    - command: имя команды/пайплайна
    - exit_code: int
    - error: класс ошибки
    - message: человекочитаемая причина
    - hint: опциональная подсказка
    """

    if isinstance(exc, HcCliError):
        payload: dict[str, object] = {
            "ok": False,
            "command": command,
            "exit_code": int(exc.exit_code),
            "error": exc.__class__.__name__,
            "message": exc.message,
        }
        if exc.hint:
            payload["hint"] = exc.hint
        return payload

    # TyperExit может прилетать из глубины (subprocess return codes, и т.п.)
    # Сообщение там не хранится, поэтому даём нейтральный текст.
    try:
        import typer  # local import, чтобы не тащить зависимость везде

        if isinstance(exc, typer.Exit):
            return {
                "ok": False,
                "command": command,
                "exit_code": int(getattr(exc, "exit_code", None) or default_exit_code),
                "error": exc.__class__.__name__,
                "message": "Команда завершилась с ошибкой.",
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": False,
        "command": command,
        "exit_code": int(default_exit_code),
        "error": exc.__class__.__name__,
        "message": str(exc) or "Неизвестная ошибка.",
    }

