"""Admin commands: /grant — manual fallback for a Tribute payment whose webhook never arrived
(docs/PLAN.md §11.2), and /stats — the funnel. Restricted to ADMIN_TELEGRAM_IDS — the bot is the
sole enforcer of *who* may call these; the API only checks the bot<->API trust boundary
(X-Internal-Token), see src/api/routers/payments.py.
"""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.api.schemas import StatsResponse
from src.bot.api_client import ApiError, GameApiClient
from src.config import get_settings

router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return (
        message.from_user is not None
        and message.from_user.id in get_settings().admin_telegram_ids
    )


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject, api: GameApiClient) -> None:
    if not _is_admin(message):
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


@router.message(Command("stats"))
async def cmd_stats(message: Message, api: GameApiClient) -> None:
    if not _is_admin(message):
        return
    try:
        day, week = await api.admin_stats(1), await api.admin_stats(7)
    except ApiError as exc:
        await message.answer(f"Ошибка: {exc.message}")
        return
    await message.answer(stats_text(day, week))


def stats_text(day: StatsResponse, week: StatsResponse) -> str:
    def row(label: str, field: str) -> str:
        return f"{label}: {getattr(day, field)} / {getattr(week, field)}"

    sources = ", ".join(f"{name} {count}" for name, count in week.top_sources) or "нет"
    fallback_share = f"{week.llm_fallbacks / week.llm_calls:.0%}" if week.llm_calls else "—"
    return "\n".join(
        [
            "<b>Воронка</b> (сутки / 7 дней)",
            row("Новые пользователи", "new_users"),
            row("Нажали /start", "bot_starts"),
            row("Открыли «Что это за игра?»", "about_opened"),
            row("Партии бесплатные", "games_started_free"),
            row("Партии платные", "games_started_paid"),
            row("Указали запрос", "intentions_set"),
            row("Вошли в игру", "entered"),
            row("Дошли до финиша", "finished_enlightenment"),
            row("Закончили иначе", "finished_other"),
            row("Брошены (48 ч)", "abandoned"),
            row("Показан пейвол", "paywall_shown"),
            row("Оплаты", "payments"),
            row("Выручка, ₽", "revenue"),
            row("Возвраты", "refunds"),
            row("Выдано через /grant", "admin_grants"),
            row("Срабатывания safety", "safety_triggered"),
            row("Напоминаний отправлено", "reminders_sent"),
            row("Нажатий на напоминания", "reminders_clicked"),
            "",
            f"Источники за 7 дней: {sources}",
            "",
            "<b>LLM</b> (сутки / 7 дней)",
            row("Вызовов", "llm_calls"),
            row("Фолбэков", "llm_fallbacks"),
            row("Срабатываний лимитов", "llm_limit_hits"),
            row("Токенов на входе", "tokens_in"),
            row("Токенов на выходе", "tokens_out"),
            row("Из кэша", "tokens_cache_read"),
            f"Доля фолбэков за 7 дней: {fallback_share}",
        ]
    )
