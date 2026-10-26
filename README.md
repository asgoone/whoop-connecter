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

Ключевые свойства:

- **Только чтение.** Ни CLI, ни MCP не изменяют данные в WHOOP.
- **Фоновая работа без вмешательства.** Обычные вызовы никогда не запускают интерактивный вход; если авторизация утрачена, возвращается понятный код ошибки (`REAUTH_REQUIRED`), а агент сообщает владельцу.
- **Дни по местному времени пользователя**, а не по UTC (см. [Как определяется день](#как-определяется-день)).

---

## Как это работает

```
WHOOP API v2 (OAuth 2.0 + PKCE)
        │
        ▼
whoop/api/client.py     ← httpx async, TTL кэш, refresh на 401, Retry-After, ограниченные повторы
        │
        ▼
whoop/schema/mappers.py ← JSON → unified dataclasses
        │
        ▼
whoop/services.py       ← WhoopService (shared core), местный день по циклам WHOOP
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

Вход выполняется **только явной командой** `whoop auth login` / `whoop auth login-headless` (OAuth 2.0 с PKCE). Обычные команды CLI и вызовы MCP никогда не открывают браузер, не ждут ввода и не поднимают callback-сервер: без токенов они возвращают ошибку `AUTH_REQUIRED`.

1. Открывается браузер → страница авторизации WHOOP.
2. На `localhost:8080/callback` ловится redirect с кодом.
3. Параметр `state` верифицируется для защиты от CSRF.
4. Токены шифруются **AES-256-GCM** и сохраняются на диск (`~/.whoop/tokens.enc`).
5. При следующих запросах токен обновляется автоматически через refresh token.

#### Надёжность токенов

- WHOOP ротирует **оба** токена при каждом refresh, а параллельный refresh со старым токеном отклоняется. Поэтому refresh выполняется под межпроцессной блокировкой (`tokens.enc.lock`, flock) с перечитыванием файла после захвата: токен, уже обновлённый другим процессом, переиспользуется.
- После `401` при «живом» по локальным часам токене выполняется **один** принудительный refresh и **один** повтор запроса; повторный `401` — ошибка `REAUTH_REQUIRED`.
- Запись атомарная: временный файл с правами 600 с момента создания → fsync → rename. Файл, который не удалось прочитать или расшифровать (например, неверный ключ), **не удаляется** — возвращается ошибка `TOKEN_STORE_ERROR`.
- Таймауты, 5xx и 429 не трогают токены. `Retry-After` соблюдается (до 10 с ждём сами, дольше — ошибка `RATE_LIMITED` с `retry_after_seconds`).
- **Токенами должен владеть один хост.** Локальная блокировка не защищает от другой машины, которая обновляет тот же refresh token: при переносе отключите WHOOP на старом сервере.

#### Коды ошибок

| Код | Что значит | Код выхода CLI |
|-----|------------|----------------|
| `AUTH_REQUIRED` | токенов нет — нужен вход | 3 |
| `REAUTH_REQUIRED` | refresh token отклонён / согласие отозвано — нужен повторный вход | 3 |
| `INSUFFICIENT_SCOPE` | нет нужного scope (403) — повторный вход | 3 |
| `TRANSIENT_ERROR` | таймаут, сеть, 5xx — повторить позже | 4 |
| `RATE_LIMITED` | 429 — повторить позже | 4 |
| `TOKEN_STORE_ERROR` | файл токенов не читается (ключ/повреждение), файл сохранён | 5 |
| `CONFIG_ERROR` | неверные client id/secret или env | 5 |
| `INCOMPLETE_DATA` | пагинация упёрлась в лимит страниц | 1 |

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

### Как определяется день

WHOOP фильтрует записи по времени начала в UTC, а каждый цикл, сон и тренировка содержат `timezone_offset` пользователя. Поэтому «день» вычисляется так:

1. Запрашивается окно с запасом (от −3 до +2 суток вокруг даты), чтобы покрыть любой часовой пояс.
2. Цикл WHOOP начинается в момент засыпания. Он относится к **местному дню пробуждения**: засыпание до местного полудня — этот же день, после полудня (вечером) — следующий.
3. Сон и восстановление привязываются к циклу по `cycle_id` (поле обязательно в API v2); тренировки — по попаданию в интервал цикла.
4. «Сегодня» — местная дата по часовому поясу последнего цикла (UTC, если циклов нет).

Пример: засыпание в 02:55 по Москве 26-го (23:55 UTC 25-го) попадёт в сводку за **26-е**, а не 25-е. День без записей (браслет не носили) — нормальный пустой результат, а не ошибка.

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
│   ├── errors.py             # Типизированные ошибки (ErrorCode, WhoopError)
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
│   ├── openclaw_mcp_config.json  # Пример записи mcp.servers для OpenClaw
│   ├── whoop-mcp-launcher.example # Пример лаунчера: секреты из env-файла, не из конфига
│   └── setup_vps.sh              # Скрипт развёртывания на VPS
│
├── tests/                    # ~290 тестов (unit + integration + acceptance), без живых секретов
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Установка

### Требования

- Python 3.10+
- Linux или macOS (межпроцессная блокировка токенов использует `fcntl.flock`; Windows не поддерживается)
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
whoop auth status   # пассивно: только чтение файла токенов
# → State: valid
whoop auth check    # реальный запрос к WHOOP
# → Check OK: WHOOP API accepted the token.

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
whoop summary                     # сегодня (по местному времени)
whoop summary --date 2026-03-15   # конкретный местный день
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

Дни в экспорте и трендах помечены местной датой пробуждения (см. [Как определяется день](#как-определяется-день)).

Экспортирует body + N дней daily health (sleep, recovery, activity) в JSON. Использует batch-запросы: **4 HTTP-вызова** независимо от количества дней (1 body + 3 paginated: cycles, recovery, sleep).

#### `whoop auth` — управление авторизацией

```bash
whoop auth status          # пассивный статус: читает файл, без сети и без refresh
whoop auth check           # активная проверка: refresh при необходимости + запрос профиля
whoop auth login           # явная авторизация через браузер
whoop auth login-headless  # явная авторизация без браузера (VPS)
whoop auth logout          # удалить токен
```

`auth status` возвращает `state`: `valid`, `refresh_needed`, `auth_required`, `reauth_required` или `store_error`. Он не делает refresh, поэтому не может доказать, что refresh token ещё принимается WHOOP; для этого — `auth check`.

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

MCP-сервер предоставляет **9 инструментов**, все только на чтение. Coach обращается к ним по имени (в OpenClaw — с префиксом сервера, например `whoop__get_daily_summary`).

**Формат ошибок.** Любая ошибка возвращается с `isError: true` и телом:

```json
{"error": {"code": "REAUTH_REQUIRED", "message": "...", "hint": "Owner must re-login: ..."}}
```

Коды — из таблицы [Коды ошибок](#коды-ошибок), плюс `INVALID_ARGUMENTS`, `UNKNOWN_TOOL`, `INTERNAL_ERROR` (детали исключения не раскрываются, только тип). Пустой период — **не** ошибка: `get_recovery` и `get_sleep` возвращают `{"no_data": true, "message": "..."}` с `isError: false`.

### `get_daily_summary`

Агрегированный summary за день. Основной инструмент для утреннего briefing.

**Параметры:**

| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `date` | string | Нет | Местный день `YYYY-MM-DD`. По умолчанию — сегодня по часовому поясу пользователя. Неверный формат → `INVALID_ARGUMENTS`. |

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
| `days` | integer | Нет | Количество дней (1-90, проверяется схемой). По умолчанию `7`. |

**Метрики:** `recovery_score`, `sleep_score`, `hrv_rmssd`, `resting_hr`, `strain`.

**Направление тренда:** вычисляется как изменение среднего между первой и второй половиной периода. `↑` — рост > 3%, `↓` — падение > 3%, `→` — стабильно.

---

### `get_recovery`

Метрики восстановления за период.

**Параметры:** `start`, `end` — ISO datetime в UTC. Необязательны; без них возвращается последняя запись. Для данных «за день» используйте `get_daily_summary` — он учитывает местное время.

**Возвращает:** `score`, `hrv_rmssd`, `resting_hr`, `spo2`, `skin_temp_deviation`.

---

### `get_sleep`

Данные сна. Автоматически выбирает основной сон (не дрёму), предпочитает `SCORED` записи.

**Параметры:** `start`, `end` — ISO datetime в UTC, как у `get_recovery`.

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

Профиль пользователя: имя, email, user_id. Агенту обычно не нужен — рекомендуется исключить его через `toolFilter` (см. [Подключение к OpenClaw](#подключение-к-openclaw)).

---

### `get_auth_status`

Статус авторизации WHOOP. По умолчанию пассивный (читает только файл токенов, без сети). С аргументом `verify: true` делает реальный запрос к WHOOP и добавляет поле `check` (`ok` или код ошибки, например `REAUTH_REQUIRED`).

| Параметр | Тип | Обязателен | Описание |
|---|---|---|---|
| `verify` | boolean | Нет | Живая проверка через API (может обновить токен). |

`state`: `valid`, `refresh_needed`, `auth_required`, `reauth_required`, `store_error`.

**Возвращает:**

```json
{
  "state": "valid",
  "authenticated": true,
  "expires_at": "2026-03-18T08:00:00+00:00",
  "expired": false,
  "has_refresh_token": true,
  "scopes": ["offline", "read:body_measurement", "..."],
  "missing_scopes": []
}
```

---

## Подключение к OpenClaw

Схема ниже проверена на OpenClaw 2026.9 (`mcp.servers`). В другой версии сверьтесь с документацией установленного OpenClaw (`docs/tools/mcp.md`).

### 1. Лаунчер вместо секретов в конфиге

Не кладите `WHOOP_CLIENT_SECRET` и ключ шифрования в конфиг OpenClaw. Используйте лаунчер, который читает env-файл (права `600`, владелец — пользователь gateway) и запускает `whoop-mcp`. Пример — [`deploy/whoop-mcp-launcher.example`](deploy/whoop-mcp-launcher.example): скопируйте, укажите пути, сделайте исполняемым. Если секреты хранятся в Vault или другом менеджере, пусть он рендерит этот env-файл.

### 2. Добавить сервер

Штатной командой OpenClaw (она проверяет сервер перед сохранением), от пользователя gateway:

```bash
openclaw mcp add whoop \
  --command /path/to/whoop-mcp-launcher \
  --include get_auth_status,get_daily_summary,get_recovery,get_sleep,get_workouts,get_cycles,get_trends,get_body_measurement \
  --approval auto --connect-timeout 15 --timeout 45
```

Итоговая запись — см. [`deploy/openclaw_mcp_config.json`](deploy/openclaw_mcp_config.json).

### 3. Ограничить доступ одним агентом

Используются **оба** механизма — они действуют в разных рантаймах:

```bash
# Codex-рантайм: сервер виден только агенту coach
openclaw config set mcp.servers.whoop.codex.agents '["coach"]' --strict-json

# Встроенный рантайм: запретить инструменты остальным агентам.
# Внимание: value заменяет весь список — включите существующие записи.
openclaw config set agents.entries.main.tools.deny '["whoop__*"]' --strict-json --dry-run
```

Изменения `mcp.servers` и `agents` применяются без перезапуска gateway (hot reload).

### 4. Проверить

```bash
openclaw mcp doctor whoop --probe   # → whoop: ok
```

Затем из сессии агента вызовите `get_auth_status` и убедитесь, что остальные агенты инструментов `whoop__*` не видят.

### 5. Настроить proactive summary (опционально)

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
8. Проверьте: `whoop auth check`.

> **Важно:** Redirect на `localhost:8080` не сработает в браузере на другом устройстве — это нормально. Просто скопируйте URL из адресной строки, даже если страница не загрузилась.

### Повторный вход

Нужен, только когда `auth check` или агент сообщает `REAUTH_REQUIRED` / `AUTH_REQUIRED`. Вход интерактивный: запускайте его в обычном терминале (для SSH — с `-t`), от того же пользователя, под которым работает MCP-сервер, иначе файл токенов окажется у другого владельца.

```bash
ssh -t <host> 'cd /tmp && sudo -u <gateway-user> /path/to/whoop auth login-headless'
```

Код авторизации в callback-URL одноразовый и живёт минуты: вставляйте его сразу и не пересылайте в чаты.

### Перенос на другой сервер

Токенами должен владеть **один** хост. Не копируйте `tokens.enc` между работающими серверами: первый же refresh на одном из них сделает копию на другом недействительной. Порядок: остановить WHOOP на старом хосте → выполнить новый вход на новом → убрать старый файл токенов.

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
| Секреты в конфиге OpenClaw | Не хранятся: лаунчер читает env-файл `600` и передаёт только разрешённые переменные |
| Доступ агентов | `toolFilter` (только чтение, без `get_profile`), `codex.agents` + `tools.deny` для остальных |
| Ошибки и логи | Без токенов, секретов и медицинских значений; детали исключений в MCP не возвращаются |
| Повреждённый файл токенов | Не удаляется автоматически; запись атомарная |
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
- **Граница дня — местный полдень.** Расчёт рассчитан на обычный ночной сон; при основном дневном сне (сменная работа) день может сместиться.
- **`get_recovery` / `get_sleep` / `get_workouts` / `get_cycles` с явными `start`/`end`** работают в окнах UTC. Для «дня» используйте `get_daily_summary`.
- **Повторный вход — только вручную.** Если WHOOP отзовёт доступ, нужен интерактивный вход владельца; агент лишь сообщает об этом.

### Технические

- **Python 3.10+** — обязательно (используется синтаксис `X | Y` для типов).
- **Только Linux/macOS**: блокировка токенов на `fcntl.flock`. Она защищает процессы одного хоста, но не несколько серверов с общим refresh token.
- **Токен доступа живёт ~1 час**, refresh выполняется автоматически при первом запросе после истечения.
- **Один MCP-сервер на процесс** — при нескольких одновременных запросах от Coach они обрабатываются последовательно (MCP stdio однопоточный).
- **`get_trends` / `export` с `days > 30`** — медленнее из-за большого объёма данных, но используют batch-запросы (3-4 HTTP-вызова при умеренном N; при больших объёмах — пагинация).
- **Лимит пагинации** — 50 страниц на запрос; при превышении возвращается `INCOMPLETE_DATA` вместо неполного списка.

### WHOOP API v2

Формат ответов — вложенный (метрики внутри объекта `score`). Маперы проекта обрабатывают как вложенный, так и плоский формат для совместимости. Официальная документация: [developer.whoop.com](https://developer.whoop.com).

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
# Запустить все тесты (~290, без сети и живых секретов)
.venv/bin/python -m pytest tests/ -v

# Только unit-тесты
.venv/bin/python -m pytest tests/unit/ -v

# Только интеграционные + приёмочные
.venv/bin/python -m pytest tests/integration/ -v
```

Структура тестов:

| Директория | Что тестирует | Количество |
|---|---|---|
| `tests/unit/` | Маперы, аналитика, кэш, токен-стор, OAuth (включая гонку refresh из нескольких процессов), ошибки | ~170 |
| `tests/integration/` | Сервисный слой, местный день, устойчивость HTTP-клиента (401/429/5xx), CLI-коды выхода, MCP-инструменты и MCP через реальный stdio | ~125 |
