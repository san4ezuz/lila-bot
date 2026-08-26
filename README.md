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
- [ ] Этап 6 — Telegram-бот
- [ ] Этап 7 — тестовые партии и тюнинг тона
- [ ] Этап 8 — Tribute
- [ ] Этап 9 — метрики
- [ ] Этап 10 — деплой

