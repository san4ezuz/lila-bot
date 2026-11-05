"""Sharing (docs/ROADMAP.md, этап 4): the finished game's card, invites, gifts, promo codes and
inline mode — `@bot` in any chat offers the player's cards and an invite.

Inline mode must be switched on in BotFather (/setinline), and `inline_shared` is only reported
with inline feedback on (/setinlinefeedback)."""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultUnion,
    InputTextMessageContent,
    Message,
)

from src.api.schemas import CardFormat, StartLinkResponse
from src.bot import keyboards, texts
from src.bot.api_client import ApiError, GameApiClient
from src.bot.share_card import render_card
from src.bot.utils import callback_message, track

logger = logging.getLogger(__name__)

router = Router(name="share")


async def bot_username(bot: Bot | None) -> str:
    if bot is None:
        return ""
    return (await bot.me()).username or ""


def deep_link(username: str, payload: str) -> str:
    return f"https://t.me/{username}?start={payload}"


def link_outcome_text(result: StartLinkResponse) -> str | None:
    """What to tell the player about a deep link or a promo code; None — nothing worth saying
    (a plain traffic source, or an invite opened by someone who already plays)."""
    if result.kind is None or result.outcome is None:
        return None
    template = texts.LINK_OUTCOME_TEXTS.get((result.kind, result.outcome))
    return template.format(credits=result.credits) if template else None


# --- the card ---------------------------------------------------------------------------------


@router.callback_query(F.data.startswith("card:"))
async def on_card(callback: CallbackQuery, api: GameApiClient) -> None:
    msg = callback_message(callback)
    try:
        _, raw_format, game_id, flag = (callback.data or "").split(":")
    except ValueError:
        await callback.answer()
        return
    if msg is None or raw_format not in ("story", "square"):
        await callback.answer()
        return
    format: CardFormat = "story" if raw_format == "story" else "square"
    with_intention = flag == "1"
    await callback.answer()

    try:
        data = await api.card_data(game_id)
        username = await bot_username(callback.bot)
        image = await asyncio.to_thread(render_card, data, format, with_intention, username)
        sent = await msg.answer_photo(
            BufferedInputFile(image, filename=f"lila-{format}.jpg"),
            caption=texts.CARD_CAPTION,
            reply_markup=keyboards.card_kb(game_id, format, with_intention, bool(data.intention)),
        )
    except (ApiError, TelegramAPIError) as exc:
        logger.warning("share card for %s failed: %s", game_id, exc)
        await msg.answer(texts.CARD_FAILED)
        return

    if sent.photo:
        try:
            await api.save_card(game_id, format, with_intention, sent.photo[-1].file_id)
        except ApiError as exc:
            logger.warning("share card for %s not saved: %s", game_id, exc)


# --- invites, gifts, promo codes --------------------------------------------------------------


@router.message(Command("invite"))
async def cmd_invite(message: Message, api: GameApiClient) -> None:
    track(api, message.chat.id, "invite_opened")
    try:
        invite = await api.invite(message.chat.id)
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR)
        return
    link = deep_link(await bot_username(message.bot), f"ref_{invite.ref_code}")
    await message.answer(
        texts.invite_text(link, invite.balance),
        reply_markup=keyboards.share_link_kb(link, texts.INVITE_SHARE_TEXT),
    )


@router.message(Command("gift"))
async def cmd_gift(message: Message, api: GameApiClient) -> None:
    try:
        status = await api.get_user_status(message.chat.id)
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR)
        return
    if status.paid_games_available <= 0:
        await message.answer(texts.GIFT_NO_CREDITS)
        return
    await message.answer(
        texts.GIFT_OFFER.format(balance=status.paid_games_available),
        reply_markup=keyboards.create_gift_kb(),
    )


@router.callback_query(F.data == "gift:create")
async def on_create_gift(callback: CallbackQuery, api: GameApiClient) -> None:
    await callback.answer()
    msg = callback_message(callback)
    if msg is None:
        return
    # One tap — one gift: the button goes before the call, so a double tap can't spend twice.
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    try:
        gift = await api.create_gift(callback.from_user.id)
    except ApiError as exc:
        await msg.answer(texts.GIFT_NO_CREDITS if exc.status_code == 409 else texts.GUIDE_THINKING_ERROR)
        return
    link = deep_link(await bot_username(callback.bot), f"gift_{gift.code}")
    await msg.answer(
        texts.gift_created_text(link, gift.balance),
        reply_markup=keyboards.share_link_kb(link, texts.INVITE_SHARE_TEXT),
    )


@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject, api: GameApiClient) -> None:
    if not command.args:
        await message.answer(texts.PROMO_USAGE)
        return
    try:
        result = await api.redeem_promo(message.chat.id, command.args.split()[0])
    except ApiError:
        await message.answer(texts.GUIDE_THINKING_ERROR)
        return
    await message.answer(link_outcome_text(result) or texts.LINK_OUTCOME_TEXTS[("promo", "invalid")])


# --- inline mode ------------------------------------------------------------------------------


@router.inline_query()
async def on_inline_query(query: InlineQuery, api: GameApiClient) -> None:
    try:
        cards = await api.user_cards(query.from_user.id)
    except ApiError:
        await query.answer([], cache_time=5, is_personal=True)
        return
    link = deep_link(await bot_username(query.bot), f"ref_{cards.ref_code}")
    caption = f"{texts.INVITE_SHARE_TEXT} {link}"
    results: list[InlineQueryResultUnion] = [
        InlineQueryResultCachedPhoto(
            id=f"card{i}",
            photo_file_id=card.file_id,
            caption=caption,
            reply_markup=keyboards.play_kb(link),
        )
        for i, card in enumerate(cards.cards)
    ]
    results.append(
        InlineQueryResultArticle(
            id="invite",
            title=texts.BTN_PLAY,
            description=texts.INVITE_SHARE_TEXT,
            input_message_content=InputTextMessageContent(message_text=caption),
            reply_markup=keyboards.play_kb(link),
        )
    )
    await query.answer(results, cache_time=30, is_personal=True)


@router.chosen_inline_result()
async def on_chosen_inline_result(chosen: ChosenInlineResult, api: GameApiClient) -> None:
    kind = "invite" if chosen.result_id == "invite" else "card"
    track(api, chosen.from_user.id, "inline_shared", kind=kind)
