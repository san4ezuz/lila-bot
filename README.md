# Lila Bot

A Telegram bot for **Leela (Lila)**, a transformational board game guided by an LLM. Players roll dice, move across a 72-cell board, and receive AI-generated interpretations of each cell from a "guide" persona.

## Stack

- **Bot**: [aiogram](https://docs.aiogram.dev/) 3.x — thin client, no game logic in handlers
- **API**: FastAPI, orchestrating game rules, persistence, and LLM calls
- **Game engine**: pure Python core (`src/core/`) — board, dice, rules
- **Database**: PostgreSQL via SQLAlchemy 2.0 (async) + Alembic migrations
- **LLM**: Anthropic Claude — cell interpretation, follow-up questions, game summaries
- **Billing**: Tribute webhook integration for paid games

## Quick start

Requires Python 3.12+.

```bash
pip install -e ".[dev]"
cp .env.example .env        # fill in BOT_TOKEN / ANTHROPIC_API_KEY
docker compose up -d db     # local Postgres
alembic upgrade head        # apply DB schema
pytest                      # engine, board data validator, repositories
python scripts/play_cli.py  # simulate 1000 games, sanity-check rule balance
```

## Project layout

```
src/core/      game engine (board, dice, rules)
src/db/        SQLAlchemy models + repositories
src/api/       FastAPI app and routers
src/llm/       Anthropic client, prompts, guide orchestration
src/bot/       aiogram bot (handlers, keyboards, texts)
src/services/  business logic tying the above together
data/          board/transition data, cell artwork
docs/          design docs, rules, prompt versions
```

Further design notes and progress log: [docs/PLAN.md](docs/PLAN.md). Game rules: [docs/RULES.md](docs/RULES.md).
