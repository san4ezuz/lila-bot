from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot import texts


def start_game_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_START_GAME, callback_data="start_game")]]
    )


def roll_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_ROLL, callback_data="roll")]]
    )


def response_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_SKIP, callback_data="skip_response")]]
    )


def paywall_kb(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_PAY, url=pay_url)]]
    )
