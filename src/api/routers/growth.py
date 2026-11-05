"""Invites, gifts, promo codes and share cards (docs/ROADMAP.md, этап 4)."""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import (
    CardDataResponse,
    GiftCreatedResponse,
    InviteResponse,
    PromoCreateRequest,
    PromoOut,
    PromoRedeemRequest,
    SaveCardRequest,
    SkipAck,
    StartLinkRequest,
    StartLinkResponse,
    UserCardsResponse,
)
from src.services import notify
from src.services.card_service import CardService
from src.services.credit_service import CreditService, LinkResult, NoCreditsError
from src.services.game_service import GameService

router = APIRouter(dependencies=[Depends(require_internal_token)])


async def _link_response(db: AsyncSession, result: LinkResult) -> StartLinkResponse:
    await db.commit()
    notify.send_in_background(result.notifications)
    return StartLinkResponse(kind=result.kind, outcome=result.outcome, credits=result.credits)


@router.post("/users/{telegram_id}/start-link", response_model=StartLinkResponse)
async def start_link(
    telegram_id: int, body: StartLinkRequest, db: AsyncSession = Depends(get_db)
) -> StartLinkResponse:
    result = await CreditService(db).apply_start_link(telegram_id, body.payload)
    return await _link_response(db, result)


@router.post("/users/{telegram_id}/promo", response_model=StartLinkResponse)
async def redeem_promo(
    telegram_id: int, body: PromoRedeemRequest, db: AsyncSession = Depends(get_db)
) -> StartLinkResponse:
    service = CreditService(db)
    user = await service.users.get_or_create(telegram_id)
    return await _link_response(db, await service.redeem_promo(user, body.code))


@router.post("/users/{telegram_id}/invite", response_model=InviteResponse)
async def invite(telegram_id: int, db: AsyncSession = Depends(get_db)) -> InviteResponse:
    service = CreditService(db)
    user = await service.users.get_or_create(telegram_id)
    return InviteResponse(ref_code=await service.ref_code(user), balance=await service.balance(user))


@router.post(
    "/users/{telegram_id}/gifts",
    response_model=GiftCreatedResponse,
    responses={409: {"description": "no credits to give"}},
)
async def create_gift(
    telegram_id: int, db: AsyncSession = Depends(get_db)
) -> GiftCreatedResponse | JSONResponse:
    service = CreditService(db)
    try:
        code = await service.create_gift(telegram_id)
    except NoCreditsError:
        return JSONResponse(
            status_code=409, content={"error": "no_credits", "message": "no credits to give"}
        )
    user = await service.users.get_or_create(telegram_id)
    return GiftCreatedResponse(code=code, balance=await service.balance(user))


@router.get("/users/{telegram_id}/cards", response_model=UserCardsResponse)
async def user_cards(telegram_id: int, db: AsyncSession = Depends(get_db)) -> UserCardsResponse:
    return await CardService(db).user_cards(telegram_id)


@router.get("/game/{game_id}/card", response_model=CardDataResponse)
async def card_data(game_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CardDataResponse:
    game = await GameService(db).get_game(game_id)
    return await CardService(db).card_data(game)


@router.post("/game/{game_id}/card", response_model=SkipAck)
async def save_card(
    game_id: uuid.UUID, body: SaveCardRequest, db: AsyncSession = Depends(get_db)
) -> SkipAck:
    game = await GameService(db).get_game(game_id)
    await CardService(db).save_card(game, body.format, body.with_intention, body.file_id)
    return SkipAck()


@router.post("/admin/promo", response_model=PromoOut, responses={422: {"description": "bad code"}})
async def create_promo(
    body: PromoCreateRequest, db: AsyncSession = Depends(get_db)
) -> PromoOut | JSONResponse:
    """Who may call this is the bot's job, as with /admin/grant."""
    service = CreditService(db)
    try:
        code = await service.create_promo(body.code, body.credits, body.max_uses, body.note)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": "bad_promo", "message": str(exc)})
    uses = await service.credits.count_grants("promo", code)
    return PromoOut(
        code=code,
        credits=body.credits,
        max_uses=body.max_uses,
        uses=uses,
        note=body.note,
        is_active=True,
    )


@router.get("/admin/promo", response_model=list[PromoOut])
async def list_promos(db: AsyncSession = Depends(get_db)) -> list[PromoOut]:
    rows = await CreditService(db).promos.list_with_uses()
    return [
        PromoOut(
            code=promo.code,
            credits=promo.credits,
            max_uses=promo.max_uses,
            uses=uses,
            note=promo.note,
            is_active=promo.is_active,
        )
        for promo, uses in rows
    ]
