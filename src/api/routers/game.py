import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import (
    CellOut,
    FeedbackAck,
    FeedbackRequest,
    FinishGameResponse,
    GameStateResponse,
    MoveOut,
    PaymentRequiredResponse,
    ReactionAck,
    ReactionRequest,
    ReminderAck,
    RespondRequest,
    RespondResponse,
    RollResponse,
    SetGenderAck,
    SetGenderRequest,
    SetIntentionRequest,
    SetIntentionResponse,
    SkipAck,
    SnoozeReminderRequest,
    StartGameRequest,
    StartGameResponse,
    UserStatusResponse,
)
from src.services.game_service import GameService, PaymentRequiredError
from src.services.reminder_service import ReminderService

router = APIRouter(dependencies=[Depends(require_internal_token)])

@router.post(
    "/game/start",
    response_model=StartGameResponse,
    responses={402: {"model": PaymentRequiredResponse}},
)
async def start_game(
    body: StartGameRequest, db: AsyncSession = Depends(get_db)
) -> StartGameResponse | JSONResponse:
    service = GameService(db)
    try:
        game, guide_message = await service.start_game(body.telegram_id, body.intention)
    except PaymentRequiredError as exc:
        return JSONResponse(
            status_code=402,
            content=PaymentRequiredResponse(
                price=exc.price, pay_url=exc.pay_url, crisis=exc.crisis
            ).model_dump(),
        )

    return StartGameResponse(
        game_id=game.id,
        status=game.status,
        position=game.position,
        in_play=game.in_play,
        turn=game.turn,
        guide_message=guide_message,
    )


@router.post("/game/{game_id}/intention", response_model=SetIntentionResponse)
async def set_intention(
    game_id: uuid.UUID, body: SetIntentionRequest, db: AsyncSession = Depends(get_db)
) -> SetIntentionResponse:
    service = GameService(db)
    game = await service.get_game(game_id)
    guide_message, safety_flag = await service.set_intention(game, body.intention)
    return SetIntentionResponse(
        game_id=game.id, status=game.status, guide_message=guide_message, safety_flag=safety_flag
    )


@router.post("/game/{game_id}/roll", response_model=RollResponse)
async def roll(
    game_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RollResponse:
    service = GameService(db)
    game = await service.get_game(game_id)
    result, guide_message, can_roll = await service.roll(game, idempotency_key)

    cell = None
    if result.final_position is not None:
        board_cell = service.board.get_cell(result.final_position)
        cell = CellOut(id=board_cell.id, name=board_cell.name, theme=board_cell.theme)

    return RollResponse(
        roll=result.roll,
        from_position=result.from_position,
        raw_position=result.raw_position,
        position=result.final_position,
        transition=result.transition,
        event=result.event,
        cell=cell,
        guide_message=guide_message,
        finished=result.finished,
        turn=result.turn,
        can_roll=can_roll,
        extra_roll=result.extra_roll,
        crisis_mode=await service.is_in_crisis(game.user_id),
    )


@router.post("/game/{game_id}/skip", response_model=SkipAck)
async def skip_response(game_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> SkipAck:
    service = GameService(db)
    game = await service.get_game(game_id)
    await service.skip_response(game)
    return SkipAck()


@router.post("/game/{game_id}/reaction", response_model=ReactionAck)
async def reaction(
    game_id: uuid.UUID, body: ReactionRequest, db: AsyncSession = Depends(get_db)
) -> ReactionAck:
    service = GameService(db)
    game = await service.get_game(game_id)
    await service.record_reaction(game, body.reaction)
    return ReactionAck()


@router.post("/game/{game_id}/respond", response_model=RespondResponse)
async def respond(
    game_id: uuid.UUID, body: RespondRequest, db: AsyncSession = Depends(get_db)
) -> RespondResponse:
    service = GameService(db)
    game = await service.get_game(game_id)
    guide_message, safety_flag = await service.respond(game, body.text)

    return RespondResponse(
        guide_message=guide_message,
        can_roll=game.status == "active",
        safety_flag=safety_flag,
        crisis_mode=await service.is_in_crisis(game.user_id),
    )


@router.get("/game/{game_id}", response_model=GameStateResponse)
async def get_game(game_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> GameStateResponse:
    service = GameService(db)
    game = await service.get_game(game_id)
    moves = await service.moves.list_for_game(game.id)

    cell = None
    if game.position is not None:
        board_cell = service.board.get_cell(game.position)
        cell = CellOut(id=board_cell.id, name=board_cell.name, theme=board_cell.theme)

    return GameStateResponse(
        game_id=game.id,
        status=game.status,
        intention=game.intention,
        position=game.position,
        in_play=game.in_play,
        turn=game.turn,
        is_paid=game.is_paid,
        finished=game.status == "finished",
        finish_reason=game.finish_reason,
        summary=game.summary,
        cell=cell,
        history=[
            MoveOut(
                turn=m.turn,
                roll=m.roll,
                from_pos=m.from_pos,
                raw_pos=m.raw_pos,
                to_pos=m.to_pos,
                transition=m.transition,
                event=m.event,
            )
            for m in moves
        ],
    )


@router.post("/game/{game_id}/finish", response_model=FinishGameResponse)
async def finish_game(game_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> FinishGameResponse:
    service = GameService(db)
    game = await service.get_game(game_id)
    summary, stats = await service.finish_game(game)
    return FinishGameResponse(summary=summary, stats=stats)


@router.post("/game/{game_id}/feedback", response_model=FeedbackAck)
async def feedback(
    game_id: uuid.UUID, body: FeedbackRequest, db: AsyncSession = Depends(get_db)
) -> FeedbackAck:
    service = GameService(db)
    game = await service.get_game(game_id)
    guide_message = await service.record_feedback(game, body.text)
    return FeedbackAck(guide_message=guide_message)


@router.get("/users/{telegram_id}/status", response_model=UserStatusResponse)
async def user_status(telegram_id: int, db: AsyncSession = Depends(get_db)) -> UserStatusResponse:
    service = GameService(db)
    status = await service.get_user_status(telegram_id)
    return UserStatusResponse(**status)


@router.post("/users/{telegram_id}/gender", response_model=SetGenderAck)
async def set_gender(
    telegram_id: int, body: SetGenderRequest, db: AsyncSession = Depends(get_db)
) -> SetGenderAck:
    service = GameService(db)
    await service.set_gender(telegram_id, body.gender)
    return SetGenderAck()


@router.post("/users/{telegram_id}/reminders/opt-out", response_model=ReminderAck)
async def opt_out_reminders(telegram_id: int, db: AsyncSession = Depends(get_db)) -> ReminderAck:
    service = ReminderService(db)
    await service.opt_out(telegram_id)
    return ReminderAck()


@router.post("/game/{game_id}/reminders/snooze", response_model=ReminderAck)
async def snooze_reminder(
    game_id: uuid.UUID, body: SnoozeReminderRequest, db: AsyncSession = Depends(get_db)
) -> ReminderAck:
    service = ReminderService(db)
    await service.snooze(game_id, body.hours)
    return ReminderAck()
