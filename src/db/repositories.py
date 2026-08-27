import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import GameState, MoveResult
from src.db.models import Event, Game, Message, Move, Payment, Reminder, User


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
        result = await self._session.execute(
            select(Game).where(Game.user_id == user_id, Game.status.in_(("setup", "active", "abandoned")))
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

    async def create(self, game_id: uuid.UUID, result: MoveResult) -> Move:
        move = Move(
            game_id=game_id,
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
