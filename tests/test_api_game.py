import httpx
import pytest_asyncio
from sqlalchemy import select

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings
from src.core.rules import ENTRY_GUARANTEE_ATTEMPTS


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
    assert body["in_play"] is False  # entry requires a 6 — see docs/RULES.md §2
    assert body["position"] is None
    game_id = body["game_id"]

    # Entry is guaranteed within ENTRY_GUARANTEE_ATTEMPTS rolls regardless of what's rolled.
    r = None
    for _ in range(ENTRY_GUARANTEE_ATTEMPTS):
        r = await client.post(f"/game/{game_id}/roll")
        assert r.status_code == 200
        if r.json()["event"] == "entered":
            break
    assert r.json()["event"] == "entered"
    assert r.json()["position"] == 6

    r = await client.post(f"/game/{game_id}/roll")
    assert r.status_code == 200
    assert r.json()["event"] in ("moved", "finished", "blocked_overshoot")

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


async def test_roll_idempotency_survives_a_process_restart(client, db_session, unique_telegram_id):
    """The dedup key lives in `moves`, so a retry reaching a restarted API process (the case a
    timeout during a deploy produces) is still a replay."""
    from src.db.models import Move

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]
    await client.post(f"/game/{game_id}/roll", headers={"Idempotency-Key": "k1"})

    move = await db_session.scalar(select(Move).where(Move.idempotency_key == "k1"))
    assert str(move.game_id) == game_id


async def test_roll_replay_regenerates_a_missing_guide_message(client, db_session, unique_telegram_id):
    """The attempt died after committing the move but before the LLM replied — the retry must get
    an answer rather than an empty message, without rolling again."""
    from sqlalchemy import delete

    from src.db.models import Message

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]
    first = await client.post(f"/game/{game_id}/roll", headers={"Idempotency-Key": "k2"})
    await db_session.execute(delete(Message).where(Message.move_id.is_not(None)))

    retry = await client.post(f"/game/{game_id}/roll", headers={"Idempotency-Key": "k2"})

    assert retry.json()["turn"] == first.json()["turn"]
    assert retry.json()["guide_message"]
    state = await client.get(f"/game/{game_id}")
    assert len(state.json()["history"]) == 1


async def test_finish_twice_reuses_the_summary_and_counts_one_finish(client, db_session, unique_telegram_id):
    from src.db.models import Event

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    first = await client.post(f"/game/{game_id}/finish")
    second = await client.post(f"/game/{game_id}/finish")

    assert second.json()["summary"] == first.json()["summary"]
    events = await db_session.execute(
        select(Event).where(Event.name == "game_finished", Event.game_id == game_id)
    )
    assert len(events.scalars().all()) == 1


async def test_user_status_exposes_game_status_for_state_recovery(client, unique_telegram_id):
    await client.post("/game/start", json={"telegram_id": unique_telegram_id})

    status = await client.get(f"/users/{unique_telegram_id}/status")

    assert status.json()["active_game_status"] == "setup"


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


