from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot import texts


def start_game_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_START_GAME, callback_data="start_game")],
            [InlineKeyboardButton(text=texts.BTN_ABOUT, callback_data="about_game")],
        ]
    )


def roll_kb(retry_key: str | None = None) -> InlineKeyboardMarkup:
    """`retry_key` re-sends a failed roll under its original idempotency key, so a roll that did
    reach the API is replayed instead of being rolled a second time."""
    callback_data = f"roll:{retry_key}" if retry_key else "roll"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_ROLL, callback_data=callback_data)]]
    )


def retry_summary_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_RETRY_SUMMARY, callback_data=f"retry_summary:{game_id}")]
        ]
    )


def response_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_SKIP, callback_data="skip_response")]]
    )


def gender_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_GENDER_MALE, callback_data="set_gender:male"),
                InlineKeyboardButton(text=texts.BTN_GENDER_FEMALE, callback_data="set_gender:female"),
            ],
            [InlineKeyboardButton(text=texts.BTN_GENDER_SKIP, callback_data="set_gender:skip")],
        ]
    )


def paywall_kb(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_PAY, url=pay_url)]]
    )


def confirm_new_game_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_CONFIRM_NEW_GAME, callback_data="confirm_new_game")],
            [InlineKeyboardButton(text=texts.BTN_CANCEL_NEW_GAME, callback_data="cancel_new_game")],
        ]
    )


def reminder_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_CONTINUE_GAME, callback_data=f"continue_game:{game_id}")],
            [
                InlineKeyboardButton(
                    text=texts.BTN_SNOOZE_REMINDER, callback_data=f"snooze_reminder:{game_id}"
                ),
                InlineKeyboardButton(text=texts.BTN_OPT_OUT_REMINDERS, callback_data="opt_out_reminders"),
            ],
        ]
    )
