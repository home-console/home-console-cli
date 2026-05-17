# Go Migration — Architecture Decision

Записано как фиксация мышления, не как план к немедленному исполнению.

---

## Контекст

Core-runtime-service: ~3,600 Python файлов, ~1.27M строк.
Plugin system: runtime dynamic import через `importlib` — фундаментально Python.
Статус проекта: pre-production, реального нагрузочного использования ещё нет.
Это важно — пока нет prod-нагрузки, можно позволить себе думать про архитектуру v2.

---

## Почему нельзя просто "переписать на Go"

Go — компилируемый язык. Нельзя взять `plugin.py` и загрузить в рантайме.
Вся система плагинов (plugin_loader → importlib → BasePlugin) несовместима с Go.

Переписать core на Go = переосмыслить систему плагинов с нуля.
Объём: 6–12 месяцев. Все существующие плагины нужно переписать или перевести на новый протокол.

---

## Варианты — оценка

### ❌ Go native plugins (.so)
Только Linux, тот же компилятор, в продакшне сломано. Не рассматривается.

### ✅ gRPC subprocess (как Hashicorp go-plugin)
```
Core (Go) запускает плагин как отдельный процесс.
Общение через gRPC по Unix socket / localhost.

Core (Go) ◄──gRPC──► plugin_yandex (Python)
                      plugin_network  (Go)
                      plugin_custom   (любой язык)
```
**Плюсы:**
- Существующие Python-плагины ЖИВУТ — просто оборачиваются в gRPC-сервер
- Новые плагины можно писать на Go, Rust, Python — без разницы
- Полная изоляция: крашнулся плагин — core не падает
- Это production-proven паттерн (Terraform, Vault, Nomad)

**Минусы:**
- Каждый плагин = отдельный процесс (чуть больше памяти)
- Python-плагины нужно обернуть в gRPC-сервер (работа, но не переписывание)
- gRPC SDK нужно спроектировать

### ✅ Гибридное ядро (Go fast-path + Python plugin runtime)
```
┌─────────────────────────────────────┐
│  Go core                            │
│  - HTTP API, routing, auth          │  ← быстрый путь, Go
│  - Storage (SQLite/Postgres)        │
│  - WebSocket, SSE, metrics          │
└──────────────┬──────────────────────┘
               │ IPC / Unix socket
┌──────────────▼──────────────────────┐
│  Python plugin runtime (sidecar)    │
│  - BasePlugin, importlib, asyncio   │  ← плагины не трогаем
│  - Event bus, Service registry      │
│  - Capabilities                     │
└─────────────────────────────────────┘
```
**Плюсы:**
- Плагины вообще не трогаются
- Go получает контроль над HTTP критическим путём
- Постепенная миграция: сначала заменяем Go слой, потом (когда-нибудь) plugin runtime

**Минусы:**
- Два процесса в деплое
- IPC boundary между Go и Python добавляет сложности
- По сути откладывает вопрос plugin system

### ✅ WASM плагины (дальняя перспектива)
```
Plugin компилируется в .wasm
Core (Go) запускает через wazero/wasmtime
```
- Язык-независимо, отличная изоляция
- Не подходит прямо сейчас — нет экосистемы, сложный SDK
- Интересно как целевая архитектура v2

### ⭐ NATS + Go core (рекомендуемая целевая архитектура v2)

**Почему NATS а не gRPC для HomeConsole:**

У проекта уже event-driven архитектура (`publish_event`, `subscribe_event`, event bus).
NATS — это нативный event bus с request-reply поверх него. Это не замена gRPC, это
замена самого event bus + service registry одновременно.

```
┌─────────────────────────────────────────────────────┐
│  Go core v2                                         │
│  - HTTP API, routing, JWT auth                      │
│  - Storage (SQLite / Postgres)                      │
│  - WebSocket, SSE                                   │
│  - NATS client (встроен)                            │
└────────────────────┬────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │  NATS server│  ← единственный брокер
              │  (JetStream)│    event bus + service registry
              └──────┬──────┘
       ┌─────────────┼─────────────┐
       │             │             │
┌──────▼──────┐ ┌────▼──────┐ ┌───▼───────────┐
│plugin_yandex│ │plugin_net │ │plugin_custom  │
│  (Python)   │ │  (Go)     │ │  (любой язык) │
└─────────────┘ └───────────┘ └───────────────┘
```

**Latency сравнение (реальные цифры):**

```
In-process Python call (сейчас):   ~100ns – 1µs
NATS Unix socket round trip:        ~50–100µs      ← 10–20x хуже in-process
gRPC Unix socket round trip:        ~200–500µs     ← в 2–5x хуже NATS
HTTP localhost:                     ~1–5ms         ← в 10–50x хуже NATS
```

**Для нагрузок умного дома это незначительно:**

