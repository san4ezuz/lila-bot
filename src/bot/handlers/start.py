from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient, PaymentRequiredError
from src.bot.states import GameStates
from src.bot.utils import callback_message, current_game_id, esc, track, typing_indicator
from src.config import get_settings

router = Router(name="start")

# Process-local, best-effort guard against a fast double-tap on "Начать партию" / gender choice /
# "Да, начать заново" while the previous tap's API call (start_game, finish_game) is still in
# flight — without it, each extra tap re-runs _begin_game() and, once free/paid games are used
# up, sends its own separate paywall message with its own payment link. Not persisted — a restart
# only loses the guard for taps already in flight, acceptable for MVP (docs/PLAN.md §7.2).
_busy_telegram_ids: set[int] = set()


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, api: GameApiClient, command: CommandObject
) -> None:
    # `t.me/<bot>?start=<source>` arrives as "/start <source>" — the traffic source.
    track(api, message.chat.id, "bot_start", source=command.args)
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
async def cmd_help(message: Message, api: GameApiClient) -> None:
    track(api, message.chat.id, "about_opened", via="command")
    settings = get_settings()
    await message.answer(texts.about_game_text(settings.price_second_game_rub))


@router.callback_query(F.data == "about_game")
async def on_about_game(callback: CallbackQuery, api: GameApiClient) -> None:
    await callback.answer()
    track(api, callback.from_user.id, "about_opened", via="button")
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
        await msg.answer(esc(result.guide_message), reply_markup=keyboards.intention_kb())
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
    track(api, telegram_id, "start_clicked")
    try:
        # No gender question on the way in (docs/ROADMAP.md, этап 3): static texts are neutral
        # and the guide reads the form of address from the player's own words.
        await _begin_game(msg, state, api, telegram_id)
    finally:
        _busy_telegram_ids.discard(telegram_id)


# Old chats still have the gender buttons from before the question was dropped.
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
        track(api, telegram_id, "gender_set", gender=gender)
        try:
            await api.set_gender(telegram_id, gender)
        except ApiError:
            pass  # not worth blocking the player over this not saving — worst case, asked again
        await _begin_game(msg, state, api, telegram_id)
    finally:
        _busy_telegram_ids.discard(telegram_id)


async def submit_intention(
    message: Message,
    state: FSMContext,
    api: GameApiClient,
    game_id: str,
    intention: str | None = None,
) -> None:
    """`intention` — a preset picked with a button; otherwise the request is `message.text`."""
    if intention is None:
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


_PRESET_INTENTIONS = {key: text for key, _, text in texts.INTENTION_PRESETS}


@router.callback_query(F.data.startswith("intention:"))
async def on_intention_preset(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    key = (callback.data or "").split(":", 1)[1]
    try:
        game_id = await current_game_id(state, api, callback.from_user.id)
    except ApiError:
        await msg.answer(texts.GUIDE_THINKING_ERROR)
        return
    if game_id is None or await state.get_state() != GameStates.waiting_intention.state:
        return  # a stale button: the request is already set, or there's no game

    if key not in _PRESET_INTENTIONS:  # "Свой вариант"
        await msg.answer(texts.ASK_OWN_INTENTION)
        return
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await submit_intention(msg, state, api, game_id, intention=_PRESET_INTENTIONS[key])


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
