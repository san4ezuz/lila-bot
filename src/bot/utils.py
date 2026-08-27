import asyncio
import contextlib
import html
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import CallbackQuery, Message


def callback_message(callback: CallbackQuery) -> Message | None:
    """`callback.message` is typed as `Message | InaccessibleMessage | None` — a callback can
    arrive for a message Telegram no longer lets us edit/reply to (e.g. it was deleted between
    the tap and our handler running). Narrow it once here instead of at every call site."""
    return callback.message if isinstance(callback.message, Message) else None


def esc(text: str) -> str:
    """Escape text interpolated into an HTML-formatted message (bot/main.py sets
    parse_mode=HTML globally). MUST wrap anything not authored by us as a trusted literal —
    cell names, and especially LLM-generated guide_message/summary text, which is free-form and
    can legitimately contain '<', '>', or '&' — before concatenating with our own <b>/<blockquote>
    tags. An unescaped '<' from Claude would otherwise break Telegram's HTML parser and the
    message would fail to send."""
    return html.escape(text)


@asynccontextmanager
async def typing_indicator(bot: Bot | None, chat_id: int) -> AsyncIterator[None]:
    """Shows Telegram's "печатает…" indicator for the duration of the `async with` block —
    LLM calls (docs/PLAN.md §8) routinely take 3-5s, and with nothing else in the UI signalling
    that, players tap the button again before the first response lands (found via user report:
    repeated taps queued up multiple real rolls that then arrived in a burst). A single
    send_chat_action call only lasts ~5s server-side, so this refreshes it in a loop rather than
    firing once and letting it silently expire mid-wait.

    `bot` is `Bot | None` because `Message.bot`/`CallbackQuery.bot` carry that type in aiogram
    (a manually-constructed update could lack one) — in practice it's always set for anything
    arriving through the Dispatcher; if it's ever missing this degrades to a no-op rather than
    forcing every call site to guard against a case that doesn't happen in real traffic."""
    if bot is None:
        yield
        return

    async def _loop() -> None:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
