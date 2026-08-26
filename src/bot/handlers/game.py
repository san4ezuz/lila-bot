from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient
from src.bot.states import GameStates
from src.bot.utils import callback_message
from src.config import get_settings

router = Router(name="game")


@router.callback_query(F.data == "roll")
async def on_roll(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    data = await state.get_data()
    game_id = data["game_id"]

    try:
        result = await api.roll(game_id, idempotency_key=callback.id)
    except ApiError:
        await msg.answer(texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.roll_kb())
        return

    settings = get_settings()
    progress = texts.PROGRESS_LINE.format(
        position=result.position if result.position is not None else "—",
        turn=result.turn,
        turn_limit=settings.turn_limit,
    )
    body = f"{progress}\n\n{result.guide_message}"

    if result.finished:
        finish = await api.finish_game(game_id)
        await msg.answer(body)
        await msg.answer(finish.summary + texts.FINISHED_SUFFIX)
        # FINISHED_SUFFIX ends with the one-word feedback question — the next text message in
        # this chat is the answer to that, not a reply to a cell (see on_feedback below).
        await state.set_state(GameStates.waiting_feedback)
        return

    if result.extra_roll:
        await msg.answer(body, reply_markup=keyboards.roll_kb())
        return

    if result.cell is not None:
        await msg.answer(f"{body}\n\n{texts.ASK_FOR_TEXT_RESPONSE}", reply_markup=keyboards.response_kb())
    else:
        await msg.answer(body, reply_markup=keyboards.roll_kb())


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

    if game_id is not None and text:
        try:
            await api.feedback(game_id, text)
        except ApiError:
            pass  # not worth blocking the player over an analytics event failing to log

    await state.clear()
    await message.answer(texts.FEEDBACK_THANKS, reply_markup=keyboards.start_game_kb())


@router.message(StateFilter(None), F.text)
async def on_player_response(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    game_id = data.get("game_id")
    if game_id is None:
        return  # no active game in this chat yet — nothing to respond to

    text = (message.text or "").strip()
    if len(text) > texts.MAX_PLAYER_MESSAGE_LENGTH:
        await message.answer(texts.MESSAGE_TOO_LONG)
        return

    try:
        result = await api.respond(game_id, text)
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.roll_kb())
        return

    body = result.guide_message
    if result.safety_flag:
        body += texts.SAFETY_HELP_LINE
    await message.answer(body, reply_markup=keyboards.roll_kb())
