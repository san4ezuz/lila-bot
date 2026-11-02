"""Handler-level tests against a fake Game API — the bot's failure paths (lost FSM state, the API
being unreachable, a stale button) can't be reached through the API contract tests, and these are
exactly the paths that used to leave a player facing silence."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from src.api.schemas import CellOut, FinishGameResponse, RollResponse, UserStatusResponse
from src.bot import texts
from src.bot.api_client import ApiError
from src.bot.handlers import game, start
from src.bot.states import GameStates

CHAT_ID = 777
GAME_ID = uuid.uuid4()
UNAVAILABLE = ApiError(503, "api_unavailable", "connection refused")


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))


def _message(text: str | None = "текст") -> MagicMock:
    message = MagicMock(spec=Message)
    # Message's reply helpers are plain methods returning awaitable API calls, so spec= alone
    # would make them non-awaitable.
    message.answer = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    message.text = text
    message.bot = None
    message.message_id = 10
    message.chat = Chat(id=CHAT_ID, type="private")
    return message


def _callback(data: str = "roll") -> AsyncMock:
    callback = AsyncMock()
    callback.data = data
    callback.bot = None
    callback.from_user = User(id=CHAT_ID, is_bot=False, first_name="Игрок")
    callback.message = _message()
    return callback


def _status(game_status: str | None = "active") -> UserStatusResponse:
    return UserStatusResponse(
        has_free_game_used=False,
        paid_games_available=0,
        active_game_id=GAME_ID if game_status else None,
        active_game_status=game_status,
    )


def _roll(event: str = "moved", **overrides) -> RollResponse:
    fields = {
        "roll": 3,
        "from_position": 6,
        "raw_position": 9,
        "position": 9,
        "transition": None,
        "event": event,
        "cell": CellOut(id=9, name="Клетка", theme="тема"),
        "guide_message": "трактовка",
        "finished": False,
        "turn": 3,
        "can_roll": True,
        "extra_roll": False,
    }
    return RollResponse(**{**fields, **overrides})


def _sent_text(message: MagicMock) -> str:
    return message.answer.await_args.args[0]


@pytest.fixture(autouse=True)
def _no_cell_images(monkeypatch):
    monkeypatch.setattr("src.bot.handlers.game.get_cell_image_path", lambda cell_id: None)


async def test_roll_recovers_game_after_fsm_state_was_lost(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status()
    api.roll.return_value = _roll()

    await game.on_roll(_callback(), state, api)

    assert api.roll.await_args.args[0] == str(GAME_ID)


async def test_double_tap_on_one_button_sends_the_same_idempotency_key(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status()
    api.roll.return_value = _roll()

    await game.on_roll(_callback(), state, api)
    await game.on_roll(_callback(), state, api)

    first, second = api.roll.await_args_list
    assert first.kwargs["idempotency_key"] == second.kwargs["idempotency_key"]


async def test_failed_roll_offers_a_retry_under_the_same_key(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status()
    api.roll.side_effect = UNAVAILABLE
    callback = _callback()

    await game.on_roll(callback, state, api)

    assert _sent_text(callback.message) == texts.GUIDE_THINKING_ERROR
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    retry_data = markup.inline_keyboard[0][0].callback_data
    assert retry_data == f"roll:{CHAT_ID}:10"

    api.roll.side_effect = None
    api.roll.return_value = _roll()
    await game.on_roll(_callback(retry_data), state, api)
    assert api.roll.await_args.kwargs["idempotency_key"] == f"{CHAT_ID}:10"


async def test_stale_button_on_finished_game_offers_a_fresh_start(state):
    await state.update_data(game_id=str(GAME_ID))
    api = AsyncMock()
    api.roll.side_effect = ApiError(409, "game_not_active", "game is not active")
    callback = _callback()

    await game.on_roll(callback, state, api)

    assert _sent_text(callback.message) == texts.WELCOME_SHORT
    assert await state.get_data() == {}


async def test_roll_with_no_game_offers_a_start(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status(None)
    callback = _callback()

    await game.on_roll(callback, state, api)

    api.roll.assert_not_awaited()
    assert _sent_text(callback.message) == texts.WELCOME_SHORT


async def test_burnt_roll_offers_the_next_roll_not_a_reply(state):
    await state.update_data(game_id=str(GAME_ID))
    api = AsyncMock()
    api.roll.return_value = _roll("blocked_overshoot")
    callback = _callback()

    await game.on_roll(callback, state, api)

    assert texts.ASK_FOR_TEXT_RESPONSE not in _sent_text(callback.message)
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == texts.BTN_ROLL


async def test_finish_failure_keeps_the_cell_and_offers_summary_retry(state):
    await state.update_data(game_id=str(GAME_ID))
    api = AsyncMock()
    api.roll.return_value = _roll("finished", finished=True, can_roll=False)
    api.finish_game.side_effect = UNAVAILABLE
    callback = _callback()

    await game.on_roll(callback, state, api)

    assert callback.message.answer.await_count == 2  # the final cell, then the retry offer
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"retry_summary:{GAME_ID}"

    api.finish_game.side_effect = None
    api.finish_game.return_value = FinishGameResponse(summary="итог", stats={})
    retry = _callback(f"retry_summary:{GAME_ID}")
    await game.on_retry_summary(retry, state, api)

    assert _sent_text(retry.message).startswith("итог")
    assert await state.get_state() == GameStates.waiting_feedback.state


async def test_text_after_lost_state_reaches_the_game(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status()
    api.respond.return_value = SimpleNamespace(guide_message="ответ", safety_flag=None)

    await game.on_player_response(_message("мой ответ"), state, api)

    api.respond.assert_awaited_once_with(str(GAME_ID), "мой ответ")


async def test_text_in_setup_game_after_lost_state_becomes_the_intention(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status("setup")
    api.set_intention.return_value = SimpleNamespace(guide_message="принято")

    await game.on_player_response(_message("мой запрос"), state, api)

    api.set_intention.assert_awaited_once_with(str(GAME_ID), "мой запрос")
    api.respond.assert_not_awaited()
    assert await state.get_state() is None


async def test_response_survives_unreachable_api(state):
    await state.update_data(game_id=str(GAME_ID))
    api = AsyncMock()
    api.respond.side_effect = UNAVAILABLE
    message = _message()

    await game.on_player_response(message, state, api)

    assert _sent_text(message) == texts.GUIDE_THINKING_ERROR


async def test_start_game_survives_unreachable_api(state):
    api = AsyncMock()
    api.start_game.side_effect = UNAVAILABLE
    message = _message()

    await start._begin_game(message, state, api, User(id=CHAT_ID, is_bot=False, first_name="И"))

    assert _sent_text(message) == texts.GUIDE_THINKING_ERROR


async def test_intention_stays_pending_when_api_is_unreachable(state):
    await state.set_state(GameStates.waiting_intention)
    await state.update_data(game_id=str(GAME_ID))
    api = AsyncMock()
    api.set_intention.side_effect = UNAVAILABLE

    await start.on_intention(_message("запрос"), state, api)

    assert await state.get_state() == GameStates.waiting_intention.state


async def test_new_game_after_lost_state_still_asks_to_confirm(state):
    api = AsyncMock()
    api.get_user_status.return_value = _status()
    message = _message("/newgame")

    await start.cmd_new_game(message, state, api)

    assert _sent_text(message) == texts.CONFIRM_NEW_GAME_TEXT
