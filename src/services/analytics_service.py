"""Funnel events the bot reports (the API logs the game's own events itself), and the admin
funnel behind /stats.

The bot sends only UI steps the API can't see: /start with its deep-link source, the "Что это за
игра?" screen, the gender step, taps on a reminder, /invite and inline-mode shares. Names are a closed list — see
src/api/schemas.py::BotEventName.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import Numeric, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import BotEventName, StatsResponse
from src.db.models import Event, LlmCall, Move, User
from src.db.repositories import EventRepository, UserRepository

# Telegram's own rule for the /start payload: A-Z, a-z, 0-9, _ and -, up to 64 characters.
_SOURCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def clean_source(raw: object) -> str | None:
    if not isinstance(raw, str) or not _SOURCE_PATTERN.fullmatch(raw):
        return None
    return raw


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.events = EventRepository(session)

    async def track(self, telegram_id: int, name: BotEventName, props: dict) -> None:
        """`bot_start` creates the user: a player who never taps "Начать" must still show up in
        the funnel, with the source they came from."""
        user = await self.users.get_or_create(telegram_id)
        if name == "bot_start":
            source = clean_source(props.get("source"))
            props = {"source": source}
            if source is not None:
                await self.users.set_source_once(user, source)
        await self.events.log(name, user.id, None, props)

    async def funnel(self, days: int) -> StatsResponse:
        since = datetime.now(UTC) - timedelta(days=days)

        async def users_with(name: str) -> int:
            result = await self.session.execute(
                select(func.count(distinct(Event.user_id))).where(
                    Event.name == name, Event.created_at >= since
                )
            )
            return int(result.scalar_one())

        async def events(name: str, **props: str) -> int:
            query = select(func.count()).where(Event.name == name, Event.created_at >= since)
            for key, value in props.items():
                query = query.where(Event.props[key].astext == value)
            result = await self.session.execute(query)
            return int(result.scalar_one())

        new_users = await self.session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= since)
        )
        sources = await self.session.execute(
            select(User.source, func.count())
            .where(User.created_at >= since, User.source.is_not(None))
            .group_by(User.source)
            .order_by(func.count().desc())
            .limit(5)
        )
        entered = await self.session.scalar(
            select(func.count(distinct(Move.game_id))).where(
                Move.event == "entered", Move.created_at >= since
            )
        )
        # Real payments only: /grant logs `admin_grant`, and before that was logged as
        # payment_succeeded with source=manual_grant.
        real_payment = (
            (Event.name == "payment_succeeded")
            & (Event.created_at >= since)
            & (func.coalesce(Event.props["source"].astext, "") != "manual_grant")
        )
        paid = await self.session.execute(
            select(func.count(), func.coalesce(func.sum(Event.props["amount"].astext.cast(Numeric)), 0))
            .where(real_payment)
        )
        payments_count, revenue = paid.one()
        llm = await self.session.execute(
            select(
                func.count(),
                func.count().filter(LlmCall.is_fallback),
                func.coalesce(func.sum(LlmCall.input_tokens), 0),
                func.coalesce(func.sum(LlmCall.output_tokens), 0),
                func.coalesce(func.sum(LlmCall.cache_read_tokens), 0),
            ).where(LlmCall.created_at >= since)
        )
        llm_calls, llm_fallbacks, tokens_in, tokens_out, tokens_cache_read = llm.one()

        return StatsResponse(
            days=days,
            new_users=int(new_users or 0),
            bot_starts=await users_with("bot_start"),
            top_sources=[(str(source), int(count)) for source, count in sources.all()],
            about_opened=await users_with("about_opened"),
            games_started_free=await events("game_started", is_paid="false"),
            games_started_paid=await events("game_started", is_paid="true"),
            intentions_set=await events("intention_set"),
            entered=int(entered or 0),
            finished_enlightenment=await events("game_finished", finish_reason="enlightenment"),
            finished_other=await events("game_finished")
            - await events("game_finished", finish_reason="enlightenment"),
            abandoned=await events("game_abandoned"),
            paywall_shown=await events("paywall_shown"),
            payments=int(payments_count),
            revenue=int(revenue),
            refunds=await events("payment_refunded"),
            admin_grants=await events("admin_grant")
            + await events("payment_succeeded", source="manual_grant"),
            safety_triggered=await events("safety_triggered"),
            reminders_sent=await events("reminder_sent"),
            reminders_clicked=await events("reminder_clicked"),
            referrals_joined=await events("referral_joined"),
            referrals_rewarded=await events("referral_rewarded"),
            gifts_created=await events("gift_created"),
            gifts_redeemed=await events("gift_redeemed"),
            promo_redeemed=await events("promo_redeemed"),
            cards_created=await events("card_created"),
            inline_shared=await events("inline_shared"),
            llm_calls=int(llm_calls),
            llm_fallbacks=int(llm_fallbacks),
            llm_limit_hits=await events("llm_limit_hit"),
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
            tokens_cache_read=int(tokens_cache_read),
        )

