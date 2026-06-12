from __future__ import annotations

import secrets
from pathlib import Path

from rich.console import Console


def ensure_core_env(console: Console, core_path: Path) -> bool:
    """Готовит `core-runtime-service/.env`, чтобы docker-compose мог стартануть.

    Возвращает True, если `.env` был создан заново (с новым RUNTIME_MASTER_KEY) —
    в этом случае любой существующий vault в volume core-data зашифрован
    под старый ключ и должен быть сброшен (см. `_reset_stale_vault` в core_ops.py).
    """
    env_path = core_path / ".env"
    if env_path.exists():
        return False

    example = core_path / ".env.example"
    if not example.exists():
        console.print(f"[yellow]Не нашёл шаблон .env.example в {core_path}[/yellow]")
        return False

    content = example.read_text(encoding="utf-8", errors="replace")
    if "RUNTIME_MASTER_KEY" not in content:
        content = "RUNTIME_MASTER_KEY=\n" + content

    key = secrets.token_hex(32)
    # Если в шаблоне есть строка RUNTIME_MASTER_KEY=, подставим значение.
    lines: list[str] = []
    replaced = False
    for line in content.splitlines():
        if line.startswith("RUNTIME_MASTER_KEY=") and not replaced:
            lines.append(f"RUNTIME_MASTER_KEY={key}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.insert(0, f"RUNTIME_MASTER_KEY={key}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]✓[/green] Создал {env_path} (с новым RUNTIME_MASTER_KEY)")
    return True


def core_env_path(core_path: Path) -> Path:
    return core_path / ".env"

