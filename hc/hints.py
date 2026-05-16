from __future__ import annotations

# Подсказки, чтобы не путать похожие команды.
ENV_STACK_HELP = (
    "Локальный dev-стек Docker Compose: up / down / logs / ps / exec. "
    "Не путать с `hc core env` — там только файл .env Core."
)
CORE_DOTENV_HELP = (
    "Файл `.env` CoreRuntime (переменные окружения). "
    "Чтобы поднять dev-стек: `hc env up`."
)
ENV_VS_CORE_DOTENV = (
    "[dim]Подсказка:[/dim] dev-стек → [bold]hc env up[/bold]  |  "
    "только .env Core → [bold]hc core env show[/bold]"
)
SETUP_ENV_HINT = (
    "[dim]Локальный dev-стек (core + proxy + БД):[/dim] [bold]hc env up[/bold]  "
    "[dim]|  только core-контейнер:[/dim] [bold]hc core up[/bold]"
)
