"""LLM limits — the cost guard (docs/ROADMAP.md, этап 2). Three of them, all from settings:

- replies per cell: past it, a reply on the same cell gets a static nudge to roll on, not a
  model reply — otherwise a free player could chat with the guide on one cell forever;
- calls per user per day and calls per day overall (rolling 24 hours, counted in llm_calls):
  past them every guide call falls back to its static text.

The safety classifier is never limited: it is counted in llm_calls, but a crisis check must not
switch off because someone ran out of budget.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.repositories import EventRepository, LlmCallRepository, MessageRepository
from src.services.alerts import fire_alert


class LlmLimits:
    def __init__(self, session: AsyncSession) -> None:
        self.calls = LlmCallRepository(session)
        self.messages = MessageRepository(session)
        self.events = EventRepository(session)
        self.settings = get_settings()

    async def allows_call(self, user_id: int, game_id: uuid.UUID | None) -> bool:
        since = datetime.now(UTC) - timedelta(days=1)
        total_cap = self.settings.llm_calls_per_day
        if total_cap is not None and await self.calls.count_since(since) >= total_cap:
            await self.events.log("llm_limit_hit", user_id, game_id, {"limit": "total"})
            fire_alert(
                "llm_total_limit",
                f"Достигнут общий лимит LLM_CALLS_PER_DAY={total_cap}: "
                "все игроки получают запасной текст вместо ведущего.",
            )
            return False
        user_cap = self.settings.llm_calls_per_user_per_day
        if user_cap is not None and await self.calls.count_since(since, user_id) >= user_cap:
            await self.events.log("llm_limit_hit", user_id, game_id, {"limit": "user"})
            return False
        return True

    async def allows_cell_reply(self, user_id: int, game_id: uuid.UUID) -> bool:
        """Call after the player's message is saved — it counts toward the limit."""
        cap = self.settings.llm_replies_per_cell
        if cap is None or await self.messages.count_player_since_last_move(game_id) <= cap:
            return True
        await self.events.log("llm_limit_hit", user_id, game_id, {"limit": "cell"})
        return False
