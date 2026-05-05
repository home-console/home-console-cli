# HomeConsole CLI (`hc`)

CLI-утилита для управления платформой HomeConsole **исключительно через HTTP API** CoreRuntime.

## Установка (локально)

```bash
cd home-console-cli
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Проверка:

```bash
hc --help
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

- `hc connect <host> [--port 8080] [--token TOKEN]`
- `hc status`
- `hc install <name> [--version X.Y.Z]`
- `hc remove <name> [--force]`
- `hc plugin list|start|stop|info`
- `hc module list|status`
- `hc logs [--follow] [--module <name>] [--level debug|info|warning|error]`
- `hc search <query>`
- `hc setup`
- `hc deploy` (по умолчанию: build+push+rollout+wait) и `hc deploy ...` (тонкие подкоманды)
- `hc deploy platform` (локальный dev flow)
- `hc deploy platform --mode image --image ghcr.io/home-console/platform-home-console --tag latest` (image-only deploy)
- `hc update core ...` (обновление core-runtime до нового image:tag)
- `hc shell`

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
