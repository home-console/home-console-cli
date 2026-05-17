# home-console-cli — Roadmap

Всё что нужно реализовать чтобы CLI стал полноценным инструментом управления ядром.
Сгруппировано по приоритету и теме. Начинать с блока **CRITICAL**.

---

## CRITICAL — без этого нельзя уронить часть системы и остаться в контроле

### 1. ✅ Режим "foreground" для native Core
`hc core up --mode native --foreground` — реализовано в 0.0.9

### 2. ✅ `hc core restart`
Реализовано в 0.0.9

### 3. `hc core attach`
Подключиться к stdout/stderr уже запущенного native-процесса интерактивно.

- [ ] Прокидывать stdin в процесс через именованный pipe (mkfifo)

### 4. ✅ `hc core signal <SIG>`
Реализовано в 0.0.9

### 5. ✅ Emergency-доступ без API (`hc emergency`)
Реализовано в 0.0.9: inspect, reset-admin, list-users, revoke-sessions

- [ ] `hc emergency disable-plugin <name>` — отметить плагин disabled в DB
- [ ] `hc emergency unlock-db` — снять WAL/lock при зависшем процессе

---

## HIGH — критично для developer experience

### 6. ✅ Unix socket транспорт
`RUNTIME_SOCKET_PATH` в Core + `HC_SOCKET` / `core.socket_path` — реализовано в 0.0.9

### 7. ✅ `hc service list`
Реализовано в 0.0.9

- [ ] `hc service call <name> [--json <payload>]` — вызвать сервис через CLI

### 8. ✅ `hc event tail` / `hc event list`
Реализовано в 0.0.9 (SSE стрим + snapshot)

- [ ] `hc event emit <event_type> [--json <payload>]` — послать event в event bus

### 9. `hc plugin capabilities`

- [ ] `hc plugin capabilities list` — все capability в системе
- [ ] `hc plugin capabilities who-provides <cap>`
- [ ] `hc plugin capabilities check <plugin>`

### 10. `hc core dump`

- [ ] `hc core dump` → JSON дамп: плагины, сервисы, события, модули
- [ ] `hc core dump --output <file>`

---

## MEDIUM — developer experience плагинов

### 11. ✅ `hc plugin new <name>` — scaffolding
Реализовано в 0.0.9

### 12. ✅ `hc plugin dev <path>` — dev-режим
Реализовано в 0.0.9 (polling watch + sync + auto-reload)

### 13. `hc plugin validate <path>`

- [ ] Проверить структуру: `plugin.py`, наследование от `BasePlugin`, `metadata` property
- [ ] Проверить `plugin.json`
- [ ] Предупреждения о deprecated API

### 14. `hc plugin publish`

- [ ] `hc plugin publish <path>` → zip → POST в marketplace-api
- [ ] `--dry-run`

---

## LOW — polish и удобство

### 15. ✅ `hc core ps` — расширить для native
uptime, memory RSS, CPU% — реализовано в 0.0.9

### 16. ✅ `hc module inspect <name>`
Реализовано в 0.0.9

### 17. ✅ `hc config edit`
Открывает $EDITOR, валидирует после сохранения — реализовано в 0.0.9

### 18. ✅ REPL улучшения (`hc shell`)
`!<bash-command>` — реализовано в 0.0.9

### 19. ✅ `hc status --components`
Реализовано в 0.0.9 (`hc status -c`)

---

## Go migration — отдельный трек

> Детали в `MIGRATION_GO.md` (создать когда придёт время).
> CLI переписывается на Go, Core остаётся Python.

- [ ] Определить transport layer (unix socket protocol между Go CLI и Python Core)
- [ ] Реализовать emergency-режим на Go (прямой доступ к DB — `mattn/go-sqlite3`)
- [ ] Перенести все команды из Python → Go (cobra + bubbletea)
- [ ] Убедиться что Core экспортирует Unix socket endpoint

---

## Порядок реализации (рекомендованный)

```
1. hc core restart             (5 строк, быстро)
2. hc core up --foreground     (изменить native_up)
3. hc core signal              (os.kill wrapper)
4. hc emergency inspect        (sqlite3, только чтение)
5. hc emergency reset-admin    (sqlite3, запись)
6. hc service list             (нужен endpoint в Core)
7. hc event tail               (SSE стрим)
8. Unix socket transport       (HCClient + Core config)
9. hc plugin new               (scaffolding)
10. hc plugin dev              (watch + reload)
```
