from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import AdminGrantRequest, AdminGrantResponse, WebhookAck
from src.config import get_settings
from src.services.billing_service import (
    BillingService,
    DuplicateWebhookError,
    InvalidWebhookSignatureError,
    parse_webhook_payload,
    verify_signature,
)

webhook_router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_internal_token)])


@webhook_router.post("/payments/tribute/webhook", response_model=WebhookAck)
async def tribute_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    trbt_signature: str | None = Header(default=None, alias="trbt-signature"),
):
    """See src.services.billing_service module docstring for what's confirmed vs. inferred
    about Tribute's actual webhook contract."""
    settings = get_settings()
    raw_body = await request.body()

    if not verify_signature(raw_body, trbt_signature, settings.tribute_api_key):
        raise InvalidWebhookSignatureError

    payload = await request.json()
    parsed = parse_webhook_payload(payload)
    if parsed is None:
        # Not a success event, or a shape we don't recognize — ack anyway so Tribute doesn't
        # retry-storm us; parse_webhook_payload() already logged why.
        return WebhookAck()

    service = BillingService(db)
    try:
        await service.process_tribute_payment(
            telegram_id=parsed.telegram_id,
            external_id=parsed.external_id,
            amount=parsed.amount,
            currency=parsed.currency,
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
