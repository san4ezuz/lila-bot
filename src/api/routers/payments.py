from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import AdminGrantRequest, AdminGrantResponse, WebhookAck
from src.config import get_settings
from src.services.billing_service import (
    BillingService,
    DuplicateWebhookError,
    InvalidWebhookSignatureError,
    verify_signature,
)

webhook_router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_internal_token)])


@webhook_router.post("/payments/tribute/webhook", response_model=WebhookAck)
async def tribute_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_tribute_signature: str | None = Header(default=None),
):
    settings = get_settings()
    raw_body = await request.body()

    if not verify_signature(raw_body, x_tribute_signature, settings.tribute_webhook_secret):
        raise InvalidWebhookSignatureError

    payload = await request.json()
    telegram_id = payload.get("telegram_id") or payload.get("metadata", {}).get("telegram_id")
    if telegram_id is None:
        raise HTTPException(status_code=400, detail="payload missing telegram_id")

    service = BillingService(db)
    try:
        await service.process_tribute_payment(
            telegram_id=int(telegram_id),
            external_id=str(payload.get("external_id") or payload.get("id")),
            amount=float(payload.get("amount", 0)),
            currency=str(payload.get("currency", "RUB")),
            raw_payload=payload,
        )
    except DuplicateWebhookError:
        pass  # already recorded — Tribute may retry delivery; ack normally, don't double-grant

    return WebhookAck()


@admin_router.post("/admin/grant", response_model=AdminGrantResponse)
async def admin_grant(body: AdminGrantRequest, db: AsyncSession = Depends(get_db)):
    """Manual fallback for when a Tribute webhook never arrives despite a real payment — see
    docs/PLAN.md §11.2. Authorization for *who* may call this is the bot's job (it only exposes
    the /grant command to docs/PLAN.md's admin telegram IDs); this endpoint only enforces the
    same bot<->API trust boundary as every other route (X-Internal-Token)."""
    service = BillingService(db)
    user = await service.grant_manual(body.telegram_id, body.count)
    return AdminGrantResponse(telegram_id=body.telegram_id, paid_games_available=user.paid_games_available)
