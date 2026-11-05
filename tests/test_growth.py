"""Stage 4 (docs/ROADMAP.md): the credit ledger, invites, gifts, promo codes and share cards."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from sqlalchemy import select

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings
from src.db.models import CreditEntry, Event, Move, PromoCode
from src.services.card_service import card_path, key_cell
from src.services.credit_service import GIFT_OPENED_TEXT, REFERRAL_REWARD_TEXT


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


def _telegram_id() -> int:
    return uuid.uuid4().int % (2**31)


async def _balance(client, telegram_id: int) -> int:
    status = await client.get(f"/users/{telegram_id}/status")
    return status.json()["paid_games_available"]


async def _grant(client, telegram_id: int, count: int = 1) -> None:
    resp = await client.post("/admin/grant", json={"telegram_id": telegram_id, "count": count})
    assert resp.status_code == 200


async def _play_to_the_end(client, telegram_id: int) -> str:
    """A full game: rolls until the finish (the turn limit guarantees one)."""
    start = await client.post("/game/start", json={"telegram_id": telegram_id, "intention": "тест"})
    game_id = start.json()["game_id"]
    for _ in range(200):
        roll = await client.post(f"/game/{game_id}/roll")
        if roll.json()["finished"]:
            break
    assert roll.json()["finished"]
    finish = await client.post(f"/game/{game_id}/finish")
    assert finish.status_code == 200
    return game_id


async def _drain_notifications() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# --- the ledger -----------------------------------------------------------------------------


async def test_a_paid_game_spends_one_credit_from_the_ledger(client, db_session):
    telegram_id = _telegram_id()
    first = await client.post("/game/start", json={"telegram_id": telegram_id, "intention": "тест"})
    await client.post(f"/game/{first.json()['game_id']}/finish")
    await _grant(client, telegram_id, 2)

    second = await client.post("/game/start", json={"telegram_id": telegram_id})
    assert second.status_code == 200
    assert await _balance(client, telegram_id) == 1

    entries = (
        await db_session.execute(
            select(CreditEntry.delta, CreditEntry.reason).where(
                CreditEntry.game_id == uuid.UUID(second.json()["game_id"])
            )
        )
    ).all()
    assert entries == [(-1, "game")]


# --- invites --------------------------------------------------------------------------------


async def test_invited_player_gets_a_game_and_the_inviter_one_after_their_finish(
    client, sent_notifications
):
    inviter, friend = _telegram_id(), _telegram_id()
    code = (await client.post(f"/users/{inviter}/invite")).json()["ref_code"]

    joined = await client.post(f"/users/{friend}/start-link", json={"payload": f"ref_{code}"})
    assert joined.json() == {"kind": "referral", "outcome": "granted", "credits": 1}
    assert await _balance(client, friend) == 1
    assert await _balance(client, inviter) == 0

    await _play_to_the_end(client, friend)
    await _drain_notifications()
    assert await _balance(client, inviter) == 1
    assert (inviter, REFERRAL_REWARD_TEXT) in sent_notifications

    # A second finished game by the same friend pays nothing more.
    await _play_to_the_end(client, friend)
    assert await _balance(client, inviter) == 1


async def test_ending_the_game_early_does_not_pay_the_inviter(client):
    inviter, friend = _telegram_id(), _telegram_id()
    code = (await client.post(f"/users/{inviter}/invite")).json()["ref_code"]
    await client.post(f"/users/{friend}/start-link", json={"payload": f"ref_{code}"})

    start = await client.post("/game/start", json={"telegram_id": friend, "intention": "тест"})
    await client.post(f"/game/{start.json()['game_id']}/finish")  # player_ended
    assert await _balance(client, inviter) == 0


async def test_invite_link_counts_only_for_someone_new(client):
    inviter, player = _telegram_id(), _telegram_id()
    code = (await client.post(f"/users/{inviter}/invite")).json()["ref_code"]
    await client.post("/game/start", json={"telegram_id": player})

    late = await client.post(f"/users/{player}/start-link", json={"payload": f"ref_{code}"})
    assert late.json()["outcome"] == "not_new"
    own = await client.post(f"/users/{inviter}/start-link", json={"payload": f"ref_{code}"})
    assert own.json()["outcome"] == "own"
    unknown = await client.post(f"/users/{player}/start-link", json={"payload": "ref_nosuchcode"})
    assert unknown.json()["outcome"] == "invalid"
    assert await _balance(client, player) == 0


async def test_a_plain_source_is_not_a_link(client):
    resp = await client.post(f"/users/{_telegram_id()}/start-link", json={"payload": "telegram_ads"})
    assert resp.json()["kind"] is None


async def test_invite_code_is_stable(client):
    telegram_id = _telegram_id()
    first = (await client.post(f"/users/{telegram_id}/invite")).json()["ref_code"]
    second = (await client.post(f"/users/{telegram_id}/invite")).json()["ref_code"]
    assert first == second


# --- gifts ----------------------------------------------------------------------------------


async def test_gift_moves_one_credit_from_giver_to_recipient(client, sent_notifications):
    giver, recipient, latecomer = _telegram_id(), _telegram_id(), _telegram_id()
    assert (await client.post(f"/users/{giver}/gifts")).status_code == 409

    await _grant(client, giver)
    gift = await client.post(f"/users/{giver}/gifts")
    assert gift.status_code == 200
    code = gift.json()["code"]
    assert gift.json()["balance"] == 0

    own = await client.post(f"/users/{giver}/start-link", json={"payload": f"gift_{code}"})
    assert own.json()["outcome"] == "own"

    opened = await client.post(f"/users/{recipient}/start-link", json={"payload": f"gift_{code}"})
    assert opened.json() == {"kind": "gift", "outcome": "granted", "credits": 1}
    await _drain_notifications()
    assert (giver, GIFT_OPENED_TEXT) in sent_notifications

    again = await client.post(f"/users/{recipient}/start-link", json={"payload": f"gift_{code}"})
    assert again.json()["outcome"] == "granted"  # the same link again: still "it's yours"
    assert await _balance(client, recipient) == 1

    late = await client.post(f"/users/{latecomer}/start-link", json={"payload": f"gift_{code}"})
    assert late.json()["outcome"] == "already_used"
    assert await _balance(client, latecomer) == 0


# --- promo codes ----------------------------------------------------------------------------


async def test_promo_code_is_used_once_per_player_and_up_to_its_limit(client):
    code = f"T{uuid.uuid4().hex[:8]}".upper()
    created = await client.post(
        "/admin/promo", json={"code": code.lower(), "credits": 2, "max_uses": 1, "note": "блог"}
    )
    assert created.status_code == 200
    assert created.json()["code"] == code

    first, second = _telegram_id(), _telegram_id()
    redeemed = await client.post(f"/users/{first}/promo", json={"code": code.lower()})
    assert redeemed.json() == {"kind": "promo", "outcome": "granted", "credits": 2}
    assert await _balance(client, first) == 2

    repeat = await client.post(f"/users/{first}/start-link", json={"payload": f"promo_{code}"})
    assert repeat.json()["outcome"] == "already_used"

    other = await client.post(f"/users/{second}/promo", json={"code": code})
    assert other.json()["outcome"] == "exhausted"

    listed = (await client.get("/admin/promo")).json()
    assert {"code": code, "uses": 1}.items() <= next(p for p in listed if p["code"] == code).items()


async def test_expired_and_unknown_promo_codes(client, db_session):
    code = f"E{uuid.uuid4().hex[:8]}".upper()
    db_session.add(PromoCode(code=code, credits=1, expires_at=datetime.now(UTC) - timedelta(days=1)))
    await db_session.flush()
    telegram_id = _telegram_id()

    expired = await client.post(f"/users/{telegram_id}/promo", json={"code": code})
    assert expired.json()["outcome"] == "expired"
    unknown = await client.post(f"/users/{telegram_id}/promo", json={"code": "нет такого"})
    assert unknown.json()["outcome"] == "invalid"


async def test_bad_promo_code_is_rejected_by_admin_endpoint(client):
    resp = await client.post("/admin/promo", json={"code": "a b", "credits": 1})
    assert resp.status_code == 422


# --- share cards ----------------------------------------------------------------------------


async def test_card_is_for_a_finished_game_and_leaves_the_request_to_the_player(client):
    telegram_id = _telegram_id()
    start = await client.post("/game/start", json={"telegram_id": telegram_id, "intention": "тест"})
    game_id = start.json()["game_id"]
    assert (await client.get(f"/game/{game_id}/card")).status_code == 409

    game_id = await _play_to_the_end(client, telegram_id)
    card = (await client.get(f"/game/{game_id}/card")).json()
    assert card["path"][-1] == 68 or not card["reached_finish"]
    assert card["insight"] == card["key_cell"]["theme"]
    assert card["intention"] == "тест"  # the bot prints it only when asked
    assert card["ref_code"]

    saved = await client.post(
        f"/game/{game_id}/card",
        json={"format": "story", "with_intention": False, "file_id": "file-1"},
    )
    assert saved.status_code == 200
    # Re-rendering the same variant replaces its file_id rather than adding a card.
    await client.post(
        f"/game/{game_id}/card",
        json={"format": "story", "with_intention": False, "file_id": "file-2"},
    )
    cards = (await client.get(f"/users/{telegram_id}/cards")).json()
    assert cards["cards"] == [{"file_id": "file-2", "format": "story", "with_intention": False}]


def _move(turn: int, to_pos: int, raw_pos: int | None = None, transition: str | None = None) -> Move:
    return Move(
        turn=turn,
        roll=3,
        from_pos=None,
        raw_pos=raw_pos if raw_pos is not None else to_pos,
        to_pos=to_pos,
        transition=transition,
        event="moved",
    )


def _reaction(position: int, reaction: str = "yes") -> Event:
    return Event(name="cell_reaction", props={"reaction": reaction, "position": position})


def test_key_cell_prefers_the_players_mark_then_the_longest_arrow():
    moves = [
        _move(1, 6),
        _move(2, 32, raw_pos=20, transition="arrow"),
        _move(3, 45, raw_pos=55, transition="snake"),
        _move(4, 50),
    ]
    assert key_cell(moves, [_reaction(32), _reaction(45, "no")]) == 32
    assert key_cell(moves, []) == 20  # where the arrow started
    assert key_cell(moves[2:], []) == 55  # no arrow: the snake
    assert key_cell([_move(1, 6), _move(2, 9)], []) == 9


def test_card_path_includes_where_a_jump_started():
    path, jumps = card_path([_move(1, 6), _move(2, 32, raw_pos=20, transition="arrow")])
    assert path == [6, 20, 32]
    assert [(j.from_cell, j.to_cell, j.kind) for j in jumps] == [(20, 32, "arrow")]
