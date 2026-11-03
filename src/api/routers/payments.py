import logging

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import AdminGrantRequest, AdminGrantResponse, WebhookAck
from src.config import get_settings
from src.db.repositories import WebhookInboxRepository
from src.services.alerts import fire_alert
from src.services.billing_service import (
    BillingService,
    DuplicateWebhookError,
    InvalidWebhookSignatureError,
    UnprocessableWebhookError,
    parse_webhook_payload,
    send_payment_confirmation,
    verify_signature,
)

logger = logging.getLogger(__name__)

webhook_router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_internal_token)])


@webhook_router.post("/payments/tribute/webhook", response_model=WebhookAck)
async def tribute_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    trbt_signature: str | None = Header(default=None, alias="trbt-signature"),
) -> WebhookAck:
    """See src.services.billing_service module docstring for what's confirmed vs. inferred
    about Tribute's actual webhook contract."""
    settings = get_settings()
    raw_body = await request.body()

    if not verify_signature(raw_body, trbt_signature, settings.tribute_api_key):
        raise InvalidWebhookSignatureError

    payload = await request.json()
    # Committed on its own, before anything can fail below — this row is what keeps a payment
    # recoverable. A crash past this point returns 500 and Tribute redelivers.
    inbox = WebhookInboxRepository(db)
    entry = await inbox.record("tribute", payload.get("name"), payload)
    await db.commit()

    service = BillingService(db)
    confirm_to: int | None = None
    try:
        parsed = parse_webhook_payload(payload, settings.tribute_product_id)
        if parsed is None:
            await inbox.set_outcome(entry, "ignored")
        elif parsed.kind == "refund":
            await service.process_tribute_refund(parsed.external_id)
            await inbox.set_outcome(entry, "processed")
        else:
            assert parsed.telegram_id is not None  # parse_webhook_payload guarantees it for payments
            await service.process_tribute_payment(
                telegram_id=parsed.telegram_id,
                external_id=parsed.external_id,
                amount=parsed.amount,
                currency=parsed.currency,
                raw_payload=payload,
            )
            await inbox.set_outcome(entry, "processed")
            confirm_to = parsed.telegram_id
    except DuplicateWebhookError:
        await inbox.set_outcome(entry, "duplicate")
    except UnprocessableWebhookError as exc:
        logger.error("tribute webhook %s not applied: %s", entry.id, exc)
        await inbox.set_outcome(entry, "failed", str(exc))
        fire_alert(
            f"webhook_failed:{entry.id}",
            f"Вебхук Tribute #{entry.id} не применён: {exc}. Проверь webhook_inbox — "
            "возможно, оплату нужно выдать через /grant.",
        )

    await db.commit()
    if confirm_to is not None:
        await send_payment_confirmation(confirm_to)
    return WebhookAck()


@admin_router.post("/admin/grant", response_model=AdminGrantResponse)
async def admin_grant(body: AdminGrantRequest, db: AsyncSession = Depends(get_db)) -> AdminGrantResponse:
    """Manual fallback for when a Tribute webhook never arrives despite a real payment — see
    docs/PLAN.md §11.2. Authorization for *who* may call this is the bot's job (it only exposes
    the /grant command to docs/PLAN.md's admin telegram IDs); this endpoint only enforces the
    same bot<->API trust boundary as every other route (X-Internal-Token)."""
    service = BillingService(db)
    user = await service.grant_manual(body.telegram_id, body.count)
    return AdminGrantResponse(telegram_id=body.telegram_id, paid_games_available=user.paid_games_available)
