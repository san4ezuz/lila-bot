import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings
from src.db.models import WebhookInbox
from src.db.repositories import CreditRepository, UserRepository
from src.services.billing_service import (
    BillingService,
    DuplicateWebhookError,
    UnprocessableWebhookError,
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


# --- parse_webhook_payload, against the shapes in tribute.tg/api/v1/openapi/en -----------------


def _purchase(telegram_id=555, purchase_id=9001, name="new_digital_product", **overrides):
    payload = {
        "product_id": 42,
        "product_name": "Партия",
        "amount": 29900,
        "currency": "rub",
        "purchase_id": purchase_id,
        "transaction_id": 777,
        "purchase_created_at": "2026-11-02T10:00:00Z",
        "telegram_user_id": telegram_id,
        **overrides,
    }
    return {
        "name": name,
        "created_at": "2026-11-02T10:00:00Z",
        "sent_at": "2026-11-02T10:00:01Z",
        "payload": payload,
    }


def test_parse_webhook_payload_reads_digital_product_purchase():
    parsed = parse_webhook_payload(_purchase())
    assert parsed is not None
    assert parsed.kind == "payment"
    assert parsed.telegram_id == 555
    assert parsed.external_id == "9001"
    assert parsed.amount == 299.0  # kopecks -> rubles
    assert parsed.currency == "RUB"


def test_parse_webhook_payload_reads_refund():
    parsed = parse_webhook_payload(_purchase(name="digital_product_refunded"))
    assert parsed is not None
    assert parsed.kind == "refund"
    assert parsed.external_id == "9001"


def test_parse_webhook_payload_ignores_other_events():
    assert parse_webhook_payload(_purchase(name="new_donation")) is None
    assert parse_webhook_payload({"payload": {}}) is None


def test_parse_webhook_payload_ignores_other_products():
    assert parse_webhook_payload(_purchase(), product_id=1) is None
    assert parse_webhook_payload(_purchase(), product_id=42) is not None


def test_parse_webhook_payload_refuses_payment_without_telegram_id():
    with pytest.raises(UnprocessableWebhookError):
        parse_webhook_payload(_purchase(telegram_id=None))


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

    user = await UserRepository(db_session).get_by_telegram_id(unique_telegram_id)
    assert await CreditRepository(db_session).balance(user.id) == 1


async def test_duplicate_webhook_does_not_double_grant(db_session, unique_telegram_id):
    service = BillingService(db_session)
    await service.process_tribute_payment(
        unique_telegram_id, "pay-dup", 299.0, "RUB", {"raw": True}
    )
    with pytest.raises(DuplicateWebhookError):
        await service.process_tribute_payment(
            unique_telegram_id, "pay-dup", 299.0, "RUB", {"raw": True}
        )

    user = await UserRepository(db_session).get_by_telegram_id(unique_telegram_id)
    assert await CreditRepository(db_session).balance(user.id) == 1  # not 2


async def test_grant_manual(db_session, unique_telegram_id):
    service = BillingService(db_session)
    assert await service.grant_manual(unique_telegram_id, count=2) == 2


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


async def _post_webhook(client, body):
    raw, sig = _sign(body, get_settings().tribute_api_key)
    return await client.post(
        "/payments/tribute/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "trbt-signature": sig},
    )


async def _inbox_statuses(db_session, purchase_id):
    result = await db_session.execute(
        select(WebhookInbox)
        .where(WebhookInbox.payload["payload"]["purchase_id"].as_integer() == purchase_id)
        .order_by(WebhookInbox.id)
    )
    return [entry.status for entry in result.scalars()]


async def test_webhook_with_valid_signature_grants_paid_game(http_client, db_session, unique_telegram_id):
    resp = await _post_webhook(http_client, _purchase(unique_telegram_id, unique_telegram_id))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 1
    assert await _inbox_statuses(db_session, unique_telegram_id) == ["processed"]


async def test_webhook_with_invalid_signature_is_rejected(http_client, unique_telegram_id):
    resp = await http_client.post(
        "/payments/tribute/webhook",
        content=json.dumps(_purchase(unique_telegram_id)).encode(),
        headers={"Content-Type": "application/json", "trbt-signature": "wrong"},
    )
    assert resp.status_code == 401


async def test_webhook_with_unrelated_event_does_not_grant(http_client, db_session, unique_telegram_id):
    body = _purchase(unique_telegram_id, unique_telegram_id, name="new_donation")
    assert (await _post_webhook(http_client, body)).status_code == 200

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 0
    assert await _inbox_statuses(db_session, unique_telegram_id) == ["ignored"]


async def test_webhook_redelivery_is_idempotent(http_client, db_session, unique_telegram_id):
    body = _purchase(unique_telegram_id, unique_telegram_id)
    assert (await _post_webhook(http_client, body)).status_code == 200
    assert (await _post_webhook(http_client, body)).status_code == 200

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 1
    assert await _inbox_statuses(db_session, unique_telegram_id) == ["processed", "duplicate"]


async def test_unattributable_payment_stays_in_inbox_as_failed(http_client, db_session, unique_telegram_id):
    body = _purchase(telegram_id=None, purchase_id=unique_telegram_id)
    assert (await _post_webhook(http_client, body)).status_code == 200
    assert await _inbox_statuses(db_session, unique_telegram_id) == ["failed"]


async def test_refund_takes_back_unused_game(http_client, db_session, unique_telegram_id):
    await _post_webhook(http_client, _purchase(unique_telegram_id, unique_telegram_id))
    refund = _purchase(unique_telegram_id, unique_telegram_id, name="digital_product_refunded")
    assert (await _post_webhook(http_client, refund)).status_code == 200
    assert (await _post_webhook(http_client, refund)).status_code == 200  # redelivery

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 0
    assert await _inbox_statuses(db_session, unique_telegram_id) == [
        "processed",
        "processed",
        "duplicate",
    ]


async def test_refund_does_not_push_balance_negative(http_client, db_session, unique_telegram_id):
    await _post_webhook(http_client, _purchase(unique_telegram_id, unique_telegram_id))
    user = await UserRepository(db_session).get_by_telegram_id(unique_telegram_id)
    await CreditRepository(db_session).add(user.id, -1, "game")

    refund = _purchase(unique_telegram_id, unique_telegram_id, name="digital_product_refunded")
    await _post_webhook(http_client, refund)

    status = await http_client.get(f"/users/{unique_telegram_id}/status")
    assert status.json()["paid_games_available"] == 0


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
