# Changelog

## [0.0.9] — 2026-05-17

### Added
- `hc core restart [--mode docker|native]` — перезапуск Core одной командой (down → up)
- `hc core up --foreground` / `-f` — запуск native Core прямо в TTY (stdout в терминал, Ctrl+C для остановки)
- `hc core signal <reload|dump|quit|term|int|N>` — отправить UNIX-сигнал native Core процессу
  - `reload` → SIGHUP (перечитать конфиг без перезапуска)
  - `dump` → SIGUSR1 (дамп состояния в лог)
- `hc emergency inspect` — дамп состояния БД без API: пользователи, сессии, API-ключи, все namespaces
- `hc emergency reset-admin` — сброс пароля пользователя напрямую в SQLite (bcrypt); интерактивный ввод; автоотзыв сессий
- `hc emergency list-users` — список пользователей из БД без API
- `hc emergency revoke-sessions <user_id>` — инвалидировать сессии пользователя напрямую в БД
- `hc plugin new <name>` — scaffolding плагина: `plugin.py`, `__init__.py`, `plugin.json`, `requirements.txt`
  - флаги `--description`, `--author`, `--capability` (повторяемый), `--output`, `--force`
- Unix socket транспорт в `HCClient` — если задан `RUNTIME_SOCKET_PATH` в Core или `HC_SOCKET` env / `core.socket_path` в config — CLI подключается через UDS вместо HTTP
- `core.socket_path` добавлен в конфиг (`~/.config/hc/config.toml`)
- `bcrypt>=4.0` добавлен как зависимость пакета
- `hc service list` — все зарегистрированные сервисы ядра с фильтром `--plugin` / `--filter`
- `hc event list` — snapshot текущих подписок на события
- `hc event tail [--filter glob]` — live SSE-стрим событий event bus (Ctrl+C для остановки)
- `hc plugin dev <path>` — dev-режим плагина: polling-watch файлов → sync → auto-reload
- `hc shell` — новый цветной промпт `[hc@host:user ●]▶` с индикатором online/offline
- `hc shell` — `!cmd` для выполнения системных команд прямо из hc shell
- `hc shell` — новый баннер с версией и статусом Core в панели
- `hc shell-config show/install/uninstall`
- `hc service call <name> [--json <kwargs>]` — вызвать сервис ядра напрямую из CLI
- `hc event emit <type> [--json <data>]` — послать событие в event bus
- `hc plugin capabilities list` — все зарегистрированные capability и провайдеры
- `hc plugin capabilities who-provides <cap>` — какой плагин даёт capability
- `hc core dump [--output file.json]` — дамп живого состояния ядра (плагины, сервисы, модули, события)

### Changed (core-runtime-service)
- `modules/admin/http_endpoints.py`: добавлен endpoint `GET /api/v1/admin/inspector/capabilities`
- `modules/api/route_binding.py`: добавлены `POST /api/v1/admin/services/{name}/call` и `POST /api/v1/admin/events/emit`
- `hc core ps --mode native` — теперь показывает uptime, memory RSS, CPU% (через `ps`)
- `hc module inspect <name>` — детальная информация о модуле ядра
- `hc config edit` — открывает `$EDITOR`, после закрытия валидирует конфиг
- `hc status --components` / `-c` — статус каждого компонента: API, Modules, Plugins, Storage — установка zsh/bash/fish конфига с алиасами и промптом
  - алиасы: `plugins`, `events`, `core-status`, `core-restart`, `emergency`, `services`
  - функция `hcs` — быстрый вход в hc shell
  - RPROMPT/right prompt показывает статус Core (● online / ○ offline)
  - `--mode auto|native|docker` — автодетект по наличию запущенного контейнера
  - `--interval` — частота проверки (default 1s)
  - `--no-reload` — только sync, без API reload
  - `--compose` — путь к compose-файлу для docker-режима

### Changed (core-runtime-service)
- `modules/api/module.py`: uvicorn слушает Unix socket если задан `RUNTIME_SOCKET_PATH` в `.env`
- `modules/api/route_binding.py`: добавлен SSE endpoint `GET /api/v1/admin/inspector/events/stream`

## [0.0.8] — 2026-05-16

### Fixed
- `hc env down`: останавливает контейнеры compose-профилей (postgres, frontend) — без `--profile` они игнорировались
- `hc env down`: передаёт только активные профили (по запущенным контейнерам), предупреждает перед удалением SQLite-томов
- `hc env down -v`: после очистки показывает оставшиеся project volumes и команды `docker volume rm`

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
