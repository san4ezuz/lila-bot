"""What the guide sees (этап 3): the dialogue on a cell for deepen, the player's own words and
reactions for the summary, a repeat visit to a cell, and the quick reactions themselves."""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.api.deps import get_db
from src.api.main import app
from src.config import get_settings
from src.core.models import MoveResult
from src.db.models import Event
from src.llm.client import LlmResult
from src.services.game_service import GameService


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


@pytest.fixture
def contexts(monkeypatch) -> dict[str, list[str]]:
    """Every context the guide is called with, by kind."""
    seen: dict[str, list[str]] = {}

    async def _capture(context, max_tokens, effort, fallback, kind, use_llm=True):
        seen.setdefault(kind, []).append(context)
        return LlmResult(text=f"ответ ведущего ({kind})", kind=kind, model="stub")

    monkeypatch.setattr("src.llm.guide._call", _capture)
    return seen


async def _game_on_a_cell(client, telegram_id) -> str:
    start = await client.post(
        "/game/start", json={"telegram_id": telegram_id, "intention": "застой — хочется сдвинуться"}
    )
    game_id = start.json()["game_id"]
    while (await client.post(f"/game/{game_id}/roll")).json()["position"] is None:
        pass
    return game_id


async def test_deepen_sees_its_own_question_and_the_dialogue(client, unique_telegram_id, contexts):
    game_id = await _game_on_a_cell(client, unique_telegram_id)

    await client.post(f"/game/{game_id}/respond", json={"text": "первый ответ"})
    await client.post(f"/game/{game_id}/respond", json={"text": "второй ответ"})

    first, second = contexts["deepen"]
    assert "Разговор на этой клетке" in first
    # Entry on cell 6 isn't interpreted on a natural first-try six, so only check the speaker.
    assert "Ты: «" in first
    assert "первый ответ" in first.split("Ответ игрока на твоё последнее сообщение")[1]
    # The second reply sees the first exchange as history.
    history = second.split("Ответ игрока на твоё последнее сообщение")[0]
    assert "Игрок: «первый ответ»" in history
    assert "Ты: «ответ ведущего (deepen)»" in history
    assert "второй ответ" not in history


async def test_summary_sees_answers_and_reactions(client, unique_telegram_id, contexts):
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    await client.post(f"/game/{game_id}/respond", json={"text": "боюсь начинать новое"})
    reaction = await client.post(f"/game/{game_id}/reaction", json={"reaction": "yes"})
    assert reaction.status_code == 200

    await client.post(f"/game/{game_id}/finish")

    (summary,) = contexts["summary"]
    assert "«боюсь начинать новое»" in summary
    assert "отметил как откликнувшиеся: 6 Заблуждение" in summary


async def test_reaction_rejects_unknown_values(client, unique_telegram_id):
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    resp = await client.post(f"/game/{game_id}/reaction", json={"reaction": "maybe"})
    assert resp.status_code == 422


async def test_reaction_is_logged_with_the_cell(client, db_session, unique_telegram_id):
    game_id = await _game_on_a_cell(client, unique_telegram_id)
    await client.post(f"/game/{game_id}/reaction", json={"reaction": "no"})

    result = await db_session.execute(select(Event).where(Event.name == "cell_reaction"))
    event = result.scalars().all()[-1]
    assert event.props["reaction"] == "no"
    assert event.props["position"] == 6


async def test_returning_to_a_cell_is_marked_as_a_revisit(db_session, unique_telegram_id, contexts):
    service = GameService(db_session)
    game, _ = await service.start_game(unique_telegram_id, "тест")
    game.in_play, game.position, game.turn = True, 8, 5
    # The player stood on 9 earlier in this game.
    await service.moves.create(
        game.id,
        MoveResult(
            turn=3, roll=3, from_position=6, raw_position=9, final_position=9,
            transition=None, event="moved", extra_roll=False, finished=False,
        ),
    )
    await db_session.flush()
    service._pick_roll = lambda game: 1  # type: ignore[method-assign]

    result, _, _ = await service.roll(game)

    assert result.final_position == 9
    assert "уже стоял на этой клетке" in contexts["interpret_cell"][-1]


async def test_first_landing_is_not_a_revisit(client, unique_telegram_id, contexts):
    await _game_on_a_cell(client, unique_telegram_id)
    assert all("уже стоял на этой клетке" not in c for c in contexts.get("interpret_cell", []))


def test_summary_path_shows_snakes_and_arrows(db_session):
    service = GameService(db_session)
    moves = [
        _move("entered", None, 6, 6, None),
        _move("moved", 6, 12, 8, "snake"),
        _move("blocked_overshoot", 8, 80, 8, None),
        _move("moved", 8, 10, 23, "arrow"),
    ]
    name = {n: f"{n} {service.board.get_cell(n).name}" for n in (6, 8, 10, 12, 23)}
    assert service._summary_path(moves) == [  # type: ignore[arg-type]
        name[6],
        f"{name[12]} —змея→ {name[8]}",
        f"{name[10]} —стрела→ {name[23]}",
    ]


def _move(event, from_pos, raw_pos, to_pos, transition):
    from types import SimpleNamespace

    return SimpleNamespace(
        event=event, from_pos=from_pos, raw_pos=raw_pos, to_pos=to_pos, transition=transition
    )
