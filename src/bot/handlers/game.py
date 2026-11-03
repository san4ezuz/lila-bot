from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

from src.api.schemas import CellOut
from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient
from src.bot.cell_images import get_cell_image_path
from src.bot.handlers.start import submit_intention
from src.bot.states import GameStates
from src.bot.utils import callback_message, current_game_id, esc, typing_indicator

router = Router(name="game")

# Telegram caption limit for photo messages is 1024 chars — well under our text messages' 4096,
# and a summary/interpretation can legitimately run longer. Rather than truncate (losing part of
# what the LLM said), send the image with no caption and follow with a normal text message.
_PHOTO_CAPTION_LIMIT = 1024


async def _send_cell_message(
    target: Message, cell_id: int | None, body: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Sends `body` as a plain text message, or — if data/cell_images/{cell_id}.* exists — as a
    photo with `body` as its caption (see src/bot/cell_images.py). Falls back to a plain message
    for any cell without art; most cells have none, this is additive, not a redesign."""
    image_path = get_cell_image_path(cell_id) if cell_id is not None else None
    if image_path is None:
        await target.answer(body, reply_markup=reply_markup)
        return

    photo = FSInputFile(image_path)
    if len(body) <= _PHOTO_CAPTION_LIMIT:
        await target.answer_photo(photo, caption=body, reply_markup=reply_markup)
    else:
        await target.answer_photo(photo)
        await target.answer(body, reply_markup=reply_markup)

# Games currently mid-roll, in this process — a lock against the exact failure mode a user
# reported: the LLM call behind a roll takes a few seconds, nothing in the UI signals that
# ("Бросить кубик" stays tappable), so an impatient second tap fires a second REAL roll before
# the first response has arrived, and both eventually land in a burst. The keyboard removal
# below closes this for a well-behaved client; this set is the backstop for a tap that lands in
# the gap between "user taps" and "our edit reaches Telegram".
_rolling_games: set[str] = set()


def _cell_header(cell: CellOut) -> str:
    return texts.CELL_HEADER.format(cell_id=cell.id, cell_name=esc(cell.name))


async def _on_game_gone(message: Message, state: FSMContext) -> None:
    """The API says this game can't continue (409) — e.g. a tap on an old button after the finish.
    Drop the stale id and offer a fresh start instead of an error that retrying can't fix."""
    await state.clear()
    await message.answer(texts.WELCOME_SHORT, reply_markup=keyboards.start_game_kb())


async def _send_summary(msg: Message, state: FSMContext, api: GameApiClient, game_id: str) -> None:
    async with typing_indicator(msg.bot, msg.chat.id):
        try:
            finish = await api.finish_game(game_id)
        except ApiError:
            await msg.answer(
                texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.retry_summary_kb(game_id)
            )
            return

    await msg.answer(esc(finish.summary) + texts.FINISHED_SUFFIX)
    # FINISHED_SUFFIX ends with the one-word feedback question — the next text message in
    # this chat is the answer to that, not a reply to a cell (see on_feedback below).
    await state.set_state(GameStates.waiting_feedback)


@router.callback_query(F.data.startswith("roll"))
async def on_roll(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    msg = callback_message(callback)
    if msg is None:
        await callback.answer()
        return

    try:
        game_id = await current_game_id(state, api, callback.from_user.id)
    except ApiError:
        await callback.answer(texts.GUIDE_THINKING_ERROR)
        return
    if game_id is None:
        await callback.answer()
        await msg.answer(texts.WELCOME_SHORT, reply_markup=keyboards.start_game_kb())
        return
    if await state.get_state() == GameStates.waiting_intention.state:
        await callback.answer()
        await msg.answer(texts.ASK_INTENTION_AGAIN)
        return

    if game_id in _rolling_games:
        await callback.answer("Проводник ещё обрабатывает предыдущий бросок — секунду.")
        return

    await callback.answer()
    # Remove the button immediately, before the (few-second) LLM call — nothing left to tap
    # again while we wait. Best-effort: an old/already-edited message can 400 here, harmless.
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    # Keyed on the button, not the tap: every tap on one button is the same roll. `callback.id`
    # would differ per tap and dedupe nothing. A retry button carries the key of the failed roll.
    idempotency_key = (callback.data or "").partition(":")[2] or f"{msg.chat.id}:{msg.message_id}"

    _rolling_games.add(game_id)
    try:
        async with typing_indicator(callback.bot, msg.chat.id):
            try:
                result = await api.roll(game_id, idempotency_key=idempotency_key)
            except ApiError as exc:
                if exc.status_code == 409:
                    await _on_game_gone(msg, state)
                else:
                    await msg.answer(
                        texts.GUIDE_THINKING_ERROR,
                        reply_markup=keyboards.roll_kb(idempotency_key),
                    )
                return
    finally:
        _rolling_games.discard(game_id)

    # No turn counter shown to the player on purpose — see docs/UX_ITERATION_1.md §1.
    cell_id = result.cell.id if result.cell is not None else None
    header = texts.move_header(
        result.roll, result.from_position, result.raw_position, result.position, result.transition, result.event
    )
    if result.cell is not None:
        body = f"{_cell_header(result.cell)}\n\n{esc(result.guide_message)}"
    else:
        body = esc(result.guide_message)
    if header is not None:
        body = f"{header}\n\n{body}"
    if result.crisis_mode:
        body += texts.SAFETY_HELP_REMINDER

    if result.finished:
        await _send_cell_message(msg, cell_id, body)
        await _send_summary(msg, state, api, game_id)
        return

    if result.extra_roll:
        await _send_cell_message(msg, cell_id, body, reply_markup=keyboards.roll_kb())
        return

    # A burnt roll landed nowhere new — there's no cell to reflect on, only the next roll.
    if result.cell is not None and result.event != "blocked_overshoot":
        await _send_cell_message(
            msg, cell_id, f"{body}\n\n{texts.ASK_FOR_TEXT_RESPONSE}", reply_markup=keyboards.response_kb()
        )
    else:
        await _send_cell_message(msg, cell_id, body, reply_markup=keyboards.roll_kb())


@router.callback_query(F.data.startswith("retry_summary:"))
async def on_retry_summary(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    await _send_summary(msg, state, api, (callback.data or "").split(":", 1)[1])


@router.callback_query(F.data == "skip_response")
async def on_skip_response(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    data = await state.get_data()
    game_id = data.get("game_id")
    if game_id is not None:
        try:
            await api.skip(game_id)
        except ApiError:
            pass  # not worth blocking the player over an analytics event failing to log

    await msg.answer(texts.SKIPPED_RESPONSE_ACK, reply_markup=keyboards.roll_kb())


@router.message(GameStates.waiting_feedback, F.text)
async def on_feedback(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    game_id = data.get("game_id")
    text = (message.text or "").strip()[: texts.MAX_PLAYER_MESSAGE_LENGTH]

    support_message = None
    if game_id is not None and text:
        try:
            ack = await api.feedback(game_id, text)
            support_message = ack.guide_message
        except ApiError:
            pass  # not worth blocking the player over an analytics event failing to log

    await state.clear()
    if support_message:
        await message.answer(
            esc(support_message) + texts.SAFETY_HELP_LINE, reply_markup=keyboards.start_game_kb()
        )
        return
    await message.answer(texts.FEEDBACK_THANKS, reply_markup=keyboards.start_game_kb())


@router.callback_query(F.data.startswith("continue_game:"))
async def on_continue_game(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    game_id = (callback.data or "").split(":", 1)[1]
    try:
        game_state = await api.get_game(game_id)
    except ApiError:
        await msg.answer(texts.GUIDE_THINKING_ERROR)
        return

    if game_state.finished:
        # Already finished (or someone else finished it) since the reminder was sent.
        await msg.answer(texts.WELCOME_SHORT, reply_markup=keyboards.start_game_kb())
        return

    await state.update_data(game_id=str(game_state.game_id))
    await state.set_state(None)
    cell_id = game_state.cell.id if game_state.cell is not None else None
    body = _cell_header(game_state.cell) if game_state.cell is not None else texts.WELCOME_BACK
    await _send_cell_message(msg, cell_id, body, reply_markup=keyboards.roll_kb())


@router.callback_query(F.data.startswith("snooze_reminder:"))
async def on_snooze_reminder(callback: CallbackQuery, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    game_id = (callback.data or "").split(":", 1)[1]
    try:
        await api.snooze_reminder(game_id)
    except ApiError:
        pass  # not worth failing the tap over an analytics-adjacent write
    await msg.answer(texts.REMINDER_SNOOZED_ACK)


@router.callback_query(F.data == "opt_out_reminders")
async def on_opt_out_reminders(callback: CallbackQuery, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    try:
        await api.opt_out_reminders(callback.from_user.id)
    except ApiError:
        pass
    await msg.answer(texts.REMINDER_OPTED_OUT_ACK)


@router.message(StateFilter(None), F.text)
async def on_player_response(message: Message, state: FSMContext, api: GameApiClient) -> None:
    try:
        game_id = await current_game_id(state, api, message.chat.id)
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR)
        return
    if game_id is None:
        # No active game in this chat — show the same entry point as /start rather than
        # silently ignoring the message (docs/UX_ITERATION_1.md §5).
        await message.answer(
            texts.WELCOME_SHORT,
            reply_markup=keyboards.start_game_kb(),
        )
        return

    # The lookup above found a game still waiting for its request — this text is that request.
    if await state.get_state() == GameStates.waiting_intention.state:
        await submit_intention(message, state, api, game_id)
        return

    text = (message.text or "").strip()
    if len(text) > texts.MAX_PLAYER_MESSAGE_LENGTH:
        await message.answer(texts.MESSAGE_TOO_LONG)
        return

    async with typing_indicator(message.bot, message.chat.id):
        try:
            result = await api.respond(game_id, text)
        except ApiError as exc:
            if exc.status_code == 409:
                await _on_game_gone(message, state)
            else:
                await message.answer(texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.roll_kb())
            return

    body = esc(result.guide_message)
    if result.safety_flag:
        body += texts.SAFETY_HELP_LINE
    elif result.crisis_mode:
        body += texts.SAFETY_HELP_REMINDER
    await message.answer(body, reply_markup=keyboards.roll_kb())


@router.message(StateFilter(None))
async def on_non_text_message(message: Message) -> None:
    await message.answer(texts.TEXT_ONLY_MESSAGE)