async def test_set_gender_endpoint_is_reflected_in_user_status(client, unique_telegram_id):
    before = await client.get(f"/users/{unique_telegram_id}/status")
    assert before.json()["gender"] is None

    resp = await client.post(f"/users/{unique_telegram_id}/gender", json={"gender": "female"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    after = await client.get(f"/users/{unique_telegram_id}/status")
    assert after.json()["gender"] == "female"


async def test_reminders_opt_out_endpoint(client, db_session, unique_telegram_id):
    from src.db.repositories import UserRepository

    resp = await client.post(f"/users/{unique_telegram_id}/reminders/opt-out")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    users = UserRepository(db_session)
    user = await users.get_or_create(unique_telegram_id)
    assert user.reminders_opt_out is True


async def test_reminder_snooze_endpoint(client, db_session, unique_telegram_id):
    from src.db.repositories import ReminderRepository

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    resp = await client.post(f"/game/{game_id}/reminders/snooze", json={"hours": 48})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    reminders = ReminderRepository(db_session)
    import uuid

    reminder = await reminders.get_or_create(uuid.UUID(game_id))
    assert reminder.snoozed_until is not None


async def test_resuming_an_abandoned_game_flips_it_back_to_active(client, db_session, unique_telegram_id):
    from src.db.repositories import GameRepository

    start = await client.post(
        "/game/start", json={"telegram_id": unique_telegram_id, "intention": "тест"}
    )
    game_id = start.json()["game_id"]

    games = GameRepository(db_session)
    import uuid

    game = await games.get(uuid.UUID(game_id))
    await games.mark_abandoned(game)
    assert game.status == "abandoned"

    roll = await client.post(f"/game/{game_id}/roll")
    assert roll.status_code == 200

    state = await client.get(f"/game/{game_id}")
    assert state.json()["status"] == "active"


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


async def _start(client, telegram_id, intention="тест"):
    resp = await client.post("/game/start", json={"telegram_id": telegram_id, "intention": intention})
    return resp.json()


async def _user(db_session, telegram_id):
    from src.db.repositories import UserRepository

    return await UserRepository(db_session).get_by_telegram_id(telegram_id)


async def test_crisis_response_turns_on_crisis_mode(client, db_session, unique_telegram_id):
    game_id = (await _start(client, unique_telegram_id))["game_id"]

    await client.post(f"/game/{game_id}/respond", json={"text": "думаю о суициде"})

    user = await _user(db_session, unique_telegram_id)
    assert user.crisis_at is not None
    roll = await client.post(f"/game/{game_id}/roll")
    assert roll.json()["crisis_mode"] is True
    later = await client.post(f"/game/{game_id}/respond", json={"text": "спасибо"})
    assert later.json()["safety_flag"] is None
    assert later.json()["crisis_mode"] is True


async def test_classifier_catches_what_the_dictionary_misses(client, db_session, unique_telegram_id, monkeypatch):
    async def _risky(text):
        return True

    game_id = (await _start(client, unique_telegram_id))["game_id"]
    monkeypatch.setattr("src.llm.safety.classify", _risky)

    resp = await client.post(f"/game/{game_id}/respond", json={"text": "всем будет легче без меня"})

    assert resp.json()["safety_flag"] == "soft_referral"
    events = await _events(db_session, "safety_triggered")
    assert events[-1].props["source"] == "classifier"


async def test_crisis_intention_is_not_saved(client, db_session, unique_telegram_id):
    game_id = (await _start(client, unique_telegram_id, intention=None))["game_id"]

    resp = await client.post(f"/game/{game_id}/intention", json={"intention": "хочу умереть"})

    body = resp.json()
    assert body["safety_flag"] == "soft_referral"
    assert body["status"] == "setup"
    assert "хочу умереть" not in body["guide_message"]
    state = await client.get(f"/game/{game_id}")
    assert state.json()["intention"] is None

    retry = await client.post(f"/game/{game_id}/intention", json={"intention": "отношения"})
    assert retry.json()["status"] == "active"


async def test_crisis_feedback_returns_support_text(client, db_session, unique_telegram_id):
    game_id = (await _start(client, unique_telegram_id))["game_id"]
    await client.post(f"/game/{game_id}/finish")

    flagged = await client.post(f"/game/{game_id}/feedback", json={"text": "не хочу больше жить"})
    calm = await client.post(f"/game/{game_id}/feedback", json={"text": "глубоко"})

    assert flagged.json()["guide_message"]
    assert calm.json()["guide_message"] is None


async def test_paywall_is_replaced_by_help_in_crisis_mode(client, db_session, unique_telegram_id):
    game_id = (await _start(client, unique_telegram_id))["game_id"]
    await client.post(f"/game/{game_id}/respond", json={"text": "хочу умереть"})
    await client.post(f"/game/{game_id}/finish")

    second = await client.post("/game/start", json={"telegram_id": unique_telegram_id})

    assert second.status_code == 402
    assert second.json()["crisis"] is True
    assert await _events(db_session, "paywall_shown", unique_telegram_id) == []


async def test_name_and_username_are_not_stored(client, db_session, unique_telegram_id):
    await client.post(
        "/game/start",
        json={"telegram_id": unique_telegram_id, "username": "alice", "first_name": "Алиса"},
    )

    user = await _user(db_session, unique_telegram_id)
    assert user.username is None
    assert user.first_name is None


async def _events(db_session, name, telegram_id=None):
    from src.db.models import Event

    query = select(Event).where(Event.name == name).order_by(Event.id)
    if telegram_id is not None:
        user = await _user(db_session, telegram_id)
        query = query.where(Event.user_id == user.id)
    return list((await db_session.execute(query)).scalars())
