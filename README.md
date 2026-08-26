# Лила — Telegram-бот

Трансформационная игра с LLM-проводником. Архитектура и этапы разработки: [docs/PLAN.md](docs/PLAN.md).
Правила игры: [docs/RULES.md](docs/RULES.md).

## Быстрый старт (после установки Python 3.12+)

```bash
pip install -e ".[dev]"
cp .env.example .env        # заполнить BOT_TOKEN / ANTHROPIC_API_KEY при необходимости
docker compose up -d db     # локальный Postgres
alembic upgrade head        # применить схему БД
pytest                      # движок, валидатор данных поля, репозитории (нужен поднятый db)
python scripts/play_cli.py  # симуляция 1000 партий, проверка баланса правил
```

## Статус

- [x] Этап 0 — окружение (структура репозитория, зависимости, `.env`, `docker-compose.yml`,
      Python 3.12 установлен через `winget`)
- [x] Этап 1 — данные поля: `data/board.json` (72 клетки), `data/transitions.json` — названия
      клеток и таблица переходов **сверены по 3 источникам** (`docs/leela_cells_final.json`),
      интерпретационный слой (theme/facets/questions/guna/element) — **черновик**, требует
      вычитки экспертом перед продакшеном; `docs/RULES.md` актуализирован под верифицированную
      механику (зона перелёта 69–72, финиш строго на 68 при физической длине доски 72)
- [x] Этап 2 — игровой движок `src/core/` + тесты `tests/test_rules.py`, `tests/test_board_data.py`
      (**28/28 passed**), симуляция `scripts/play_cli.py` — лимит ходов подобран по симуляции
      1000 партий: **60** (76% партий доходят до финиша; 40 из исходного описания давало только 58%)
- [x] Этап 3 — БД: `src/db/models.py` (SQLAlchemy 2.0, 6 таблиц), `src/db/repositories.py`,
      Alembic-миграция применена к локальному Postgres (`docker compose up -d db`),
      `tests/test_db_repositories.py` — **6/6 passed**
- [x] Этап 4 — Game API: `src/api/` (FastAPI, роутер `game.py`), `src/services/game_service.py`
      (оркестрация правил+БД), `src/llm/guide.py` — пока **заглушки текста** без реального LLM
      (Этап 5 заменит тела функций, сигнатуры и `game_service.py` не изменятся); проверено и
      тестами (`tests/test_api_game.py`, 10/10), и вживую через `curl` на поднятом `uvicorn`
- [x] Этап 5 — LLM-оркестрация: `src/llm/client.py` (Anthropic SDK, `claude-opus-5`),
      `src/llm/prompts.py` (`GUIDE_SYSTEM_PROMPT` v1 + сборка контекста, версии — `docs/PROMPTS.md`),
      `src/llm/guide.py` — три реальных вызова (`interpret_cell`/`deepen`/`summarize_game`,
      prompt caching, effort medium/medium/high, таймаут+фолбэк на ошибку); процедурные
      сообщения (вход не удался, перелёт, лимит ходов) остаются статичным текстом.
      **Не проверено на живом ключе** (решение пользователя — писать код без него сейчас);
      `tests/test_llm_guide.py` (10 тестов) проверяет плагин на моках. Обязательно прогнать
      реальные партии на живом ключе перед Этапом 7
- [x] Этап 6 — Telegram-бот: `src/bot/` (aiogram 3.x, тонкий клиент — никакой игровой логики
      и LLM-вызовов в хендлерах, только вызовы `api_client.py`). FSM (`states.py`) используется
      только для UI-шага «жду текст запроса», не для игровых данных. `tests/test_bot_api_client.py`
      (6 тестов) проверяет контракт бот↔API через реальный FastAPI-app (без сети).
      **Хендлеры не проверены вживую в Telegram** — нет `BOT_TOKEN` в этой сессии; нужен
      реальный прогон `python -m src.bot.main` с токеном от BotFather перед Этапом 7
