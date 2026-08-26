import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db.models import Base


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Runs each test inside its own transaction against the local dev Postgres,
    rolled back afterwards so tests never leave data behind. Requires `docker compose up -d db`
    and DATABASE_URL pointed at it (see .env.example)."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)

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
