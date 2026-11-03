import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    has_free_game_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    paid_games_available: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reminders_opt_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 'male' | 'female' | 'skip' (explicitly declined) | None (never asked) — asked once per
    # user, not per game, so correct address forms carry across a player's whole history.
    gender: Mapped[str | None] = mapped_column(String(10))
    # Last time the player's text tripped the crisis screen (src/llm/safety.py). While recent, the
    # bot adds help contacts to replies and holds back reminders and the paywall — see
    # src/services/crisis.py.
    crisis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index(
            "ix_games_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_games_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="setup")
    intention: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(Integer)
    in_play: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    turn: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    board_version: Mapped[str | None] = mapped_column(String(50))
    finish_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped on every UPDATE (roll, intention set, summary set, ...) — used to detect abandoned
    # games by last *activity*, not by creation time (see GameRepository.abandon_stale).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)


class Move(Base):
    __tablename__ = "moves"
    __table_args__ = (
        Index("ix_moves_game_turn", "game_id", "turn"),
        Index(
            "uq_moves_game_idempotency_key",
            "game_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
    # Client-supplied key of the roll that created this move — a repeat of the same key replays
    # this move instead of rolling again (see GameService.roll). NULL for moves made without one.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    roll: Mapped[int] = mapped_column(Integer, nullable=False)
    from_pos: Mapped[int | None] = mapped_column(Integer)
    raw_pos: Mapped[int | None] = mapped_column(Integer)
    to_pos: Mapped[int | None] = mapped_column(Integer)
    transition: Mapped[str | None] = mapped_column(String(10))
    event: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_game", "game_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
    move_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("moves.id"))
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # 'guide' | 'player'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_payments_provider_external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="tribute")
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookInbox(Base):
    """Every signature-valid payment webhook, stored before it is interpreted. A payload we fail
    to act on stays here with its reason, so a real payment can be found and granted later
    instead of living only in a log line."""

    __tablename__ = "webhook_inbox"
    __table_args__ = (Index("ix_webhook_inbox_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_name: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 'received' | 'processed' | 'ignored' | 'duplicate' | 'failed'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reminder(Base):
    """Tracks the abandoned-game reminder cascade (docs/UX_ITERATION_1.md §2) — deliberately its
    own table, not columns on `games`: `games.updated_at` has `onupdate=func.now()` and is used
    both by this feature's own thresholds AND by `abandon_stale_games` (48h). Writing reminder
    bookkeeping into the same `games` row would bump `updated_at` on every reminder sent, making
    the game look freshly active and breaking both features' thresholds."""

    __tablename__ = "reminders"

    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"), primary_key=True)
    stage: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_name_created", "name", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    game_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    props: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
