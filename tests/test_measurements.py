"""Этап 2: LLM call accounting, LLM limits, funnel events, /admin/stats and admin alerts."""

import asyncio

import httpx
import pytest_asyncio
from sqlalchemy import select

from src.api.deps import get_db
from src.api.main import app
from src.bot.handlers.admin import stats_text
from src.config import get_settings
from src.db.models import Event, LlmCall, User
from src.llm.client import LlmResult
from src.services import alerts
from src.services.billing_service import BillingService


@pytest_asyncio.fixture
async def client(db_session):
    async def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Token": get_settings().api_internal_token},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def _user(db_session, telegram_id) -> User:
    result = await db_session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one()


async def _events(db_session, name):
    result = await db_session.execute(select(Event).where(Event.name == name).order_by(Event.id))
    return list(result.scalars())


async def _game_on_a_cell(client, telegram_id) -> str:
    start = await client.post("/game/start", json={"telegram_id": telegram_id, "intention": "тест"})
    game_id = start.json()["game_id"]
    while (await client.post(f"/game/{game_id}/roll")).json()["position"] is None:
        pass
    return game_id


# --- llm_calls ----------------------------------------------------------------------------


async def test_llm_calls_are_recorded_with_tokens(client, db_session, unique_telegram_id):
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    await client.post(f"/game/{game_id}/respond", json={"text": "про отношения"})

    calls = list((await db_session.execute(select(LlmCall).order_by(LlmCall.id))).scalars())
    kinds = {call.kind for call in calls}
    assert {"deepen", "classifier"} <= kinds
    deepen = next(call for call in calls if call.kind == "deepen")
    assert deepen.input_tokens == 100
    assert deepen.output_tokens == 20
    assert deepen.is_fallback is False
    assert str(deepen.game_id) == game_id


# --- limits -------------------------------------------------------------------------------


