from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient
from src.config import get_settings

router = Router(name="game")


@router.callback_query(F.data == "roll")
async def on_roll(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    data = await state.get_data()
    game_id = data["game_id"]

    try:
        result = await api.roll(game_id, idempotency_key=callback.id)
    except ApiError:
        await callback.message.answer(texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.roll_kb())
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
        await callback.message.answer(body)
        await callback.message.answer(
            finish.summary + texts.FINISHED_SUFFIX, reply_markup=keyboards.start_game_kb()
        )
        return

    if result.extra_roll:
        await callback.message.answer(body, reply_markup=keyboards.roll_kb())
        return

    if result.cell is not None:
        await callback.message.answer(f"{body}\n\n{texts.ASK_FOR_TEXT_RESPONSE}", reply_markup=keyboards.response_kb())
    else:
        await callback.message.answer(body, reply_markup=keyboards.roll_kb())


@router.callback_query(F.data == "skip_response")
async def on_skip_response(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer(texts.SKIPPED_RESPONSE_ACK, reply_markup=keyboards.roll_kb())


@router.message(StateFilter(None), F.text)
async def on_player_response(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    game_id = data.get("game_id")
    if game_id is None:
        return  # no active game in this chat yet — nothing to respond to

    text = message.text.strip()
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
