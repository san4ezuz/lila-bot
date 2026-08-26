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
        "external_id": "ext-abc",
        "amount": 299,
        "currency": "RUB",
        "metadata": {"telegram_id": unique_telegram_id},
    }
    raw, sig = _sign(body, settings.tribute_webhook_secret)

    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Tribute-Signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 1


async def test_webhook_with_invalid_signature_is_rejected(http_client, unique_telegram_id):
    body = {"external_id": "ext-bad", "amount": 299, "metadata": {"telegram_id": unique_telegram_id}}
    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Tribute-Signature": "wrong"},
    )
    assert resp.status_code == 401


async def test_webhook_redelivery_is_idempotent(http_client, unique_telegram_id):
    settings = get_settings()
    body = {
        "external_id": "ext-redelivered",
        "amount": 299,
        "currency": "RUB",
        "metadata": {"telegram_id": unique_telegram_id},
    }
    raw, sig = _sign(body, settings.tribute_webhook_secret)
    headers = {"Content-Type": "application/json", "X-Tribute-Signature": sig}

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
