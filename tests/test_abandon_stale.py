from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core.board import get_default_board
from src.db.models import Event
from src.db.repositories import GameRepository, UserRepository
from src.services.game_service import GameService


async def test_abandon_stale_games_marks_inactive_games_and_logs_event(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    games = GameRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(unique_telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)
    await games.set_intention(game, "тест")

    # Simulate 49h of inactivity — explicit assignment overrides the onupdate=func.now() default.
    game.updated_at = datetime.now(UTC) - timedelta(hours=49)
    await db_session.flush()

    service = GameService(db_session)
    count = await service.abandon_stale_games(hours=48)

    assert count == 1
    assert game.status == "abandoned"

    result = await db_session.execute(select(Event).where(Event.name == "game_abandoned", Event.game_id == game.id))
    event = result.scalar_one()
    assert event.game_id == game.id
    assert event.user_id == user.id


async def test_abandon_stale_games_leaves_recently_active_games_alone(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    games = GameRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(unique_telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)
    await games.set_intention(game, "тест")  # updated_at defaults to now()

    service = GameService(db_session)
    count = await service.abandon_stale_games(hours=48)

    assert count == 0
    assert game.status == "active"
