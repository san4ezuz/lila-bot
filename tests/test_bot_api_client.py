"""Exercises src.bot.api_client.GameApiClient against the real FastAPI app (via ASGITransport,
no real network) to verify the bot<->API contract end to end — the piece of Stage 6 that's
testable without a live Telegram bot token. Handlers themselves (src/bot/handlers/) need a real
BOT_TOKEN and manual verification per docs/PLAN.md §9, not covered here."""

import httpx
import pytest

from src.api.deps import get_db
from src.api.main import app
from src.bot.api_client import ApiError, GameApiClient, PaymentRequiredError
from src.config import get_settings


@pytest.fixture
async def api_client(db_session):
    async def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    settings = get_settings()
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Token": settings.api_internal_token},
    )
    client = GameApiClient(client=http_client)
    yield client
    await http_client.aclose()
    app.dependency_overrides.clear()


async def test_start_and_roll_through_client(api_client, unique_telegram_id):
    start = await api_client.start_game(unique_telegram_id, intention="проверка клиента")
    assert start.status == "active"

    entered = False
    for _ in range(50):
        roll = await api_client.roll(str(start.game_id))
        if roll.event == "entered":
            entered = True
            break
    assert entered

    state = await api_client.get_game(str(start.game_id))
    assert state.in_play is True


async def test_respond_returns_safety_flag(api_client, unique_telegram_id):
    start = await api_client.start_game(unique_telegram_id, intention="тест")
    result = await api_client.respond(str(start.game_id), "я не хочу жить дальше")
    assert result.safety_flag == "soft_referral"


async def test_paywall_raises_payment_required_error(api_client, unique_telegram_id):
    start = await api_client.start_game(unique_telegram_id, intention="тест")
    await api_client.finish_game(str(start.game_id))

    with pytest.raises(PaymentRequiredError) as exc_info:
        await api_client.start_game(unique_telegram_id)

    assert exc_info.value.price > 0
    assert exc_info.value.pay_url


async def test_unknown_game_raises_api_error(api_client):
    import uuid

    with pytest.raises(ApiError) as exc_info:
        await api_client.get_game(str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


async def test_roll_idempotency_key_is_forwarded(api_client, unique_telegram_id):
    start = await api_client.start_game(unique_telegram_id, intention="тест")
    game_id = str(start.game_id)

    r1 = await api_client.roll(game_id, idempotency_key="fixed-key")
    r2 = await api_client.roll(game_id, idempotency_key="fixed-key")
    assert r1 == r2

    state = await api_client.get_game(game_id)
    assert len(state.history) == 1


async def test_get_user_status_reflects_active_game(api_client, unique_telegram_id):
    status_before = await api_client.get_user_status(unique_telegram_id)
    assert status_before.active_game_id is None

    start = await api_client.start_game(unique_telegram_id, intention="тест")

    status_after = await api_client.get_user_status(unique_telegram_id)
    assert status_after.active_game_id == start.game_id
