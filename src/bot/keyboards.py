from urllib.parse import quote

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



def card_offer_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_CARD_STORY, callback_data=f"card:story:{game_id}:0")],
            [InlineKeyboardButton(text=texts.BTN_CARD_SQUARE, callback_data=f"card:square:{game_id}:0")],
        ]
    )


def card_kb(game_id: str, format: str, with_intention: bool, has_intention: bool) -> InlineKeyboardMarkup:
    """Under a sent card: the other format, the request on or off, and sending it to a chat
    through inline mode (`@bot` — the player's cards come up there)."""
    other = "square" if format == "story" else "story"
    other_label = texts.BTN_CARD_SQUARE if format == "story" else texts.BTN_CARD_STORY
    rows = [[InlineKeyboardButton(text=texts.BTN_SEND_TO_FRIEND, switch_inline_query="")]]
    if has_intention:
        label = texts.BTN_CARD_REMOVE_INTENTION if with_intention else texts.BTN_CARD_ADD_INTENTION
        flag = 0 if with_intention else 1
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"card:{format}:{game_id}:{flag}")]
        )
    rows.append(
        [InlineKeyboardButton(text=other_label, callback_data=f"card:{other}:{game_id}:{int(with_intention)}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_link_kb(link: str, text: str) -> InlineKeyboardMarkup:
    """Telegram's own share sheet: the player picks a chat, the link goes with `text`."""
    url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_SHARE_LINK, url=url)]]
    )


def create_gift_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_CREATE_GIFT, callback_data="gift:create")]]
    )


def play_kb(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_PLAY, url=link)]]
    )
