# Changelog

## [0.0.7] — 2026-05-16

### Fixed
- Краш `AttributeError: 'HCClient' object attribute '_auth_hint' is read-only` на Python 3.14
  — monkey-patching методов на `slots=True` датаклассе запрещён в 3.14;
  заменено на поле `silent: bool` с проверкой внутри методов

## [0.0.6] — 2026-05-16

### Added
- `hc env rebuild` — пересборка образов с интерактивным выбором сервисов (тот же UX что у `hc env up`): чекбоксы, `--profile`, `--no-cache`

### Changed
- `hc core update` — теперь обновляет **исходники Core** (`git pull --ff-only`), а не Docker-образ
- `hc update core` остаётся каноническим способом обновить Docker-образ из registry
- Удалён дублирующий алиас `hc core update → hc update core`

## [0.0.5] — 2026-05-16

### Added
- `hc env stats` — CPU%, RAM, NET I/O, BLOCK I/O контейнеров; `--watch` для live-режима
- `hc env health` — healthcheck статус каждого сервиса (healthy / unhealthy / starting)
- `hc status --watch` — live-мониторинг Core API с латентностью (avg за последние 10 запросов)
- `hc doctor` — полная диагностика: Docker, git, конфиг, исходники Core, compose-файлы, порты, диск
- `hc shell`: уведомление о новой версии при старте — проверяет PyPI раз в 24 часа в фоне, не блокирует запуск (кэш `~/.local/state/hc/version_check.json`)
- `hc env up`: автоматический `git pull --ff-only` исходников Core перед запуском — только если рабочее дерево чистое; при локальных изменениях молча пропускает

## [0.0.4] — 2026-05-16

### Fixed
- `hc core update` / `hc deploy core`: при ошибке `docker compose pull` (denied / unauthorized) теперь выводится подсказка с командой `docker login <registry>` вместо молчаливого выхода
- `hc env up`: исправлено ложное определение монорепо — `_find_repo_root()` теперь требует наличия других известных папок монорепо рядом с `core-runtime-service`, чтобы не принять `~/core-runtime-service` за корень монорепо
- `hc env up`: при отсутствии compose-файла для выбранного режима ошибка теперь показывает, какие режимы реально доступны, и предлагает конкретную команду для запуска
- `DEFAULT_CORE_IMAGE`: исправлено имя образа (`ghcr.io/home-console/core-runtime` → `ghcr.io/home-console/core-runtime-service`)

### Added
- `hc env up`: на чистой машине при отсутствии исходников Core предлагает скачать их автоматически (`git clone` из публичного репо) вместо ошибки с выходом

## [0.0.3] — 2026-05-15

### Fixed
- Версия CLI теперь читается из метаданных установленного пакета (`importlib.metadata`) — единственный источник истины в `pyproject.toml`
- `hc shell`: при старте больше не выводятся двойные ошибки «Core недоступен» когда Core не запущен
- `hc shell`: баннер теперь точно отражает состояние: `connected` / `offline • configured for` / `not connected`
- Удалена неиспользуемая константа `APP_VERSION`

## [0.0.2] — 2026-05-14

### Added
- Команда `hc env` — dev-окружение с интерактивным выбором сервисов и БД (`up` / `down` / `logs` / `restart` / `status`)
- Профили окружения: `base` | `backend` | `platform` | `hmr` | `full`
- Поддержка PostgreSQL и SQLite через radio-выбор при `hc env up`

### Fixed
- API-префиксы, конфигурация deploy/update
- `hc deploy`: условный `compose pull`, импорт secrets

## [0.0.1] — первоначальный релиз

### Added
- Базовая реализация CLI: `hc connect`, `hc status`, `hc plugin`, `hc deploy`, `hc auth`, `hc core`, `hc setup`