- [ ] Этап 7 — тестовые партии и тюнинг тона
- [x] Этап 8 — Tribute: `src/services/billing_service.py` (проверка подписи вебхука,
      идемпотентность по `external_id`, начисление платной партии, уведомление в Telegram через
      raw Bot API), `POST /payments/tribute/webhook` + `POST /admin/grant` (роутер `payments.py`),
      команда `/grant` в боте (только для `ADMIN_TELEGRAM_IDS`). **Обновлено по официальной
      документации Tribute** (wiki.tribute.tg, найдена и прочитана в этой сессии): заголовок
      подписи — **`trbt-signature`** (не выдуманный `X-Tribute-Signature`), подписывается тем же
      **`Api-Key`** (`TRIBUTE_API_KEY`), а не отдельным webhook-секретом; добавлен фильтр по
      реальным именам событий Tribute (успех/отказ/неизвестное — игнорируется, а не начисляется
      вслепую). Ссылка оплаты — статическая (`TRIBUTE_PAY_URL`): у Tribute нет API для создания
      персональной ссылки с метаданными, корреляция с пользователем — через `telegramId` в теле
      вебхука. Точная вложенность полей в теле вебхука **всё ещё не подтверждена** (GitBook
      прячет OpenAPI-схему за виджетом) — см. предупреждение в начале файла и `docs/PLAN.md` §11.2.
      `tests/test_billing.py` (17 тестов)
- [x] Этап 9 — метрики: `scripts/metrics.sql` (4 метрики MVP из §9 плана + бонусная воронка
      пейволла), проверен вживую через `docker exec ... psql -f scripts/metrics.sql` на локальной
      БД. Добавил недостающее логирование события `paywall_shown` (было пропущено в Этапе 4/8).
      Не логируются `response_skipped`/`game_abandoned`/`feedback_given` — см. пометку в начале
      SQL-файла
- [x] Этап 10 (артефакты) — `Dockerfile` + `.dockerignore` (единый образ для `api` и `bot`,
      разные команды запуска). **Собран и прогнан локально по-настоящему**: `docker build`,
      контейнер поднят с реальным `alembic upgrade head` и `uvicorn`, `/health` ответил `ok`.
      Сам деплой на Railway **не выполнялся** — нужен доступ к вашему аккаунту Railway,
      см. раздел «Деплой» ниже

## Деплой (Railway)

Три компонента (docs/PLAN.md §13): управляемый Postgres + два сервиса из одного образа
(`Dockerfile`), с разными командами запуска.

1. `railway login`, затем `railway init` в корне репозитория (или через railway.app UI:
   New Project → Deploy from GitHub repo).
2. Добавить плагин **PostgreSQL** — Railway сам создаст `DATABASE_URL` в своих переменных;
   его нужно будет переложить в `DATABASE_URL` сервиса `api`/`bot` в формате
   `postgresql+asyncpg://...` (Railway по умолчанию даёт `postgresql://` — заменить схему).
3. Сервис **api**: source = этот репозиторий (Dockerfile обнаружится автоматически).
   - Start command: `alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
   - Переменные: `DATABASE_URL`, `API_INTERNAL_TOKEN`, `ANTHROPIC_API_KEY`, `TRIBUTE_WEBHOOK_SECRET`,
     `PRICE_SECOND_GAME_RUB`, `LLM_MODEL=claude-opus-5`, `BOT_TOKEN` (нужен и здесь — используется
     для уведомлений после оплаты, см. `src/services/billing_service.py`)
   - Healthcheck: `/health`
4. Сервис **bot**: тот же репозиторий/образ.
   - Start command: `python -m src.bot.main`
   - Переменные: `BOT_TOKEN`, `API_BASE_URL` (внутренний URL сервиса `api` в сети Railway),
     `API_INTERNAL_TOKEN` (тот же секрет, что у `api`), `ADMIN_TELEGRAM_IDS`
5. Бэкап Postgres — включить в настройках плагина (или `pg_dump` по крону) до пуска в люди.

Актуальные шаги/имена полей в UI Railway могут отличаться от изложенного — сверить с
их текущей документацией на момент деплоя.
