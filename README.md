# WHOOP Connecter

MCP-сервер и CLI для подключения данных WHOOP к персональному агенту Coach через платформу OpenClaw.

## Содержание

- [Назначение](#назначение)
- [Как это работает](#как-это-работает)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Первый запуск](#первый-запуск)
- [MCP-инструменты](#mcp-инструменты)
- [CLI](#cli)
- [Подключение к OpenClaw](#подключение-к-openclaw)
- [Безопасность](#безопасность)
- [Ограничения](#ограничения)
- [Расширение](#расширение)

---

## Назначение

Проект даёт персональному агенту Coach (развёрнутому в OpenClaw) доступ к данным WHOOP через протокол MCP. Coach может:

- читать метрики восстановления, сна, нагрузки и HRV;
- давать рекомендации по тренировкам на основе recovery score;
- проактивно присылать утренний summary в Telegram через cron;
- показывать тренды за 7, 14 или 30 дней;
- замечать ухудшения и советовать снизить нагрузку.

---

## Как это работает

```
WHOOP API (OAuth 2.0)
        │
        ▼
whoop/api/client.py     ← httpx async, TTL кэш 5 мин
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
(stdio)     (Typer)
     │
     ▼
OpenClaw Gateway → Coach → Telegram
```

### Протокол MCP

Сервер запускается OpenClaw как дочерний процесс. Общение происходит через **stdio** (JSON-RPC по stdin/stdout). Это самый простой и надёжный вариант для локального развёртывания.

### Аутентификация

При первом запуске выполняется **OAuth 2.0 с PKCE**:

1. Открывается браузер → страница авторизации WHOOP.
2. На `localhost:8080/callback` ловится redirect с кодом.
3. Параметр `state` верифицируется для защиты от CSRF.
4. Токены шифруются **AES-256-GCM** и сохраняются на диск (`~/.whoop/tokens.enc`).
5. При следующих запросах токен обновляется автоматически через refresh token.

### Кэширование

Все GET-запросы к WHOOP API кэшируются в памяти с TTL 5 минут. Устаревшие записи вычищаются лениво при следующем обращении, плюс полная зачистка раз в 60 секунд.

---

## Структура проекта

```
whoop_connecter/
├── whoop/                    # Shared core — библиотека
│   ├── auth/
│   │   ├── oauth.py          # OAuth 2.0 PKCE flow
│   │   └── token_store.py    # AES-256-GCM хранилище токенов
│   ├── api/
│   │   ├── client.py         # httpx async клиент, авто-retry на 401
│   │   ├── cache.py          # In-memory TTL кэш
│   │   └── endpoints.py      # Константы WHOOP API endpoints
│   ├── schema/
│   │   ├── unified.py        # Source-agnostic dataclasses
│   │   └── mappers.py        # WHOOP JSON → unified schema
│   ├── analytics/
│   │   ├── daily_summary.py  # Агрегация дня + рекомендация
│   │   └── trends.py         # Тренды за N дней (↑↓→)
│   └── services.py           # WhoopService facade
│
├── mcp_server/               # MCP-сервер (stdio)
│   ├── server.py             # Точка входа, регистрация тулов
│   └── tools/                # По одному файлу на тул
│       ├── summary.py
│       ├── trends.py
│       ├── recovery.py
│       ├── sleep.py
│       ├── workouts.py
│       ├── cycles.py
│       ├── profile.py
│       └── auth_status.py
│
├── cli/
│   └── main.py               # CLI (Typer + Rich)
│
├── deploy/
│   ├── openclaw_mcp_config.json
│   └── setup_vps.sh
│
├── .env.example
└── pyproject.toml
```

---

## Установка

### Требования

- Python 3.10+
- Аккаунт разработчика WHOOP: [developer.whoop.com](https://developer.whoop.com)
- OpenClaw + Coach, развёрнутые на VPS

### Шаги

```bash
# 1. Клонировать репозиторий
git clone <repo-url> whoop_connecter
cd whoop_connecter

# 2. Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS

# 3. Установить зависимости
pip install -e .
```

---

## Конфигурация

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

```dotenv
# Обязательно — из developer.whoop.com
WHOOP_CLIENT_ID=ваш_client_id
WHOOP_CLIENT_SECRET=ваш_client_secret

# URI должен совпадать с тем, что указан в WHOOP Developer Dashboard
WHOOP_REDIRECT_URI=http://localhost:8080/callback

# Путь для хранения зашифрованных токенов
WHOOP_TOKEN_PATH=~/.whoop/tokens.enc

# TTL кэша в секундах (по умолчанию 5 минут)
WHOOP_CACHE_TTL=300
```

### Генерация ключа шифрования

Ключ шифрования токенов **не хранится в `.env`** — он задаётся отдельно, чтобы не попасть в git:

```bash
# Сгенерировать ключ
python3 -c "import secrets; print(secrets.token_hex(32))"

# Добавить в окружение (или в .env, если файл в .gitignore)
export WHOOP_TOKEN_ENCRYPTION_KEY=ваш_64_символьный_hex
```

### Переменные окружения — полный список

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `WHOOP_CLIENT_ID` | Да | — | OAuth client ID из WHOOP Developer Dashboard |
| `WHOOP_CLIENT_SECRET` | Да | — | OAuth client secret |
| `WHOOP_TOKEN_ENCRYPTION_KEY` | Да | — | 64-символьная hex-строка (32 байта AES-ключа) |
| `WHOOP_REDIRECT_URI` | Нет | `http://localhost:8080/callback` | Redirect URI для OAuth callback |
| `WHOOP_TOKEN_PATH` | Нет | `~/.whoop/tokens.enc` | Путь к файлу токенов |
| `WHOOP_CACHE_TTL` | Нет | `300` | TTL кэша API-ответов, секунды |
| `LOG_LEVEL` | Нет | `WARNING` | Уровень логов (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Первый запуск

### 1. Авторизация

```bash
whoop auth login
```

Откроется браузер. Войдите в WHOOP и разрешите доступ. Токены автоматически сохранятся в зашифрованном виде.

### 2. Проверка

```bash
whoop auth status
# → Authenticated — token is valid, expires at 2026-03-18T08:00:00+00:00

whoop summary
# → 🟢 Recovery 74% | Sleep 85 | HRV 61 | RHR 55 | Strain 8.3
#   → Good recovery. You can train at full intensity.
```

---

## MCP-инструменты

MCP-сервер предоставляет 8 инструментов. Coach обращается к ним по имени.

### `get_daily_summary`

Агрегированный summary за день. Основной инструмент для утреннего briefing.

**Параметры:**
| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `date` | string | Нет | Дата в формате `YYYY-MM-DD`. По умолчанию — сегодня. |

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
| ≥ 67 | 🟢 | Полная нагрузка |
| 34–66 | 🟡 | Умеренная нагрузка |
| < 34 | 🔴 | Только отдых или лёгкая активность |
| нет данных | ⚪ | Нет данных |

---

### `get_trends`

Тренды метрик за последние N дней.

**Параметры:**
| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `days` | integer | Нет | Количество дней (1–90). По умолчанию `7`. |

**Пример ответа:**
```json
{
  "days": 7,
  "from_date": "2026-03-10",
  "to_date": "2026-03-17",
  "metrics": [
    {"metric": "recovery_score", "average": 68.5, "direction": "↑", "change_pct": 8.2},
    {"metric": "sleep_score",    "average": 81.0, "direction": "→", "change_pct": 1.1},
    {"metric": "hrv_rmssd",      "average": 57.3, "direction": "↑", "change_pct": 5.7},
    {"metric": "resting_hr",     "average": 56.0, "direction": "↓", "change_pct": -3.6},
    {"metric": "strain",         "average": 10.2, "direction": "↑", "change_pct": 12.0}
  ]
}
```

**Направление тренда:** вычисляется как изменение среднего между первой и второй половиной периода. `↑` — рост > 3%, `↓` — падение > 3%, `→` — стабильно.

---

### `get_recovery`

Метрики восстановления за период.

**Параметры:** `start`, `end` — ISO datetime (например `2026-03-17T00:00:00.000Z`). Необязательны, по умолчанию — сегодня.

**Возвращает:** `score`, `hrv_rmssd`, `resting_hr`, `spo2`, `skin_temp_deviation`.

---

### `get_sleep`

Данные сна. Автоматически возвращает основной сон (не дрёму).

**Параметры:** `start`, `end`.

**Возвращает:** `score`, `duration_hours`, `efficiency`, `stages`.

---

### `get_workouts`

Список тренировок за период.

**Параметры:** `start`, `end`.

**Возвращает:** массив объектов с полями `sport`, `strain`, `duration_minutes`, `avg_hr`, `max_hr`, `calories`, `started_at`.

---

### `get_cycles`

Физиологические циклы WHOOP (нагрузка и калории за день).

**Параметры:** `start`, `end`.

---

### `get_profile`

Профиль пользователя: имя, email, user_id.

---

### `get_auth_status`

Статус OAuth-токена.

**Возвращает:**
```json
{
  "authenticated": true,
  "expires_at": "2026-03-18T08:00:00+00:00",
  "expired": false
}
```

---

## CLI

CLI использует тот же `WhoopService`, что и MCP-сервер — никакого дублирования логики.

### Команды

```bash
# Дневной summary
whoop summary
whoop summary --date 2026-03-15
whoop summary --raw          # JSON-вывод

# Восстановление
whoop recovery
whoop recovery --start 2026-03-10T00:00:00Z --end 2026-03-17T23:59:59Z

# Сон
whoop sleep
whoop sleep --raw

# Тренды
whoop trends              # 7 дней
whoop trends --days 30
whoop trends --raw

# Аутентификация
whoop auth status
whoop auth login
whoop auth logout
```

### Флаг `--raw`

Все команды поддерживают `--raw` для вывода чистого JSON — удобно для скриптов и отладки:

```bash
whoop summary --raw | jq '.recovery_score'
```

---

## Подключение к OpenClaw

### 1. Заполнить пути в конфиге

Отредактируйте `deploy/openclaw_mcp_config.json`:

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

Добавьте секцию `whoop` в MCP-конфигурацию OpenClaw (путь зависит от версии — см. документацию OpenClaw).

### 3. Настроить cron для утреннего summary

Пример: Coach вызывает `get_daily_summary` каждый день в 08:00.

```
0 8 * * * /home/user/whoop_connecter/.venv/bin/python -c \
  "import asyncio; from dotenv import load_dotenv; load_dotenv('/home/user/whoop_connecter/.env'); \
   from whoop.services import _build_service_from_env; \
   s = _build_service_from_env(); \
   r = asyncio.run(s.get_daily_summary()); \
   print(r.format_line())"
```

> **Примечание:** Конкретный способ настройки проактивных сообщений зависит от того, как в вашей инсталляции OpenClaw работает cron-интеграция.

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

### Что добавить в `.gitignore`

```gitignore
.env
.venv/
__pycache__/
*.pyc
~/.whoop/
```

---

## Ограничения

### Функциональные

- **OAuth требует браузера.** Первая авторизация (`whoop auth login`) должна выполняться в среде с GUI или с возможностью открыть URL вручную. На headless VPS — открыть ссылку на своём компьютере, но callback должен быть доступен на VPS (нужен проброс порта или выполнить `login` локально с тем же `WHOOP_TOKEN_PATH`).
- **Данные только от WHOOP.** Другие источники (Oura, Apple Health) не поддерживаются в текущей версии, но архитектура подготовлена к расширению через `unified schema`.
- **Кэш сбрасывается при рестарте сервера.** Используется in-memory кэш, данные не переживают перезапуск процесса.
- **Исторические данные ограничены API.** WHOOP API не гарантирует данные старше 1 года.

### Технические

- **Python 3.10+** — обязательно (используется синтаксис `X | Y` для типов).
- **`get_trends` с `days > 30`** — может быть медленнее из-за большого объёма данных от WHOOP API (до ~90 циклов × 2 paginated запроса аггрегации).
- **Один MCP-сервер на процесс** — при нескольких одновременных запросах от Coach они обрабатываются последовательно (MCP stdio однопоточный).

---

## Расширение

### Добавление нового MCP-инструмента

1. Создать файл `mcp_server/tools/my_tool.py`:

```python
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

2. Добавить модуль в `_TOOL_MODULES` в `mcp_server/server.py`.

### Добавление нового источника данных (Oura, Apple Health)

1. Реализовать новый клиент и mapper в `whoop/api/` и `whoop/schema/`.
2. Mapper должен возвращать те же `DailyHealth` dataclasses с `source="oura"`.
3. Вся аналитика (`build_daily_summary`, `build_trends`) работает без изменений.

### Отладка

Включить детальное логирование:

```bash
LOG_LEVEL=DEBUG whoop summary
# или для MCP-сервера:
LOG_LEVEL=DEBUG whoop-mcp
```

Логи MCP-сервера идут в **stderr**, stdout зарезервирован для протокола MCP.
