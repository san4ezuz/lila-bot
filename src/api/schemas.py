import uuid

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    message: str


class StartGameRequest(BaseModel):
    telegram_id: int
    intention: str | None = None


class StartGameResponse(BaseModel):
    game_id: uuid.UUID
    status: str
    position: int | None
    in_play: bool
    turn: int
    guide_message: str


class PaymentRequiredResponse(BaseModel):
    error: str = "payment_required"
    price: int
    pay_url: str
    # The player is in crisis mode: show help contacts instead of the pay button.
    crisis: bool = False


class SetIntentionRequest(BaseModel):
    intention: str


class SetIntentionResponse(BaseModel):
    game_id: uuid.UUID
    status: str
    guide_message: str
    safety_flag: str | None = None


class CellOut(BaseModel):
    id: int
    name: str
    theme: str


class RollResponse(BaseModel):
    roll: int
    from_position: int | None
    raw_position: int | None
    position: int | None
    transition: str | None
    event: str
    cell: CellOut | None
    guide_message: str
    finished: bool
    turn: int
    can_roll: bool
    extra_roll: bool
    crisis_mode: bool = False


class RespondRequest(BaseModel):
    text: str


class RespondResponse(BaseModel):
    guide_message: str
    can_roll: bool
    # Set when this message tripped the crisis screen; `crisis_mode` — an earlier one did, recently.
    safety_flag: str | None = None
    crisis_mode: bool = False


class MoveOut(BaseModel):
    turn: int
    roll: int
    from_pos: int | None
    raw_pos: int | None
    to_pos: int | None
    transition: str | None
    event: str


class GameStateResponse(BaseModel):
    game_id: uuid.UUID
    status: str
    intention: str | None
    position: int | None
    in_play: bool
    turn: int
    is_paid: bool
    finished: bool
    finish_reason: str | None
    summary: str | None
    cell: CellOut | None
    history: list[MoveOut]


class FinishGameResponse(BaseModel):
    summary: str
    stats: dict


class UserStatusResponse(BaseModel):
    has_free_game_used: bool
    paid_games_available: int
    active_game_id: uuid.UUID | None
    active_game_status: str | None = None
    gender: str | None = None


class SetGenderRequest(BaseModel):
    gender: str


class SetGenderAck(BaseModel):
    ok: bool = True


class WebhookAck(BaseModel):
    ok: bool = True


class SkipAck(BaseModel):
    ok: bool = True


class FeedbackRequest(BaseModel):
    text: str


class FeedbackAck(BaseModel):
    ok: bool = True
    # Supportive text to show instead of the thanks, when the answer tripped the crisis screen.
    guide_message: str | None = None


class AdminGrantRequest(BaseModel):
    telegram_id: int
    count: int = 1


class AdminGrantResponse(BaseModel):
    telegram_id: int
    paid_games_available: int


class ReminderAck(BaseModel):
    ok: bool = True


class SnoozeReminderRequest(BaseModel):
    hours: int = 72
