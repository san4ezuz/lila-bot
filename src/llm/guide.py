"""Guide message generation.

Procedural moments (entry attempt failed, overshoot bounced, turn limit hit) stay static text —
there's nothing to interpret, only a mechanic to report, and burning an LLM call there would
just add latency and cost for no product value (docs/PLAN.md §8.1). The three calls that
actually need the LLM — interpret_cell / deepen / summarize — are implemented here against the
Anthropic Messages API; see docs/PLAN.md §8 for the design and docs/PROMPTS.md for the prompt
version history. On any API failure, each falls back to a short static message rather than
losing the player's move (docs/PLAN.md §8.5).
"""

import asyncio
import logging
from typing import Literal

import anthropic

from src.config import get_settings
from src.core.board import Cell
from src.llm.client import extract_text, get_client
from src.llm.prompts import (
    GUIDE_SYSTEM_PROMPT,
    build_deepen_context,
    build_interpret_cell_context,
    build_summary_context,
)

logger = logging.getLogger(__name__)

_CALL_TIMEOUT_SECONDS = 40

Effort = Literal["low", "medium", "high", "xhigh", "max"]


def _system_block() -> list[dict]:
    return [{"type": "text", "text": GUIDE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


async def _call(
    context: str,
    max_tokens: int,
    effort: Effort,
    fallback: str,
) -> str:
    client = get_client()
    settings = get_settings()
    try:
        # The dict literals below are the plain wire shapes from Anthropic's Messages API
        # (docs/PLAN.md §8.2); typing them as the SDK's exact TypedDicts is more ceremony than
        # value here, so this one call is exempted from strict overload matching.
        message = await asyncio.wait_for(
            client.messages.create(  # type: ignore[call-overload]
                model=settings.llm_model,
                max_tokens=max_tokens,
                system=_system_block(),
                messages=[{"role": "user", "content": context}],
                output_config={"effort": effort},
            ),
            timeout=_CALL_TIMEOUT_SECONDS,
        )
    except (TimeoutError, anthropic.APIError) as exc:
        logger.warning("guide LLM call failed, using fallback: %s", exc)
        return fallback

    text = extract_text(message)
    return text or fallback


# --- static, procedural (no LLM call) -----------------------------------------------------


def intro_message() -> str:
    return (
        "Здравствуй. Я буду твоим проводником в игре Лила — путешествии от клетки к клетке, "
        "где выпавшее число и твои собственные ответы складываются в историю. Прежде чем "
        "бросить кубик в первый раз, расскажи коротко: с каким вопросом или запросом ты сейчас "
        "приходишь к этой игре?"
    )


def intention_ack_message(intention: str) -> str:
    return (
        f"Понимаю. Ты пришёл с этим: «{intention}». Буду держать это в уме на протяжении всего "
        "пути. Чтобы войти в игру, нужно выбросить 6 — бросай, когда будешь готов."
    )


def not_entered_message(roll: int) -> str:
    return f"Выпало {roll}. Чтобы войти в игру, нужна ровно шестёрка — попробуй ещё раз."


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


def safety_referral_message() -> str:
    """Fixed, vetted supportive text used INSTEAD of the LLM when the layer-1 lexical prescreen
    (src/llm/safety.py) fires. A crisis disclosure is not the place to trust an unproven model
    response even with the boundaries instruction in the system prompt — see docs/PROMPTS.md."""
    return (
        "Спасибо, что рассказал об этом. То, о чём ты пишешь, звучит как что-то серьёзное, "
        "с чем стоит поговорить с живым специалистом, а не только с игрой. Если тебе тяжело "
        "прямо сейчас, пожалуйста, обратись за поддержкой — я рядом, если хочешь продолжить "
        "или на сегодня остановиться здесь."
    )


# --- LLM calls -------------------------------------------------------------------------------


async def interpret_cell(
    intention: str | None,
    cell: Cell,
    event: str,
    transition: str | None,
    visited: list[str],
    recent_player_answers: list[str],
) -> str:
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
    )
    fallback = (
        f"Фишка на клетке {cell.id}, «{cell.name}». {cell.theme} {cell.questions[0]}"
    )
    return await _call(context, max_tokens=400, effort="medium", fallback=fallback)


async def deepen(intention: str | None, cell: Cell, player_text: str) -> str:
    context = build_deepen_context(intention, cell.name, player_text)
    fallback = "Слышу тебя. Идём дальше — бросай кубик, когда будешь готов."
    return await _call(context, max_tokens=350, effort="medium", fallback=fallback)


async def summarize_game(intention: str | None, visited: list[str]) -> str:
    context = build_summary_context(intention, visited)
    path = " → ".join(visited) if visited else "путь ещё не начался"
    fallback = (
        f"Вот путь, который ты прошёл за эту партию: {path}. "
        "Что из пройденного отзывается в тебе сильнее всего прямо сейчас?"
    )
    return await _call(context, max_tokens=900, effort="high", fallback=fallback)