```
Синхронизация устройств (раз в 5 мин):
  +100µs overhead → не ощутимо вообще

Device command (пользователь нажал кнопку):
  Яндекс API отвечает 100–300ms
  +100µs NATS overhead = +0.1% к latency → незаметно

Realtime events (10–100 событий/сек):
  100 × 100µs = 10ms суммарно за секунду → ок

Проблема возникнет при 10,000+ событий/сек:
  10,000 × 100µs = 1 секунда задержки/сек → плохо
  Но это нереальная нагрузка для домашней автоматики
```

**Как выглядит плагин с NATS (минимальная адаптация):**

```python
# Сейчас (in-process):
async def on_load(self):
    await self.register_service("yandex.sync_devices", self._sync)
    await self.subscribe_event("device_command_requested", self._on_cmd)

# С NATS (изменений минимум):
async def on_load(self):
    self.nc = await nats.connect()
    # register_service → subscribe на request-reply топик
    await self.nc.subscribe("service.yandex.sync_devices", cb=self._sync)
    # subscribe_event → subscribe на event топик
    await self.nc.subscribe("event.device_command_requested", cb=self._on_cmd)

# Core вызывает сервис:
resp = await nc.request("service.yandex.sync_devices", data, timeout=5.0)

# Core публикует событие:
await nc.publish("event.device_command_requested", payload)
```

Структура кода плагина не меняется — меняется только транспорт.

**Что NATS JetStream даёт дополнительно (важно для умного дома):**

```
Персистентность событий:
  Плагин был оффлайн 10 минут → при reconnect получает все пропущенные события
  Критично для: device state sync, automation triggers

At-least-once delivery:
  Событие гарантированно доставлено даже если плагин перезапускался

Event replay:
  Отладка: воспроизвести поток событий с любого момента времени
  Полезно для automation debugging

Consumer groups:
  Несколько экземпляров одного плагина получают события round-robin
  Горизонтальное масштабирование плагина без изменений кода
```

**Целевая архитектура v2 с NATS:**

```
Go core v2
  ├── HTTP/WebSocket layer (chi/fiber)
  ├── Auth & RBAC engine
  ├── Storage (SQLite / Postgres / Redis)
  ├── NATS JetStream (встроен или sidecar)
  └── Plugin supervisor (запуск/остановка процессов плагинов)

Plugin SDK (открытый, язык-независимый):
  ├── Python SDK  → nats.py обёртка над BasePlugin контрактом
  ├── Go SDK      → nats.go нативный
  └── Контракт:  topics convention + JSON/msgpack envelope

Топики (конвенция):
  service.<plugin>.<name>     ← request-reply (вызов сервиса)
  event.<type>                ← pub-sub (события)
  admin.<plugin>.<cmd>        ← управление плагином из core
  log.<plugin>                ← логи плагина → core собирает
```

**Для проприетарного v2:**
- Core закрытый (Go бинарь)
- NATS топики + JSON envelope — открытый протокол (Plugin SDK публичный)
- Сообщество пишет плагины на любом языке зная только протокол
- Enterprise фичи: multi-tenant NATS subjects, audit log через JetStream, HA через NATS cluster

---

## Рекомендованный путь (поэтапно)

```
Сейчас
  Python core + Python CLI
  ↓
Шаг 1 (2–3 недели)
  Python core + Go CLI
  - CLI переписывается на Go
  - Core не трогается
  - CLI получает: один бинарь, быстрый старт, embedded emergency-режим
  ↓
Шаг 2 (живём и строим фичи, месяцы)
  Python core + Go CLI
  - Реализуем ROADMAP.md
  - Получаем реальное использование
  - Понимаем где реально узкие места
  ↓
Шаг 3 (когда появится реальная нагрузка и понимание)
  Выбрать: Hybrid или gRPC
  - Hybrid если хотим сохранить Python-плагины без изменений
  - gRPC если хотим language-agnostic plugin system
```

---

## О проприетарном v2 ядре

Это разумная стратегия для будущего — open-source CLI + runtime, проприетарное ядро v2.
Именно так работают: HashiCorp (Terraform OSS → Enterprise), Grafana, Elastic.

**Если строить v2 с нуля — правильная архитектура:**
```
Go core v2
  ├── HTTP/gRPC API layer
  ├── Auth & RBAC engine
  ├── Plugin runtime: gRPC subprocess protocol
  ├── Event bus (NATS или собственный)
  ├── Storage abstraction (SQLite / Postgres / Redis)
  └── WASM plugin sandbox (для untrusted plugins)

Plugin SDK (открытый):
  ├── Go SDK (native performance)
  ├── Python SDK (обратная совместимость)
  └── gRPC proto (для любого языка)
```

Это позволяет:
- Ядро закрытое, протокол открытый
- Плагины пишутся сообществом на любом языке
- Проприетарные фичи (enterprise RBAC, audit, HA) только в v2

**Когда об этом думать:** когда появятся первые реальные пользователи и понимание
что именно они хотят платить/использовать. Не раньше.

---

## Файлы по теме

- `ROADMAP.md` — что реализовать в CLI прямо сейчас (Python)
- `MIGRATION_GO.md` — этот файл, архитектурные варианты
