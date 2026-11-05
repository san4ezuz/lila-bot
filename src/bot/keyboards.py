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


def cell_kb(with_reactions: bool = True) -> InlineKeyboardMarkup:
    """Under a cell reading: one tap rolls on (no separate "skip" step), and an optional quick
    reaction for players who don't want to write. The reaction tap leaves only "Дальше"."""
    rows = [[InlineKeyboardButton(text=texts.BTN_NEXT, callback_data="next")]]
    if with_reactions:
        rows.append(
            [
                InlineKeyboardButton(text=texts.BTN_REACT_YES, callback_data="react:yes"),
                InlineKeyboardButton(text=texts.BTN_REACT_NO, callback_data="react:no"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def intention_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"intention:{key}")]
        for key, label, _ in texts.INTENTION_PRESETS
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_INTENTION_OWN, callback_data="intention:own")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

