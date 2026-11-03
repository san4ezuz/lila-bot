"""Guide message generation.

Procedural moments (entry attempt failed, overshoot bounced, turn limit hit) stay static text —
there's nothing to interpret, only a mechanic to report, and burning an LLM call there would
just add latency and cost for no product value (docs/PLAN.md §8.1). The three calls that
actually need the LLM — interpret_cell / deepen / summarize — are implemented here against the
Anthropic Messages API; see docs/PLAN.md §8 for the design and docs/PROMPTS.md for the prompt
version history. On any API failure, each falls back to a short static message rather than
losing the player's move (docs/PLAN.md §8.5).
"""

from src.core.board import Cell
from src.llm.client import Effort, LlmKind, LlmResult, call_model, skipped_result
from src.llm.prompts import (
    GUIDE_SYSTEM_PROMPT,
    build_deepen_context,
    build_interpret_cell_context,
    build_not_entered_context,
    build_summary_context,
)

_CALL_TIMEOUT_SECONDS = 40

# Hard ceilings, not length targets: reply length is set by the word limits in the system prompt.
# Thinking counts against max_tokens, so a ceiling near the expected reply can leave no text at all.
_MAX_TOKENS_SHORT = 1000
_MAX_TOKENS_REPLY = 2000
_MAX_TOKENS_SUMMARY = 3000


