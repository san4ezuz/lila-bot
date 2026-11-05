"""Bot side of stage 4: the share card, deep-link outcomes, inline mode, the cell photo cache."""

import uuid
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendPhoto
from aiogram.types import (
    Chat,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    Message,
    PhotoSize,
    User,
)
from PIL import Image

from src.api.schemas import (
    CardDataResponse,
    CardJump,
    CellOut,
    StartLinkResponse,
    UserCardsResponse,
)
from src.bot import cell_images, texts
from src.bot.handlers import game, share, start
from src.bot.share_card import SIZES, render_card
from src.core.board import get_default_board

CHAT_ID = 777
GAME_ID = str(uuid.uuid4())


def _card_data(intention: str | None = "личный запрос") -> CardDataResponse:
    cell = get_default_board().get_cell(46)
    return CardDataResponse(
        game_id=uuid.UUID(GAME_ID),
        path=[6, 9, 20, 32, 46, 68],
        jumps=[CardJump(from_cell=20, to_cell=32, kind="arrow")],
        key_cell=CellOut(id=cell.id, name=cell.name, theme=cell.theme),
        insight=cell.theme,
        question=cell.questions[0],
        intention=intention,
        turns=21,
        reached_finish=True,
        ref_code="abcd2345",
    )


def _photo_message(file_id: str = "photo-file-id") -> MagicMock:
    sent = MagicMock(spec=Message)
    sent.photo = [PhotoSize(file_id=file_id, file_unique_id="u", width=1080, height=1920)]
    return sent


def _message(text: str = "текст") -> MagicMock:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock(return_value=_photo_message())
    message.edit_reply_markup = AsyncMock()
    message.text = text
    message.bot = None
    message.chat = Chat(id=CHAT_ID, type="private")
    return message


def _callback(data: str) -> AsyncMock:
    callback = AsyncMock()
    callback.data = data
    callback.bot = None
    callback.from_user = User(id=CHAT_ID, is_bot=False, first_name="Игрок")
    callback.message = _message()
    return callback


@pytest.mark.parametrize("format", ["story", "square"])
def test_card_renders_in_its_format(format):
    image = Image.open(BytesIO(render_card(_card_data(), format, False, "lila_bot")))
    assert image.format == "JPEG"
    assert image.size == SIZES[format]


def test_card_without_art_or_intention_still_renders(monkeypatch):
    monkeypatch.setattr("src.bot.share_card.get_cell_image_path", lambda cell_id: None)
    data = _card_data(intention=None).model_copy(update={"path": [], "jumps": []})
    assert render_card(data, "square", True, "")


def test_the_request_changes_the_card_only_when_asked():
    data = _card_data()
    without = render_card(data, "story", False, "lila_bot")
    assert without == render_card(data.model_copy(update={"intention": None}), "story", False, "lila_bot")
    assert render_card(data, "story", True, "lila_bot") != without


async def test_card_button_sends_the_card_and_saves_its_file_id():
    api = AsyncMock()
    api.card_data.return_value = _card_data()
    callback = _callback(f"card:square:{GAME_ID}:1")

    await share.on_card(callback, api)

    sent = callback.message.answer_photo.await_args
    assert sent.kwargs["caption"] == texts.CARD_CAPTION
    buttons = [b.text for row in sent.kwargs["reply_markup"].inline_keyboard for b in row]
    assert texts.BTN_CARD_REMOVE_INTENTION in buttons
    api.save_card.assert_awaited_once_with(GAME_ID, "square", True, "photo-file-id")


async def test_card_failure_says_so():
    api = AsyncMock()
    api.card_data.side_effect = share.ApiError(409, "game_not_active", "")
    callback = _callback(f"card:story:{GAME_ID}:0")

    await share.on_card(callback, api)

    callback.message.answer.assert_awaited_once_with(texts.CARD_FAILED)
    api.save_card.assert_not_awaited()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (StartLinkResponse(kind="gift", outcome="granted", credits=1), texts.LINK_OUTCOME_TEXTS[("gift", "granted")]),
        (StartLinkResponse(kind="promo", outcome="granted", credits=3), "Промокод принят: партий на балансе +3."),
        (StartLinkResponse(kind="referral", outcome="not_new"), None),
        (StartLinkResponse(kind=None), None),
    ],
)
def test_link_outcome_text(result, expected):
    assert share.link_outcome_text(result) == expected


async def test_start_with_an_invite_says_what_it_gave_before_the_welcome():
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))
    api = AsyncMock()
    api.start_link.return_value = StartLinkResponse(kind="referral", outcome="granted", credits=1)
    message = _message("/start ref_abcd2345")

    await start.cmd_start(message, state, api, CommandObject(command="start", args="ref_abcd2345"))

    api.start_link.assert_awaited_once_with(CHAT_ID, "ref_abcd2345")
    first, welcome = message.answer.await_args_list
    assert first.args[0] == texts.LINK_OUTCOME_TEXTS[("referral", "granted")]
    assert welcome.args[0].startswith(texts.WELCOME_SHORT)


async def test_gift_without_credits_explains_instead_of_offering():
    api = AsyncMock()
    api.get_user_status.return_value = MagicMock(paid_games_available=0)
    message = _message("/gift")

    await share.cmd_gift(message, api)

    message.answer.assert_awaited_once_with(texts.GIFT_NO_CREDITS)


async def test_inline_query_offers_the_cards_and_an_invite():
    api = AsyncMock()
    api.user_cards.return_value = UserCardsResponse.model_validate(
        {"cards": [{"file_id": "f1", "format": "story", "with_intention": False}], "ref_code": "abcd2345"}
    )
    query = AsyncMock()
    query.from_user = User(id=CHAT_ID, is_bot=False, first_name="Игрок")
    query.bot = None

    await share.on_inline_query(query, api)

    results = query.answer.await_args.args[0]
    assert isinstance(results[0], InlineQueryResultCachedPhoto)
    assert results[0].photo_file_id == "f1"
    assert "?start=ref_abcd2345" in results[0].caption
    assert isinstance(results[-1], InlineQueryResultArticle)
    assert query.answer.await_args.kwargs["is_personal"] is True


# --- the cell photo cache ----------------------------------------------------------------------


@pytest.fixture
def clean_photo_cache():
    cell_images._file_ids.clear()
    yield
    cell_images._file_ids.clear()


async def test_cell_photo_is_uploaded_once_then_sent_by_file_id(clean_photo_cache):
    target = _message()
    target.answer_photo = AsyncMock(return_value=_photo_message("cell-7"))

    await game._send_cell_message(target, 7, "текст")
    await game._send_cell_message(target, 7, "текст")

    first, second = target.answer_photo.await_args_list
    assert not isinstance(first.args[0], str)  # the file itself
    assert second.args[0] == "cell-7"


async def test_a_refused_file_id_is_uploaded_again(clean_photo_cache):
    cell_images._file_ids[7] = "stale"
    target = _message()
    refused = TelegramBadRequest(method=SendPhoto(chat_id=CHAT_ID, photo="stale"), message="bad")
    target.answer_photo = AsyncMock(side_effect=[refused, _photo_message("fresh")])

    await game._send_cell_message(target, 7, "текст")

    assert target.answer_photo.await_count == 2
    assert cell_images._file_ids[7] == "fresh"
