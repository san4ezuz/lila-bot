import hashlib
import hmac
import json

import httpx
import pytest

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings
from src.db.repositories import UserRepository
from src.services.billing_service import (
    BillingService,
    DuplicateWebhookError,
    parse_webhook_payload,
    verify_signature,
)


@pytest.fixture(autouse=True)
def _no_telegram_notifications(monkeypatch):
    async def _noop(telegram_id, text):
        return None

    monkeypatch.setattr("src.services.billing_service.notify_user", _noop)


def test_verify_signature_accepts_correct_hmac():
    secret = "s3cr3t"
    body = b'{"a": 1}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, secret) is True


def test_verify_signature_rejects_wrong_hmac():
    assert verify_signature(b'{"a": 1}', "deadbeef", "s3cr3t") is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b'{"a": 1}', None, "s3cr3t") is False


# --- parse_webhook_payload (docs/PLAN.md's "verify against real Tribute docs" placeholder,
# now grounded in wiki.tribute.tg's confirmed event names / telegramId field convention — see
# src/services/billing_service.py module docstring) --------------------------------------------


def test_parse_webhook_payload_extracts_success_event():
    parsed = parse_webhook_payload(
        {
            "event": "shopOrderPaymentReceived",
            "id": "order-123",
            "amount": 299,
            "currency": "RUB",
            "user": {"telegramId": 555, "firstName": "A"},
        }
    )
    assert parsed is not None
    assert parsed.telegram_id == 555
    assert parsed.external_id == "order-123"
    assert parsed.amount == 299.0
    assert parsed.currency == "RUB"


def test_parse_webhook_payload_ignores_known_non_success_event():
    parsed = parse_webhook_payload(
        {"event": "shopOrderRefunded", "id": "order-123", "user": {"telegramId": 555}}
    )
    assert parsed is None


def test_parse_webhook_payload_ignores_unrecognized_event():
    parsed = parse_webhook_payload({"event": "somethingWeirdWeDontKnow", "id": "x"})
    assert parsed is None


def test_parse_webhook_payload_ignores_missing_event_field():
    parsed = parse_webhook_payload({"id": "x", "amount": 100})
    assert parsed is None


def test_parse_webhook_payload_falls_back_to_flat_telegram_id():
    parsed = parse_webhook_payload(
        {"event": "newDonation", "id": "d-1", "amount": 100, "telegram_id": 777}
    )
    assert parsed is not None
    assert parsed.telegram_id == 777


# --- service-level tests (no HTTP) ---------------------------------------------------------


async def test_process_tribute_payment_grants_paid_game(db_session, unique_telegram_id):
    service = BillingService(db_session)
    payment = await service.process_tribute_payment(
        telegram_id=unique_telegram_id,
        external_id="pay-1",
        amount=299.0,
        currency="RUB",
        raw_payload={"raw": True},
    )
    assert payment.status == "paid"

    users = UserRepository(db_session)
    user = await users.get_by_telegram_id(unique_telegram_id)
    assert user.paid_games_available == 1


async def test_duplicate_webhook_does_not_double_grant(db_session, unique_telegram_id):
    service = BillingService(db_session)
    await service.process_tribute_payment(
        unique_telegram_id, "pay-dup", 299.0, "RUB", {"raw": True}
    )
    with pytest.raises(DuplicateWebhookError):
        await service.process_tribute_payment(
            unique_telegram_id, "pay-dup", 299.0, "RUB", {"raw": True}
        )

    users = UserRepository(db_session)
    user = await users.get_by_telegram_id(unique_telegram_id)
    assert user.paid_games_available == 1  # not 2


async def test_grant_manual(db_session, unique_telegram_id):
    service = BillingService(db_session)
    user = await service.grant_manual(unique_telegram_id, count=2)
    assert user.paid_games_available == 2


# --- API-level tests (real FastAPI app via ASGITransport) --------------------------------


@pytest.fixture
async def http_client(db_session):
    async def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    settings = get_settings()
    transport = httpx.ASGITransport(app=app)
    # Webhook/admin routes are exercised with their own explicit headers per test; the default
    # X-Internal-Token here is just so GET /users/{id}/status (a game.router route) works too.
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Token": settings.api_internal_token},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def _sign(body: dict, secret: str) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, sig


async def test_webhook_with_valid_signature_grants_paid_game(http_client, unique_telegram_id):
    settings = get_settings()
    body = {
        "event": "shopOrderPaymentReceived",
        "id": "ext-abc",
        "amount": 299,
        "currency": "RUB",
        "user": {"telegramId": unique_telegram_id},
    }
    raw, sig = _sign(body, settings.tribute_api_key)

    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "trbt-signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 1


async def test_webhook_with_invalid_signature_is_rejected(http_client, unique_telegram_id):
    body = {
        "event": "shopOrderPaymentReceived",
        "id": "ext-bad",
        "amount": 299,
        "user": {"telegramId": unique_telegram_id},
    }
    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "trbt-signature": "wrong"},
    )
    assert resp.status_code == 401


async def test_webhook_with_non_success_event_does_not_grant(http_client, unique_telegram_id):
    settings = get_settings()
    body = {
        "event": "shopOrderRefunded",
        "id": "ext-refund",
        "user": {"telegramId": unique_telegram_id},
    }
    raw, sig = _sign(body, settings.tribute_api_key)

    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "trbt-signature": sig},
    )
    assert resp.status_code == 200  # acked, but...

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 0  # ...nothing was granted


async def test_webhook_redelivery_is_idempotent(http_client, unique_telegram_id):
    settings = get_settings()
    body = {
        "event": "shopOrderPaymentReceived",
        "id": "ext-redelivered",
        "amount": 299,
        "currency": "RUB",
        "user": {"telegramId": unique_telegram_id},
    }
    raw, sig = _sign(body, settings.tribute_api_key)
    headers = {"Content-Type": "application/json", "trbt-signature": sig}

    r1 = await http_client.post("/payments/tribute/webhook", content=raw, headers=headers)
    r2 = await http_client.post("/payments/tribute/webhook", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 1


async def test_admin_grant_rejects_wrong_internal_token(http_client, unique_telegram_id):
    resp = await http_client.post(
        "/admin/grant",
        json={"telegram_id": unique_telegram_id},
        headers={"X-Internal-Token": "wrong-token"},
    )
    assert resp.status_code == 401


async def test_admin_grant_with_valid_token(http_client, unique_telegram_id):
    settings = get_settings()
    resp = await http_client.post(
        "/admin/grant",
        json={"telegram_id": unique_telegram_id, "count": 3},
        headers={"X-Internal-Token": settings.api_internal_token},
    )
    assert resp.status_code == 200
    assert resp.json()["paid_games_available"] == 3
