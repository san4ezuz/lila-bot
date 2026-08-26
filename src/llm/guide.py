"""Guide message generation — STUB implementation for Stage 4 (Game API).

Every function here returns deterministic, non-LLM text so the game mechanics (Stage 4) can be
verified end-to-end before the LLM is wired up (Stage 5, see docs/PLAN.md §8). Stage 5 replaces
the *bodies* of these functions with real `anthropic` calls (interpret_cell / deepen / summarize
from the plan) while keeping the same signatures, so `src/services/game_service.py` — and every
test written against it — does not need to change.
"""

from src.core.board import Cell
from src.db.models import Move


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


def entered_message(cell: Cell) -> str:
    return (
        f"Шестёрка — ты вошёл в игру. Фишка на клетке {cell.id}, «{cell.name}». "
        f"{cell.theme} {cell.facets[0]} {cell.questions[0]}"
    )


def blocked_overshoot_message(cell: Cell) -> str:
    return (
        f"Этот бросок унёс бы фишку дальше края доски — ход сгорает, ты остаёшься на клетке "
        f"{cell.id}, «{cell.name}». Бросай ещё раз."
    )


def moved_message(cell: Cell, transition: str | None) -> str:
    lead = {
        "arrow": f"Стрела поднимает тебя к клетке {cell.id}, «{cell.name}».",
        "snake": f"Змея уводит тебя вниз, к клетке {cell.id}, «{cell.name}».",
        None: f"Фишка на клетке {cell.id}, «{cell.name}».",
    }[transition]
    return f"{lead} {cell.theme} {cell.facets[0]} {cell.questions[0]}"


def finished_message(cell: Cell) -> str:
    return (
        f"Ты дошёл до клетки {cell.id}, «{cell.name}» — путь пройден. {cell.theme} "
        f"{cell.questions[-1]}"
    )


def turn_limit_message() -> str:
    return (
        "Партия подошла к своему пределу ходов, хотя ты не успел дойти до финальной клетки — "
        "и в этом тоже есть смысл. Сейчас соберём то, что удалось пройти, в единую картину."
    )


def response_message(player_text: str, safety_flag: str | None) -> str:
    if safety_flag == "soft_referral":
        return (
            "Спасибо, что рассказал об этом. То, о чём ты пишешь, звучит как что-то серьёзное, "
            "с чем стоит поговорить с живым специалистом, а не только с игрой. Если тебе тяжело "
            "прямо сейчас, пожалуйста, обратись за поддержкой — я рядом, если хочешь продолжить "
            "или на сегодня остановиться здесь."
        )
    return "Слышу тебя. Идём дальше — бросай кубик, когда будешь готов."


def summary_message(moves: list[Move], cells: dict[int, Cell]) -> str:
    visited = [cells[m.to_pos].name for m in moves if m.to_pos is not None and m.to_pos in cells]
    path = " → ".join(visited) if visited else "путь ещё не начался"
    return (
        f"Вот путь, который ты прошёл за эту партию: {path}. "
        "Каждая клетка на этом пути — не случайность, а отражение того, с чем ты работал. "
        "Что из пройденного отзывается в тебе сильнее всего прямо сейчас?"
    )
