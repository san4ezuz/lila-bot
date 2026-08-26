"""Tribute payment webhook handling + the manual /grant admin fallback (docs/PLAN.md §11).

Confirmed against Tribute's own docs (wiki.tribute.tg, fetched 2026-08-26 — see
docs/PLAN.md §11.2 for the standing note that provider details should be re-checked at
implementation time):
  - Signature header is `trbt-signature` (not a made-up `X-Tribute-Signature`).
  - It's an HMAC-SHA256 of the raw request body, signed with your **Api-Key** — Tribute does
    NOT use a separate webhook secret. `settings.tribute_api_key` is the same key used for the
    `Api-Key` header on outbound requests to Tribute's API.
  - Real event names exist for two product families: shop orders (`shopOrder`,
    `shopOrderChargeSuccess`, `shopOrderPaymentReceived`, `shopOrderChargeFailed`,
    `shopOrderCancelled`, `shopOrderRefunded`, `shopOrderPaymentFailed`) and content-creator
    products (`newSubscription`, `renewedSubscription`, `newDonation`, `recurrentDonation`,
    `newDigitalProduct`).
  - Tribute's data model nests a `telegramId` field on a `User` object (confirmed from Tribute's
    own Go SDK struct definitions, github.com/izeberg/go-tribute-payments-hook) — since Tribute
    payments happen inside Telegram, this is how a webhook correlates to our own users, not a
    custom metadata field we attach ourselves (no such field is exposed in their public product-
    creation docs, which is not an API — products/prices are configured in Creator Dashboard).

Still NOT confirmed (GitBook's OpenAPI payload examples aren't reachable as static content) and
worth checking against a real test payment before launch: the exact byte encoding of the digest
(hex assumed below — the most common convention), and the precise top-level JSON shape per event
(whether `user`/`amount`/`currency`/`id` sit at the payload root or nested under a per-event key
like `payload.subscription`/`payload.donation`). `parse_webhook_payload()` below tries several
plausible shapes and logs + safely no-ops (ack without granting) rather than guessing wrong.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.models import Payment, User
from src.db.repositories import EventRepository, PaymentRepository, UserRepository

logger = logging.getLogger(__name__)

# Event names confirmed from wiki.tribute.tg (for-shops/api/webhooks and
# for-content-creators/api-documentation/webhooks) — see module docstring.
_SUCCESS_EVENTS = frozenset(
    {
        "shopOrderPaymentReceived",
        "shopOrderChargeSuccess",
        "newDigitalProduct",
        "newSubscription",
        "renewedSubscription",
        "newDonation",
        "recurrentDonation",
    }
)
_IGNORABLE_EVENTS = frozenset(
    {
        "shopOrder",
        "shopOrderChargeFailed",
        "shopOrderCancelled",
        "shopOrderRefunded",
        "shopOrderPaymentFailed",
    }
)


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


@dataclass(frozen=True, slots=True)
class ParsedTributePayment:
    telegram_id: int
    external_id: str
    amount: float
    currency: str


def parse_webhook_payload(payload: dict[str, Any]) -> ParsedTributePayment | None:
    """Returns None for events we should acknowledge but not act on: a recognized non-success
    event (refund/cancel/failure), or a shape we don't recognize at all — never guess and grant
    on an event we can't positively identify as a successful payment."""
    event = payload.get("event") or payload.get("name") or payload.get("type")

    if event in _IGNORABLE_EVENTS:
        logger.info("tribute webhook event %r — no action needed", event)
        return None
    if event is not None and event not in _SUCCESS_EVENTS:
        logger.warning("unrecognized tribute webhook event %r — ignoring, not granting", event)
        return None
    if event is None:
        logger.warning("tribute webhook payload has no recognizable event field: %r", payload)
        return None

    # Some event families nest the object under "payload"; try both root and nested.
    nested = payload.get("payload")
    body: dict[str, Any] = nested if isinstance(nested, dict) else payload
    user: dict[str, Any] = body.get("user") or payload.get("user") or {}

    telegram_id = (
        user.get("telegramId")
        or body.get("telegramId")
        or payload.get("telegram_id")
        or (payload.get("metadata") or {}).get("telegram_id")
    )
    if telegram_id is None:
        logger.warning("tribute webhook (%s) has no telegram id we recognize: %r", event, payload)
        return None

    external_id = body.get("id") or payload.get("id") or body.get("subscriptionId")
    if external_id is None:
        logger.warning("tribute webhook (%s) has no id field we recognize: %r", event, payload)
        return None

    amount = float(body.get("amount") or payload.get("amount") or 0)
    currency = str(body.get("currency") or payload.get("currency") or "RUB")

    return ParsedTributePayment(
        telegram_id=int(telegram_id), external_id=str(external_id), amount=amount, currency=currency
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
