from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient, PaymentRequiredError
from src.bot.states import GameStates
from src.bot.utils import callback_message, current_game_id, esc, typing_indicator
from src.config import get_settings

router = Router(name="start")

# Process-local, best-effort guard against a fast double-tap on "Начать партию" / gender choice /
# "Да, начать заново" while the previous tap's API call (start_game, finish_game) is still in
# flight — without it, each extra tap re-runs _begin_game() and, once free/paid games are used
# up, sends its own separate paywall message with its own payment link. Not persisted — a restart
# only loses the guard for taps already in flight, acceptable for MVP (docs/PLAN.md §7.2).
_busy_telegram_ids: set[int] = set()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        texts.WELCOME_SHORT + texts.WELCOME_DISCLAIMER, reply_markup=keyboards.start_game_kb()
    )


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    url = get_settings().privacy_url
    await message.answer(texts.document_text("Политика конфиденциальности", url))


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    url = get_settings().terms_url
    await message.answer(texts.document_text("Оферта и условия игры", url))


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(texts.paysupport_text(message.chat.id, get_settings().support_contact))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    settings = get_settings()
    await message.answer(texts.about_game_text(settings.price_second_game_rub))


@router.callback_query(F.data == "about_game")
async def on_about_game(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    settings = get_settings()
    await msg.answer(
        texts.about_game_text(settings.price_second_game_rub), reply_markup=keyboards.start_game_kb()
    )


async def _begin_game(msg: Message, state: FSMContext, api: GameApiClient, telegram_id: int) -> None:
    """Shared by "Начать партию" and the confirmed "Новая игра" flow — both end up doing exactly
    the same thing: ask the Game API for a game (it resumes one in progress, or starts a fresh
    one, hitting the paywall on a second free attempt) and render whatever it gives back."""
    try:
        result = await api.start_game(telegram_id=telegram_id)
    except PaymentRequiredError as exc:
        if exc.crisis:
            await msg.answer(texts.CRISIS_PAYWALL_MESSAGE)
            return
        text = texts.PAYWALL_MESSAGE.format(price=exc.price)
        await msg.answer(text, reply_markup=keyboards.paywall_kb(exc.pay_url))
        return
    except ApiError:
        await msg.answer(texts.GUIDE_THINKING_ERROR, reply_markup=keyboards.start_game_kb())
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

    telegram_id = callback.from_user.id
    if telegram_id in _busy_telegram_ids:
        return
    _busy_telegram_ids.add(telegram_id)
    try:
        # Ask once per user, right at the moment they actually mean to play, rather than on every
        # /start — gender isn't needed until there's text to address them with.
        try:
            status = await api.get_user_status(telegram_id)
        except ApiError:
            status = None
        if status is not None and status.gender is None:
            await msg.answer(texts.ASK_GENDER_TEXT, reply_markup=keyboards.gender_kb())
            return

        await _begin_game(msg, state, api, telegram_id)
    finally:
        _busy_telegram_ids.discard(telegram_id)


@router.callback_query(F.data.startswith("set_gender:"))
async def on_set_gender(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    telegram_id = callback.from_user.id
    if telegram_id in _busy_telegram_ids:
        return
    _busy_telegram_ids.add(telegram_id)
    try:
        gender = (callback.data or "").split(":", 1)[1]
        try:
            await api.set_gender(telegram_id, gender)
        except ApiError:
            pass  # not worth blocking the player over this not saving — worst case, asked again
        await _begin_game(msg, state, api, telegram_id)
    finally:
        _busy_telegram_ids.discard(telegram_id)


async def submit_intention(
    message: Message, state: FSMContext, api: GameApiClient, game_id: str
) -> None:
    intention = (message.text or "").strip()[: texts.MAX_PLAYER_MESSAGE_LENGTH]
    if not intention:
        await message.answer(texts.ASK_INTENTION_AGAIN)
        return

    async with typing_indicator(message.bot, message.chat.id):
        try:
            result = await api.set_intention(game_id, intention)
        except ApiError:
            # Stays in waiting_intention, so simply sending the request again retries this.
            await message.answer(texts.GUIDE_THINKING_ERROR)
            return
    if result.safety_flag:
        # The request wasn't saved — stay in waiting_intention, the next text is a new request.
        await message.answer(esc(result.guide_message) + texts.SAFETY_HELP_LINE)
        return
    await state.set_state(None)
    await message.answer(esc(result.guide_message), reply_markup=keyboards.roll_kb())


# Commands are excluded so /newgame and /grant, registered later, aren't swallowed as the request.
@router.message(GameStates.waiting_intention, F.text, ~F.text.startswith("/"))
async def on_intention(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    await submit_intention(message, state, api, data["game_id"])


@router.message(GameStates.waiting_intention, ~F.text)
async def on_non_text_intention(message: Message) -> None:
    await message.answer(texts.ASK_INTENTION_AGAIN)


@router.message(Command("newgame"))
async def cmd_new_game(message: Message, state: FSMContext, api: GameApiClient) -> None:
    try:
        game_id = await current_game_id(state, api, message.chat.id)
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR)
        return
    if game_id is None:
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

    telegram_id = callback.from_user.id
    if telegram_id in _busy_telegram_ids:
        return
    _busy_telegram_ids.add(telegram_id)
    try:
        try:
            game_id = await current_game_id(state, api, telegram_id)
            if game_id is not None:
                await api.finish_game(game_id)
        except ApiError:
            pass  # already finished/invalid — fine, we're about to replace it anyway

        await state.clear()
        await _begin_game(msg, state, api, telegram_id)
    finally:
        _busy_telegram_ids.discard(telegram_id)


@router.callback_query(F.data == "cancel_new_game")
async def on_cancel_new_game(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    await msg.answer(texts.CANCEL_NEW_GAME_ACK, reply_markup=keyboards.roll_kb())
