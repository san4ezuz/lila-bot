"""Abandoned-game reminder cascade — docs/UX_ITERATION_1.md §2.

Three stages, all measured from `games.updated_at` (last real player activity, not partie
creation): 24h / 3d / 7d. Each check is against the same fixed `updated_at` value, so the
cascade doesn't stack (a silent player hits all three thresholds off one anchor, not
24h-then-+3d-then-+7d — see docs/UX_ITERATION_1.md §2 for why this matters).

Reminder bookkeeping lives in its own `reminders` table, not on `games` — see
src/db/models.py::Reminder's docstring for why (touching `games.updated_at` here would corrupt
both this cascade and `abandon_stale_games`'s 48h threshold).

Sending happens outside the aiogram process (scripts/send_reminders.py, a cron entrypoint) via
the raw Bot API (src/services/notify.py) — same pattern as billing_service.py's payment
confirmation.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.board import Board, get_default_board
from src.db.models import Game, User
from src.db.repositories import (
    EventRepository,
    GameRepository,
    ReminderRepository,
    UserRepository,
)
from src.services import notify
from src.services.crisis import in_crisis_mode

_STAGE_THRESHOLDS: dict[int, timedelta] = {
    1: timedelta(hours=24),
    2: timedelta(days=3),
    3: timedelta(days=7),
}
_MAX_STAGE = 3

_STAGE_1_TEXT = "Твоя партия в «Лиле» всё ещё ждёт — если будет минутка, буду рад продолжить."
_STAGE_2_TEXT = "Ты остановился на клетке «{cell_name}». Не спешу — просто напоминаю, что путь никуда не делся."
_STAGE_3_TEXT = (
    "Последнее напоминание — дальше писать не буду. Партия сохранена, вернёшься — начнём "
    "с того же места, где остановился."
)


def _reminder_text(stage: int, cell_name: str | None) -> str:
    if stage == 1:
        return _STAGE_1_TEXT
    if stage == 2:
        return _STAGE_2_TEXT.format(cell_name=cell_name or "текущей клетке")
    return _STAGE_3_TEXT


def _reminder_keyboard(game_id: uuid.UUID) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "▶️ Продолжить партию", "callback_data": f"continue_game:{game_id}"}],
            [
                {"text": "Напомнить позже", "callback_data": f"snooze_reminder:{game_id}"},
                {"text": "Больше не напоминать", "callback_data": "opt_out_reminders"},
            ],
        ]
    }


class ReminderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.games = GameRepository(session)
        self.users = UserRepository(session)
        self.reminders = ReminderRepository(session)
        self.events = EventRepository(session)
        self.board: Board = get_default_board()

    async def send_due_reminders(self) -> int:
        """Returns how many reminders were sent. Meant to run periodically (see
        scripts/send_reminders.py) — cheap to call often, each game is only ever nudged once per
        stage regardless of how frequently this runs."""
        now = datetime.now(UTC)
        # Includes 'abandoned' (scripts/abandon_stale_games.py flips status at 48h — well before
        # the 3d/7d reminder stages) — GameService treats it as resumable, so the reminder
        # cascade must still see and nudge these games, not just strictly 'active' ones.
        result = await self.session.execute(select(Game).where(Game.status.in_(("active", "abandoned"))))
        games = result.scalars().all()

        sent = 0
        for game in games:
            if await self._maybe_remind(game, now):
                sent += 1
        return sent

    async def _maybe_remind(self, game: Game, now: datetime) -> bool:
        user = await self.session.get(User, game.user_id)
        if user is None or user.reminders_opt_out or in_crisis_mode(user, now):
            return False

        reminder = await self.reminders.get_or_create(game.id)
        if reminder.stage >= _MAX_STAGE:
            return False
        if reminder.snoozed_until is not None and now < reminder.snoozed_until:
            return False

        next_stage = reminder.stage + 1
        threshold = _STAGE_THRESHOLDS[next_stage]
        if now - game.updated_at < threshold:
            return False

        cell_name = self.board.get_cell(game.position).name if game.position else None
        text = _reminder_text(next_stage, cell_name)
        delivered = await notify.send_message(
            user.telegram_id, text, reply_markup=_reminder_keyboard(game.id)
        )
        if not delivered:
            # Don't advance the stage on a failed send — the next cron run should retry this
            # same stage, not silently skip it (see notify.send_message's docstring).
            return False
        await self.reminders.set_stage(reminder, next_stage)
        await self.events.log(
            "reminder_sent", user.id, game.id, {"stage": next_stage, "position": game.position}
        )
        return True

    async def opt_out(self, telegram_id: int) -> None:
        user = await self.users.get_or_create(telegram_id)
        await self.users.set_reminders_opt_out(user, True)

    async def snooze(self, game_id: uuid.UUID, hours: int = 72) -> None:
        reminder = await self.reminders.get_or_create(game_id)
        await self.reminders.set_snoozed_until(reminder, datetime.now(UTC) + timedelta(hours=hours))
