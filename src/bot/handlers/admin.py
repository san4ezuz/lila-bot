"""Manual /grant fallback for a Tribute payment whose webhook never arrived (docs/PLAN.md
§11.2). Restricted to ADMIN_TELEGRAM_IDS — the bot is the sole enforcer of *who* may call this;
the API only checks the bot<->API trust boundary (X-Internal-Token), see src/api/routers/payments.py.
"""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.api_client import ApiError, GameApiClient
from src.config import get_settings

router = Router(name="admin")


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject, api: GameApiClient) -> None:
    settings = get_settings()
    if message.from_user.id not in settings.admin_telegram_ids:
        return  # silently ignore — don't reveal the command exists to non-admins

    if not command.args:
        await message.answer("Использование: /grant <telegram_id> [count]")
        return

    parts = command.args.split()
    try:
        telegram_id = int(parts[0])
        count = int(parts[1]) if len(parts) > 1 else 1
    except ValueError:
        await message.answer("Использование: /grant <telegram_id> [count]")
        return

    try:
        result = await api.admin_grant(telegram_id, count)
    except ApiError as exc:
        await message.answer(f"Ошибка: {exc.message}")
        return

    await message.answer(
        f"Готово. У пользователя {telegram_id} теперь {result.paid_games_available} "
        "доступных платных партий."
    )
