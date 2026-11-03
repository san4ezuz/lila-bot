# Lila Bot — контекст для работы с проектом

Telegram-бот трансформационной игры «Лила» с LLM-ведущим. Пользователь и тексты — на русском.

**Что делать дальше и почему — [`docs/ROADMAP.md`](docs/ROADMAP.md).** Там статус прода, этапы,
открытые вопросы и решения владельца. Обновляй его, когда закрываешь пункт.

## Архитектура

- `src/core/` — чистый движок (правила, кубик), без БД и сети. Правила: [`docs/RULES.md`](docs/RULES.md).
- `src/api/` — FastAPI, единственный владелец состояния игры. `src/services/` — бизнес-логика.
- `src/bot/` — тонкий клиент aiogram. **В хендлерах нет игровой логики и вызовов LLM**: только
  `src/bot/api_client.py`. FSM (MemoryStorage) — только для UI-шагов и как кэш `game_id`; партию бот
  восстанавливает через API (`current_game_id` в `src/bot/utils.py`), потому что память стирается при
  деплое.
- `src/llm/` — промпты и вызовы Claude. История версий промпта — [`docs/PROMPTS.md`](docs/PROMPTS.md).

## Команды

```bash
docker compose up -d db          # Postgres для тестов и локального запуска
pytest -q                        # тесты используют отдельную БД <имя>_test, создаётся сама
ruff check src && mypy src
alembic upgrade head && alembic check
python scripts/play_cli.py 1000  # симуляция баланса партий
```

Lock-файл после правки зависимостей в `pyproject.toml`:
`uv pip compile pyproject.toml --python-version 3.12 --python-platform linux -o requirements.lock`.

## Подводные камни

- ID моделей Claude пишутся через дефисы: `claude-haiku-5-5`, не `claude-haiku-5.5` (иначе 404, и бот
  молча отдаёт шаблон «О чем для тебя эта клетка?» — это запасной текст `interpret_cell`).
- У Haiku 5.5 и новее мышление расходует `max_tokens` — не опускать лимиты в `src/llm/guide.py`.
- Фолбэк LLM ничего не показывает игроку: при подозрении смотри таблицу `llm_calls`
  (`is_fallback`, `error`, `stop_reason`) и логи api `LLM call failed` / `stopped early`. Сбои шлют
  алерт админам (`src/services/alerts.py`), если `ADMIN_TELEGRAM_IDS` задан на сервисе api.
- Все вызовы модели идут через `src/llm/client.py::call_model` и возвращают `LlmResult`; сервис
  пишет каждый в `llm_calls`. Новый вызов LLM — тоже через `call_model` и `_record_llm`.
- Формат вебхука Tribute — по OpenAPI `tribute.tg/api/v1/openapi/en`; каждый вебхук лежит в
  `webhook_inbox` с причиной статуса. При сохранении адреса Tribute шлёт `{"test_event": ...}`.
- `games.updated_at` обновляется на любой UPDATE — не писать служебные поля в `games` (см.
  `Reminder` в `src/db/models.py`).
- Тестовая БД `<имя>_test` создаётся через `create_all`, который не добавляет колонки в
  существующие таблицы. После новой колонки: `ALTER TABLE ...` в `lila_test` или пересоздать её.
- Safety: словарь `src/llm/safety.py` и классификатор там же. Новая фраза → кейс в
  `tests/test_safety.py`, и бытовая фраза-антипример, если шаблон рискует её поймать.
- Локальный `.env` может указывать другую модель, чем прод (`LLM_MODEL`) — живые проверки
  промптов делать на прод-модели.
- На этой машине Windows и TLS-перехват: внутри Docker pip не достаёт PyPI, это не ошибка проекта.

## Прод

Railway (сервисы `api`, `bot`, Postgres), деплой из `master`. Секреты только в переменных Railway;
API не стартует с `change-me`. Админ выдаёт партии командой боту `/grant <telegram_id> [count]`.
