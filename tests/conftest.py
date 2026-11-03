import uuid

import pytest
import pytest_asyncio
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db.models import Base


@pytest.fixture(autouse=True)
def stub_llm_calls(monkeypatch):
    """Game/API tests exercise src.llm.guide through GameService, but should stay fast and
    network-free by default — only tests/test_llm_guide.py cares about the real _call() plumbing
    and unsets this via monkeypatch.undo() / a local override where needed."""

    async def _fake_call(context: str, max_tokens: int, effort: str, fallback: str) -> str:
        return f"[stub effort={effort}] {fallback}"

    async def _fake_classify(text: str) -> bool:
        return False

    monkeypatch.setattr("src.llm.guide._call", _fake_call)
    monkeypatch.setattr("src.llm.safety.classify", _fake_classify)


async def _ensure_database(server_url: URL, name: str) -> None:
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        exists = await connection.scalar(
            text("select 1 from pg_database where datname = :name"), {"name": name}
        )
        if not exists:
            await connection.execute(text(f'create database "{name}"'))
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Runs each test inside its own transaction, rolled back afterwards so tests never leave data
    behind. Uses a separate `<name>_test` database on the DATABASE_URL server, because cron-style
    services scan whole tables and would otherwise trip over rows in the dev database. Requires
    `docker compose up -d db` (see .env.example)."""
    url = make_url(get_settings().database_url)
    test_url = url.set(database=f"{url.database}_test")
    await _ensure_database(url, test_url.database)
    engine = create_async_engine(test_url)

    async with engine.connect() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.commit()  # close the autobegin transaction opened by create_all
        trans = await connection.begin()
        sessionmaker = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        async with sessionmaker() as session:
            yield session
        await trans.rollback()

    await engine.dispose()


@pytest.fixture
def unique_telegram_id() -> int:
    # Large random-ish int derived from a uuid so parallel test runs don't collide.
    return uuid.uuid4().int % (2**31)
