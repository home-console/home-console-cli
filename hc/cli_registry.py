from __future__ import annotations

"""Единый реестр команд для `hc nav` и REPL (держать в синхроне)."""

# Typer-группы: `use <group>` в shell, затем подкоманда.
REPL_GROUPS: frozenset[str] = frozenset(
    {
        "core",
        "auth",
        "env",
        "deploy",
        "update",
        "plugin",
        "module",
        "recovery",
        "reset",
        "secrets",
        "marketplace",
        "config",
        "workspace",
    }
)

NAV_TREE: dict[str, dict[str, object]] = {
    "connect": {"desc": "Подключение к core", "children": {}},
    "status": {"desc": "Проверка статуса API (--watch)", "children": {}},
    "ping": {"desc": "Доступность Core без авторизации", "children": {}},
    "setup": {"desc": "Мастер первого запуска", "children": {}},
    "env": {
        "desc": "Локальное dev-окружение (hot-reload)",
        "children": {
            "up": {"desc": "Поднять сервисы (--profile, --db, --dry-run)", "children": {}},
            "down": {"desc": "Остановить окружение (--volumes, --dry-run)", "children": {}},
            "pull": {"desc": "git pull исходников core-runtime-service", "children": {}},
            "ps": {"desc": "Контейнеры, порты, URL", "children": {}},
            "exec": {"desc": "Команда в контейнере (sh по умолчанию)", "children": {}},
            "logs": {"desc": "Логи сервисов", "children": {}},
            "restart": {"desc": "Перезапустить сервис(ы)", "children": {}},
            "rebuild": {"desc": "Пересборка образов", "children": {}},
            "status": {"desc": "Статус контейнеров", "children": {}},
            "stats": {"desc": "CPU/RAM/NET (--watch)", "children": {}},
            "health": {"desc": "Healthcheck сервисов", "children": {}},
            "clean": {"desc": "Очистить orphan Docker images/volumes", "children": {}},
            "dotenv": {
                "desc": "Управление .env файлом core-runtime-service",
                "children": {
                    "show": {"desc": "Показать .env (секреты маскируются)", "children": {}},
                    "set": {"desc": "Добавить/обновить KEY=VALUE", "children": {}},
                    "unset": {"desc": "Удалить переменную", "children": {}},
                    "edit": {"desc": "Открыть в $EDITOR (SSH: download→edit→upload)", "children": {}},
                },
            },
        },
    },
    "core": {
        "desc": "CoreRuntime: init/update/up/down и .env",
        "children": {
            "init": {"desc": "Клонировать исходники", "children": {}},
            "update": {"desc": "git pull исходников", "children": {}},
            "up": {"desc": "Поднять core (docker/native)", "children": {}},
            "down": {"desc": "Остановить core", "children": {}},
        },
    },
    "deploy": {
        "desc": "Деплой core/platform/stack (image-based, local/remote)",
        "children": {
            "core": {"desc": "build / push / rollout / wait / logs", "children": {}},
            "platform": {"desc": "Deploy platform web", "children": {}},
            "stack": {"desc": "Полный image stack (dev|prod)", "children": {}},
            "rollback": {"desc": "Откат core на тег (или last_tag из config)", "children": {}},
            "config": {"desc": "Параметры deploy по умолчанию", "children": {}},
        },
    },
    "rollback": {"desc": "Откат core-runtime на тег (или последний задеплоенный)", "children": {}},
    "update": {"desc": "Обновление core image", "children": {"core": {"desc": "Обновить core", "children": {}}}},
    "plugin": {"desc": "Управление плагинами", "children": {}},
    "module": {"desc": "Модули core", "children": {}},
    "logs": {"desc": "Логи Core (--module для фильтра по модулю)", "children": {}},
    "search": {"desc": "Поиск", "children": {}},
    "install": {"desc": "Установка компонентов (--dry-run)", "children": {}},
    "remove": {"desc": "Удаление компонентов (--dry-run)", "children": {}},
    "auth": {"desc": "JWT / API key", "children": {}},
    "secrets": {"desc": "SecretStore Core", "children": {}},
    "recovery": {"desc": "Recovery-стек (db, backup, compose)", "children": {}},
    "reset": {"desc": "Сброс конфига/кэша", "children": {}},
    "doctor": {
        "desc": "Диагностика: --quick | --api | полная",
        "children": {},
    },
    "marketplace": {"desc": "Каталог плагинов", "children": {}},
    "config": {
        "desc": "Конфигурация ~/.config/hc/config.toml",
        "children": {
            "show": {"desc": "Показать конфиг", "children": {}},
            "set": {"desc": "Установить ключ", "children": {}},
            "edit": {"desc": "Открыть в редакторе", "children": {}},
        },
    },
    "workspace": {
        "desc": "Привязка к локальному монорепо разработчика",
        "children": {
            "status": {"desc": "Активный workspace и его источник", "children": {}},
            "set": {"desc": "Записать workspace.path в config", "children": {}},
            "use": {"desc": "Алиас для set", "children": {}},
            "unset": {"desc": "Убрать workspace.path из config", "children": {}},
        },
    },
    "version": {"desc": "Версия CLI и проверка PyPI", "children": {}},
    "upgrade": {"desc": "Обновить homeconsole-cli", "children": {}},
    "repl": {"desc": "Интерактивный режим", "children": {}},
    "shell": {"desc": "Алиас для repl", "children": {}},
    "nav": {"desc": "Навигация по командам", "children": {}},
}

# Порядок важен для удобства в shell; ключи из NAV_TREE добавляются, если забыли.
_REPL_PRIMARY: tuple[str, ...] = (
    "connect",
    "status",
    "ping",
    "env",
    "core",
    "deploy",
    "rollback",
    "update",
    "plugin",
    "module",
    "install",
    "remove",
    "search",
    "logs",
    "auth",
    "secrets",
    "marketplace",
    "setup",
    "doctor",
    "recovery",
    "reset",
    "config",
    "workspace",
    "version",
    "upgrade",
    "nav",
)

_REPL_META: tuple[str, ...] = (
    "shell",
    "repl",
    "help",
    "?",
    "exit",
    "back",
    "..",
    "use",
    "history",
    "clear",
)


def repl_root_commands() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in (*_REPL_PRIMARY, *sorted(NAV_TREE.keys()), *_REPL_META):
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out
