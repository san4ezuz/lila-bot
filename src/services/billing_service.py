"""Tribute payment webhook handling + the manual /grant admin fallback (docs/PLAN.md §11).

The contract below comes from Tribute's own OpenAPI spec (tribute.tg/api/v1/openapi/en, checked
2026-11-02), for the "digital product" product type this bot sells:
  - Signature header is `trbt-signature`: an HMAC-SHA256 of the raw request body, signed with your
    **Api-Key** — there is no separate webhook secret. Hex encoding is assumed (the spec doesn't
    state it); a real test payment is what confirms that.
  - The body is `{"name", "created_at", "sent_at", "payload"}`. `name` is `new_digital_product` or
    `digital_product_refunded`.
  - `payload` carries `product_id`, `amount` and `currency`, `purchase_id` (the dedup key, also
    links a refund to its purchase), and an optional `telegram_user_id`.
  - `amount` is in the currency's smallest unit (kopecks for RUB).

Every event with a valid signature is stored in `webhook_inbox` before it is interpreted, so a
payment we can't act on stays recoverable instead of existing only in a log line.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Payment, User
from src.db.repositories import EventRepository, PaymentRepository, UserRepository
from src.services import notify

logger = logging.getLogger(__name__)

_PAYMENT_EVENT = "new_digital_product"
_REFUND_EVENT = "digital_product_refunded"


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


class UnprocessableWebhookError(Exception):
    """A payment event we recognize but can't apply (e.g. no Telegram id, unknown purchase). The
    webhook is acked — Tribute's retries wouldn't change the payload — and the inbox row stays
    `failed` with this message, for manual resolution through /grant."""


@dataclass(frozen=True, slots=True)
class ParsedTributeEvent:
    kind: Literal["payment", "refund"]
    telegram_id: int | None
    external_id: str
    amount: float
    currency: str


def parse_webhook_payload(
    body: dict[str, Any], product_id: int | None = None
) -> ParsedTributeEvent | None:
    """Returns None for events that are simply not ours to act on: another event type, or another
    product when `product_id` is set. Raises UnprocessableWebhookError when an event is ours but
    can't be applied — never guess and grant on a payment we can't attribute."""
    name = body.get("name")
    if name not in (_PAYMENT_EVENT, _REFUND_EVENT):
        logger.info("tribute webhook event %r — no action needed", name)
        return None

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise UnprocessableWebhookError(f"{name}: no payload object")

    if product_id is not None and payload.get("product_id") != product_id:
        logger.info("tribute webhook for product %r — not ours", payload.get("product_id"))
        return None

    purchase_id = payload.get("purchase_id")
    if purchase_id is None:
        raise UnprocessableWebhookError(f"{name}: no purchase_id")

    kind: Literal["payment", "refund"] = "payment" if name == _PAYMENT_EVENT else "refund"
    telegram_id = payload.get("telegram_user_id")
    if kind == "payment" and telegram_id is None:
        raise UnprocessableWebhookError(f"{name}: no telegram_user_id")

    return ParsedTributeEvent(
        kind=kind,
        telegram_id=int(telegram_id) if telegram_id is not None else None,
        external_id=str(purchase_id),
        amount=int(payload.get("amount") or 0) / 100,
        currency=str(payload.get("currency") or "rub").upper(),
    )


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
        raw_payload: dict[str, Any],
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
        return payment

    async def process_tribute_refund(self, external_id: str) -> None:
        """Marks the purchase refunded and takes back its game if it's still unused. A game
        already started stays playable — the refund is recorded either way."""
        payment = await self.payments.get_by_external_id("tribute", external_id)
        if payment is None:
            raise UnprocessableWebhookError(f"refund for unknown purchase {external_id}")
        if payment.status == "refunded":
            raise DuplicateWebhookError(payment)

        payment.status = "refunded"
        await self.session.flush()
        user = await self.session.get(User, payment.user_id)
        revoked = user is not None and await self.users.revoke_paid_game(user)
        await self.events.log(
            "payment_refunded", payment.user_id, None, {"external_id": external_id, "revoked": revoked}
        )

    async def grant_manual(self, telegram_id: int, count: int = 1) -> User:
        user = await self.users.get_or_create(telegram_id)
        await self.users.grant_paid_game(user, count)
        # Not payment_succeeded: an admin grant is not revenue (see AnalyticsService.funnel).
        await self.events.log("admin_grant", user.id, None, {"count": count})
        return user


PAYMENT_CONFIRMED_TEXT = "Оплата прошла — можно начинать следующую партию. Жми /start, когда будешь готов."

# Re-exported for callers/tests already importing notify_user from this module — the actual raw
# Bot API call now lives in src/services/notify.py, shared with reminder_service.py.
notify_user = notify.send_message


async def send_payment_confirmation(telegram_id: int) -> None:
    """Call after commit — the player must not be told about a grant that could still roll back."""
    await notify_user(telegram_id, PAYMENT_CONFIRMED_TEXT)
