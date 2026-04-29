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
- `hc shell`

## Ошибки и UX

- Traceback пользователю не показывается.
- Все ошибки печатаются через Rich: `[red]Ошибка: ...[/red]`.
- При ошибке утилита завершает выполнение с кодом `1`.
