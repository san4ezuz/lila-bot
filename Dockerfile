# Single image for both services (docs/PLAN.md §13) — Railway runs it twice with different
# start commands: `uvicorn src.api.main:app ...` for the API, `python -m src.bot.main` for the
# bot. Neither process needs a C toolchain; asyncpg ships wheels for this base image.
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml requirements.lock ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY data ./data

# Exact versions from requirements.lock, so a redeploy can't pick up a new major of a dependency.
# Regenerate after changing pyproject.toml:
#   uv pip compile pyproject.toml --python-version 3.12 --python-platform linux -o requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .

# No CMD here on purpose — Railway sets the start command per service (see docs/PLAN.md §13):
#   api service: alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port $PORT
#   bot service: python -m src.bot.main
