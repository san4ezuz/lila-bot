import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from src.bot.api_client import GameApiClient
from src.bot.handlers import admin, game, start
from src.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set — see .env.example")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(game.router)

    api_client = GameApiClient()
    dp["api"] = api_client

    # Telegram's Bot API has no persistent menu button with custom text that fires an action
    # directly (that requires a Mini App / WebApp button, out of scope for now — see
    # docs/UX_ITERATION_1.md §5) — the closest real equivalent is a labeled entry in the
    # command list, opened via the standard "Menu" button next to the input field.
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🎲 Играть"),
            BotCommand(command="newgame", description="🆕 Новая игра"),
            BotCommand(command="help", description="ℹ️ Что это за игра?"),
        ]
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await api_client.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
