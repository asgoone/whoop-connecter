# ТЗ: WHOOP MCP-сервер для OpenClaw Coach

## Цель

Дать персональному агенту Coach (работающему в OpenClaw) доступ к данным WHOOP через MCP-протокол, чтобы он мог:
- учитывать recovery / sleep / strain / HRV
- адаптировать рекомендации по тренировкам
- объяснять тренды по самочувствию
- проактивно отправлять утренний summary в Telegram
- замечать ухудшения и рекомендовать снизить нагрузку или обратиться к врачу

---

## Архитектура

### Схема
```
WHOOP API (OAuth 2.0) → MCP-сервер (Python) → OpenClaw Gateway → Coach → Telegram
```

### Компоненты

1. **WHOOP MCP-сервер** (Python)
   - Форк `RomanEvstigneev/whoop-mcp-server` с доработками
   - Прямой OAuth 2.0 с WHOOP (без прокси appspot.com)
   - Unified data schema (под будущие Oura / Apple Health)
   - Дополнительные MCP-тулы: `get_daily_summary`, `get_trends`
   - CLI-интерфейс для ручного доступа к данным

2. **OpenClaw + Coach**
   - Уже развёрнут и работает на VPS
   - Coach — skill/агент внутри OpenClaw
   - Канал общения: Telegram
   - Cron уже настроен — будет использоваться для утреннего summary

3. **Деплой**
   - VPS: Ubuntu 22.04.5 LTS (x86_64)
   - Python (нативный, без Docker в MVP)
   - MCP-сервер рядом с OpenClaw на том же сервере

---

## Базовый MCP-сервер (форк)

### Источник: `RomanEvstigneev/whoop-mcp-server`
- Python 3.8+
- 6 MCP tools: profile, workouts, recovery, sleep, cycles, auth_status
- AES-шифрование токенов
- Кэширование (5 мин TTL)
- Auto-refresh токенов

### Что убрать из форка
- OAuth-прокси через appspot.com → заменить на прямой OAuth с WHOOP API
- Smithery TypeScript реализацию (не нужна)

### Что добавить

#### Новые MCP-тулы
- `get_daily_summary` — агрегированный summary за день (recovery score, sleep score, HRV, RHR, strain, рекомендация)
- `get_trends(days: int)` — тренды за N дней (7/14/30) с направлением изменений

#### CLI-интерфейс
Вдохновлён `xonika9/whoop-cli`:
- `whoop summary` — однострочный обзор здоровья
- `whoop recovery` — recovery метрики
- `whoop sleep` — данные по сну
- `whoop trends --days 7` — тренды
- `whoop auth status` — статус токена

CLI и MCP-сервер используют один и тот же Python-код (shared core).

#### Unified data schema
Схема данных готова к расширению на другие источники:

```python
{
    "source": "whoop",           # whoop | oura | apple_health
    "date": "2026-03-16",
    "fetched_at": "2026-03-16T08:00:00Z",
    "sleep": {
        "score": 82,             # 0-100
        "duration_hours": 7.5,
        "efficiency": 0.91,
        "stages": {...}          # source-specific
    },
    "recovery": {
        "score": 68,             # 0-100 (recovery для WHOOP, readiness для Oura)
        "hrv_rmssd": 54.2,       # мс
        "resting_hr": 58,        # уд/мин
        "spo2": 96.5,            # % (если доступно)
        "skin_temp_deviation": null  # °C (если доступно)
    },
    "activity": {
        "strain": 11.4,          # WHOOP strain (0-21)
        "calories": 2150,
        "workouts": [...]
    }
}
```

---

## Обязательные метрики (MVP)

| Метрика | WHOOP API endpoint | Приоритет |
|---------|-------------------|-----------|
| Sleep score | `/v1/activity/sleep` | P0 |
| Recovery score | `/v1/recovery` | P0 |
| HRV (RMSSD) | `/v1/recovery` | P0 |
| Resting HR | `/v1/recovery` | P0 |
| Strain | `/v1/cycle` | P0 |
| Workouts | `/v1/activity/workout` | P1 |
| SpO₂ | `/v1/recovery` | P1 |
| Body measurements | `/v1/body_measurement` | P2 |

