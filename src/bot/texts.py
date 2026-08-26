"""All bot-facing static text lives here so it can be edited without touching handler code
(docs/PLAN.md §9.1). Anything that depends on game content (cell interpretations, summaries)
comes from the API's `guide_message` field instead — this module is UI chrome only."""

BTN_START_GAME = "Начать партию"
BTN_ROLL = "🎲 Бросить кубик"
BTN_SKIP = "Пропустить"
BTN_PAY = "Оплатить"

WELCOME_BACK = "С возвращением. Готов продолжить партию?"

ASK_FOR_TEXT_RESPONSE = "Можешь ответить текстом — или нажми «Пропустить»."

SKIPPED_RESPONSE_ACK = "Хорошо, идём дальше."

FINISHED_SUFFIX = "\n\nПартия завершена. Спасибо, что прошёл этот путь. Как тебе игра — что бы ты сказал одним словом?"
FEEDBACK_THANKS = "Спасибо! Если захочешь пройти путь ещё раз — я здесь."

PROGRESS_LINE = "Клетка {position} · ход {turn} из {turn_limit}"

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
