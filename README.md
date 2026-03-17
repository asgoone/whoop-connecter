# WHOOP Connecter

MCP-сервер и CLI для подключения данных WHOOP к персональному агенту Coach через платформу OpenClaw.

## Содержание

- [Назначение](#назначение)
- [Как это работает](#как-это-работает)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Первый запуск](#первый-запуск)
- [CLI](#cli)
- [MCP-инструменты](#mcp-инструменты)
- [Подключение к OpenClaw](#подключение-к-openclaw)
- [Развёртывание на VPS](#развёртывание-на-vps)
- [Безопасность](#безопасность)
- [Ограничения](#ограничения)
- [Расширение](#расширение)

---

## Назначение

Проект даёт персональному агенту Coach (развёрнутому в OpenClaw) доступ к данным WHOOP через протокол MCP. Coach может:

- читать метрики восстановления, сна, нагрузки и HRV;
- анализировать данные тела (рост, вес, макс. ЧСС) и вычислять BMI;
- давать рекомендации по тренировкам на основе recovery score;
- показывать тренды за 7, 14 или 30 дней;
- экспортировать данные за период в JSON;
- замечать ухудшения и советовать снизить нагрузку.

Помимо MCP-сервера, проект включает полноценный CLI — удобный для отладки, мониторинга и скриптов.

---

## Как это работает

```
WHOOP API v2 (OAuth 2.0 + PKCE)
        │
        ▼
whoop/api/client.py     ← httpx async, TTL кэш 5 мин, auto-retry на 401
        │
        ▼
whoop/schema/mappers.py ← JSON → unified dataclasses
        │
        ▼
whoop/services.py       ← WhoopService (shared core)
       / \
      /   \
     ▼     ▼
mcp_server  cli
(stdio)     (Typer + Rich)
     │
     ▼
OpenClaw Gateway → Coach → Telegram
```

### Протокол MCP

Сервер запускается OpenClaw как дочерний процесс. Общение — через **stdio** (JSON-RPC по stdin/stdout). Это самый простой и надёжный вариант для локального развёртывания.

### Аутентификация

При первом запуске выполняется **OAuth 2.0 с PKCE**:

1. Открывается браузер → страница авторизации WHOOP.
2. На `localhost:8080/callback` ловится redirect с кодом.
3. Параметр `state` верифицируется для защиты от CSRF.
4. Токены шифруются **AES-256-GCM** и сохраняются на диск (`~/.whoop/tokens.enc`).
5. При следующих запросах токен обновляется автоматически через refresh token.

Для VPS без браузера доступен **headless-режим** — см. [Развёртывание на VPS](#развёртывание-на-vps).

#### OAuth scopes

Сервер запрашивает следующие разрешения при авторизации:

| Scope | Доступ |
|---|---|
| `read:profile` | Имя, email, user_id |
| `read:body_measurement` | Рост, вес, макс. ЧСС |
| `read:recovery` | Recovery score, HRV, resting HR, SpO2 |
| `read:sleep` | Данные сна: стадии, эффективность, дыхание |
| `read:workout` | Тренировки: strain, HR, калории, дистанция |
| `read:cycles` | Физиологические циклы: дневная нагрузка |
| `offline` | Refresh token для автоматического обновления |

> Все scopes необходимо включить в WHOOP Developer Dashboard при создании приложения.

### Кэширование

Все GET-запросы к WHOOP API кэшируются в памяти с TTL 5 минут (настраивается через `WHOOP_CACHE_TTL`). Устаревшие записи вычищаются лениво при следующем обращении, плюс полная зачистка раз в 60 секунд.

---

## Структура проекта

```
whoop_connecter/
├── whoop/                    # Shared core — библиотека
│   ├── auth/
│   │   ├── oauth.py          # OAuth 2.0 PKCE flow + headless mode
│   │   └── token_store.py    # AES-256-GCM хранилище токенов
│   ├── api/
│   │   ├── client.py         # httpx async клиент, авто-retry на 401
│   │   ├── cache.py          # In-memory TTL кэш
│   │   └── endpoints.py      # Константы WHOOP API v2 endpoints
│   ├── schema/
│   │   ├── unified.py        # Source-agnostic dataclasses
│   │   └── mappers.py        # WHOOP JSON → unified schema
│   ├── analytics/
│   │   ├── daily_summary.py  # Агрегация дня + рекомендация
│   │   └── trends.py         # Тренды за N дней (↑↓→)
│   └── services.py           # WhoopService facade
│
├── mcp_server/               # MCP-сервер (stdio)
│   ├── server.py             # Точка входа, регистрация инструментов
│   └── tools/                # По одному файлу на инструмент
│       ├── summary.py        # get_daily_summary
│       ├── trends.py         # get_trends
│       ├── recovery.py       # get_recovery
│       ├── sleep.py          # get_sleep
│       ├── workouts.py       # get_workouts
│       ├── cycles.py         # get_cycles
│       ├── body.py           # get_body_measurement
│       ├── profile.py        # get_profile
│       └── auth_status.py    # get_auth_status
│
├── cli/
│   └── main.py               # CLI (Typer + Rich)
│
├── deploy/
│   ├── openclaw_mcp_config.json  # Шаблон MCP-конфига для OpenClaw
│   └── setup_vps.sh              # Скрипт развёртывания на VPS
│
├── damp/
│   └── openapi.json          # OpenAPI-спецификация WHOOP API v2
│
├── tests/                    # 199 тестов (unit + integration + acceptance)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Установка

### Требования

- Python 3.10+
- Аккаунт разработчика WHOOP: [developer.whoop.com](https://developer.whoop.com)

### Шаги

```bash
# 1. Клонировать репозиторий
git clone https://github.com/asgoone/whoop-connecter.git
cd whoop-connecter

# 2. Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# 3. Установить зависимости
pip install -e .
```

После установки доступны две команды:

| Команда | Описание |
|---------|----------|
| `whoop` | CLI для работы с данными WHOOP |
| `whoop-mcp` | MCP-сервер (stdio) для OpenClaw |

---

## Конфигурация

### 1. Создать `.env`

```bash
cp .env.example .env
chmod 600 .env
```

### 2. Заполнить параметры

```dotenv
# Обязательно — из developer.whoop.com
WHOOP_CLIENT_ID=ваш_client_id
WHOOP_CLIENT_SECRET=ваш_client_secret

# Ключ шифрования токенов (генерируется один раз)
WHOOP_TOKEN_ENCRYPTION_KEY=ваш_64_символьный_hex

# Опционально
WHOOP_REDIRECT_URI=http://localhost:8080/callback
WHOOP_TOKEN_PATH=~/.whoop/tokens.enc
WHOOP_CACHE_TTL=300
LOG_LEVEL=WARNING
```

### 3. Сгенерировать ключ шифрования

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Полный список переменных окружения

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `WHOOP_CLIENT_ID` | Да | — | OAuth client ID из WHOOP Developer Dashboard |
| `WHOOP_CLIENT_SECRET` | Да | — | OAuth client secret |
| `WHOOP_TOKEN_ENCRYPTION_KEY` | Да | — | 64-символьная hex-строка (32 байта AES-ключа) |
| `WHOOP_REDIRECT_URI` | Нет | `http://localhost:8080/callback` | Redirect URI для OAuth callback |
| `WHOOP_TOKEN_PATH` | Нет | `~/.whoop/tokens.enc` | Путь к файлу токенов |
| `WHOOP_CACHE_TTL` | Нет | `300` | TTL кэша API-ответов, секунды |
| `LOG_LEVEL` | Нет | `WARNING` | Уровень логов (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Настройка WHOOP Developer App

В [developer.whoop.com](https://developer.whoop.com) при создании приложения:

1. **Redirect URI** — укажите `http://localhost:8080/callback`.
2. **Scopes** — включите все: `read:profile`, `read:body_measurement`, `read:recovery`, `read:sleep`, `read:workout`, `read:cycles`.
3. Скопируйте `Client ID` и `Client Secret` в `.env`.

---

## Первый запуск

### 1. Авторизация

```bash
# С браузером (локальная машина)
whoop auth login

# Без браузера (VPS, сервер)
whoop auth login-headless
```

При `login` откроется браузер. Войдите в WHOOP и разрешите доступ. Токены автоматически сохранятся в зашифрованном виде.

При `login-headless` в терминале появится URL — откройте его в браузере на любом устройстве, авторизуйтесь и вставьте callback URL обратно в терминал.

### 2. Проверка

```bash
whoop auth status
# → Authenticated — token is valid, expires at 2026-03-18T08:00:00+00:00

whoop summary
# → 🟢 Recovery 74% | Sleep 85 | HRV 61 | RHR 55 | Strain 8.3
#   → Good recovery. You can train at full intensity.
```

---

## CLI

CLI использует тот же `WhoopService`, что и MCP-сервер — никакого дублирования логики.

### Команды

#### `whoop summary` — дневной отчёт

```bash
whoop summary                     # сегодня
whoop summary --date 2026-03-15   # конкретная дата
whoop summary --raw               # JSON
```

#### `whoop recovery` — метрики восстановления

```bash
whoop recovery
whoop recovery --start 2026-03-10T00:00:00Z --end 2026-03-17T23:59:59Z
whoop recovery --raw
```

Показывает: recovery score, HRV (RMSSD), resting HR, SpO2.

#### `whoop sleep` — данные сна

```bash
whoop sleep
whoop sleep --raw
```

Показывает: sleep score, duration, efficiency, стадии сна, respiratory rate, sleep consistency, sleep needed (baseline/debt/strain/nap).

#### `whoop body` — измерения тела

```bash
whoop body
whoop body --raw
```

Показывает: рост, вес, макс. ЧСС, BMI (вычисляется автоматически).

#### `whoop trends` — тренды за период

```bash
whoop trends              # 7 дней
whoop trends --days 30    # 30 дней
whoop trends --raw
```

Показывает 5 метрик с направлением (↑↓→) и процентом изменения.

#### `whoop export` — экспорт данных

```bash
whoop export                           # 7 дней → stdout
whoop export --days 30                 # 30 дней
whoop export --days 14 --output data.json  # в файл
```

Экспортирует body + N дней daily health (sleep, recovery, activity) в JSON. Использует batch-запросы: **4 HTTP-вызова** независимо от количества дней (1 body + 3 paginated: cycles, recovery, sleep).

#### `whoop auth` — управление авторизацией

```bash
whoop auth status          # проверить статус токена
whoop auth login           # авторизация через браузер
whoop auth login-headless  # авторизация без браузера (VPS)
whoop auth logout          # удалить токен
```

`auth status` автоматически пробует обновить токен через refresh, если он истёк, и показывает актуальный статус.

#### `whoop raw` — сырые данные API (отладка)

```bash
whoop raw profile
whoop raw body
whoop raw recovery --start 2026-03-10T00:00:00Z
whoop raw sleep
whoop raw workouts
whoop raw cycles
```

Выводит необработанный JSON-ответ WHOOP API — полезно для отладки и изучения формата данных.

### Флаг `--raw`

Все команды поддерживают `--raw` для вывода чистого JSON — удобно для скриптов:

```bash
whoop summary --raw | jq '.recovery_score'
whoop sleep --raw | jq '.sleep_needed'
whoop export --days 7 | jq '.daily[0].recovery'
```

---

## MCP-инструменты

MCP-сервер предоставляет **9 инструментов**. Coach обращается к ним по имени.

### `get_daily_summary`

Агрегированный summary за день. Основной инструмент для утреннего briefing.

**Параметры:**

| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `date` | string | Нет | Дата `YYYY-MM-DD`. По умолчанию — сегодня. |

**Пример ответа:**

```json
{
  "date": "2026-03-17",
  "recovery_score": 74,
  "sleep_score": 85,
  "hrv_rmssd": 61.2,
  "resting_hr": 55,
  "strain": 8.3,
  "recommendation": "Good recovery. You can train at full intensity.",
  "emoji": "🟢",
  "summary_line": "🟢 Recovery 74% | Sleep 85 | HRV 61 | RHR 55 | Strain 8.3\n→ Good recovery. You can train at full intensity."
}
```

**Логика рекомендации:**

| Recovery score | Цвет | Рекомендация |
|---|---|---|
| >= 67 | 🟢 | Полная нагрузка |
| 34-66 | 🟡 | Умеренная нагрузка |
| < 34 | 🔴 | Только отдых или лёгкая активность |
| нет данных | ⚪ | Нет данных |

---

### `get_trends`

Тренды метрик за последние N дней.

**Параметры:**

| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `days` | integer | Нет | Количество дней (1-90). По умолчанию `7`. |

**Метрики:** `recovery_score`, `sleep_score`, `hrv_rmssd`, `resting_hr`, `strain`.

**Направление тренда:** вычисляется как изменение среднего между первой и второй половиной периода. `↑` — рост > 3%, `↓` — падение > 3%, `→` — стабильно.

---

### `get_recovery`

Метрики восстановления за период.

**Параметры:** `start`, `end` — ISO datetime. Необязательны.

**Возвращает:** `score`, `hrv_rmssd`, `resting_hr`, `spo2`, `skin_temp_deviation`.

---

### `get_sleep`

Данные сна. Автоматически выбирает основной сон (не дрёму), предпочитает `SCORED` записи.

**Параметры:** `start`, `end`.

**Возвращает:**

| Поле | Тип | Описание |
|---|---|---|
| `score` | int | Sleep performance percentage (0-100) |
| `duration_hours` | float | Время в кровати |
| `efficiency` | float | Доля сна от времени в кровати (0.0-1.0) |
| `stages` | dict | Стадии сна в миллисекундах (light, SWS, REM, awake) |
| `respiratory_rate` | float | Частота дыхания (вдохов/мин) |
| `sleep_consistency` | int | Консистентность сна (0-100%) |
| `sleep_needed` | dict | Потребность во сне: baseline, debt, strain, nap (мс) |

---

### `get_workouts`

Список тренировок за период.

**Параметры:** `start`, `end`.

**Возвращает:** массив объектов:

| Поле | Тип | Описание |
|---|---|---|
| `sport` | string | Название вида спорта |
| `strain` | float | Нагрузка (0-21) |
| `duration_minutes` | float | Длительность |
| `avg_hr` | int | Средний пульс |
| `max_hr` | int | Макс. пульс |
| `calories` | int | Калории |
| `distance_meter` | float | Дистанция в метрах (если есть GPS) |
| `altitude_gain_meter` | float | Набор высоты в метрах (если есть) |
| `percent_recorded` | float | % записанных HR-данных |
| `zone_durations` | dict | Время в пульсовых зонах (zone_zero..zone_five, мс) |

---

### `get_cycles`

Физиологические циклы WHOOP (нагрузка и калории за день).

**Параметры:** `start`, `end`.

**Возвращает:** `strain`, `calories`.

---

### `get_body_measurement`

Измерения тела пользователя. Требует scope `read:body_measurement`.

**Параметры:** нет.

**Возвращает:**

```json
{
  "height_meter": 1.80,
  "weight_kilogram": 82.5,
  "max_heart_rate": 195
}
```

---

### `get_profile`

Профиль пользователя: имя, email, user_id.

---

### `get_auth_status`

Статус OAuth-токена. Автоматически пытается обновить токен через refresh, если он истёк.

**Возвращает:**

```json
{
  "authenticated": true,
  "expires_at": "2026-03-18T08:00:00+00:00",
  "expired": false
}
```

---

## Подключение к OpenClaw

### 1. Заполнить пути в конфиге

Отредактируйте `deploy/openclaw_mcp_config.json`, заменив пути и credentials:

```json
{
  "mcpServers": {
    "whoop": {
      "command": "/home/user/whoop_connecter/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/home/user/whoop_connecter",
      "env": {
        "WHOOP_CLIENT_ID": "ваш_client_id",
        "WHOOP_CLIENT_SECRET": "ваш_client_secret",
        "WHOOP_REDIRECT_URI": "http://localhost:8080/callback",
        "WHOOP_TOKEN_PATH": "/home/user/.whoop/tokens.enc",
        "WHOOP_TOKEN_ENCRYPTION_KEY": "ваш_ключ",
        "WHOOP_CACHE_TTL": "300",
        "LOG_LEVEL": "WARNING"
      }
    }
  }
}
```

### 2. Добавить конфиг в OpenClaw

Добавьте секцию `whoop` в MCP-конфигурацию вашей инсталляции OpenClaw. Конкретный путь к конфигу зависит от версии — см. документацию OpenClaw.

### 3. Проверить

После добавления конфига Coach должен видеть 9 WHOOP-инструментов. Попросите Coach: _«Покажи мой recovery за сегодня»_ — он вызовет `get_daily_summary` или `get_recovery`.

### 4. Настроить proactive summary (опционально)

Пример cron-задачи для утреннего summary в 08:00:

```bash
0 8 * * * cd /home/user/whoop_connecter && \
  .venv/bin/whoop summary --raw 2>/dev/null | \
  curl -s -X POST "https://your-webhook-url" \
    -H "Content-Type: application/json" \
    -d @-
```

> Конкретный способ доставки summary в Telegram зависит от архитектуры вашего Coach. Это может быть webhook, cron → OpenClaw API, или scheduled task внутри Coach.

---

## Развёртывание на VPS

### Быстрый старт

```bash
# 1. Клонировать и установить
git clone https://github.com/asgoone/whoop-connecter.git
cd whoop-connecter
bash deploy/setup_vps.sh

# 2. Сохранить ключ шифрования, который вывел скрипт!

# 3. Заполнить .env
nano .env   # CLIENT_ID, CLIENT_SECRET, ENCRYPTION_KEY

# 4. Авторизация (headless — без браузера)
.venv/bin/whoop auth login-headless
# → Скрипт покажет URL. Откройте его в браузере на другом устройстве,
#   авторизуйтесь, скопируйте URL редиректа и вставьте в терминал.

# 5. Проверить
.venv/bin/whoop auth status
.venv/bin/whoop summary
```

### Headless OAuth flow

На VPS нет браузера, поэтому используется headless-режим:

1. Запустите `whoop auth login-headless`.
2. В терминале появится URL авторизации.
3. Откройте этот URL **в браузере на любом устройстве** (телефон, ноутбук).
4. Авторизуйтесь в WHOOP.
5. Браузер перенаправит на `localhost:8080/callback?code=...&state=...` — скопируйте **полный URL** из адресной строки.
6. Вставьте URL в терминал VPS.
7. Токены сохраняются, далее обновление происходит автоматически через refresh token.

> **Важно:** Redirect на `localhost:8080` не сработает в браузере на другом устройстве — это нормально. Просто скопируйте URL из адресной строки, даже если страница не загрузилась.

### Структура файлов на VPS

```
/home/user/
├── whoop_connecter/          # Репозиторий
│   ├── .venv/                # Виртуальное окружение
│   └── .env                  # Credentials (chmod 600)
└── .whoop/
    └── tokens.enc            # Зашифрованные токены (chmod 600)
```

---

## Безопасность

| Аспект | Реализация |
|---|---|
| Хранение токенов | AES-256-GCM шифрование, nonce уникален для каждой записи |
| Права доступа к файлу токенов | `600` (только владелец) |
| Ключ шифрования | В переменной окружения, не в коде и не в `.env` под git |
| OAuth CSRF | Параметр `state` верифицируется в callback-обработчике |
| OAuth PKCE | `code_verifier` + `code_challenge` (SHA-256), одноразовый |
| Секреты в конфиге OpenClaw | Передавать через переменные окружения, не хардкодить в JSON |
| Медицинский авторитет | Coach **не ставит диагнозы**. При плохих метриках рекомендует снизить нагрузку или обратиться к врачу. |

### `.gitignore` включает

```
.env           # Credentials
*.enc          # Зашифрованные токены
.venv/         # Виртуальное окружение
```

---

## Ограничения

### Функциональные

- **Данные только от WHOOP.** Другие источники (Oura, Apple Health) не поддерживаются в текущей версии, но архитектура подготовлена к расширению через `unified schema`.
- **Кэш сбрасывается при рестарте сервера.** Используется in-memory кэш, данные не переживают перезапуск процесса.
- **Исторические данные ограничены API.** WHOOP API не гарантирует данные старше 1 года.
- **WHOOP API не предоставляет**: возраст, дату рождения, биологический возраст, WHOOP-возраст, уровень стресса.

### Технические

- **Python 3.10+** — обязательно (используется синтаксис `X | Y` для типов).
- **Один MCP-сервер на процесс** — при нескольких одновременных запросах от Coach они обрабатываются последовательно (MCP stdio однопоточный).
- **`get_trends` / `export` с `days > 30`** — медленнее из-за большого объёма данных, но используют batch-запросы (3-4 HTTP-вызова независимо от N).

### WHOOP API v2

Формат ответов — вложенный (метрики внутри объекта `score`). Маперы проекта обрабатывают как вложенный, так и плоский формат для совместимости. Полная спецификация API хранится в `damp/openapi.json`.

---

## Расширение

### Добавление нового MCP-инструмента

1. Создать файл `mcp_server/tools/my_tool.py`:

```python
import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="my_tool",
    description="Описание инструмента для Coach.",
    inputSchema={
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "Описание параметра."},
        },
    },
)

async def handle(arguments: dict, service: WhoopService) -> str:
    result = await service.some_method(arguments.get("param"))
    return json.dumps(result)
```

2. Добавить импорт в `mcp_server/server.py` и модуль в `_TOOL_MODULES`.

### Добавление нового источника данных (Oura, Apple Health)

1. Реализовать новый клиент и mapper в `whoop/api/` и `whoop/schema/`.
2. Mapper должен возвращать те же `DailyHealth` dataclasses с `source="oura"`.
3. Вся аналитика (`build_daily_summary`, `build_trends`) работает без изменений.

### Отладка

```bash
# Детальное логирование CLI
LOG_LEVEL=DEBUG whoop summary

# Детальное логирование MCP-сервера
LOG_LEVEL=DEBUG whoop-mcp

# Сырые данные API (минуя маппинг)
whoop raw recovery
whoop raw sleep --start 2026-03-10T00:00:00Z
```

Логи MCP-сервера идут в **stderr**, stdout зарезервирован для протокола MCP.

### Тесты

```bash
# Запустить все тесты (199)
.venv/bin/python -m pytest tests/ -v

# Только unit-тесты
.venv/bin/python -m pytest tests/unit/ -v

# Только интеграционные + приёмочные
.venv/bin/python -m pytest tests/integration/ -v
```

Структура тестов:

| Директория | Что тестирует | Количество |
|---|---|---|
| `tests/unit/` | Маперы, аналитика, кэш, токен-стор | ~130 |
| `tests/integration/` | Сервисный слой, MCP-инструменты, приёмочные сценарии | ~70 |
