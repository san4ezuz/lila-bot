import hmac
from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.session import get_session

__all__ = ["get_db", "require_internal_token"]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def require_internal_token(x_internal_token: str = Header(...)) -> None:
    """Bot -> API trust boundary for MVP (see docs/PLAN.md §7.1).

    A second dependency validating Telegram Mini App `initData` (HMAC over BOT_TOKEN) is the
    extension point for when the Mini App becomes a second client of this same API — not
    implemented in MVP, but this is where it plugs in without touching route handlers.
    """
    settings = get_settings()
    if not hmac.compare_digest(x_internal_token.encode(), settings.api_internal_token.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")