def _system_block() -> list[dict]:
    return [{"type": "text", "text": GUIDE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _gendered(male: str, female: str, neutral: str, gender: str | None) -> str:
    if gender == "male":
        return male
    if gender == "female":
        return female
    return neutral


async def _call(
    context: str,
    max_tokens: int,
    effort: Effort,
    fallback: str,
    kind: LlmKind,
    use_llm: bool = True,
) -> LlmResult:
    """`use_llm=False` — an LLM limit was hit (src/services/llm_limits.py): answer with the
    fallback without calling the model."""
    if not use_llm:
        return skipped_result(kind, fallback)
    return await call_model(
        kind=kind,
        system=_system_block(),
        content=context,
        max_tokens=max_tokens,
        effort=effort,
        fallback=fallback,
        timeout=_CALL_TIMEOUT_SECONDS,
    )


# --- static, procedural (no LLM call) -----------------------------------------------------


def intro_message() -> str:
    return (
        "Здравствуй. Я буду твоим проводником в игре Лила — путешествии от клетки к клетке, "
        "где выпавшее число и твои собственные ответы складываются в историю. Прежде чем "
        "бросить кубик в первый раз, расскажи коротко: с каким вопросом или запросом ты сейчас "
        "приходишь к этой игре?"
    )


def intention_ack_message(intention: str, gender: str | None = None) -> str:
    came = _gendered("пришёл", "пришла", "пришёл(-а)", gender)
    return (
        f"Понимаю. Ты {came} с этим: «{intention}». Буду держать это в уме на протяжении всего "
        "пути. Чтобы войти в игру, по правилам «Лилы» нужно выбросить шестёрку — бросай кубик."
    )


def first_try_entry_message(intention: str | None, gender: str | None = None) -> str:
    """Roll #1 landed a natural six — the rare, lucky case. Deliberately not run through
    interpret_cell(): every game starts on this same cell, so a deep LLM reading of it would
    say the same thing to every player on turn one. Celebrate the roll and the request instead
    (per product feedback: "поздравить и сказать что запрос реально важен, особо не
    анализировать")."""
    entered = _gendered("вошёл", "вошла", "вошёл(-а)", gender)
    if intention:
        request_line = f" Ты сразу {entered} с запросом «{intention}» — и это само по себе важно: он поведёт тебя дальше."
    else:
        request_line = f" Ты сразу {entered} в игру."
    return (
        "Шестёрка с первого броска — редкая удача, поздравляю!" + request_line + " Клетку "
        "«Заблуждение» разбирать подробно не будем — с неё начинают все, настоящий путь впереди."
    )


def forced_entry_message() -> str:
    return (
        "Шестёрка так и не выпала — но иногда дверь открывается не по нашей готовности, а по "
        "своему часу. Ты входишь в игру."
    )


def six_extra_roll_note() -> str:
    """Rolling a 6 always grants another roll — the classic rule, treated here as always a
    "yes" from the board (product feedback: "это типо всегда «да», нужно красиво объяснять")."""
    return "🎲 Шестёрка — это всегда «да»: держи ещё один бросок прямо сейчас."


def blocked_overshoot_message(cell: Cell) -> str:
    return (
        f"Этот бросок унёс бы фишку дальше края доски — ход сгорает, ты остаёшься на клетке "
        f"{cell.id}, «{cell.name}». Бросай ещё раз."
    )


def turn_limit_message() -> str:
    return (
        "Партия подошла к своему пределу ходов, хотя ты не успел дойти до финальной клетки — "
        "и в этом тоже есть смысл. Сейчас соберём то, что удалось пройти, в единую картину."
    )


def cell_reply_limit_message() -> str:
    """The per-cell reply limit is hit (src/services/llm_limits.py) — a nudge onward instead of
    another model reply on the same cell."""
    return (
        "Ты уже много сказал(а) об этой клетке — пусть это побудет с тобой. "
        "Бросай кубик, путь продолжается."
    )


def safety_referral_message(asks_intention: bool = False) -> str:
    """Fixed, vetted supportive text used INSTEAD of the LLM when the crisis screen
    (src/llm/safety.py) fires. A crisis disclosure is not the place to trust an unproven model
    response even with the boundaries instruction in the system prompt — see docs/PROMPTS.md.
    Help contacts are appended by the bot (texts.SAFETY_HELP_LINE). `asks_intention` — the text
    was the game request, which isn't saved, so the player is asked for another one."""
    message = (
        "Спасибо, что поделился(-ась) этим. То, о чём ты пишешь, звучит серьёзно, и с этим "
        "стоит поговорить с живым человеком, а не только с игрой. Если тебе тяжело прямо "
        "сейчас, пожалуйста, позвони по одному из номеров ниже — там выслушают."
    )
    if asks_intention:
        return (
            f"{message}\n\nИгра никуда не денется. Если захочешь сыграть — напиши, с каким "
            "вопросом ты приходишь, или вернись в другой день."
        )
    return f"{message}\n\nЯ рядом, если хочешь продолжить или на сегодня остановиться здесь."


# --- LLM calls -------------------------------------------------------------------------------


async def not_entered_message(
    intention: str | None,
    roll: int,
    attempt: int,
    guarantee_after: int,
    gender: str | None = None,
    use_llm: bool = True,
) -> LlmResult:
    """`attempt` is how many entry rolls have been made so far (including this one); once it
    reaches `guarantee_after`, apply_roll() lets the player in regardless of the roll — see
    ENTRY_GUARANTEE_ATTEMPTS in src/core/rules.py. Rather than a mechanical "attempt N of M"
    (found to feel like pressure), the LLM turns the not-yet-entered moment into a brief nudge
    to reflect on the player's own intention — why the door isn't opening yet, what they're
    waiting for. Attempt/guarantee numbers are only fed to the LLM as private context to shift
    tone near the guarantee, never asked to be recited to the player."""
    context = build_not_entered_context(
        intention, roll, near_guarantee=attempt >= guarantee_after - 1, gender=gender
    )
    fallback = f"Выпало {roll} — не шестёрка. Бросай ещё раз, дорога откроется."
    return await _call(
        context, _MAX_TOKENS_SHORT, "low", fallback, kind="not_entered", use_llm=use_llm
    )


async def interpret_cell(
    intention: str | None,
    cell: Cell,
    event: str,
    transition: str | None,
    visited: list[str],
    recent_player_answers: list[str],
    skipped_cell: Cell | None = None,
    gender: str | None = None,
    use_llm: bool = True,
) -> LlmResult:
    context = build_interpret_cell_context(
        intention=intention,
        cell_id=cell.id,
        cell_name=cell.name,
        theme=cell.theme,
        facets=list(cell.facets),
        questions=list(cell.questions),
        event=event,
        transition=transition,
        visited=visited[-15:],
        recent_player_answers=recent_player_answers[-2:],
        skipped_cell_id=skipped_cell.id if skipped_cell else None,
        skipped_cell_name=skipped_cell.name if skipped_cell else None,
        skipped_cell_theme=skipped_cell.theme if skipped_cell else None,
        gender=gender,
    )
    fallback = (
        f"Фишка на клетке {cell.id}, «{cell.name}». {cell.theme} {cell.questions[0]}"
    )
    return await _call(
        context, _MAX_TOKENS_REPLY, "low", fallback, kind="interpret_cell", use_llm=use_llm
    )


async def deepen(
    intention: str | None,
    cell: Cell,
    player_text: str,
    gender: str | None = None,
    use_llm: bool = True,
) -> LlmResult:
    context = build_deepen_context(intention, cell.name, player_text, gender=gender)
    fallback = "Слышу тебя. Идём дальше — бросай кубик, когда будешь готов."
    return await _call(context, _MAX_TOKENS_REPLY, "low", fallback, kind="deepen", use_llm=use_llm)


async def summarize_game(
    intention: str | None,
    visited: list[str],
    gender: str | None = None,
    use_llm: bool = True,
) -> LlmResult:
    context = build_summary_context(intention, visited, gender=gender)
    path = " → ".join(visited) if visited else "путь ещё не начался"
    fallback = (
        f"Вот путь, который ты прошёл за эту партию: {path}. "
        "Что из пройденного отзывается в тебе сильнее всего прямо сейчас?"
    )
    return await _call(
        context, _MAX_TOKENS_SUMMARY, "low", fallback, kind="summary", use_llm=use_llm
    )
