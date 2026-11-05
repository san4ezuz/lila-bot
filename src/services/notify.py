"""Raw Telegram Bot API sender, usable from any server-side process (API, cron scripts) without
pulling in a full aiogram Dispatcher — see src/services/billing_service.py's original docstring
for why this lives outside the bot process. Shared by billing_service.py (payment confirmation)
and reminder_service.py (abandoned-game nudges)."""

import asyncio
import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()


async def send_message(telegram_id: int, text: str, reply_markup: dict | None = None) -> bool:
    """Best-effort — failures are logged, never raised (a notification failing must not fail
    whatever triggered it: a webhook response, a cron run). Returns whether the send actually
    succeeded — callers that track "has this been sent" state (reminder_service.py's stage
    cascade) MUST check this before persisting that state, or a transient failure silently burns
    a reminder stage the player never received, with no retry (found via a live smoke test: a
    real 400 from Telegram was swallowed and the stage advanced anyway)."""
    settings = get_settings()
    if not settings.bot_token:
        logger.warning("BOT_TOKEN not set — skipping Telegram notification to %s", telegram_id)
        return False
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    payload: dict = {"chat_id": telegram_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("failed to notify user %s: %s", telegram_id, exc)
        return False
    return True


def send_in_background(messages: list[tuple[int, str]]) -> None:
    """For a side notification (a referral reward, an opened gift) that must not delay the
    player's own response. Call after commit."""
    for telegram_id, text in messages:
        task = asyncio.create_task(send_message(telegram_id, text))
        _background.add(task)
        task.add_done_callback(_background.discard)
