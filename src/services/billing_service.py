"""Tribute payment webhook handling + the manual /grant admin fallback (docs/PLAN.md §11).

IMPORTANT — verify against real Tribute docs before going live: the signature header name,
HMAC algorithm, and webhook payload shape below (`X-Tribute-Signature`, HMAC-SHA256 over the
raw body, `metadata.telegram_id`) are a placeholder built on the most common webhook-signing
convention, not confirmed against Tribute's actual API — see docs/PLAN.md §11.2's own note that
this needs the provider's current documentation at implementation time. Everything else (the
idempotency-by-external_id logic, the paid-game grant, the admin fallback) is provider-agnostic
and does not need to change when the real payload shape is plugged in.
"""

import hashlib
import hmac
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.models import Payment, User
from src.db.repositories import EventRepository, PaymentRepository, UserRepository

logger = logging.getLogger(__name__)


class InvalidWebhookSignatureError(Exception):
    pass


class DuplicateWebhookError(Exception):
    """Raised when a webhook for an already-recorded external_id arrives again. Callers should
    treat this as a normal, expected outcome (return 200) — not an error to alert on."""

    def __init__(self, payment: Payment) -> None:
        self.payment = payment
        super().__init__(f"payment {payment.external_id} already recorded")


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class BillingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.payments = PaymentRepository(session)
        self.events = EventRepository(session)

    async def process_tribute_payment(
        self,
        telegram_id: int,
        external_id: str,
        amount: float,
        currency: str,
        raw_payload: dict,
    ) -> Payment:
        existing = await self.payments.get_by_external_id("tribute", external_id)
        if existing is not None:
            raise DuplicateWebhookError(existing)

        user = await self.users.get_or_create(telegram_id)
        payment = await self.payments.create(
            user_id=user.id,
            provider="tribute",
            external_id=external_id,
            amount=amount,
            currency=currency,
            raw_payload=raw_payload,
        )
        await self.users.grant_paid_game(user, 1)
        await self.events.log(
            "payment_succeeded", user.id, None, {"amount": amount, "currency": currency}
        )
        await notify_user(telegram_id, PAYMENT_CONFIRMED_TEXT)
        return payment

    async def grant_manual(self, telegram_id: int, count: int = 1) -> User:
        user = await self.users.get_or_create(telegram_id)
        await self.users.grant_paid_game(user, count)
        await self.events.log("payment_succeeded", user.id, None, {"source": "manual_grant", "count": count})
        return user


PAYMENT_CONFIRMED_TEXT = "Оплата прошла — можно начинать следующую партию. Жми /start, когда будешь готов."


async def notify_user(telegram_id: int, text: str) -> None:
    """Best-effort notification via the raw Telegram Bot API — the billing flow lives in the
    API process, not the bot process, so this avoids pulling in a full aiogram Dispatcher just
    to send one message. Failure here must never fail the webhook response (Tribute would
    retry-storm us for something that isn't actually our processing failing)."""
    settings = get_settings()
    if not settings.bot_token:
        logger.warning("BOT_TOKEN not set — skipping Telegram notification to %s", telegram_id)
        return
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json={"chat_id": telegram_id, "text": text})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("failed to notify user %s after payment: %s", telegram_id, exc)