---

## Что должен уметь Coach после интеграции

### 1. Daily summary (проактивно, утром через cron)
Пример сообщения в Telegram:
```
🟡 Recovery 68% | Sleep 82 | HRV 54 | RHR 58 | Strain 11.4
→ Умеренная нагрузка. Не перегружайся.
```

### 2. Ответы по запросу
- "Как я спал?" → данные сна
- "Стоит ли сегодня тренироваться?" → decision support на основе recovery/strain
- "Покажи тренды за неделю" → get_trends(7)

### 3. Trend analysis
- Как меняется сон, recovery, HRV
- Связь между нагрузкой и восстановлением
- Обнаружение негативных трендов

### 4. Safety escalation (логика на стороне Coach)
Coach НЕ ставит диагнозы. При стабильно плохих метриках:
- рекомендует снизить нагрузку
- советует обратиться к врачу
- не даёт медицинских заключений

---

## Аутентификация

### WHOOP OAuth 2.0
- Свои client_id / client_secret (developer.whoop.com)
- Прямой OAuth без прокси
- Scopes: `read:profile`, `read:workout`, `read:recovery`, `read:sleep`, `offline`
- Токены хранятся локально, зашифрованы AES
- Auto-refresh при истечении

### Переменные окружения
```
WHOOP_CLIENT_ID=...
WHOOP_CLIENT_SECRET=...
WHOOP_REDIRECT_URI=...
```

---

## Безопасность

- Токены зашифрованы AES на диске
- Файлы токенов с правами 600
- Секреты в .env, НЕ в коде и НЕ в промптах
- Хранить только нужные summary-данные
- Coach не имеет медицинского авторитета

---

## Этапы внедрения

### Этап 1 — MVP: WHOOP MCP-сервер
1. Форк `RomanEvstigneev/whoop-mcp-server`
2. Заменить OAuth-прокси на прямой OAuth
3. Добавить `get_daily_summary` и `get_trends` тулы
4. Unified data schema
5. Подключить к OpenClaw как MCP-сервер
6. Настроить cron для утреннего summary
7. Проверить что Coach корректно читает данные

### Этап 2 — CLI
1. Добавить CLI-интерфейс (shared core с MCP)
2. Команды: summary, recovery, sleep, trends, auth

### Этап 3 — (будущее) Oura
1. Новый MCP-сервер или расширение текущего
2. Unified schema уже готова
3. Лучший кандидат: `YuzeHao2023/MCP-oura` (113 stars, Python)

### Этап 4 — (будущее) Apple Health
1. Нет прямого REST API (только HealthKit)
2. Вариант: экспорт → Parquet → DuckDB (как `sbmeaper/healthkit-mcp-v3`)

---

## Критерии успеха MVP

- [ ] MCP-сервер запускается и подключается к OpenClaw
- [ ] Coach может запросить recovery/sleep/strain через MCP
- [ ] get_daily_summary возвращает агрегированные данные
- [ ] get_trends возвращает тренды за 7/14/30 дней
- [ ] Утренний summary приходит в Telegram через cron
- [ ] Токены обновляются автоматически
- [ ] CLI работает для ручной проверки данных

---

## Референсные проекты

| Проект | URL | Роль |
|--------|-----|------|
| RomanEvstigneev/whoop-mcp-server | https://github.com/RomanEvstigneev/whoop-mcp-server | Базовый форк |
| xonika9/whoop-cli | https://github.com/xonika9/whoop-cli | Референс CLI и аналитики |
| ctvidic/whoop-mcp-server | https://github.com/ctvidic/whoop-mcp-server | Референс (healthspan метрики) |
| nissand/whoop-mcp-server-claude | https://github.com/nissand/whoop-mcp-server-claude | Референс (полное покрытие v2 API) |
| YuzeHao2023/MCP-oura | https://github.com/YuzeHao2023/MCP-oura | Будущая интеграция Oura |
| OpenClaw | https://github.com/openclaw/openclaw | Платформа агента |
