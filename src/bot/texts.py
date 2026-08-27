"""All bot-facing static text lives here so it can be edited without touching handler code
(docs/PLAN.md §9.1). Anything that depends on game content (cell interpretations, summaries)
comes from the API's `guide_message` field instead — this module is UI chrome only.

The bot renders messages with parse_mode=HTML (src/bot/main.py). Static strings here that embed
HTML tags (<b>, <blockquote>) are written by us and trusted; anything interpolated from the API
(cell names, LLM-generated guide_message, player text) MUST be passed through `esc()` first —
see src/bot/utils.py — or a stray '<'/'&' in a Claude response breaks message delivery outright.
"""

BTN_START_GAME = "🎲 Начать партию"
BTN_ABOUT = "ℹ️ Что это за игра?"
BTN_ROLL = "🎲 Бросить кубик"
BTN_SKIP = "Пропустить"
BTN_PAY = "Оплатить"
BTN_CONTINUE_GAME = "▶️ Продолжить партию"
BTN_SNOOZE_REMINDER = "Напомнить позже"
BTN_OPT_OUT_REMINDERS = "Больше не напоминать"
BTN_NEW_GAME = "🆕 Новая игра"
BTN_CONFIRM_NEW_GAME = "Да, начать заново"
BTN_CANCEL_NEW_GAME = "Нет, продолжить текущую"
BTN_GENDER_MALE = "Мужской"
BTN_GENDER_FEMALE = "Женский"
BTN_GENDER_SKIP = "Не говорить"

MENU_BUTTON_TEXT = "🎲 Играть"

CONFIRM_NEW_GAME_TEXT = (
    "Точно хочешь начать новую партию? Текущая будет считаться завершённой, и следующая "
    "партия — платная."
)
CANCEL_NEW_GAME_ACK = "Хорошо, продолжаем текущую партию."

WELCOME_SHORT = (
    "Здравствуй. Я проводник в игре «Лила» — путешествии от клетки к клетке, где выпавшее "
    "число и твои собственные ответы складываются в историю."
)

def about_game_text(price: int) -> str:
    """`price` comes from settings.price_second_game_rub — passed in rather than hardcoded so
    this stays correct if pricing ever changes without touching this module."""
    return (
        "<blockquote expandable>"
        "<b>Лила</b> — древняя индийская игра самопознания. Ты бросаешь кубик, фишка движется по "
        "72 клеткам, и на каждой я помогаю увидеть, что эта клетка может значить для тебя именно "
        "сейчас — в контексте твоего собственного запроса.\n\n"
        "<b>Правила коротко:</b>\n"
        "— Чтобы войти в игру, нужно выбросить шестёрку. Не везёт сразу — не страшно: если "
        "шестёрка долго не идёт, через несколько попыток дорога откроется сама.\n"
        "— Шестёрка — это всегда «да»: после неё даётся ещё один бросок, в любой момент партии.\n"
        "— На доске есть стрелы (резкий подъём вперёд) и змеи (откат назад, урок) — если фишка "
        "попадает на такую клетку, я коротко назову ту, что проскочили, и почему переход случился "
        "именно там.\n"
        "— Партия заканчивается, когда фишка точно попадает на клетку 68 — финал пути.\n\n"
        "Партия обычно занимает от 15 до 40 ходов — можно пройти за один присест или возвращаться "
        "к ней по частям, ничего не потеряется.\n\n"
        "<b>Анонимность:</b> партия привязана только к твоему Telegram-аккаунту — я не прошу и не "
        "храню имя, фамилию или другие личные данные, только то, что ты сам решишь мне рассказать "
        "по ходу игры.\n\n"
        f"<b>Оплата:</b> первая партия бесплатна. Каждая следующая — {price} ₽.\n\n"
        "Я не терапевт, не гуру и не предсказываю будущее. Я предлагаю ракурсы для размышления, "
        "а не готовые ответы — что откликнется, решаешь ты."
        "</blockquote>"
    )


ASK_GENDER_TEXT = "Чтобы обращаться к тебе правильно — как к тебе лучше обращаться?"

WELCOME_BACK = "С возвращением! Продолжим партию?"

ASK_FOR_TEXT_RESPONSE = "Можешь ответить текстом — или нажми «Пропустить»."

SKIPPED_RESPONSE_ACK = "Хорошо, идём дальше."

FINISHED_SUFFIX = "\n\nПартия завершена. Спасибо, что прошёл этот путь. Как тебе игра — что бы ты сказал одним словом?"
FEEDBACK_THANKS = "Спасибо! Если захочешь пройти путь ещё раз — я здесь."

# {cell_id} and {cell_name} must already be HTML-escaped by the caller before formatting in.
CELL_HEADER = "📍 <b>Клетка {cell_id} — {cell_name}</b>"

_TRANSITION_LABELS = {"arrow": "🏹 стрела", "snake": "🐍 змея"}


def move_header(
    roll: int,
    from_position: int | None,
    raw_position: int | None,
    final_position: int | None,
    transition: str | None,
    event: str,
) -> str | None:
    """Deterministic, per-roll line — "what the dice did" — rendered above the LLM's
    interpretation of the cell so the mechanic (roll, movement, snake/arrow) stays visually
    separate from the narrative text. All inputs come straight off RollResponse; nothing here
    depends on game content, so it belongs in this UI-chrome module, not the guide layer."""
    if event == "not_entered" or final_position is None:
        return None
    if event == "entered":
        return f"<i>🎲 Выпало: {roll} · входим на клетку {final_position}</i>"
    if event == "blocked_overshoot":
        return f"<i>🎲 Выпало: {roll} · ход не помещается на доску, остаёшься на клетке {final_position}</i>"
    if transition is not None and raw_position is not None:
        label = _TRANSITION_LABELS.get(transition, transition)
        return (
            f"<i>🎲 Выпало: {roll} · клетка {from_position} → {raw_position} → "
            f"{label} → клетка {final_position}</i>"
        )
    return f"<i>🎲 Выпало: {roll} · клетка {from_position} → {final_position}</i>"

PAYWALL_MESSAGE = (
    "Первая партия уже сыграна. Следующая — платная: {price} ₽.\n"
    "Если готов продолжить путь, оплати по ссылке ниже."
)

GUIDE_THINKING_ERROR = "Проводник задумался. Попробуй ещё раз через момент."

SAFETY_HELP_LINE = (
    "\n\nЕсли тебе нужна поддержка прямо сейчас: Телефон доверия — 8-800-2000-122 (бесплатно, "
    "круглосуточно)."
)

MESSAGE_TOO_LONG = "Сообщение получилось длинным — сократи его немного, пожалуйста."
MAX_PLAYER_MESSAGE_LENGTH = 1500

# --- reminders (docs/UX_ITERATION_1.md §2) --------------------------------------------------
# The reminder message text itself lives in src/services/reminder_service.py, not here — it's
# sent by a standalone cron script via the raw Bot API, outside the aiogram process, so it can't
# reach into this module's f-string-with-cell-name formatting the way handlers do. This module
# only holds what the bot process itself renders in reaction to a tap on a reminder's buttons.

REMINDER_SNOOZED_ACK = "Хорошо, напомню позже."
REMINDER_OPTED_OUT_ACK = "Понял, больше не буду напоминать про незавершённые партии."
