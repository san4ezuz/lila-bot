from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import keyboards, texts
from src.bot.api_client import GameApiClient, PaymentRequiredError
from src.bot.states import GameStates
from src.bot.utils import callback_message

router = Router(name="start")

WELCOME = (
    "Здравствуй. Я проводник в игре Лила — путешествии от клетки к клетке, где выпавшее "
    "число и твои собственные ответы складываются в историю."
)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=keyboards.start_game_kb())


@router.callback_query(F.data == "start_game")
async def on_start_game(callback: CallbackQuery, state: FSMContext, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return

    user = callback.from_user
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
        await msg.answer(result.guide_message)
    else:
        await state.set_state(None)
        await msg.answer(result.guide_message, reply_markup=keyboards.roll_kb())


@router.message(GameStates.waiting_intention)
async def on_intention(message: Message, state: FSMContext, api: GameApiClient) -> None:
    data = await state.get_data()
    game_id = data["game_id"]
    intention = (message.text or "").strip()[: texts.MAX_PLAYER_MESSAGE_LENGTH]

    result = await api.set_intention(game_id, intention)
    await state.set_state(None)
    await message.answer(result.guide_message, reply_markup=keyboards.roll_kb())
