from datetime import UTC, datetime, timedelta

import pytest

from src.core.board import get_default_board
from src.db.repositories import GameRepository, ReminderRepository, UserRepository
from src.services.reminder_service import ReminderService


@pytest.fixture(autouse=True)
def _no_telegram_notifications(monkeypatch):
    calls = []

    async def _fake_send(telegram_id, text, reply_markup=None):
        calls.append((telegram_id, text, reply_markup))
        return True

    monkeypatch.setattr("src.services.reminder_service.notify.send_message", _fake_send)
    return calls


async def _make_active_game(db_session, telegram_id, age: timedelta):
    users = UserRepository(db_session)
    games = GameRepository(db_session)
    board = get_default_board()

    user = await users.get_or_create(telegram_id)
    game = await games.create(user_id=user.id, board_version=board.version, is_paid=False)
    await games.set_intention(game, "тест")
    game.updated_at = datetime.now(UTC) - age
    await db_session.flush()
    return user, game


async def test_sends_stage_1_after_24h_and_records_stage(db_session, unique_telegram_id, _no_telegram_notifications):
    _, game = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))

    service = ReminderService(db_session)
    sent = await service.send_due_reminders()

    assert sent == 1
    assert len(_no_telegram_notifications) == 1
    telegram_id, _text, reply_markup = _no_telegram_notifications[0]
    assert telegram_id == unique_telegram_id
    assert reply_markup is not None

    reminders = ReminderRepository(db_session)
    reminder = await reminders.get_or_create(game.id)
    assert reminder.stage == 1


async def test_does_not_send_before_threshold(db_session, unique_telegram_id):
    await _make_active_game(db_session, unique_telegram_id, timedelta(hours=1))

    service = ReminderService(db_session)
    sent = await service.send_due_reminders()

    assert sent == 0


async def test_does_not_resend_same_stage_twice(db_session, unique_telegram_id, _no_telegram_notifications):
    await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))

    service = ReminderService(db_session)
    await service.send_due_reminders()
    sent_again = await service.send_due_reminders()

    assert sent_again == 0
    assert len(_no_telegram_notifications) == 1


async def test_progresses_to_stage_2_after_3_days(db_session, unique_telegram_id, _no_telegram_notifications):
    _, game = await _make_active_game(db_session, unique_telegram_id, timedelta(days=3, hours=1))

    service = ReminderService(db_session)
    await service.send_due_reminders()  # stage 0 -> 1
    sent = await service.send_due_reminders()  # stage 1 -> 2, same call since threshold already passed

    assert sent == 1
    reminders = ReminderRepository(db_session)
    reminder = await reminders.get_or_create(game.id)
    assert reminder.stage == 2


async def test_stops_after_stage_3(db_session, unique_telegram_id, _no_telegram_notifications):
    await _make_active_game(db_session, unique_telegram_id, timedelta(days=8))

    service = ReminderService(db_session)
    await service.send_due_reminders()  # -> 1
    await service.send_due_reminders()  # -> 2
    await service.send_due_reminders()  # -> 3
    sent = await service.send_due_reminders()  # nothing left to send

    assert sent == 0
    assert len(_no_telegram_notifications) == 3


async def test_opted_out_user_is_never_reminded(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    user, _ = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))
    await users.set_reminders_opt_out(user, True)

    service = ReminderService(db_session)
    sent = await service.send_due_reminders()

    assert sent == 0


async def test_snoozed_game_is_skipped_until_snooze_expires(db_session, unique_telegram_id):
    _, game = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))

    service = ReminderService(db_session)
    await service.snooze(game.id, hours=48)

    sent = await service.send_due_reminders()
    assert sent == 0


async def test_abandoned_games_are_still_eligible_for_reminders(db_session, unique_telegram_id, _no_telegram_notifications):
    games = GameRepository(db_session)
    _, game = await _make_active_game(db_session, unique_telegram_id, timedelta(days=4))
    await games.mark_abandoned(game)

    service = ReminderService(db_session)
    sent = await service.send_due_reminders()

    assert sent == 1


async def test_failed_delivery_does_not_advance_stage(db_session, unique_telegram_id, monkeypatch):
    """Regression test for a bug found via a live smoke test: a real 400 from Telegram (e.g. the
    bot has never talked to this chat) was silently swallowed and the reminder stage advanced
    anyway, meaning the player would never actually get notified and no retry would happen."""

    async def _fake_failed_send(telegram_id, text, reply_markup=None):
        return False

    monkeypatch.setattr("src.services.reminder_service.notify.send_message", _fake_failed_send)
    _, game = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))

    service = ReminderService(db_session)
    sent = await service.send_due_reminders()

    assert sent == 0
    reminders = ReminderRepository(db_session)
    reminder = await reminders.get_or_create(game.id)
    assert reminder.stage == 0  # still eligible for a retry on the next cron run


async def test_user_in_crisis_mode_is_not_reminded(db_session, unique_telegram_id):
    users = UserRepository(db_session)
    user, _ = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))
    await users.mark_crisis(user)

    sent = await ReminderService(db_session).send_due_reminders()

    assert sent == 0


async def test_crisis_mode_expires(db_session, unique_telegram_id):
    user, _ = await _make_active_game(db_session, unique_telegram_id, timedelta(hours=25))
    user.crisis_at = datetime.now(UTC) - timedelta(days=30)
    await db_session.flush()

    sent = await ReminderService(db_session).send_due_reminders()

    assert sent == 1
