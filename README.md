# HomeConsole CLI (`hc`)

CLI-утилита для управления платформой HomeConsole **исключительно через HTTP API** CoreRuntime.

## Установка

### Рекомендуется — `pipx` (Debian / Ubuntu / Raspbian / OrangePi OS)

`pipx` создаёт изолированный venv автоматически и регистрирует `hc` глобально:

```bash
apt install pipx          # или: pip install pipx --user
pipx ensurepath           # добавляет ~/.local/bin в PATH (один раз)
pipx install homeconsole-cli
```

После `pipx ensurepath` перезапусти шелл или выполни `source ~/.bashrc`.

Обновление:
```bash
pipx upgrade homeconsole-cli
```

### Альтернатива — `pip --user`

```bash
pip install --user homeconsole-cli
```

Убедись, что `~/.local/bin` в `PATH`:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

### Локальная разработка (из исходников)

```bash
cd home-console-cli
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Проверка:

```bash
hc --help
hc nav
```

## Конфигурация

Файл: `~/.config/hc/config.toml`

```toml
[core]
host = "localhost"
port = 8080
token = "jwt-token-here"
verify_ssl = true

[display]
color = true
emoji = true
```

Токен берётся по приоритету:

- `--token` (если у команды есть такой флаг)
- переменная окружения `HC_TOKEN`
- `config.toml`

## Быстрый старт

Подключиться и сохранить конфиг:

```bash
hc connect localhost --port 8080
```

Статус:

```bash
hc status
```

Список плагинов:

```bash
hc plugin list
```

## Команды

### Подключение и Core API

- `hc connect <host> [--port 8080] [--token TOKEN]`
- `hc status` — статус Core; `--watch` / `-w` для live-мониторинга с латентностью
- `hc ping` — доступность Core без авторизации
- `hc auth` — JWT / API key
- `hc plugin list|start|stop|info|…`
- `hc module list|status`
- `hc logs [--follow] [--module <name>] [--level debug|info|warning|error]`
- `hc search <query>`
- `hc install <name> [--version X.Y.Z] [--dry-run]`
- `hc remove <name> [--force] [--dry-run]`
- `hc marketplace` — каталог плагинов
- `hc secrets` — SecretStore Core

### Конфигурация CLI

- `hc config show` — весь `~/.config/hc/config.toml` (токены скрыты)
- `hc config set <ключ> <значение>` — например `hc config set core.port 8080`
- `hc config edit` — открыть файл в `$EDITOR`
- `hc deploy config …` — только дефолты деплоя (image, mode, ssh)

Ключи для `hc config set`: `core.host`, `core.port`, `core.token`, `core.auth`, `core.verify_ssl`, `display.color`, `display.emoji`, `recovery.mode`, `deploy.core_image`, `deploy.core_mode`, `deploy.ssh`, `deploy.path`.

### Версия CLI

- `hc version` — версия и проверка PyPI
- `hc upgrade` — обновить через pipx (если установлено так) или `pip install -U`
- `hc upgrade --check` — только проверить, есть ли новее

При любом запуске команды (кроме `shell` / `repl`) CLI показывает баннер, если на PyPI есть более новая версия.

### Локальное dev-окружение

- `hc env up` — интерактивный выбор сервисов и БД (SQLite / PostgreSQL); последний выбор сохраняется в `~/.local/state/hc/last_env.json`
- `hc env up --dry-run` / `hc env down --dry-run` — план без запуска docker
- `hc env pull` — `git pull --ff-only` исходников core-runtime-service
- `hc env ps` — контейнеры, порты и подсказки URL
- `hc env exec <service> [cmd…]` — зайти в контейнер (по умолчанию `sh`)
- `hc env down` / `logs` / `restart` / `status` / `rebuild`

Не путать: **`hc env`** — dev-стек Docker; **`hc core env`** — только файл `.env` Core.
- `hc env stats [--watch]` — CPU/RAM/сеть контейнеров
- `hc env health` — healthcheck сервисов
- Профили: `base` | `backend` | `platform` | `hmr` | `full` — `hc env up --profile hmr`

### Core (исходники и compose)

- `hc core init` / `hc core update` — клон / `git pull` исходников
- `hc core up|down|logs|status` — docker или `--mode native`
- `hc core env` — файл `.env` Core (не путать с `hc env`)

### Деплой и обновление образов

- `hc deploy` (по умолчанию: build+push+rollout+wait) и подкоманды `hc deploy core|platform|stack|config`
- `hc deploy platform --mode image --image ghcr.io/home-console/platform-home-console --tag latest`
- `hc deploy stack dev` / `hc deploy stack prod`
- `hc update core …` — обновить Docker-образ core из registry

### Диагностика и recovery

- `hc doctor` — полная диагностика; `hc doctor --quick` — Docker/конфиг/режимы; `hc doctor --api` — Core API
- `hc doctor --json` / `hc status --json` / `hc plugin list --json` / `hc env ps --json`
- `hc recovery …` — recovery-стек (core, db, compose, backup)
- `hc setup` — мастер первого запуска

### Прочее

- `hc shell` / `hc repl` — интерактивный режим
- `hc nav [section ...]` — навигация по командам без запоминания синтаксиса

### Навигация по командам (без запоминания)

```bash
hc nav
hc nav deploy
hc nav deploy dev
```

Подсказка: на любом уровне можно проваливаться дальше и смотреть доступные разделы.

### Быстрый dev up по профилям

```bash
hc deploy dev up --profile base
hc deploy dev up --profile platform
hc deploy dev up --profile cache
hc deploy dev up --profile db
```

Профили:
- `base` = `core+proxy`
- `platform` = `core+proxy+platform`
- `cache` = `core+proxy+platform+cache`
- `db` = `core+proxy+platform+cache+db`

### Deploy “одной командой”

По умолчанию `hc deploy` делает полный жизненный цикл:

- **build**: `docker build -t <image>:<tag>`
- **push**: `docker push <image>:<tag>`
- **rollout**: `docker compose pull core-runtime && docker compose up -d`
- **wait**: ждёт **healthy** (проверка `curl` внутри контейнера)

Полезные флаги:

- `--no-build`, `--no-push`, `--no-rollout`
- `--wait/--no-wait` (по умолчанию `--wait`)
- `--timeout 180` (сек), `--interval 1.0` (сек)
- `--health-url http://localhost:8000/api/v1/monitor/health` (внутри контейнера)
- `--quiet` (минимальный вывод)
- `--json` (машинный вывод; удобно для CI/скриптов)

Логи core-runtime (для диагностики таймаута):

- `hc deploy core logs -f`

### Deploy платформы

Локальный dev flow:

```bash
hc deploy platform
```

Только подготовить статику, без запуска стека:

```bash
hc deploy platform --no-start
```

Image-only deploy через GHCR:

```bash
hc deploy platform --mode image --image ghcr.io/home-console/platform-home-console --tag latest
```

Этот вариант использует `platform-home-console/docker-compose.image.yml` и `PLATFORM_IMAGE`.

## Если “нет команды `hc deploy`”

Почти всегда это означает, что в окружении стоит **не тот пакет**, который предоставляет команду `hc`.

Проверка (в активированном venv):

```bash
python -c "import hc, inspect; print(hc.__file__)"
```

Должно указывать на `.../home-console-cli/hc/__init__.py` (или site-packages `homeconsole-cli`).

## Ошибки и UX

- Traceback пользователю не показывается.
- Все ошибки печатаются через Rich: `[red]Ошибка: ...[/red]`.
- При ошибке утилита завершает выполнение с кодом `1`.
