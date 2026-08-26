import httpx
import pytest_asyncio

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings


@pytest_asyncio.fixture
async def client(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    settings = get_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Token": settings.api_internal_token},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def _override_get_db(session):
    async def _get_db():
        yield session

    return _get_db


async def test_health_does_not_require_auth():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_missing_internal_token_is_rejected(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/game/start", json={"telegram_id": 1})
    app.dependency_overrides.clear()
    assert resp.status_code in (401, 422)


async def test_full_game_playthrough_via_api(client, unique_telegram_id):
    start = await client.post(
        "/game/start",
        json={"telegram_id": unique_telegram_id, "intention": "разобраться с застоем"},
    )
    assert start.status_code == 200
    body = start.json()
    assert body["status"] == "active"
    game_id = body["game_id"]

    # Enter the game — roll until a 6 comes up (deterministic within a bounded number of tries).
    entered = False
    for _ in range(50):
        r = await client.post(f"/game/{game_id}/roll")
        assert r.status_code == 200
        if r.json()["event"] == "entered":
            entered = True
            break
    assert entered

    respond = await client.post(f"/game/{game_id}/respond", json={"text": "Чувствую сопротивление."})
    assert respond.status_code == 200
    assert respond.json()["safety_flag"] is None

    state = await client.get(f"/game/{game_id}")
    assert state.status_code == 200
    state_body = state.json()
    assert state_body["in_play"] is True
    assert len(state_body["history"]) >= 1

    status = await client.get(f"/users/{unique_telegram_id}/status")
    assert status.status_code == 200
    assert status.json()["active_game_id"] == game_id


async def test_second_free_game_start_is_idempotent_and_resumes_active_game(client, unique_telegram_id):
    first = await client.post("/game/start", json={"telegram_id": unique_telegram_id})
    game_id = first.json()["game_id"]

    second = await client.post("/game/start", json={"telegram_id": unique_telegram_id})
    assert second.status_code == 200
    assert second.json()["game_id"] == game_id


async def test_roll_idempotency_key_prevents_double_roll(client, unique_telegram_id):
    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    r1 = await client.post(f"/game/{game_id}/roll", headers={"Idempotency-Key": "abc"})
    r2 = await client.post(f"/game/{game_id}/roll", headers={"Idempotency-Key": "abc"})

    assert r1.json() == r2.json()

    state = await client.get(f"/game/{game_id}")
    assert len(state.json()["history"]) == 1  # only one move was actually recorded


async def test_paywall_blocks_second_paid_game(client, unique_telegram_id):
    first = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = first.json()["game_id"]
    await client.post(f"/game/{game_id}/finish")

    second = await client.post("/game/start", json={"telegram_id": unique_telegram_id})
    assert second.status_code == 402
    body = second.json()
    assert body["error"] == "payment_required"
    assert body["price"] > 0
    assert "pay_url" in body


async def test_finish_marks_free_game_used_and_grants_access_after_payment(client, db_session, unique_telegram_id):
    from src.db.repositories import UserRepository

    first = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = first.json()["game_id"]
    finish = await client.post(f"/game/{game_id}/finish")
    assert finish.status_code == 200
    assert "summary" in finish.json()

    users = UserRepository(db_session)
    user = await users.get_by_telegram_id(unique_telegram_id)
    assert user.has_free_game_used is True

    await users.grant_paid_game(user, 1)

    second = await client.post("/game/start", json={"telegram_id": unique_telegram_id})
    assert second.status_code == 200
    assert second.json()["status"] == "setup"


async def test_get_unknown_game_returns_404(client):
    import uuid

    resp = await client.get(f"/game/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"] == "game_not_found"


async def test_rolling_a_finished_game_returns_409(client, unique_telegram_id):
    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]
    await client.post(f"/game/{game_id}/finish")

    resp = await client.post(f"/game/{game_id}/roll")
    assert resp.status_code == 409
    assert resp.json()["error"] == "game_not_active"


async def test_skip_response_acks_and_logs_event(client, db_session, unique_telegram_id):
    from src.db.models import Event

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    resp = await client.post(f"/game/{game_id}/skip")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    from sqlalchemy import select

    result = await db_session.execute(
        select(Event).where(Event.name == "response_skipped", Event.game_id == game_id)
    )
    assert result.scalar_one_or_none() is not None


async def test_skip_response_on_finished_game_returns_409(client, unique_telegram_id):
    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]
    await client.post(f"/game/{game_id}/finish")

    resp = await client.post(f"/game/{game_id}/skip")
    assert resp.status_code == 409


async def test_feedback_is_accepted_on_a_finished_game(client, db_session, unique_telegram_id):
    from sqlalchemy import select

    from src.db.models import Event

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]
    await client.post(f"/game/{game_id}/finish")

    resp = await client.post(f"/game/{game_id}/feedback", json={"text": "здорово"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    result = await db_session.execute(
        select(Event).where(Event.name == "feedback_given", Event.game_id == game_id)
    )
    event = result.scalar_one()
    assert event.props == {"text": "здорово"}


async def test_safety_flag_is_surfaced_on_crisis_marker(client, unique_telegram_id):
    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    resp = await client.post(
        f"/game/{game_id}/respond", json={"text": "я не хочу жить дальше"}
    )
    assert resp.status_code == 200
    assert resp.json()["safety_flag"] == "soft_referral"