async def test_reply_limit_per_cell_stops_the_llm(client, db_session, unique_telegram_id, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_replies_per_cell", 2)
    game_id = await _game_on_a_cell(client, unique_telegram_id)

    replies = [
        (await client.post(f"/game/{game_id}/respond", json={"text": f"ответ {n}"})).json()
        for n in range(3)
    ]

    assert replies[0]["guide_message"].startswith("[stub")
    assert replies[1]["guide_message"].startswith("[stub")
    assert "Бросай кубик" in replies[2]["guide_message"]
    assert replies[2]["can_roll"] is True
    hits = await _events(db_session, "llm_limit_hit")
    assert [hit.props["limit"] for hit in hits] == ["cell"]

    # A new move opens a new cell — and resets the count.
    await client.post(f"/game/{game_id}/roll")
    again = await client.post(f"/game/{game_id}/respond", json={"text": "снова"})
    assert again.json()["guide_message"].startswith("[stub")


async def test_daily_user_limit_falls_back_without_a_call(client, db_session, unique_telegram_id, monkeypatch):
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    monkeypatch.setattr(get_settings(), "llm_calls_per_user_per_day", 0)
    before = len(list((await db_session.execute(select(LlmCall.id))).scalars()))

    reply = await client.post(f"/game/{game_id}/respond", json={"text": "ответ"})

    assert not reply.json()["guide_message"].startswith("[stub")
    after = list((await db_session.execute(select(LlmCall).where(LlmCall.id > 0))).scalars())
    # Only the safety classifier ran: it is never limited.
    assert [call.kind for call in after[before:]] == ["classifier"]
    assert (await _events(db_session, "llm_limit_hit"))[-1].props["limit"] == "user"


# --- funnel -------------------------------------------------------------------------------


async def test_bot_start_records_the_first_source_only(client, db_session, unique_telegram_id):
    for source in ("tg_ads", "blogger_1"):
        resp = await client.post(
            "/events",
            json={"telegram_id": unique_telegram_id, "name": "bot_start", "props": {"source": source}},
        )
        assert resp.status_code == 200

    user = await _user(db_session, unique_telegram_id)
    assert user.source == "tg_ads"
    starts = await _events(db_session, "bot_start")
    assert [event.props["source"] for event in starts] == ["tg_ads", "blogger_1"]


async def test_bad_source_and_unknown_event_are_rejected(client, db_session, unique_telegram_id):
    await client.post(
        "/events",
        json={"telegram_id": unique_telegram_id, "name": "bot_start", "props": {"source": "<b>x</b>"}},
    )
    assert (await _user(db_session, unique_telegram_id)).source is None

    unknown = await client.post(
        "/events", json={"telegram_id": unique_telegram_id, "name": "payment_succeeded"}
    )
    assert unknown.status_code == 422


async def test_stats_count_the_funnel_and_keep_grants_out_of_revenue(client, db_session, unique_telegram_id):
    # Deltas, not absolutes: the test database may hold rows a past run committed.
    before = (await client.get("/admin/stats", params={"days": 1})).json()
    await client.post(
        "/events",
        json={"telegram_id": unique_telegram_id, "name": "bot_start", "props": {"source": "tg_ads"}},
    )
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    await client.post(f"/game/{game_id}/finish")
    billing = BillingService(db_session)
    await billing.grant_manual(unique_telegram_id, 2)
    await billing.process_tribute_payment(unique_telegram_id, "p-1", 299.0, "RUB", {})

    stats = (await client.get("/admin/stats", params={"days": 1})).json()
    delta = {key: stats[key] - before[key] for key in stats if isinstance(stats[key], int)}

    assert ["tg_ads", 1] in stats["top_sources"]
    assert delta["bot_starts"] == 1
    assert delta["games_started_free"] == 1
    assert delta["intentions_set"] == 1
    assert delta["entered"] == 1
    assert delta["finished_other"] == 1
    assert delta["payments"] == 1
    assert delta["revenue"] == 299
    assert delta["admin_grants"] == 1
    assert delta["llm_calls"] >= 2
    assert f"Оплаты: {stats['payments']} / {stats['payments']}" in stats_text(
        _stats_model(stats), _stats_model(stats)
    )


def _stats_model(raw):
    from src.api.schemas import StatsResponse

    return StatsResponse.model_validate(raw)


# --- alerts -------------------------------------------------------------------------------


def _capture_alerts(monkeypatch) -> list[str]:
    sent: list[str] = []

    async def _fake_send(telegram_id, text, reply_markup=None):
        sent.append(text)
        return True

    monkeypatch.setattr("src.services.alerts.notify.send_message", _fake_send)
    monkeypatch.setattr(get_settings(), "admin_telegram_ids_raw", "42")
    return sent


def _failed(error: str) -> LlmResult:
    return LlmResult(text="fallback", kind="deepen", model="claude-x", fallback=True, error=error)


async def test_critical_llm_error_alerts_at_once_and_is_throttled(monkeypatch):
    sent = _capture_alerts(monkeypatch)

    alerts.note_llm_result(_failed("NotFoundError: model: claude-haiku-5.5"))
    alerts.note_llm_result(_failed("NotFoundError: model: claude-haiku-5.5"))
    await asyncio.sleep(0)

    assert len(sent) == 1
    assert "NotFoundError" in sent[0]


async def test_single_fallbacks_alert_only_past_the_threshold(monkeypatch):
    sent = _capture_alerts(monkeypatch)

    for _ in range(4):
        alerts.note_llm_result(_failed("timeout"))
    await asyncio.sleep(0)
    assert sent == []

    alerts.note_llm_result(_failed("timeout"))
    await asyncio.sleep(0)
    assert len(sent) == 1


async def test_ok_and_skipped_results_never_alert(monkeypatch):
    sent = _capture_alerts(monkeypatch)
    ok = LlmResult(text="ok", kind="deepen", model="m", stop_reason="end_turn")
    skipped = LlmResult(text="f", kind="deepen", model="m", fallback=True, skipped=True)

    for _ in range(10):
        alerts.note_llm_result(ok)
        alerts.note_llm_result(skipped)
    await asyncio.sleep(0)

    assert sent == []
