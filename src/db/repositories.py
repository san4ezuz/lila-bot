import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import GameState, MoveResult
from src.db.models import (
    Event,
    Game,
    LlmCall,
    Message,
    Move,
    Payment,
    Reminder,
    User,
    WebhookInbox,
)
from src.llm.client import LlmResult


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self._session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_or_create(
        self, telegram_id: int, username: str | None = None, first_name: str | None = None
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None:
            return user
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        self._session.add(user)
        await self._session.flush()
        return user

    async def mark_free_game_used(self, user: User) -> None:
        user.has_free_game_used = True
        await self._session.flush()

    async def grant_paid_game(self, user: User, count: int = 1) -> None:
        user.paid_games_available += count
        await self._session.flush()

    async def revoke_paid_game(self, user: User) -> bool:
        """Takes back one unused paid game. Returns False if none is left to take back — the
        game may already have been started, and a refund must not push the balance negative."""
        if user.paid_games_available <= 0:
            return False
        user.paid_games_available -= 1
        await self._session.flush()
        return True

    async def consume_paid_game(self, user: User) -> None:
        if user.paid_games_available <= 0:
            raise ValueError("user has no paid games available")
        user.paid_games_available -= 1
        await self._session.flush()

    async def set_reminders_opt_out(self, user: User, opted_out: bool = True) -> None:
        user.reminders_opt_out = opted_out
        await self._session.flush()

    async def set_gender(self, user: User, gender: str) -> None:
        user.gender = gender
        await self._session.flush()

    async def mark_crisis(self, user: User) -> None:
        user.crisis_at = datetime.now(UTC)
        await self._session.flush()

    async def set_source_once(self, user: User, source: str) -> None:
        """First touch wins: a later `/start <other>` doesn't reattribute the player."""
        if user.source is None:
            user.source = source
            await self._session.flush()


class GameRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, game_id: uuid.UUID) -> Game | None:
        return await self._session.get(Game, game_id)

    async def get_active_for_user(self, user_id: int) -> Game | None:
        """A game the user can still resume: intention not set yet ('setup'), in progress
        ('active'), or auto-marked stale by scripts/abandon_stale_games.py ('abandoned' — see
        GameService._RESUMABLE_STATUSES; a background cron flipping the status must not force
        the player to start a fresh paid/free game just because they came back after 48h).
        Excludes 'finished' — that's the only truly terminal status."""
        # The unique index only covers 'active', so two racing starts can leave two 'setup' rows —
        # pick the newest rather than raising MultipleResultsFound on every later call.
        result = await self._session.execute(
            select(Game)
            .where(Game.user_id == user_id, Game.status.in_(("setup", "active", "abandoned")))
            .order_by(Game.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, user_id: int, board_version: str, is_paid: bool) -> Game:
        game = Game(
            user_id=user_id,
            status="setup",
            board_version=board_version,
            is_paid=is_paid,
        )
        self._session.add(game)
        await self._session.flush()
        return game

    async def set_intention(self, game: Game, intention: str) -> None:
        game.intention = intention
        game.status = "active"
        await self._session.flush()

    async def apply_state(self, game: Game, state: GameState) -> None:
        game.position = state.position
        game.in_play = state.in_play
        game.turn = state.turn
        if state.finished:
            game.status = "finished"
            game.finish_reason = state.finish_reason
            game.finished_at = datetime.now(UTC)
        await self._session.flush()

    async def set_summary(self, game: Game, summary: str) -> None:
        game.summary = summary
        await self._session.flush()

    async def find_stale_active(self, cutoff: datetime) -> list[Game]:
        """Games with no activity since `cutoff` — filtered by `updated_at`, not `created_at`,
        so a game with recent moves isn't wrongly flagged just because it started days ago."""
        result = await self._session.execute(
            select(Game).where(Game.status == "active", Game.updated_at < cutoff)
        )
        return list(result.scalars().all())

    async def mark_abandoned(self, game: Game) -> None:
        """Marking a game abandoned is bookkeeping, not player activity — it must NOT reset
        `updated_at` (used both as the abandon-cutoff itself and as the reminder cascade's
        threshold anchor, see src/services/reminder_service.py). A plain `flush()` here would
        trigger the column's `onupdate=func.now()` and erase the real last-activity time right
        as the game becomes stale, silently pushing every later reminder threshold out by
        however long the game had already been sitting idle. Freeze the value across the flush
        with an explicit follow-up UPDATE — `onupdate` only fires for columns not present in the
        given SET values, so re-stating the frozen value suppresses it."""
        frozen_updated_at = game.updated_at
        game.status = "abandoned"
        await self._session.flush()
        await self._session.execute(
            update(Game).where(Game.id == game.id).values(updated_at=frozen_updated_at)
        )
        await self._session.refresh(game)


class MoveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_idempotency_key(self, game_id: uuid.UUID, key: str) -> Move | None:
        result = await self._session.execute(
            select(Move).where(Move.game_id == game_id, Move.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def create(
        self, game_id: uuid.UUID, result: MoveResult, idempotency_key: str | None = None
    ) -> Move:
        move = Move(
            game_id=game_id,
            idempotency_key=idempotency_key,
            turn=result.turn,
            roll=result.roll,
            from_pos=result.from_position,
            raw_pos=result.raw_position,
            to_pos=result.final_position,
            transition=result.transition,
            event=result.event,
        )
        self._session.add(move)
        await self._session.flush()
        return move

    async def list_for_game(self, game_id: uuid.UUID) -> list[Move]:
        result = await self._session.execute(
            select(Move).where(Move.game_id == game_id).order_by(Move.turn)
        )
        return list(result.scalars().all())


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        game_id: uuid.UUID,
        role: str,
        content: str,
        move_id: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> Message:
        message = Message(
            game_id=game_id,
            move_id=move_id,
            role=role,
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def get_for_move(self, move_id: int) -> Message | None:
        result = await self._session.execute(
            select(Message)
            .where(Message.move_id == move_id, Message.role == "guide")
            .order_by(Message.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_since_last_move(self, game_id: uuid.UUID) -> list[Message]:
        """The conversation on the current cell: the guide's message about the last move and
        everything after it, oldest first."""
        last_move_message = (
            select(func.coalesce(func.max(Message.id), 0))
            .where(Message.game_id == game_id, Message.move_id.is_not(None))
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(Message)
            .where(Message.game_id == game_id, Message.id >= last_move_message)
            .order_by(Message.id)
        )
        return list(result.scalars().all())

    async def recent_player_texts(self, game_id: uuid.UUID, limit: int) -> list[str]:
        """The player's last `limit` messages, oldest first."""
        result = await self._session.execute(
            select(Message.content)
            .where(Message.game_id == game_id, Message.role == "player")
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def count_player_since_last_move(self, game_id: uuid.UUID) -> int:
        """Player messages since the guide's last message about a move — i.e. replies on the
        current cell. Feeds the per-cell LLM limit."""
        last_move_message = (
            select(func.coalesce(func.max(Message.id), 0))
            .where(Message.game_id == game_id, Message.move_id.is_not(None))
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(func.count()).where(
                Message.game_id == game_id,
                Message.role == "player",
                Message.id > last_move_message,
            )
        )
        return int(result.scalar_one())

    async def list_for_game(self, game_id: uuid.UUID) -> list[Message]:
        result = await self._session.execute(
            select(Message).where(Message.game_id == game_id).order_by(Message.id)
        )
        return list(result.scalars().all())


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_external_id(self, provider: str, external_id: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.provider == provider, Payment.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        provider: str,
        external_id: str,
        amount: float,
        currency: str,
        raw_payload: dict,
    ) -> Payment:
        payment = Payment(
            user_id=user_id,
            provider=provider,
            external_id=external_id,
            amount=amount,
            currency=currency,
            status="paid",
            raw_payload=raw_payload,
        )
        self._session.add(payment)
        await self._session.flush()
        return payment


class WebhookInboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, provider: str, event_name: str | None, payload: dict) -> WebhookInbox:
        entry = WebhookInbox(
            provider=provider, event_name=event_name, payload=payload, status="received"
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def set_outcome(self, entry: WebhookInbox, status: str, note: str | None = None) -> None:
        entry.status = status
        entry.note = note
        await self._session.flush()


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, game_id: uuid.UUID) -> Reminder:
        reminder = await self._session.get(Reminder, game_id)
        if reminder is not None:
            return reminder
        reminder = Reminder(game_id=game_id)
        self._session.add(reminder)
        await self._session.flush()
        return reminder

    async def set_stage(self, reminder: Reminder, stage: int) -> None:
        reminder.stage = stage
        await self._session.flush()

    async def set_snoozed_until(self, reminder: Reminder, until: datetime) -> None:
        reminder.snoozed_until = until
        await self._session.flush()


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        name: str,
        user_id: int | None = None,
        game_id: uuid.UUID | None = None,
        props: dict | None = None,
    ) -> Event:
        event = Event(name=name, user_id=user_id, game_id=game_id, props=props or {})
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_game(self, game_id: uuid.UUID, name: str) -> list[Event]:
        result = await self._session.execute(
            select(Event).where(Event.game_id == game_id, Event.name == name).order_by(Event.id)
        )
        return list(result.scalars().all())


class LlmCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, result: LlmResult, user_id: int | None, game_id: uuid.UUID | None
    ) -> LlmCall | None:
        """A skipped call (an LLM limit was hit) cost nothing and isn't recorded."""
        if result.skipped:
            return None
        call = LlmCall(
            user_id=user_id,
            game_id=game_id,
            kind=result.kind,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            stop_reason=result.stop_reason,
            is_fallback=result.fallback,
            error=result.error,
            duration_ms=result.duration_ms,
        )
        self._session.add(call)
        await self._session.flush()
        return call

    async def count_since(self, since: datetime, user_id: int | None = None) -> int:
        query = select(func.count()).select_from(LlmCall).where(LlmCall.created_at >= since)
        if user_id is not None:
            query = query.where(LlmCall.user_id == user_id)
        result = await self._session.execute(query)
        return int(result.scalar_one())
