from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient, PaymentRequiredError
from src.bot.states import GameStates
from src.bot.utils import callback_message, esc, typing_indicator

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.WELCOME_SHORT, reply_markup=keyboards.start_game_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.ABOUT_GAME_TEXT)


@router.callback_query(F.data == "about_game")
async def on_about_game(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    await msg.answer(texts.ABOUT_GAME_TEXT, reply_markup=keyboards.start_game_kb())


async def _begin_game(msg: Message, state: FSMContext, api: GameApiClient, user: User) -> None:
    """Shared by "Начать партию" and the confirmed "Новая игра" flow — both end up doing exactly
    the same thing: ask the Game API for a game (it resumes one in progress, or starts a fresh
    one, hitting the paywall on a second free attempt) and render whatever it gives back."""
    try:
        result = await api.start_game(
            telegram_id=user.id, username=user.username, first_name=user.first_name
        )
    except PaymentRequiredError as exc:
        text = texts.PAYWALL_MESSAGE.format(price=exc.price)
        await msg.answer(text, reply_markup=keyboards.paywall_kb(exc.pay_url))
        return

    await state.update_data(game_id=str(result.game_id))

    if result.status == "setup":
        await state.set_state(GameStates.waiting_intention)
        await msg.answer(esc(result.guide_message))
    else:
        await state.set_state(None)
        await msg.answer(esc(result.guide_message), reply_markup=keyboards.roll_kb())


@router.callback_query(F.data == "start_game")
async def on_start_game(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    await _begin_game(msg, state, api, callback.from_user)


@router.message(GameStates.waiting_intention)
async def on_intention(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    game_id = data["game_id"]
    intention = (message.text or "").strip()[: texts.MAX_PLAYER_MESSAGE_LENGTH]

    async with typing_indicator(message.bot, message.chat.id):
        result = await api.set_intention(game_id, intention)
    await state.set_state(None)
    await message.answer(esc(result.guide_message), reply_markup=keyboards.roll_kb())


@router.message(Command("newgame"))
async def cmd_new_game(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("game_id") is None:
        # Nothing to lose — just start one directly rather than asking to confirm a no-op.
        await message.answer(texts.WELCOME_SHORT, reply_markup=keyboards.start_game_kb())
        return
    await message.answer(texts.CONFIRM_NEW_GAME_TEXT, reply_markup=keyboards.confirm_new_game_kb())


@router.callback_query(F.data == "confirm_new_game")
async def on_confirm_new_game(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    data = await state.get_data()
    game_id = data.get("game_id")
    if game_id is not None:
        try:
            await api.finish_game(game_id)
        except ApiError:
            pass  # already finished/invalid — fine, we're about to replace it anyway

    await state.clear()
    await _begin_game(msg, state, api, callback.from_user)


@router.callback_query(F.data == "cancel_new_game")
async def on_cancel_new_game(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    await msg.answer(texts.CANCEL_NEW_GAME_ACK, reply_markup=keyboards.roll_kb())
