from src.core.board import get_default_board
from src.core.rules import apply_roll, create_initial_state
from src.db.repositories import (
    EventRepository,
    GameRepository,
    MessageRepository,
    MoveRepository,
    PaymentRepository,
    UserRepository,
)


async def test_get_or_create_user_is_idempotent(db_session, unique_telegram_id):
    repo = UserRepository(db_session)

    user1 = await repo.get_or_create(unique_telegram_id, username="alice")
    user2 = await repo.get_or_create(unique_telegram_id, username="alice-again")

    assert user1.id == user2.id
    assert user1.telegram_id == unique_telegram_id


async def test_free_game_flag_and_paid_game_balance(db_session, unique_telegram_id):
    repo = UserRepository(db_session)
    user = await repo.get_or_create(unique_telegram_id)

    assert user.has_free_game_used is False
    assert user.paid_games_available == 0

    await repo.mark_free_game_used(user)
    assert user.has_free_game_used is True

    await repo.grant_paid_game(user, count=2)
    assert user.paid_games_available == 2

    await repo.consume_paid_game(user)
    assert user.paid_games_available == 1


async def test_set_gender_persists_choice(db_session, unique_telegram_id):
    repo = UserRepository(db_session)
    user = await repo.get_or_create(unique_telegram_id)
    assert user.gender is None

    await repo.set_gender(user, "female")
    assert user.gender == "female"


async def test_game_lifecycle_and_state_sync(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    games = GameRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(unique_telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)
    assert game.status == "setup"

    await games.set_intention(game, "разобраться в текущем застое")
    assert game.status == "active"

    active = await games.get_active_for_user(user.id)
    assert active is not None
    assert active.id == game.id

    state = create_initial_state()
    state, _ = apply_roll(state, 6, board)
    await games.apply_state(game, state)

    assert game.position == 6
    assert game.in_play is True

    finished_state, _ = apply_roll(state, 3, board)  # not a finish, just advancing
    await games.apply_state(game, finished_state)
    assert game.status == "active"


async def test_moves_and_messages_are_recorded_and_ordered(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    games = GameRepository(db_session)
    moves = MoveRepository(db_session)
    messages = MessageRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(unique_telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)

    state = create_initial_state()
    state, result1 = apply_roll(state, 6, board)
    move1 = await moves.create(game.id, result1)
    state, result2 = apply_roll(state, 3, board)
    move2 = await moves.create(game.id, result2)

    stored_moves = await moves.list_for_game(game.id)
    assert [m.turn for m in stored_moves] == [1, 2]
    assert stored_moves[0].id == move1.id
    assert stored_moves[1].id == move2.id

    await messages.create(game.id, role="guide", content="Добро пожаловать.", move_id=move1.id)
    await messages.create(game.id, role="player", content="Спасибо.", move_id=move1.id)

    stored_messages = await messages.list_for_game(game.id)
    assert [m.role for m in stored_messages] == ["guide", "player"]


async def test_payment_external_id_is_unique_per_provider(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    payments = PaymentRepository(db_session)

    user = await users.get_or_create(unique_telegram_id)
    payment = await payments.create(
        user_id=user.id,
        provider="tribute",
        external_id="ext-123",
        amount=299.0,
        currency="RUB",
        raw_payload={"raw": True},
    )

    found = await payments.get_by_external_id("tribute", "ext-123")
    assert found is not None
    assert found.id == payment.id

    not_found = await payments.get_by_external_id("tribute", "does-not-exist")
    assert not_found is None


async def test_event_logging(db_session, unique_telegram_id):
    events = EventRepository(db_session)
    event = await events.log("game_started", user_id=unique_telegram_id, props={"is_paid": False})
    assert event.name == "game_started"
    assert event.props == {"is_paid": False}


async def test_find_stale_active_filters_by_last_activity_not_creation(db_session, unique_telegram_id):
    from datetime import UTC, datetime, timedelta

    users = UserRepository(db_session)
    games = GameRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(unique_telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)
    await games.set_intention(game, "тест")  # status -> active

    now = datetime.now(UTC)
    old_cutoff = now - timedelta(hours=1)
    future_cutoff = now + timedelta(hours=1)

    # Freshly active game must not be flagged stale against a cutoff in the past.
    assert game not in await games.find_stale_active(old_cutoff)
    # ...but is flagged once the cutoff is set after its last activity.
    stale = await games.find_stale_active(future_cutoff)
    assert game in stale

    updated_at_before_abandon = game.updated_at
    await games.mark_abandoned(game)
    assert game.status == "abandoned"
    assert game not in await games.find_stale_active(future_cutoff)  # no longer 'active'
    # mark_abandoned must not reset updated_at — see its docstring for why (it would silently
    # push out every reminder threshold computed from this same column).
    assert game.updated_at == updated_at_before_abandon
